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
    Task,
    TaskStatus,
    User,
)
from app.schemas import (
    ApprovalDecision,
    ApprovalOut,
    CompleteTaskIn,
    CustomerOut,
    DashboardOut,
    DocumentOut,
    EmailOut,
    GoalIn,
    RunOut,
    TaskBrief,
    TaskDetail,
)
from app.security import agent_limiter, get_current_user

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
        can_simulate_reply=has_sent and active is None and task.agent_enabled,
    )


def task_fingerprint(db: Session, task: Task) -> tuple:
    """Cheap summary of everything the task view shows; when it is unchanged, nothing needs to be pushed."""
    db.refresh(task)
    last_log = db.scalar(select(func.max(AgentActionLog.id)).where(AgentActionLog.task_id == task.id))
    runs = db.execute(select(AgentRun.id, AgentRun.status, AgentRun.steps, AgentRun.updated_at)
                      .where(AgentRun.task_id == task.id).order_by(AgentRun.id.desc()).limit(1)).first()
    return (task.updated_at.isoformat() if task.updated_at else "", task.status, last_log, tuple(runs) if runs else None)


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
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 완료된 업무입니다.")
    try:
        return orchestrator.start_run(db, user, task, "manual_retry")
    except orchestrator.AgentBusyError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


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
