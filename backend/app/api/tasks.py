import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import orchestrator
from app.agent.audit import log_action
from app.agent.llm import get_llm
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import (
    AgentActionLog,
    AgentRun,
    ApprovalRequest,
    ApprovalStatus,
    Customer,
    Document,
    Email,
    EmailDirection,
    ReplyDraft,
    Task,
    TaskStatus,
    User,
)
from app.schemas import (
    ApprovalDecision,
    ApprovalOut,
    CancelTaskIn,
    CloseTaskIn,
    CompleteTaskIn,
    CustomerOut,
    DashboardOut,
    DeadlineIn,
    DocumentOut,
    EmailOut,
    GoalIn,
    GoalUpdateIn,
    MailExtras,
    NoReplyIn,
    MergeIn,
    MergeUndoIn,
    PriorityIn,
    PromoteOut,
    ReplyConfirmIn,
    ReplyDraftIn,
    ReplyDraftOut,
    ReplyDraftUpdateIn,
    RunOut,
    SnoozeIn,
    TaskBrief,
    TaskDetail,
    TodoOpIn,
)
from app.mail import changes as mail_changes
from app.mail import inbox as mail_inbox
from app.mail import lifecycle
from app.mail import merge as mail_merge
from app.google.client import get_google_client
from app.mail import reply_draft
from app.mail import reply_send
from app.mail import sync as mail_sync
from app.mail import task_actions
from app.mail.task_actions import ActionError
from app.security import agent_limiter, ai_action_limiter, calendar_retry_limiter, get_current_user, send_limiter

router = APIRouter(prefix="/api", tags=["tasks"])


def get_owned(db: Session, model, obj_id: int, user: User):
    """Fetch a row only if it belongs to the current user; otherwise 404 (never reveal existence)."""
    obj = db.scalar(select(model).where(model.id == obj_id, model.user_id == user.id))
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "요청한 데이터를 찾을 수 없습니다.")
    return obj


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Task.status, func.count()).where(Task.user_id == user.id).group_by(Task.status)).all()
    counts = {s.value: 0 for s in TaskStatus}
    counts.update({st: n for st, n in rows})
    tasks = db.scalars(select(Task).where(Task.user_id == user.id).order_by(Task.updated_at.desc())).all()
    pending = db.scalar(select(func.count()).select_from(ApprovalRequest).where(
        ApprovalRequest.user_id == user.id, ApprovalRequest.status == ApprovalStatus.PENDING))
    s = get_settings()
    return DashboardOut(counts=counts, tasks=tasks, pending_approvals=pending or 0,
                        agent_configured=bool(s.llm_api_key), model=f"{s.llm_provider} · {s.llm_model}")


@router.post("/tasks", response_model=TaskBrief, status_code=201)
def create_task(body: GoalIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agent_limiter.check(f"agent:{user.id}")
    goal = body.goal.strip()
    title = (body.title or "").strip() or (goal[:40] + ("…" if len(goal) > 40 else ""))
    task = Task(user_id=user.id, title=title, goal=goal, status=TaskStatus.PENDING, origin="user",
                current_step="업무 목표 접수", next_action="Agent가 목표를 분석합니다.")
    db.add(task)
    db.flush()
    orchestrator.start_run(db, user, task, "goal_submitted")
    db.refresh(task)
    return task


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def task_detail(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = get_owned(db, Task, task_id, user)
    return build_task_detail(db, task, user)


def build_task_detail(db: Session, task: Task, user: User) -> TaskDetail:
    uid = user.id
    emails = db.scalars(select(Email).where(Email.user_id == uid, Email.task_id == task.id).order_by(Email.created_at)).all()
    logs = db.scalars(select(AgentActionLog).where(AgentActionLog.user_id == uid, AgentActionLog.task_id == task.id)
                      .order_by(AgentActionLog.id)).all()
    approvals = db.scalars(select(ApprovalRequest).where(ApprovalRequest.user_id == uid, ApprovalRequest.task_id == task.id)
                           .order_by(ApprovalRequest.id.desc())).all()
    email_by_id = {e.id: e for e in emails}
    approvals_out = []
    for a in approvals:
        out = ApprovalOut.model_validate(a)
        if a.email_id and a.email_id in email_by_id:
            out.email = EmailOut.model_validate(email_by_id[a.email_id])
        approvals_out.append(out)
    runs = db.scalars(select(AgentRun).where(AgentRun.user_id == uid, AgentRun.task_id == task.id).order_by(AgentRun.id.desc())).all()
    followups = db.scalars(select(Task).where(Task.user_id == uid, Task.parent_task_id == task.id).order_by(Task.id)).all()
    parent = get_owned(db, Task, task.parent_task_id, user) if task.parent_task_id else None
    active = next((r for r in runs if r.status in orchestrator.ACTIVE_RUN_STATUSES), None)
    has_sent = any(e.direction == EmailDirection.OUTBOUND for e in emails)
    return TaskDetail(
        task=task, customer=task.customer, parent=parent, followups=followups, emails=emails, logs=logs,
        approvals=approvals_out, runs=[RunOut.model_validate(r) for r in runs[:10]],
        active_run=RunOut.model_validate(active) if active else None, max_steps=get_settings().max_agent_steps,
        can_simulate_reply=get_settings().demo_mode and has_sent and active is None and task.agent_enabled and task.status not in orchestrator.CLOSED_TASK_STATUSES,
        mail=_mail_extras(db, task, emails) if task.origin == "mail" else None,
    )


def _merged_sources(db: Session, task: Task) -> list[dict]:
    """이 업무로 바로 합쳐진 원본 업무들. 합친 순서대로."""
    sources = db.scalars(select(Task).where(Task.user_id == task.user_id, Task.merged_into_id == task.id)
                         .order_by(Task.completed_at, Task.id)).all()
    out = []
    for s in sources:
        summary = (s.brief or {}).get("summary")
        out.append({"task_id": s.id, "title": s.title, "summary": summary if isinstance(summary, str) else "",
                    "merged_at": s.completed_at})
    return out


def _mail_extras(db: Session, task: Task, emails: list[Email]) -> MailExtras:
    now = mail_inbox.now_utc()
    tz = get_settings().user_timezone
    inbound = [e for e in emails if e.direction == EmailDirection.INBOUND]
    latest = max(inbound, key=lambda e: (mail_inbox._utc(e.received_at or e.created_at), e.id), default=None)
    latest_at = mail_inbox._utc(latest.received_at or latest.created_at) if latest else None
    wake = mail_inbox.snooze_wake_at(task, tz)
    candidates = mail_inbox.merge_candidates(db, task, now)
    source_ids = mail_merge.undoable_sources(task, now)
    titles = {}
    if source_ids:
        titles = dict(db.execute(select(Task.id, Task.title).where(Task.user_id == task.user_id,
                                                                    Task.id.in_(source_ids))).all())
    return MailExtras(
        is_snoozed=mail_inbox.is_snoozed(task, now, tz), wake_at=wake,
        returned=mail_inbox.returned_at(task, now, tz) is not None,
        nudge_days=mail_inbox.nudge_days(task, latest_at, now, tz),
        merge_candidates=[{"task_id": c.id, "title": c.title, "evidence": mail_inbox.merge_evidence(task, c)}
                          for c in candidates],
        undoable_merges=[{"source_id": i, "title": titles.get(i, "")} for i in source_ids],
        merged_sources=_merged_sources(db, task),
        merged_into_id=task.merged_into_id, deadline_badge=mail_inbox.deadline_badge(task, now, tz),
        priority_source=task.priority_source,
        no_reply=mail_inbox.no_reply_active(task), reply_sent_at=task.reply_sent_at,
        draft=_draft_out(db, task, now),
        remaining_todos_prompt=reply_send.remaining_todos_prompt(task),
        merge_analyzing=task.merged_into_id is None and (task.merge_analysis or {}).get("state") == "pending",
    )


def _draft_out(db: Session, task: Task, now: datetime) -> ReplyDraftOut | None:
    d = db.scalar(select(ReplyDraft).where(ReplyDraft.user_id == task.user_id, ReplyDraft.task_id == task.id))
    if d is None:
        return None
    warnings = d.warnings if isinstance(d.warnings, dict) else {}
    unknown = reply_send.is_unknown(d, now)  # 5분 넘게 sending에 멈춘 초안도 결과 불분명으로 보인다
    return ReplyDraftOut(
        purpose=d.purpose, to_addr=d.to_addr, cc_candidates=list(d.cc_candidates or []),
        cc_selected=list(d.cc_selected or []), subject=d.subject, body=d.body,
        status="unknown" if unknown else d.status, warnings=warnings,
        can_send=d.status == "draft" and not reply_draft.placeholders(d.body),
        unknown_since=d.sending_at if unknown else None, updated_at=d.updated_at,
        replied_after=d.status == "draft" and reply_send.replied_after_draft(db, task, d),
    )


def task_fingerprint(db: Session, task: Task) -> tuple:
    """Cheap summary of everything the task view shows; when it is unchanged, nothing needs to be pushed."""
    db.refresh(task)
    last_log = db.scalar(select(func.max(AgentActionLog.id)).where(AgentActionLog.task_id == task.id))
    runs = db.execute(select(AgentRun.id, AgentRun.status, AgentRun.steps, AgentRun.updated_at)
                      .where(AgentRun.task_id == task.id).order_by(AgentRun.id.desc()).limit(1)).first()
    mail_count = db.scalar(select(func.count()).select_from(Email).where(Email.task_id == task.id))
    snoozed = task.snoozed_until.isoformat() if task.snoozed_until else ""
    undo = len(task.merge_undo) if isinstance(task.merge_undo, dict) else 0
    return (task.updated_at.isoformat() if task.updated_at else "", task.status, last_log, tuple(runs) if runs else None,
            snoozed, undo, mail_count or 0)


SSE_POLL_SECONDS = 0.4
SSE_HEARTBEAT_SECONDS = 15.0


def _task_snapshot(task_id: int, uid: int, previous: tuple | None) -> tuple[tuple | None, str | None]:
    """(fingerprint, payload) — payload is None when nothing changed, fingerprint is None when the task is gone."""
    with SessionLocal() as s:
        task = s.scalar(select(Task).where(Task.id == task_id, Task.user_id == uid))
        if task is None:
            return None, None
        fp = task_fingerprint(s, task)
        if fp == previous:
            return fp, None
        return fp, build_task_detail(s, task, s.get(User, uid)).model_dump_json()


async def task_event_frames(task_id: int, uid: int, is_disconnected):
    """SSE frames for one task: the current view immediately, then every change, with keep-alive comments."""
    last: tuple | None = None
    last_sent = time.monotonic()
    while not await is_disconnected():
        last, payload = await run_in_threadpool(_task_snapshot, task_id, uid, last)
        if last is None:
            yield "event: gone\ndata: {}\n\n"
            return
        if payload is not None:
            yield f"event: task\ndata: {payload}\n\n"
            last_sent = time.monotonic()
        elif time.monotonic() - last_sent > SSE_HEARTBEAT_SECONDS:
            yield ": ping\n\n"
            last_sent = time.monotonic()
        await asyncio.sleep(SSE_POLL_SECONDS)


@router.get("/tasks/{task_id}/events", include_in_schema=False)
async def task_events(task_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Server-sent events: the full task view, pushed immediately and then whenever it changes."""
    get_owned(db, Task, task_id, user)  # 404 before the stream starts
    return StreamingResponse(task_event_frames(task_id, user.id, request.is_disconnected), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/tasks/{task_id}/agent/retry", response_model=RunOut)
def retry_agent(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    agent_limiter.check(f"agent:{user.id}")
    task = get_owned(db, Task, task_id, user)
    if task.status in orchestrator.CLOSED_TASK_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 완료되었거나 취소된 업무입니다.")
    try:
        return orchestrator.start_run(db, user, task, "manual_retry")
    except orchestrator.AgentBusyError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.post("/tasks/{task_id}/calendar/retry", response_model=TaskDetail)
def retry_calendar(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Put a task whose calendar sync failed back in the queue; the sync worker picks it up right away."""
    task = get_owned(db, Task, task_id, user)
    if task.origin != "mail":
        raise HTTPException(status.HTTP_409_CONFLICT, "메일 업무만 캘린더에 반영합니다.")
    if task.calendar_state == "deleted_by_user":
        raise HTTPException(status.HTTP_409_CONFLICT, "캘린더에서 삭제된 일정은 다시 만들지 않습니다.")
    calendar_retry_limiter.check(f"cal:{user.id}")  # after the checks above so rejected calls cost nothing
    task.calendar_attempts = 0
    task.calendar_state = "pending"
    task.calendar_error = ""
    db.commit()
    mail_sync.wake()
    db.refresh(task)
    return build_task_detail(db, task, user)


@router.post("/tasks/{task_id}/cancel", response_model=TaskBrief)
def cancel_task(task_id: int, body: CancelTaskIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Close the task for good. The agent is stopped, pending approvals are voided, history stays."""
    task = get_owned(db, Task, task_id, user)
    try:
        orchestrator.cancel_task(db, user, task, body.reason)
    except orchestrator.AgentBusyError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    db.refresh(task)
    return task


# ------------------------------------------------------------------ 받은 업무: 메일 업무 동작

def _mail_task(db: Session, task_id: int, user: User) -> Task:
    task = get_owned(db, Task, task_id, user)
    if task.origin != "mail":
        raise HTTPException(status.HTTP_409_CONFLICT, "메일 업무에서만 쓸 수 있습니다.")
    return task


def _act(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ActionError as e:
        raise HTTPException(e.status, str(e))


def _done(db: Session, task: Task, user: User) -> TaskDetail:
    db.commit()
    db.refresh(task)
    if task.calendar_state == "pending":
        mail_sync.wake()  # 캘린더 반영이 필요해졌으면 다음 주기(90초)를 기다리지 않는다
    return build_task_detail(db, task, user)


@router.post("/tasks/{task_id}/snooze", response_model=TaskDetail)
def snooze_task(task_id: int, body: SnoozeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    _act(task_actions.snooze, task, preset=body.preset, until=body.until, now=datetime.now(timezone.utc),
         tz=get_settings().user_timezone)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/unsnooze", response_model=TaskDetail)
def unsnooze_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    _act(task_actions.unsnooze, task)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/priority", response_model=TaskDetail)
def set_task_priority(task_id: int, body: PriorityIn, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    _act(task_actions.set_priority, task, body.priority)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/deadline", response_model=TaskDetail)
def set_task_deadline(task_id: int, body: DeadlineIn, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    _act(task_actions.set_deadline, task, date=body.date, time=body.time, clear=body.clear, confirm=body.confirm,
         tz=get_settings().user_timezone)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/todos", response_model=TaskDetail)
def edit_task_todos(task_id: int, body: TodoOpIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    _act(task_actions.edit_todos, task, body.model_dump(exclude_none=True))
    return _done(db, task, user)


@router.post("/tasks/{task_id}/close", response_model=TaskDetail)
def close_task(task_id: int, body: CloseTaskIn, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """답장 필요 없음으로 닫기."""
    task = _mail_task(db, task_id, user)
    _act(task_actions.close_without_reply, db, user, task, body.reason)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/no-reply", response_model=TaskDetail)
def answer_no_reply(task_id: int, body: NoReplyIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """상대 답장 없음 물음에 답한다: 다시 연락 · 더 기다리기 · 완료로 닫기."""
    task = _mail_task(db, task_id, user)
    _act(lifecycle.answer_no_reply, db, user, task, body.choice, datetime.now(timezone.utc))
    return _done(db, task, user)


@router.post("/tasks/{task_id}/changes/{change_id}/undo", response_model=TaskDetail)
def undo_task_change(task_id: int, change_id: int, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """상대 재답장으로 바뀐 항목 하나를 이전 값으로 되돌린다."""
    task = _mail_task(db, task_id, user)
    _act(mail_changes.undo_change, task, change_id)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/changes/{change_id}/apply", response_model=TaskDetail)
def apply_task_change(task_id: int, change_id: int, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """사용자가 정한 기한을 상대가 바꾼 제안을 반영한다."""
    task = _mail_task(db, task_id, user)
    _act(mail_changes.apply_suggestion, task, change_id, get_settings().user_timezone)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/seen", response_model=TaskDetail)
def mark_task_seen(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """화면이 업무 상세를 열 때 부른다: "내용 바뀜" 표시를 끈다."""
    task = _mail_task(db, task_id, user)
    task.changed_unseen = False
    return _done(db, task, user)


@router.post("/tasks/{task_id}/reply-draft", response_model=TaskDetail)
def create_reply_draft(task_id: int, body: ReplyDraftIn, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """답장 초안을 만든다(있으면 대체). 받는 사람은 서버가 정한다."""
    task = _mail_task(db, task_id, user)
    _act(reply_draft.ensure_can_generate, db, task)
    ai_action_limiter.check(f"ai:{user.id}")  # after the checks above so rejected calls cost nothing
    _act(reply_draft.generate_draft, db, task, user, get_llm(), purpose=body.purpose, user_input=body.user_input,
         tz=get_settings().user_timezone)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/reply-draft/update", response_model=TaskDetail)
def update_reply_draft(task_id: int, body: ReplyDraftUpdateIn, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    draft = _act(reply_draft.find_draft, db, task)
    _act(reply_draft.update_draft, draft, subject=body.subject, body=body.body, cc_selected=body.cc_selected)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/reply-draft/delete", response_model=TaskDetail)
def delete_reply_draft(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    draft = _act(reply_draft.find_draft, db, task)
    _act(reply_draft.delete_draft, db, draft)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/reply-draft/send", response_model=TaskDetail)
def send_reply_draft(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """승인한 초안을 원래 Gmail 스레드로 보낸다. 받는 사람은 서버가 다시 확인한다. 결과가 불분명하면 unknown."""
    task = _mail_task(db, task_id, user)
    draft = _act(reply_draft.find_draft, db, task)
    _act(reply_send.ensure_can_send, db, user, task, draft)
    send_limiter.check(f"send:{user.id}")  # after the checks above so rejected calls cost nothing
    _act(reply_send.send_draft, db, user, task, draft, get_google_client(), datetime.now(timezone.utc))
    return _done(db, task, user)


@router.post("/tasks/{task_id}/reply-draft/confirm", response_model=TaskDetail)
def confirm_reply_sent(task_id: int, body: ReplyConfirmIn, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """발송 결과가 불분명한 초안: 사용자가 보낸편지함에서 확인한 결과(5분 뒤부터)."""
    task = _mail_task(db, task_id, user)
    draft = _act(reply_draft.find_draft, db, task)
    _act(reply_send.confirm_unknown, db, user, task, draft, sent=body.sent, now=datetime.now(timezone.utc))
    return _done(db, task, user)


@router.post("/tasks/{task_id}/todos/complete-all", response_model=TaskDetail)
def complete_all_todos(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """남은 할 일을 모두 완료로 정리한다(답장을 보낸 뒤 안내)."""
    task = _mail_task(db, task_id, user)
    reply_send.complete_remaining_todos(task)
    return _done(db, task, user)


@router.post("/tasks/{task_id}/reanalyze", response_model=TaskDetail)
def reanalyze_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _mail_task(db, task_id, user)
    _act(task_actions.ensure_reanalyzable, task)
    ai_action_limiter.check(f"ai:{user.id}")  # after the checks above so rejected calls cost nothing
    _act(task_actions.request_reanalyze, task)
    detail = _done(db, task, user)
    mail_sync.wake()
    return detail


@router.post("/tasks/{task_id}/merge", response_model=TaskDetail)
def merge_task(task_id: int, body: MergeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """이 업무(B)를 같은 상대의 기존 업무(A)에 합친다. 응답은 A."""
    source = _mail_task(db, task_id, user)
    target = get_owned(db, Task, body.into_task_id, user)
    _act(mail_merge.merge, db, user, source, target, datetime.now(timezone.utc))
    detail = _done(db, target, user)
    if (target.merge_analysis or {}).get("state") == "pending" and target.calendar_state != "pending":
        mail_sync.wake()  # 합친 내용 분석은 워커가 바로 이어서 한다(설계 7.3)
    return detail


@router.post("/tasks/{task_id}/merge/undo", response_model=TaskDetail)
def undo_merge_task(task_id: int, body: MergeUndoIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """합친 대상(A)에서 10분 안에 원본 하나(source_id)의 합치기를 되돌린다."""
    target = _mail_task(db, task_id, user)
    _act(mail_merge.undo_merge, db, user, target, body.source_id, datetime.now(timezone.utc))
    return _done(db, target, user)


@router.post("/tasks/{task_id}/merge/dismiss", response_model=TaskDetail)
def dismiss_merge_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """합치기 제안 '따로 두기'."""
    task = _mail_task(db, task_id, user)
    _act(mail_merge.dismiss_merge, task)
    return _done(db, task, user)


@router.post("/emails/{email_id}/promote", response_model=PromoteOut)
def promote_email(email_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """'업무 아님' · '자동 필터' 메일을 업무로 처리한다. 분석은 워커가 이어서 한다."""
    email = get_owned(db, Email, email_id, user)
    _act(task_actions.ensure_promotable, user, email)
    ai_action_limiter.check(f"ai:{user.id}")
    task = _act(task_actions.promote_email, db, user, email)
    db.commit()
    mail_sync.wake()
    return PromoteOut(task_id=task.id)


@router.post("/tasks/{task_id}/goal", response_model=TaskBrief)
def update_goal(task_id: int, body: GoalUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rewrite the goal. An agent-driven task is re-planned right away; a follow-up task just gets the new text."""
    task = get_owned(db, Task, task_id, user)
    if task.status in orchestrator.CLOSED_TASK_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 완료되었거나 취소된 업무의 목표는 수정할 수 없습니다.")
    if orchestrator.active_run(db, task) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent가 실행 중이거나 승인을 기다리고 있습니다. 실행이 끝난 뒤 수정하세요.")
    new_goal = body.goal.strip()
    if new_goal == task.goal:
        return task
    previous = task.goal
    task.goal = new_goal
    log_action(db, user_id=user.id, task_id=task.id, run_id=None, action_type="GOAL_UPDATED",
               input_data={"from": previous, "to": new_goal}, reason="사용자가 업무 목표를 수정했습니다.")
    db.commit()
    if task.agent_enabled:
        agent_limiter.check(f"agent:{user.id}")
        try:
            orchestrator.start_run(db, user, task, "goal_updated", {"previous_goal": previous})
        except orchestrator.AgentBusyError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/complete", response_model=TaskBrief)
def complete_followup(task_id: int, body: CompleteTaskIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The user closes a follow-up task the agent created for them (agent-driven tasks are closed by the agent)."""
    task = get_owned(db, Task, task_id, user)
    if task.origin != "agent" or task.agent_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent가 처리하는 업무는 Agent가 완료합니다. 후속 업무만 직접 완료할 수 있습니다.")
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 완료된 업무입니다.")
    note = body.note.strip()
    before = task.status
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(timezone.utc)
    task.current_step = "사용자가 완료 처리"
    task.next_action = ""
    if note:
        task.result_summary = note
    log_action(db, user_id=user.id, task_id=task.id, run_id=None, action_type="USER_COMPLETED",
               input_data={"from": before, "to": TaskStatus.COMPLETED}, output_data={"note": note},
               reason=note or "사용자가 후속 업무를 완료 처리했습니다.")
    if task.parent_task_id:
        log_action(db, user_id=user.id, task_id=task.parent_task_id, run_id=None, action_type="FOLLOWUP_COMPLETED",
                   input_data={"followup_task_id": task.id}, output_data={"note": note},
                   reason=f"후속 업무 '{task.title}' 완료" + (f" — {note}" if note else ""))
    db.commit()
    db.refresh(task)
    return task


@router.post("/approvals/{approval_id}/decide", response_model=RunOut)
def decide(approval_id: int, body: ApprovalDecision, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    approval = get_owned(db, ApprovalRequest, approval_id, user)
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 처리된 승인 요청입니다.")
    try:
        return orchestrator.decide_approval(db, user, approval, body.approved, body.comment,
                                            edit={"subject": body.subject, "body": body.body})
    except orchestrator.AgentBusyError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.get("/customers", response_model=list[CustomerOut])
def customers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Customer).where(Customer.user_id == user.id).order_by(Customer.id)).all()


@router.get("/documents", response_model=list[DocumentOut])
def documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.id)).all()


@router.get("/emails", response_model=list[EmailOut])
def emails(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Email).where(Email.user_id == user.id, Email.direction != EmailDirection.DRAFT)
                      .order_by(Email.created_at.desc())).all()
