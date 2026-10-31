"""업무 합치기.

같은 상대가 새 스레드로 보낸 메일 업무(B, 원본)를 기존 업무(A, 대상)에 합치고, 10분 안에 되돌린다.
합쳐진 업무(merged_into_id가 있음)로 오는 메일과 동작은 합쳐진 대상 업무로 간다.
판별(MAIL_TRIAGE_ENABLED)이 켜져 있으면 요청 · 기한 · 요약은 합칠 때 바꾸지 않고, 워커가 옮긴 메일로 변경 분석을 해서
고친다(설계 7.3, app.mail.merge_analysis). 꺼져 있거나 분석이 실패하면 5단계 규칙(요청 합집합 · 이른 기한)으로 마무리한다.
- 커밋하지 않는다. API가 커밋한다.
- 타임라인에는 업무 번호만 남긴다. 할 일 제목 · 요청 · 메일 내용은 남기지 않는다.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.audit import log_action
from app.config import get_settings
from app.mail.calendar_sync import mark_calendar_dirty
from app.mail.errors import ActionError
from app.mail.inbox import finalize_snooze
from app.mail.status import CLOSED_TASK_STATUSES
from app.models import Email, ReplyDraft, Task, TaskStatus, User

MAX_HOPS = 5
UNDO_WINDOW = timedelta(minutes=10)
MERGE_TARGET_STATUSES = (TaskStatus.ACTION_REQUIRED.value, TaskStatus.IN_PROGRESS.value,
                         TaskStatus.BLOCKED.value, TaskStatus.WAITING.value)
_RANK = {"HIGH": 3, "NORMAL": 2, "LOW": 1}
_DEADLINE_KEYS = ("deadline_at", "deadline_all_day", "deadline_kind", "deadline_source")
_PRIORITY_KEYS = ("priority", "priority_source")

CANNOT_MERGE = "합칠 수 없는 업무입니다."
NOTHING_TO_UNDO = "되돌릴 수 있는 합치기가 없습니다."
ANALYZING = "합친 내용을 분석하는 중입니다. 잠시 후 다시 합쳐 주세요."
SENDING = "보내는 중인 답장이 있어 합칠 수 없습니다. 발송 결과를 확인한 뒤 다시 시도해 주세요."
MERGED_TODO_STEP = "합친 업무에서 할 일이 생겼습니다"


def resolve_merged(db: Session, task: Task) -> Task:
    """merged_into_id를 따라 최종 대상 업무를 돌려준다.

    최대 5번까지만 따라간다. 순환이 보이거나, 대상이 없거나, 다른 사용자의 업무이면 거기서 멈추고 마지막으로 찾은 업무를 돌려준다.
    """
    seen = {task.id}
    current = task
    for _ in range(MAX_HOPS):
        target_id = current.merged_into_id
        if target_id is None or target_id in seen:
            break
        target = db.scalar(select(Task).where(Task.id == target_id, Task.user_id == task.user_id))
        if target is None:
            break
        seen.add(target.id)
        current = target
    return current


# ---------------------------------------------------------------- 도우미

def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _iso(dt: datetime | None) -> str | None:
    dt = _utc(dt)
    return dt.isoformat() if dt else None


def _parse(text) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return _utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _fields(task: Task) -> dict:
    """되돌릴 때 비교 · 복구하는 대상 업무의 기한 · 중요도."""
    return {"deadline_at": _iso(task.deadline_at), "deadline_all_day": bool(task.deadline_all_day),
            "deadline_kind": task.deadline_kind, "deadline_source": task.deadline_source,
            "priority": task.priority, "priority_source": task.priority_source}


def _title(item: dict) -> str:
    title = item.get("title")
    return title.strip() if isinstance(title, str) else ""


def _state(task: Task) -> dict:
    """합친 내용 분석이 바꿀 수 있는 대상 업무의 상태. 되돌릴 때 '합친 · 분석한 뒤 값 그대로'인 것만 합치기 전 값으로."""
    brief = task.brief or {}
    return {"status": str(task.status), "current_step": task.current_step, "waiting_for": task.waiting_for,
            "next_action": task.next_action, "completed_at": _iso(task.completed_at),
            "no_reply_asked_at": _iso(task.no_reply_asked_at), "changed_unseen": bool(task.changed_unseen),
            "needs_reply": brief.get("needs_reply"), "thanks_email_id": brief.get("thanks_email_id")}


def _restore_state(task: Task, before: dict, after: dict) -> None:
    if not before or not after:
        return
    current = _state(task)
    keys = [k for k in before if k in after and current.get(k) == after.get(k)]
    brief = dict(task.brief or {})
    for key in keys:
        value = before[key]
        if key == "status":
            try:
                task.status = TaskStatus(value)
            except ValueError:
                pass
        elif key in ("completed_at", "no_reply_asked_at"):
            setattr(task, key, _parse(value))
        elif key in ("needs_reply", "thanks_email_id"):
            if value is None:
                brief.pop(key, None)
            else:
                brief[key] = value
        else:
            setattr(task, key, value)
    task.brief = brief


def note_analysis_state(task: Task, source_id: int) -> None:
    """합친 내용 분석을 반영한 뒤의 상태를 되돌리기 기록에 남긴다. 되돌릴 때 이 값 그대로인 것만 합치기 전으로 돌린다."""
    records = dict(task.merge_undo or {})
    rec = records.get(str(source_id))
    if isinstance(rec, dict):
        records[str(source_id)] = {**rec, "state_after": _state(task)}
        task.merge_undo = records


def _int_ids(plan: list) -> set[int]:
    return {p["id"] for p in plan if isinstance(p, dict) and isinstance(p.get("id"), int)
            and not isinstance(p.get("id"), bool)}


def _requests(task: Task) -> list:
    reqs = (task.brief or {}).get("requests")
    return list(reqs) if isinstance(reqs, list) else []


def _log(db: Session, user: User, task: Task, action_type: str, data: dict, reason: str) -> None:
    log_action(db, user_id=user.id, task_id=task.id, run_id=None, action_type=action_type,
               output_data=data, reason=reason)


# ---------------------------------------------------------------- 되돌리기 기록

def _live_records(task: Task, now: datetime) -> dict:
    """merge_undo({원본 번호: 기록}) 중 아직 10분이 지나지 않은 것만 새 dict로 돌려준다."""
    now = _utc(now)
    raw = task.merge_undo if isinstance(task.merge_undo, dict) else {}
    out = {}
    for key, rec in raw.items():
        at = _parse(rec.get("at")) if isinstance(rec, dict) else None
        if at is not None and now - at <= UNDO_WINDOW and isinstance(rec.get("source_id"), int):
            out[str(key)] = rec
    return out


def undoable_sources(task: Task, now: datetime) -> list[int]:
    """아직 되돌릴 수 있는 원본 업무 번호. 최근에 합친 것부터."""
    live = list(_live_records(task, now).values())
    live.sort(key=lambda r: (_parse(r["at"]), r["source_id"]), reverse=True)
    return [r["source_id"] for r in live]


# ---------------------------------------------------------------- 합치기

def _mergeable(user: User, source: Task, target: Task) -> bool:
    return (source.id != target.id
            and source.user_id == user.id and target.user_id == user.id
            and source.origin == "mail" and target.origin == "mail"
            and source.merged_into_id is None and target.merged_into_id is None
            and str(source.status) not in CLOSED_TASK_STATUSES
            and (source.brief or {}).get("analysis") != "pending"  # 분석 중이면 워커가 결과를 덮어쓸 수 있다
            and str(target.status) in MERGE_TARGET_STATUSES)


def merge(db: Session, user: User, source: Task, target: Task, now: datetime) -> None:
    """원본 B(source)를 대상 A(target)에 합친다.

    되돌리기용 기록은 target.merge_undo[str(B 번호)]에 남긴다. 10분 안에 여러 업무를 합쳐도 각각 되돌릴 수 있다.
    """
    if not _mergeable(user, source, target):
        raise ActionError(CANNOT_MERGE, 409)
    if any((t.merge_analysis or {}).get("state") == "pending" for t in (source, target)):
        raise ActionError(ANALYZING, 409)  # 대상마다 합친 내용 분석은 하나씩(분석 결과가 섞이지 않게)
    if db.scalar(select(ReplyDraft.id).where(ReplyDraft.user_id == user.id,
                                             ReplyDraft.task_id.in_([source.id, target.id]),
                                             ReplyDraft.status.in_(("sending", "unknown"))).limit(1)) is not None:
        raise ActionError(SENDING, 409)  # 발송 결과(보낸 메일 · 답장 보냄)가 어느 업무로 갈지 흐려지지 않게
    now = _utc(now)
    before = _fields(target)
    state_before = _state(target)

    # 메일
    emails = db.scalars(select(Email).where(Email.user_id == user.id, Email.task_id == source.id)).all()
    for email in emails:
        email.task_id = target.id
    moved_email_ids = sorted(e.id for e in emails)

    # 할 일: 같은 제목(앞뒤 공백 무시)은 건너뛰고 B에 남긴다. 옮긴 것은 A의 새 번호를 받는다.
    target_plan = [dict(p) if isinstance(p, dict) else p for p in (target.plan or [])]
    titles = {_title(p) for p in target_plan if isinstance(p, dict)}
    next_id = max(_int_ids(target_plan), default=0) + 1
    kept, added_ids, id_from = [], [], {}
    for p in source.plan or []:
        title = _title(p) if isinstance(p, dict) else ""
        if not title or title in titles:
            kept.append(dict(p) if isinstance(p, dict) else p)
            continue
        titles.add(title)
        target_plan.append({**p, "id": next_id, "merged_from": source.id})  # 되돌릴 때 번호가 아니라 이 표시로 찾는다
        id_from[str(next_id)] = p.get("id")
        added_ids.append(next_id)
        next_id += 1
    target.plan = target_plan
    source.plan = kept

    # 상대 요청 · 기한: 판별이 켜져 있으면 합친 내용 분석이 고친다. 꺼져 있으면 5단계 규칙.
    analyze = get_settings().mail_triage_enabled
    added_requests = [] if analyze else _union_requests(source, target)
    if not analyze:
        _take_earlier_deadline(source, target)
    elif str(target.status) == TaskStatus.WAITING.value and any(
            isinstance(p, dict) and p.get("merged_from") == source.id and not p.get("done") for p in target_plan):
        # 답장 보냄 업무에 끝나지 않은 할 일이 들어왔다: 새 일이 생겼으므로 처리할 일로
        target.status = TaskStatus.ACTION_REQUIRED
        target.current_step = MERGED_TODO_STEP
        target.waiting_for = ""
        target.next_action = next(p["title"] for p in target_plan if isinstance(p, dict) and not p.get("done"))

    # 중요도: 높은 쪽
    if _RANK.get(source.priority, 0) > _RANK.get(target.priority, 0):
        target.priority = source.priority
        if source.priority_source == "user":
            target.priority_source = "user"

    source_before = {"status": str(source.status), "current_step": source.current_step,
                     "completed_at": _iso(source.completed_at), "snoozed_until": _iso(source.snoozed_until),
                     "next_action": source.next_action}
    source.merged_into_id = target.id
    source.status = TaskStatus.COMPLETED
    source.completed_at = now
    source.current_step = f"업무 #{target.id}에 합쳐짐"
    source.next_action = ""
    source.snoozed_until = None

    records = _live_records(target, now)  # 10분 지난 기록은 쓸 때 정리한다
    records[str(source.id)] = {
        "source_id": source.id, "at": now.isoformat(), "moved_email_ids": moved_email_ids,
        "added_todo_ids": added_ids, "added_todo_from": id_from, "added_requests": added_requests,
        "before": before, "after": _fields(target), "source_before": source_before,
        "state_before": state_before, "state_after": _state(target),
    }
    target.merge_undo = records
    if analyze:
        target.merge_analysis = {"state": "pending", "source_id": source.id, "email_ids": moved_email_ids,
                                 "at": now.isoformat()}
    else:
        target.merge_analysis = {}  # 이전 합치기의 분석 상태가 이번 합치기로 보이지 않게
    source.merge_analysis = {}  # 합쳐진 업무에는 분석 상태를 남기지 않는다(pending은 위에서 막았다)
    mark_calendar_dirty(source)  # 합쳐진 업무의 일정은 지운다(되돌리면 되살린다)
    mark_calendar_dirty(target)
    data = {"source_id": source.id, "target_id": target.id}
    _log(db, user, target, "TASK_MERGED", data, f"업무 #{source.id}을 이 업무에 합쳤습니다")
    _log(db, user, source, "TASK_MERGED", data, f"업무 #{target.id}에 합쳤습니다")


def _union_requests(source: Task, target: Task) -> list[str]:
    """5단계: B의 상대 요청 중 A에 없는 것을 A에 더한다. 더한 요청을 돌려준다."""
    reqs = _requests(target)
    seen = {r.strip() for r in reqs if isinstance(r, str)}
    added = []
    for r in _requests(source):
        if isinstance(r, str) and r.strip() and r.strip() not in seen:
            seen.add(r.strip())
            added.append(r)
    if added:
        target.brief = {**(target.brief or {}), "requests": reqs + added}
    return added


def _take_earlier_deadline(source: Task, target: Task) -> None:
    """5단계: B가 더 이르거나 A에 없으면 B 기한."""
    s_due, t_due = _utc(source.deadline_at), _utc(target.deadline_at)
    if s_due is not None and (t_due is None or s_due < t_due):
        target.deadline_at = source.deadline_at
        target.deadline_all_day = source.deadline_all_day
        target.deadline_kind = source.deadline_kind
        target.deadline_source = source.deadline_source


def finish_by_stage5_rules(db: Session, target: Task, source: Task) -> None:
    """합친 내용 분석을 못 했을 때(형식 오류 두 번 · 판별 꺼짐) 5단계 규칙으로 마무리한다. 커밋하지 않는다.

    되돌리기 기록도 함께 고친다: 더한 요청, 그리고 기한을 바꿨으면 '합친 직후 값'. 합친 뒤 사용자가 A 기한을 직접
    바꿨으면 기한은 건드리지 않는다(사용자 수정을 덮지 않는다).
    """
    records = dict(target.merge_undo or {})
    rec = dict(records.get(str(source.id)) or {})
    added = _union_requests(source, target)
    after = dict(rec.get("after") or {})
    current = _fields(target)
    if all(current[k] == after.get(k) for k in _DEADLINE_KEYS):
        _take_earlier_deadline(source, target)
        after.update({k: v for k, v in _fields(target).items() if k in _DEADLINE_KEYS})
    if rec:
        rec["added_requests"] = [*(rec.get("added_requests") or []), *added]
        rec["after"] = after
        records[str(source.id)] = rec
        target.merge_undo = records
    mark_calendar_dirty(target)
    log_action(db, user_id=target.user_id, task_id=target.id, run_id=None, action_type="MERGE_ANALYSIS_FALLBACK",
               output_data={"source_id": source.id},
               reason=f"업무 #{source.id}의 내용을 자동 분석하지 못해 요청을 합치고 이른 기한을 골랐습니다")


def _revert_merge_changes(target: Task, email_ids: set[int]) -> None:
    """합친 내용 분석 · 그 뒤 재답장 분석이 이 메일들로 바꾼 것을 최근 것부터 되돌리고 기록에서 지운다.

    되돌리지 못한 항목(그 뒤 사용자가 고쳤거나 업무가 닫힘)은 지금 값을 그대로 두고 기록도 남긴다(나중에 직접 볼 수 있게).
    """
    from app.mail import changes  # changes → intake → merge 순환 import를 피한다
    entries = [e for e in target.change_log or [] if isinstance(e, dict) and e.get("email_id") in email_ids]
    kept: set[int] = set()
    for entry in sorted(entries, key=lambda e: e.get("id") or 0, reverse=True):
        if entry.get("status") == "applied":
            try:
                changes.undo_change(target, entry["id"])
            except ActionError:
                kept.add(entry["id"])
    target.change_log = [e for e in target.change_log or []
                         if not (isinstance(e, dict) and e.get("email_id") in email_ids and e.get("id") not in kept)]


# ---------------------------------------------------------------- 되돌리기

def _restore(task: Task, values: dict, keys: tuple) -> None:
    for key in keys:
        value = values.get(key)
        if key == "deadline_at":
            value = _parse(value)
        setattr(task, key, value)


def undo_merge(db: Session, user: User, target: Task, source_id: int, now: datetime) -> Task:
    """합친 뒤 10분 안이면 원본 source_id에서 옮겨 온 것만 돌려보낸다.

    사용자가 그사이 바꾼 A의 기한 · 중요도는 유지한다. 다른 원본의 합치기 기록은 그대로 둔다.
    """
    records = _live_records(target, now)
    rec = records.pop(str(source_id), None)
    if rec is None or target.user_id != user.id or target.merged_into_id is not None:
        raise ActionError(NOTHING_TO_UNDO, 409)
    source = db.scalar(select(Task).where(Task.id == source_id, Task.user_id == user.id))
    if source is None or source.merged_into_id != target.id:
        raise ActionError(NOTHING_TO_UNDO, 409)

    # 메일: 옮긴 메일, 그리고 합친 뒤 B 스레드로 들어와 A에 붙은 메일
    moved = [i for i in rec.get("moved_email_ids") or [] if isinstance(i, int)]
    emails = list(db.scalars(select(Email).where(Email.user_id == user.id, Email.task_id == target.id,
                                                 Email.id.in_(moved))).all()) if moved else []
    if source.gmail_thread_id:
        emails += db.scalars(select(Email).where(Email.user_id == user.id, Email.task_id == target.id,
                                                 Email.gmail_thread_id == source.gmail_thread_id)).all()
    returned_ids = {e.id for e in emails}
    _revert_merge_changes(target, returned_ids)
    if (target.merge_analysis or {}).get("source_id") == source.id:
        target.merge_analysis = {}  # 아직 분석 중이면 워커가 결과를 버린다. 다른 원본의 분석은 그대로 둔다
    _restore_state(target, rec.get("state_before") or {}, rec.get("state_after") or {})
    if (target.brief or {}).get("thanks_email_id") in returned_ids:
        target.brief = {k: v for k, v in target.brief.items() if k != "thanks_email_id"}
    for email in emails:
        email.task_id = source.id

    # 할 일: B에서 온 표시(merged_from)가 있는 것만, 지금 상태(체크 · 메모) 그대로 B로. 원래 번호가 겹치면 새 번호.
    # 번호로 고르지 않는다. 사용자가 지운 뒤 새로 만든 할 일이 같은 번호를 받을 수 있기 때문이다.
    id_from = rec.get("added_todo_from") if isinstance(rec.get("added_todo_from"), dict) else {}
    target_plan, returning = [], []
    for p in target.plan or []:
        if isinstance(p, dict) and p.get("merged_from") == source.id:
            item = dict(p)
            del item["merged_from"]
            returning.append(item)
        else:
            target_plan.append(dict(p) if isinstance(p, dict) else p)
    source_plan = [dict(p) if isinstance(p, dict) else p for p in (source.plan or [])]
    used = _int_ids(source_plan)
    for item in returning:
        orig = id_from.get(str(item["id"]), item["id"])
        if not isinstance(orig, int) or isinstance(orig, bool) or orig in used:
            orig = max(used, default=0) + 1
        item["id"] = orig
        used.add(orig)
        source_plan.append(item)
    target.plan = target_plan
    source.plan = source_plan

    # 요청
    added_requests = [r for r in rec.get("added_requests") or [] if isinstance(r, str)]
    if added_requests:
        reqs = _requests(target)
        for r in added_requests:
            if r in reqs:
                reqs.remove(r)
        target.brief = {**(target.brief or {}), "requests": reqs}

    # 기한 · 중요도: 합친 직후 값 그대로일 때만 이전 값으로. 이전 기한이 더 늦으면 이미 풀린 스누즈가 다시 숨지 않게 먼저 끝낸다.
    finalize_snooze(target, now, get_settings().user_timezone)
    current, after, before = _fields(target), rec.get("after") or {}, rec.get("before") or {}
    if all(current[k] == after.get(k) for k in _DEADLINE_KEYS):
        _restore(target, before, _DEADLINE_KEYS)
    if all(current[k] == after.get(k) for k in _PRIORITY_KEYS):
        _restore(target, before, _PRIORITY_KEYS)

    sb = rec.get("source_before") or {}
    try:
        source.status = TaskStatus(sb.get("status"))
    except ValueError:
        source.status = TaskStatus.ACTION_REQUIRED
    source.current_step = sb.get("current_step") or ""
    source.completed_at = _parse(sb.get("completed_at"))
    source.snoozed_until = _parse(sb.get("snoozed_until"))
    source.next_action = sb.get("next_action") or ""
    source.merged_into_id = None

    target.merge_undo = records  # 이 원본의 기록만 뺀 새 dict
    mark_calendar_dirty(source)
    mark_calendar_dirty(target)
    data = {"source_id": source.id, "target_id": target.id}
    _log(db, user, target, "TASK_UNMERGED", data, f"업무 #{source.id} 합치기를 되돌렸습니다")
    _log(db, user, source, "TASK_UNMERGED", data, f"업무 #{target.id}와의 합치기를 되돌렸습니다")
    return target


def dismiss_merge(task: Task) -> None:
    """'따로 두기'. 이 업무에는 다시 합치기를 제안하지 않는다."""
    if task.origin != "mail":
        raise ActionError(CANNOT_MERGE, 409)
    task.merge_dismissed = True
