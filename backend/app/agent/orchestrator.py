"""Agent loop: LLM decides → tool runs → result goes back to the LLM → repeat.

Runs are persisted (messages, step count) so they can pause for user approval and resume later,
and new runs are started by events (goal submitted, customer reply) from the saved task state.
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.audit import log_action
from app.agent.llm import LLMError, add_usage, get_llm, normalize_usage
from app.agent.prompts import REMINDER_SET_STATUS, SYSTEM_PROMPT
from app.agent.tools import TOOLS, ToolContext, ToolError, serialize_output, tool_definitions
from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    AgentActionLog,
    AgentRun,
    ApprovalRequest,
    ApprovalStatus,
    Email,
    EmailDirection,
    RiskLevel,
    RunStatus,
    Task,
    TaskStatus,
    User,
)

log = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = (RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL)
RETRY_BACKOFF_SECONDS = (3.0, 8.0)  # wait before the 1st, 2nd, ... in-run retry of a transient LLM failure


class AgentBusyError(Exception):
    pass


# ------------------------------------------------------------------ dispatch (background threads)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
_dispatch: Callable[[int], None] = lambda run_id: _executor.submit(execute_run, run_id)  # noqa: E731


def set_dispatcher(fn: Callable[[int], None] | None) -> None:
    """Tests run the loop inline instead of on a thread."""
    global _dispatch
    _dispatch = fn or (lambda run_id: _executor.submit(execute_run, run_id))


_task_locks: dict[int, threading.RLock] = {}
_locks_guard = threading.Lock()


def _task_lock(task_id: int) -> threading.RLock:
    with _locks_guard:
        return _task_locks.setdefault(task_id, threading.RLock())


# ------------------------------------------------------------------ run creation

def active_run(db: Session, task: Task) -> AgentRun | None:
    return db.scalar(select(AgentRun).where(AgentRun.task_id == task.id, AgentRun.status.in_(ACTIVE_RUN_STATUSES)))


def _task_state_json(db: Session, task: Task) -> str:
    customer = task.customer
    emails = db.scalars(select(Email).where(Email.user_id == task.user_id, Email.task_id == task.id)
                        .order_by(Email.created_at)).all()
    state = {
        "task_id": task.id, "title": task.title, "status": task.status, "current_step": task.current_step,
        "next_action": task.next_action, "waiting_for": task.waiting_for, "plan": task.plan or [],
        "customer": {"customer_id": customer.id, "company_name": customer.company_name} if customer else None,
        "emails_in_this_task": [
            {"email_id": e.id, "direction": e.direction, "subject": e.subject, "at": e.created_at.isoformat(timespec="minutes")}
            for e in emails
        ],
    }
    return json.dumps(state, ensure_ascii=False, indent=1)


def _history(db: Session, task: Task) -> str:
    logs = db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == task.id, AgentActionLog.user_id == task.user_id)
                      .order_by(AgentActionLog.id.desc()).limit(25)).all()
    lines = [f"- {l.created_at.isoformat(timespec='minutes')} {l.action_type} {l.tool_name} [{l.status}] {l.reason[:120]}"
             for l in reversed(logs)]
    return "\n".join(lines) or "(없음)"


def build_event_message(db: Session, task: Task, trigger: str, event: dict) -> str:
    if trigger == "goal_submitted":
        event_text = "사용자가 새 업무 목표를 등록했습니다. 목표 분석부터 시작하세요."
    elif trigger == "email_received":
        event_text = (f"이 업무와 관련된 새 메일이 도착했습니다 (email_id={event.get('email_id')}, "
                      f"보낸사람={event.get('sender')}, 제목={event.get('subject')!r}). "
                      "check_email과 analyze_email을 한 턴에 함께 호출해 내용을 확인·분석한 뒤 다음 행동을 결정하세요.")
    else:
        event_text = "사용자가 Agent 재시도를 요청했습니다. 저장된 상태와 이력을 보고 중단된 지점부터 이어가세요."
    return (
        f"[이벤트] {event_text}\n\n"
        f"<user_goal>\n{task.goal}\n</user_goal>\n\n"
        f"<task_state>\n{_task_state_json(db, task)}\n</task_state>\n\n"
        f"<recent_agent_history>\n{_history(db, task)}\n</recent_agent_history>\n\n"
        f"현재 시각: {datetime.now(timezone.utc).isoformat(timespec='minutes')}"
    )


def start_run(db: Session, user: User, task: Task, trigger: str, event: dict | None = None) -> AgentRun:
    if not task.agent_enabled:
        raise AgentBusyError("이 업무는 사용자가 직접 처리하는 후속 업무입니다.")
    # The "is a run active?" check and the insert happen under the task lock so two concurrent
    # requests (double-click, reply event racing a retry) cannot both start a run.
    with _task_lock(task.id):
        if active_run(db, task):
            raise AgentBusyError("이 업무에서 Agent가 이미 실행 중이거나 승인을 기다리고 있습니다.")
        return _create_run(db, user, task, trigger, event or {})


def _create_run(db: Session, user: User, task: Task, trigger: str, event: dict) -> AgentRun:
    run = AgentRun(user_id=user.id, task_id=task.id, trigger=trigger, status=RunStatus.RUNNING,
                   messages=[{"role": "user", "content": build_event_message(db, task, trigger, event)}])
    db.add(run)
    db.flush()
    task.status = TaskStatus.IN_PROGRESS
    task.waiting_for = ""
    label = {"goal_submitted": "업무 목표 접수 — Agent 시작", "email_received": "새 메일 이벤트 — Agent 재실행",
             "manual_retry": "사용자 요청으로 Agent 재시도"}.get(trigger, trigger)
    log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="AGENT_STARTED",
               input_data={"trigger": trigger, **event}, reason=label)
    db.commit()
    _dispatch(run.id)
    return run


# ------------------------------------------------------------------ approval decisions

def decide_approval(db: Session, user: User, approval: ApprovalRequest, approved: bool, comment: str = "",
                    edit: dict | None = None) -> AgentRun:
    """`edit` may carry a user-rewritten `subject` / `body` for the draft; it is applied only when approving."""
    with _task_lock(approval.task_id):
        return _decide_approval(db, user, approval, approved, comment, edit or {})


def _apply_user_edit(db: Session, approval: ApprovalRequest, edit: dict) -> dict | None:
    """Rewrite the pending draft with the user's text. Returns the original text when something changed."""
    if approval.email_id is None:
        return None
    email = db.scalar(select(Email).where(Email.id == approval.email_id, Email.user_id == approval.user_id,
                                          Email.direction == EmailDirection.DRAFT))
    if email is None:
        return None
    original = {"subject": email.subject, "body": email.content}
    changed = False
    for field, attr in (("subject", "subject"), ("body", "content")):
        value = (edit.get(field) or "").strip()
        if value and value != getattr(email, attr):
            setattr(email, attr, value)
            changed = True
    return original if changed else None


def _decide_approval(db: Session, user: User, approval: ApprovalRequest, approved: bool, comment: str, edit: dict) -> AgentRun:
    db.refresh(approval)  # another request may have decided it while we waited for the lock
    run = db.get(AgentRun, approval.run_id)
    task = db.get(Task, approval.task_id)
    if approval.status != ApprovalStatus.PENDING:
        raise AgentBusyError("이미 처리된 승인 요청입니다.")
    if run is None or task is None or run.user_id != user.id or run.status != RunStatus.AWAITING_APPROVAL:
        raise AgentBusyError("승인 대기 중인 Agent 실행을 찾을 수 없습니다.")
    spec = TOOLS[approval.tool_name]
    now = datetime.now(timezone.utc)
    approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    approval.decided_at = now
    approval.user_comment = comment[:500]
    original = _apply_user_edit(db, approval, edit) if approved else None
    decision = {"approved": approved, "comment": comment[:500], "edited": original is not None}
    if original is not None:
        decision["original"] = original
    log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="APPROVAL_DECIDED", tool_name=spec.name,
               input_data={"approval_id": approval.id}, output_data=decision,
               reason=("사용자가 내용을 수정한 뒤 승인했습니다" if original is not None else "사용자가 승인했습니다")
               if approved else "사용자가 취소했습니다",
               risk_level=RiskLevel.HIGH, status="APPROVED" if approved else "REJECTED")

    if approved:
        ctx = ToolContext(db=db, user=user, task=task, run=run, llm=_NoLLM())
        try:
            output = spec.handler(ctx, spec.parse(approval.tool_input))
            if original is not None:
                output["edited_by_user"] = True
                output["note"] = "사용자가 제목/본문을 수정한 뒤 승인해 발송되었습니다. " + output.get("note", "")
            result = {"type": "tool_result", "tool_use_id": approval.tool_use_id, "content": serialize_output(output)}
            status = "SUCCESS"
        except ToolError as e:
            output = {"error": str(e)}
            result = {"type": "tool_result", "tool_use_id": approval.tool_use_id, "content": str(e), "is_error": True}
            status = "ERROR"
        log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="TOOL_CALL", tool_name=spec.name,
                   input_data=approval.tool_input, output_data=output, reason=approval.reason,
                   risk_level=spec.risk, status=status)
    else:
        output = {"status": "rejected_by_user", "user_comment": comment[:500]}
        result = {"type": "tool_result", "tool_use_id": approval.tool_use_id, "content": serialize_output(output)}

    run.messages = [*run.messages, {"role": "user", "content": [*run.pending_results, result]}]
    run.pending_results = []
    run.steps = 0  # a human approval is a checkpoint: the autonomous step budget starts over
    run.status = RunStatus.RUNNING
    task.status = TaskStatus.IN_PROGRESS
    task.waiting_for = ""
    db.commit()
    _dispatch(run.id)
    return run


class _NoLLM:
    def agent_turn(self, *a, **k):
        raise LLMError("not available")

    def structured(self, *a, **k):
        raise LLMError("not available")


# ------------------------------------------------------------------ the loop

def execute_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        if run is None:
            return
        with _task_lock(run.task_id):
            _loop(db, run_id)
    except Exception:
        log.exception("agent run %s crashed", run_id)
        db.rollback()
        run = db.get(AgentRun, run_id)
        if run is not None and run.status == RunStatus.RUNNING:
            _fail(db, run, "내부 오류로 Agent 실행이 중단되었습니다. 'Agent 재시도'를 눌러주세요.")
    finally:
        db.close()


def _loop(db: Session, run_id: int) -> None:
    s = get_settings()
    run = db.get(AgentRun, run_id)
    task = db.get(Task, run.task_id) if run else None
    user = db.get(User, run.user_id) if run else None
    if run is None or task is None or user is None or run.status != RunStatus.RUNNING:
        return

    try:
        llm = get_llm()
    except LLMError as e:
        _fail(db, run, str(e))
        return

    def on_wait(seconds: float, reason: str) -> None:
        log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="LLM_WAIT",
                   reason=f"{reason} — {seconds:.0f}초 후 자동으로 이어서 실행합니다.", output_data={"seconds": round(seconds)})
        db.commit()

    if hasattr(llm, "on_wait"):
        llm.on_wait = on_wait

    tools = tool_definitions()
    deadline = time.monotonic() + s.agent_timeout_seconds
    reminded = False
    retries = 0  # consecutive transient failures of the current turn

    while True:
        db.refresh(run)
        if run.status != RunStatus.RUNNING:
            return
        if run.steps >= s.max_agent_steps:
            _stop(db, run, task, "STEP_LIMIT",
                  f"안전상의 이유로 최대 실행 횟수({s.max_agent_steps}단계)를 초과해 Agent를 중단했습니다. 사용자의 확인이 필요합니다.")
            return
        if time.monotonic() > deadline:
            _stop(db, run, task, "TIMEOUT",
                  f"실행 시간 제한({s.agent_timeout_seconds}초)을 초과해 Agent를 중단했습니다. 사용자의 확인이 필요합니다.")
            return

        try:
            resp = llm.agent_turn(SYSTEM_PROMPT, tools, run.messages)
        except LLMError as e:
            if e.retryable and retries < s.llm_transient_retries:
                wait = RETRY_BACKOFF_SECONDS[min(retries, len(RETRY_BACKOFF_SECONDS) - 1)]
                if time.monotonic() + wait < deadline:
                    retries += 1
                    log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="LLM_RETRY",
                               reason=f"LLM 일시 오류: {e} — {wait:.0f}초 후 자동 재시도 ({retries}/{s.llm_transient_retries})",
                               output_data={"wait_seconds": wait, "attempt": retries}, risk_level=RiskLevel.MEDIUM, status="RETRY")
                    db.commit()
                    time.sleep(wait)
                    continue
            _fail(db, run, f"{e} ({retries}회 자동 재시도 후에도 실패)" if retries else str(e))
            return
        retries = 0
        run.steps += 1  # counted only for turns the LLM actually answered

        run.messages = [*run.messages, {"role": "assistant", "content": resp.content}]
        turn_usage = normalize_usage(resp.usage)
        run.usage = add_usage(run.usage, turn_usage)
        if resp.text and resp.stop_reason != "end_turn":  # the final summary is logged by AGENT_FINISHED
            log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="AGENT_THOUGHT",
                       reason=resp.text[:1500],
                       output_data={"stop_reason": resp.stop_reason, "request_id": resp.request_id, "usage": turn_usage})
        db.commit()

        if resp.stop_reason == "tool_use":
            outcome = _handle_tool_uses(db, llm, user, task, run, resp.tool_uses)
            if outcome == "paused":
                return
            if outcome == "settled":
                # The agent committed a task status in an error-free turn: end the run here instead of
                # spending another LLM request only to produce a closing summary.
                _finish(db, run, task, user, _status_summary(task, resp.text))
                return
            continue

        if resp.stop_reason == "pause_turn":
            continue

        if resp.stop_reason == "end_turn":
            db.refresh(task)
            if task.status == TaskStatus.IN_PROGRESS and not reminded and run.steps < s.max_agent_steps:
                reminded = True
                run.messages = [*run.messages, {"role": "user", "content": REMINDER_SET_STATUS}]
                db.commit()
                continue
            _finish(db, run, task, user, resp.text)
            return

        _fail(db, run, f"LLM 응답이 예상과 다르게 종료되었습니다(stop_reason={resp.stop_reason}). 'Agent 재시도'를 눌러주세요.")
        return


SETTLED_STATUSES = (TaskStatus.WAITING, TaskStatus.ACTION_REQUIRED, TaskStatus.BLOCKED, TaskStatus.COMPLETED)


def _status_summary(task: Task, text: str) -> str:
    if text:
        return text
    label = {TaskStatus.WAITING: "대기", TaskStatus.ACTION_REQUIRED: "사용자 조치 필요",
             TaskStatus.BLOCKED: "중단", TaskStatus.COMPLETED: "완료"}.get(task.status, task.status)
    parts = [f"업무 상태: {label}" + (f" ({task.waiting_for})" if task.waiting_for else "")]
    if task.status == TaskStatus.COMPLETED and task.result_summary:
        parts.append(task.result_summary)
    else:
        parts += [p for p in (task.current_step, task.next_action) if p]
    return " · ".join(parts)


def _handle_tool_uses(db: Session, llm, user: User, task: Task, run: AgentRun, tool_uses: list[dict]) -> str:
    """Execute tool calls from one assistant turn.

    Returns "paused" (waiting for approval), "settled" (task status committed with no errors) or "continue".
    """
    ctx = ToolContext(db=db, user=user, task=task, run=run, llm=llm)
    results: list[dict] = []
    approval: ApprovalRequest | None = None
    status_committed = False

    for tu in tool_uses:
        name, raw, tu_id = tu.get("name", ""), tu.get("input") or {}, tu["id"]
        reason = str(raw.get("reason", ""))[:300] if isinstance(raw, dict) else ""
        spec = TOOLS.get(name)
        if spec is None:
            msg = f"허용되지 않은 도구입니다: {name}"
            results.append({"type": "tool_result", "tool_use_id": tu_id, "content": msg, "is_error": True})
            log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="TOOL_BLOCKED", tool_name=name[:50],
                       input_data=raw, reason=msg, risk_level=RiskLevel.HIGH, status="BLOCKED")
            continue
        try:
            args = spec.parse(raw)
            if spec.requires_approval:
                if approval is not None:
                    raise ToolError("한 번에 하나의 승인 요청만 가능합니다. 이전 요청의 결과를 기다리세요.")
                preview = spec.prepare(ctx, args) if spec.prepare else {}
                approval = ApprovalRequest(user_id=user.id, task_id=task.id, run_id=run.id, tool_use_id=tu_id,
                                           tool_name=name, tool_input=args.model_dump(), email_id=preview.get("email_id"),
                                           reason=args.reason, status=ApprovalStatus.PENDING)
                db.add(approval)
                db.flush()
                log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="APPROVAL_REQUESTED",
                           tool_name=name, input_data=args.model_dump(), output_data=preview, reason=args.reason,
                           risk_level=spec.risk, status="PENDING")
                continue
            output = spec.handler(ctx, args)
            status_committed = status_committed or name == "update_task_status"
            results.append({"type": "tool_result", "tool_use_id": tu_id, "content": serialize_output(output)})
            log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="TOOL_CALL", tool_name=name,
                       input_data=args.model_dump(), output_data=output, reason=args.reason, risk_level=spec.risk)
        except (ToolError, LLMError) as e:
            results.append({"type": "tool_result", "tool_use_id": tu_id, "content": f"오류: {e}", "is_error": True})
            log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="TOOL_CALL", tool_name=name,
                       input_data=raw, output_data={"error": str(e)}, reason=reason, risk_level=spec.risk, status="ERROR")
        db.commit()

    if approval is not None:
        run.pending_results = results
        run.status = RunStatus.AWAITING_APPROVAL
        task.status = TaskStatus.ACTION_REQUIRED
        task.waiting_for = ""
        task.current_step = "이메일 초안 작성 완료"
        task.next_action = "AI가 작성한 메일을 확인하고 [승인] 또는 [취소]를 선택하세요."
        db.commit()
        return "paused"

    run.messages = [*run.messages, {"role": "user", "content": results}]
    db.commit()
    if status_committed and task.status in SETTLED_STATUSES and not any(r.get("is_error") for r in results):
        return "settled"
    return "continue"


# ------------------------------------------------------------------ terminal states

def _finish(db: Session, run: AgentRun, task: Task, user: User, text: str) -> None:
    now = datetime.now(timezone.utc)
    run.status = RunStatus.FINISHED
    run.final_message = text[:3000]
    run.finished_at = now
    if task.status == TaskStatus.IN_PROGRESS:
        task.status = TaskStatus.ACTION_REQUIRED
        task.next_action = "Agent가 업무 상태를 확정하지 못했습니다. 내용을 확인하거나 'Agent 재시도'를 눌러주세요."
    log_action(db, user_id=user.id, task_id=task.id, run_id=run.id, action_type="AGENT_FINISHED",
               output_data={"task_status": task.status, "steps": run.steps}, reason=text[:1500] or "Agent 실행 종료")
    db.commit()

    # An email that arrived while this run was busy is picked up now (only emails newer than the run,
    # so an email the agent chose not to open cannot re-trigger runs forever).
    if task.status == TaskStatus.WAITING:
        pending = db.scalar(select(Email).where(Email.user_id == user.id, Email.task_id == task.id,
                                                Email.direction == EmailDirection.INBOUND, Email.processed.is_(False),
                                                Email.created_at > run.created_at))
        if pending is not None:
            start_run(db, user, task, "email_received",
                      {"email_id": pending.id, "sender": pending.sender, "subject": pending.subject})


def _stop(db: Session, run: AgentRun, task: Task, kind: str, message: str) -> None:
    run.status = RunStatus.STOPPED
    run.error = message
    run.finished_at = datetime.now(timezone.utc)
    task.status = TaskStatus.BLOCKED
    task.next_action = message
    log_action(db, user_id=run.user_id, task_id=task.id, run_id=run.id, action_type=kind, reason=message,
               risk_level=RiskLevel.HIGH, status="STOPPED", output_data={"steps": run.steps})
    db.commit()


def _fail(db: Session, run: AgentRun, message: str) -> None:
    run.status = RunStatus.FAILED
    run.error = message
    run.finished_at = datetime.now(timezone.utc)
    task = db.get(Task, run.task_id)
    if task is not None and task.status in (TaskStatus.IN_PROGRESS, TaskStatus.PENDING):
        task.status = TaskStatus.BLOCKED
        task.next_action = message
    log_action(db, user_id=run.user_id, task_id=run.task_id, run_id=run.id, action_type="AGENT_FAILED", reason=message,
               risk_level=RiskLevel.HIGH, status="ERROR")
    db.commit()


def recover_stale_runs() -> None:
    """On startup, runs left RUNNING by a crash/restart are marked failed so the user can retry."""
    db = SessionLocal()
    try:
        for run in db.scalars(select(AgentRun).where(AgentRun.status == RunStatus.RUNNING)).all():
            _fail(db, run, "서버 재시작으로 Agent 실행이 중단되었습니다. 'Agent 재시도'를 눌러주세요.")
    finally:
        db.close()
