"""상대 재답장으로 업무 고치기 (설계 6절).

같은 스레드에 상대 메일이 오면 업무 하나에 LLM 분석 1회(그 주기의 새 메일을 묶어서)를 돌린다.
- 코드가 거른다: 근거(evidence)가 새 메일 본문에 그대로 없으면 버리고, 기한은 수신 시각 기준으로 다시 계산한다.
- 반영: 제목 · 요약 · 요청 · 기한은 바로 바꾸고(사용자가 정한 기한은 제안만), 할 일은 추가만 한다. 질문은 반영하지 않는다.
- 변경마다 전후 값 · 근거 · 보낸 메일을 change_log에 남겨 항목별로 되돌릴 수 있게 한다.
- LLMError(장애)는 아무것도 바꾸지 않고 메일 번호를 brief[PENDING_KEY]에 남겨 다음 주기에 다시 분석한다.
  형식 오류는 1회 다시 묻고, 또 틀리면 brief.change_failed_at을 남기고 처리할 일로 올린다.
- 커밋하지 않는다. 로그에는 메일 · 업무 번호와 예외 타입만, 타임라인에는 전후 값 한 줄(각 40자)만 남긴다.
"""
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.agent.audit import log_action
from app.agent.guard import detect_injection
from app.agent.llm import LLMClient, LLMError
from app.mail import intake
from app.mail.calendar_sync import mark_calendar_dirty
from app.mail.deadline import USER_DEADLINE_SOURCE, normalize_deadline
from app.mail.change_prompt import (CHANGES_SCHEMA, CHANGES_SYSTEM, _asks, _build_prompt, _fmt_deadline, _sender,
                                    _validate, evidence_in)
from app.mail.errors import ActionError
from app.mail.intake import ITEM_CHARS, MAX_ITEMS, SUMMARY_CHARS, TITLE_CHARS, _is_suspicious
from app.mail.lifecycle import clear_wait_on_inbound
from app.mail.task_actions import REOPENED_STEP, _iso, _require_open
from app.models import Email, RiskLevel, Task, TaskStatus

log = logging.getLogger(__name__)

PENDING_KEY = intake.CHANGES_PENDING_KEY  # LLM 장애 등으로 아직 변경 분석을 못 한 메일 번호 (다음 주기에 다시)
LOG_MAX = 50
VALUE_CHARS = 40
CHANGED_STEP = "상대 답장으로 내용이 바뀌었습니다"
REPLIED_STEP = "상대가 다시 답장했습니다"
FAILED_STEP = "새 메일 자동 분석에 실패했습니다 — 메일을 직접 확인해 주세요"
CONFLICT_MESSAGE = "그 뒤에 직접 고친 내용이 있어 되돌릴 수 없습니다."
_CLOSED = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)
_LABELS = {"title": "제목: ", "summary": "요약: ", "requests": "요청: ", "deadline": "기한 "}


def _cut(text) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS - 1] + "…"


def _line(entry: dict) -> str:
    """타임라인 한 줄: 전후 값만, 각 40자."""
    field, status = entry["field"], entry["status"]
    if status == "question":
        return f"확인 필요: {_cut(entry['after'] or entry['before'])}"
    if field == "todo_add":
        return f"할 일 추가: {_cut(entry['after'])}"
    prefix = "기한 제안 " if status == "suggested" else _LABELS[field]
    return f"{prefix}{_cut(entry['before'] or '없음')} → {_cut(entry['after'])}"


def _deadline_value(task: Task) -> dict:
    return {"at": _iso(task.deadline_at), "all_day": bool(task.deadline_all_day),
            "kind": task.deadline_kind or "none", "source": task.deadline_source or ""}


def _set_deadline(task: Task, value: dict) -> None:
    at = value.get("at")
    task.deadline_at = datetime.fromisoformat(at) if at else None
    task.deadline_all_day = bool(value.get("all_day"))
    task.deadline_kind = value.get("kind") or "none"
    task.deadline_source = value.get("source") or ""
    mark_calendar_dirty(task)


# ---------------------------------------------------------------- 분석 · 반영

def _drop_pending(brief: dict, ids: list[int]) -> None:
    left = [i for i in brief.get(PENDING_KEY) or [] if i not in ids]
    if left:
        brief[PENDING_KEY] = left
    else:
        brief.pop(PENDING_KEY, None)


def _raise_to_todo(task: Task, step: str) -> None:
    """처리할 일로 올린다. 닫힌 업무는 다시 연다."""
    if str(task.status) in _CLOSED:
        task.completed_at = None
        step = step if step == FAILED_STEP else REOPENED_STEP
        mark_calendar_dirty(task)
    task.status = TaskStatus.ACTION_REQUIRED
    task.current_step = step
    task.waiting_for = ""


def analyze_changes(db: Session, task: Task, emails: list[Email], llm: LLMClient, tz: str) -> bool:
    """새 메일 묶음으로 변경 분석 1회. 반영했으면 True. 커밋하지 않는다."""
    return run_analysis(db, task, emails, llm, tz) in (APPLIED, DROPPED)


APPLIED, DROPPED, OUTAGE, INVALID = "applied", "dropped", "outage", "invalid"


def run_analysis(db: Session, task: Task, emails: list[Email], llm: LLMClient, tz: str,
                 still_wanted: Callable[[Task], bool] | None = None) -> str:
    """변경 분석 1회. 결과: applied / dropped(분석 사이 합쳐짐 · 원하지 않게 됨) / outage(LLM 장애) / invalid.

    still_wanted가 없으면(재답장) 장애 때 메일을 대기열에 남기고, 형식 오류 두 번 · 반영 중 오류는 실패로 기록한다.
    still_wanted가 있으면(합친 내용 분석, 설계 7.3) 대기열 · 실패 기록은 부른 쪽이 정한다. 분석 직후 업무를 다시 읽어
    still_wanted(task)가 거짓이면(되돌려짐) 결과를 버린다.
    """
    merge_mode = still_wanted is not None
    emails = sorted(emails, key=lambda e: (intake._reference_time(e), e.id))
    ids = [e.id for e in emails]
    if not merge_mode:  # 합치기 모드는 LLM 호출 전에 아무것도 쓰지 않는다(쓰기 잠금을 잡은 채 기다리지 않게)
        clear_wait_on_inbound(task)  # 상대 메일이 왔으므로 '답 없음' 물음을 거둔다
    prompt = _build_prompt(db, task, emails, tz)
    data = None
    for _ in range(2):  # 형식 오류는 한 번만 다시 묻는다
        try:
            data = _validate(llm.structured(CHANGES_SYSTEM, prompt, CHANGES_SCHEMA))
            break
        except LLMError as e:
            if not merge_mode:
                brief = dict(task.brief or {})
                brief[PENDING_KEY] = list(dict.fromkeys([*(brief.get(PENDING_KEY) or []), *ids]))
                task.brief = brief
            log.warning("change analysis for task %s postponed: %s", task.id, type(e).__name__)
            return OUTAGE
        except Exception as e:  # noqa: BLE001 - 형식 오류 · 예상 밖 오류도 횟수 제한 안에서만 다시 묻는다
            log.warning("change analysis for task %s got an invalid result: %s", task.id, type(e).__name__)
    task_id = task.id
    db.flush()
    db.refresh(task)  # 분석하는 동안 합쳐졌거나 합치기가 되돌려졌을 수 있다
    brief = dict(task.brief or {})
    _drop_pending(brief, ids)
    task.brief = brief
    if task.merged_into_id is not None or (merge_mode and not still_wanted(task)):
        log.info("change analysis: task %s changed during analysis; result dropped", task_id)
        return DROPPED
    if data is None:
        if not merge_mode:
            _fail(db, task, ids)
        return INVALID
    try:
        with db.begin_nested():  # 반영 중 오류면 이 저장점만 되돌린다(호출한 쪽의 커밋 전 작업은 남는다)
            _apply(db, task, emails, data, tz, merge_mode=merge_mode)
    except Exception as e:  # noqa: BLE001 - 반쯤 반영한 것을 버리고 실패로 남겨야 같은 메일을 끝없이 다시 분석하지 않는다
        log.warning("change analysis for task %s failed while applying: %s", task_id, type(e).__name__)
        task = db.get(Task, task_id)
        if task is not None and not merge_mode:
            _fail(db, task, ids)
        return INVALID
    return APPLIED


def _fail(db: Session, task: Task, ids: list[int]) -> None:
    """형식 오류가 두 번이거나 반영 중 오류: 다시 분석하지 않고 처리할 일로 올린다(안전한 쪽, 설계 6.5)."""
    clear_wait_on_inbound(task)
    brief = dict(task.brief or {})
    _drop_pending(brief, ids)
    brief["change_failed_at"] = datetime.now(UTC).isoformat()
    task.brief = brief
    _raise_to_todo(task, FAILED_STEP)
    log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type="CHANGES_FAILED",
               output_data={"email_count": len(ids)}, reason="새 메일 분석에 실패해 처리할 일로 올렸습니다")
    db.flush()


def _source_mail(evidence: str, emails: list[Email]) -> Email | None:
    return next((e for e in reversed(emails) if evidence_in(evidence, e.content or "")), None)


def _newer_than_reply(task: Task, email: Email) -> bool:
    sent = task.reply_sent_at
    if sent is None:
        return True
    sent = sent.replace(tzinfo=UTC) if sent.tzinfo is None else sent
    return intake._reference_time(email) > sent


def _apply(db: Session, task: Task, emails: list[Email], data: dict, tz: str, merge_mode: bool = False) -> None:
    """merge_mode(합친 메일 분석): '답장 필요 없음' 표시(thanks_email_id)는 남기지 않고, 옮겨 온 메일이 내가 답장을
    보낸 뒤에 온 것일 때만 답장 필요 여부 · '답 없음' 물음을 그 메일 기준으로 고친다(이미 답한 옛 메일이 상태를 되돌리지 않게)."""
    sus = data["suspicious"]
    brief = dict(task.brief or {})
    plan = [dict(p) if isinstance(p, dict) else p for p in task.plan or []]
    history = [dict(e) for e in task.change_log or [] if isinstance(e, dict)]
    next_id = max((e.get("id", 0) for e in history if isinstance(e.get("id"), int)), default=0) + 1
    # 앞선 메일(이번 묶음 밖, 받은 · 보낸 메일 모두)에도 있는 근거는 인용된 옛 문장일 수 있어 버린다
    earlier = db.scalars(select(Email.content).where(Email.user_id == task.user_id, Email.task_id == task.id,
                                                     Email.id.not_in([e.id for e in emails]))).all()
    entries = []
    for c in data["changes"]:
        field, after = c["field"], c["after"]
        if _is_suspicious(after, sus) or _is_suspicious(c["evidence"], sus):
            continue
        src = _source_mail(c["evidence"], emails)
        if src is None or (field != "deadline" and not after):
            continue
        if any(evidence_in(c["evidence"], body or "") for body in earlier):
            continue
        if _asks(c["evidence"], src.content or ""):
            c = {**c, "kind": "question"}  # 물음 문장을 확정 변경으로 반영하지 않는다
        entry = {"id": next_id, "email_id": src.id, "sender": _sender(src, task),
                 "received_at": intake._reference_time(src).isoformat(), "field": field, "before": c["before"],
                 "after": after, "evidence": c["evidence"], "status": "applied", "prev": None}
        if c["kind"] == "question":
            entry["status"] = "question"
        elif field == "title":
            new = after[:TITLE_CHARS]
            if new == task.title:
                continue
            entry.update(before=task.title, after=new, prev=task.title)
            task.title = new
            mark_calendar_dirty(task)
        elif field == "summary":
            new, old = after[:SUMMARY_CHARS], brief.get("summary") or ""
            if new == old:
                continue
            entry.update(before=old, after=new, prev=old)
            brief["summary"] = new
        elif field == "requests":
            reqs = [r for r in brief.get("requests") or [] if isinstance(r, str)]
            if after in reqs:
                continue
            idx = next((i for i, r in enumerate(reqs) if c["before"] and intake._norm(r) == intake._norm(c["before"])), None)
            prev = reqs[idx] if idx is not None else None
            if idx is None:
                reqs.append(after)
            else:
                reqs[idx] = after
            entry.update(before=prev or "", prev=prev)
            brief["requests"] = reqs[:MAX_ITEMS]
            task.goal = "\n".join(brief["requests"]) or brief.get("summary") or task.title
        elif field == "deadline":
            dl = c["deadline"]
            due = normalize_deadline(dl["text"], dl["date"], dl["time"], dl["kind"], intake._reference_time(src), tz)
            if due is None or (_iso(task.deadline_at) == due.at.isoformat() and task.deadline_all_day == due.all_day):
                continue
            value = {"at": due.at.isoformat(), "all_day": due.all_day, "kind": due.kind, "source": due.source}
            entry.update(before=_fmt_deadline(task.deadline_at, task.deadline_all_day, tz),
                         after=_fmt_deadline(due.at, due.all_day, tz), value=value)
            if task.deadline_source == USER_DEADLINE_SOURCE:
                entry["status"] = "suggested"  # 사용자가 정한 기한은 덮지 않고 제안만 한다
            else:
                entry["prev"] = _deadline_value(task)
                _set_deadline(task, value)
        else:  # todo_add: 추가만 한다
            title = after[:ITEM_CHARS]
            if any(isinstance(p, dict) and intake._norm(str(p.get("title", ""))) == intake._norm(title) for p in plan):
                continue
            new_id = max((p["id"] for p in plan if isinstance(p, dict) and isinstance(p.get("id"), int)), default=0) + 1
            plan.append({"id": new_id, "title": title, "done": False, "evidence": "", "due_at": None, "source": "agent"})
            entry.update(before="", after=title, prev=new_id)
        entries.append(entry)
        next_id += 1

    newest = emails[-1]
    current = not merge_mode or _newer_than_reply(task, newest)
    needs_reply = data["needs_reply"] if current else False
    if current:
        brief["needs_reply"] = data["needs_reply"]
        if merge_mode:
            clear_wait_on_inbound(task)
    if needs_reply or entries:
        brief.pop("thanks_email_id", None)
        _raise_to_todo(task, CHANGED_STEP if entries else REPLIED_STEP)
        undone = [p for p in plan if isinstance(p, dict) and not p.get("done")]
        task.next_action = undone[0]["title"] if undone else ""
    elif str(task.status) == TaskStatus.WAITING.value and not merge_mode:
        brief["thanks_email_id"] = newest.id  # 화면: "상대가 답장했습니다(답장 필요 없음) [완료로 닫기]"
    task.brief = brief
    task.plan = plan
    if entries:
        task.change_log = (history + entries)[-LOG_MAX:]
        task.changed_unseen = True
    for e in emails:
        e.processed = True
    for entry in entries:
        log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type="TASK_CHANGED",
                   output_data={"change_id": entry["id"], "field": entry["field"], "status": entry["status"],
                                "email_id": entry["email_id"]}, reason=_line(entry))
    flags = list(dict.fromkeys([*(s[:ITEM_CHARS] for s in sus[:MAX_ITEMS]),
                                *(f for e in emails for f in detect_injection(e.content or ""))]))
    if flags:
        log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type="SECURITY_WARNING",
                   tool_name="mail_changes", input_data={"email_ids": [e.id for e in emails]},
                   output_data={"patterns": flags}, risk_level=RiskLevel.HIGH, status="FLAGGED",
                   reason="새 메일에서 Prompt Injection 의심 문구 탐지 — 지시를 따르지 않고 반영하지 않음")
    db.flush()


# ---------------------------------------------------------------- 되돌리기 · 제안 반영

def _entry(task: Task, change_id: int) -> tuple[list[dict], dict]:
    history = [dict(e) for e in task.change_log or [] if isinstance(e, dict)]
    for e in history:
        if e.get("id") == change_id:
            return history, e
    raise ActionError("변경 기록을 찾을 수 없습니다. 화면을 새로 고쳐 주세요.", 404)


def _record(task: Task, history: list[dict], entry: dict, action_type: str, reason: str) -> None:
    task.change_log = history  # 새 list
    log_action(object_session(task), user_id=task.user_id, task_id=task.id, run_id=None, action_type=action_type,
               output_data={"change_id": entry["id"], "field": entry["field"]}, reason=reason)


def undo_change(task: Task, change_id: int) -> None:
    """반영된 변경 하나를 이전 값으로 돌린다. 그 뒤 사용자가 고쳤으면 덮지 않고 409."""
    _require_open(task)
    history, entry = _entry(task, change_id)
    if entry.get("status") != "applied":
        raise ActionError("반영된 변경만 되돌릴 수 있습니다.", 409)
    field, after, prev = entry["field"], entry.get("after"), entry.get("prev")
    brief = dict(task.brief or {})
    if field == "title":
        if task.title != after:
            raise ActionError(CONFLICT_MESSAGE, 409)
        task.title = prev or task.title
        mark_calendar_dirty(task)
    elif field == "summary":
        if (brief.get("summary") or "") != after:
            raise ActionError(CONFLICT_MESSAGE, 409)
        brief["summary"] = prev or ""
    elif field == "requests":
        reqs = list(brief.get("requests") or [])
        if after not in reqs:
            raise ActionError(CONFLICT_MESSAGE, 409)
        i = reqs.index(after)
        if prev is None:
            reqs.pop(i)
        else:
            reqs[i] = prev
        brief["requests"] = reqs
        task.goal = "\n".join(reqs) or brief.get("summary") or task.title
    elif field == "deadline":
        value = entry.get("value") or {}
        if (_iso(task.deadline_at) != value.get("at") or bool(task.deadline_all_day) != bool(value.get("all_day"))
                or (task.deadline_source or "") != (value.get("source") or "")):
            raise ActionError(CONFLICT_MESSAGE, 409)  # 같은 날짜라도 사용자가 다시 정했으면 덮지 않는다
        _set_deadline(task, prev or {})
    else:  # todo_add: 그 할 일을 지운다. 체크했거나 고쳤으면 그대로 둔다
        plan = [dict(p) if isinstance(p, dict) else p for p in task.plan or []]
        i = next((i for i, p in enumerate(plan) if isinstance(p, dict) and p.get("id") == prev), None)
        if i is None or plan[i].get("done") or plan[i].get("title") != after:
            raise ActionError(CONFLICT_MESSAGE, 409)
        plan.pop(i)
        task.plan = plan
        mark_calendar_dirty(task)
    task.brief = brief
    entry["status"] = "undone"
    _record(task, history, entry, "TASK_CHANGE_UNDONE", f"되돌림 — {_line(dict(entry, status='applied'))}")


def apply_suggestion(task: Task, change_id: int, tz: str) -> None:
    """사용자가 정한 기한을 상대가 바꾼 제안을 반영한다. 반영한 뒤에는 되돌릴 수 있다."""
    _require_open(task)
    history, entry = _entry(task, change_id)
    if entry.get("status") != "suggested" or entry.get("field") != "deadline" or not entry.get("value"):
        raise ActionError("반영할 기한 제안이 없습니다.", 409)
    entry["before"] = _fmt_deadline(task.deadline_at, task.deadline_all_day, tz)  # 반영 직전 값으로 다시 쓴다
    entry["prev"] = _deadline_value(task)
    _set_deadline(task, entry["value"])  # deadline_source는 변경 근거(메일 속 원문 표현)
    entry["status"] = "applied"
    _record(task, history, entry, "TASK_CHANGE_APPLIED", f"제안 반영 — {_line(entry)}")
