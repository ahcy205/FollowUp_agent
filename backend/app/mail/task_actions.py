"""받은 업무 화면에서 사용자가 메일 업무에 하는 동작.

스누즈 · 중요도 · 기한 · 할 일 · 답장 필요 없음으로 닫기 · 다시 분석 · 업무로 처리.
- 커밋하지 않는다. API가 커밋한다(업무로 처리에서 새 업무를 만들 때만 intake가 커밋한다).
- 잘못된 요청은 ActionError(사용자에게 보일 한국어 문구, HTTP 상태)로 알린다.
- 시각은 UTC로 저장하고, 날짜 · 시각 입력과 선택지(오늘 저녁 · 내일 아침 · 다음 주 월요일)는 사용자 시간대로 해석한다.
- 타임라인에는 업무 번호 · 메일 번호 · 시각 · 동작 종류만 남긴다. 할 일 제목 · 메모 · 메일 내용은 남기지 않는다.
"""
import re
from datetime import UTC, date as _date, datetime, time as _time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, object_session

from app.agent.audit import log_action
from app.config import get_settings
from app.mail import intake
from app.mail.calendar_sync import mark_calendar_dirty
from app.mail.deadline import MAX_AHEAD_DAYS
from app.mail.inbox import snooze_wake_at
from app.models import Email, EmailDirection, Task, TaskStatus, User

PRESETS = ("evening", "tomorrow", "next_monday")
EVENING = _time(18, 0)
MORNING = _time(9, 0)
SNOOZE_MAX = timedelta(days=365)
PRIORITIES = ("HIGH", "NORMAL", "LOW")
TODO_TITLE_MAX = 300
TODO_MEMO_MAX = 1000
DUE_NOTE_MAX = 300
CLOSE_REASON_MAX = 500
USER_DEADLINE_SOURCE = "사용자가 직접 정함"
CLOSED_STEP = "답장 필요 없음으로 닫음"
REOPENED_STEP = "새 메일로 다시 열림"
PROMOTED_REASON = "사용자가 업무로 지정"
PROMOTABLE_TRIAGE = ("not_work", "filtered")

_CLOSED = (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_RE = re.compile(r"(\d{2}):(\d{2})")

CLOSED_MESSAGE = "이미 완료되었거나 취소된 업무입니다."


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _iso(dt: datetime | None) -> str | None:
    dt = _utc(dt)
    return dt.isoformat() if dt else None


def _require_open(task: Task) -> None:
    if str(task.status) in _CLOSED or task.merged_into_id is not None:
        raise ActionError(CLOSED_MESSAGE, 409)


def _log(task: Task, action_type: str, data: dict | None = None, reason: str = "") -> None:
    db = object_session(task)
    if db is None:  # 세션에 붙지 않은 업무는 기록할 곳이 없다 (호출 쪽 버그)
        raise RuntimeError("task is not attached to a session")
    log_action(db, user_id=task.user_id, task_id=task.id, run_id=None, action_type=action_type,
               output_data=data or {}, reason=reason)


# ---------------------------------------------------------------- 스누즈

def _local_at(day: _date, at: _time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, at, tzinfo=zone).astimezone(UTC)


def _preset_until(preset: str, now: datetime, tz: str) -> datetime:
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    if preset == "evening":
        until = _local_at(today, EVENING, zone)
        if until <= now:
            raise ActionError("오늘 오후 6시가 지났습니다. 다른 시각을 고르세요.", 400)
        return until
    if preset == "tomorrow":
        return _local_at(today + timedelta(days=1), MORNING, zone)
    if preset == "next_monday":
        return _local_at(today + timedelta(days=7 - today.weekday()), MORNING, zone)
    raise ActionError("알 수 없는 나중에 보기 선택지입니다.", 400)


def snooze(task: Task, *, preset: str | None, until: datetime | None, now: datetime, tz: str) -> None:
    """정한 시각까지 처리할 일 목록에서 숨긴다. 실제 해제는 min(정한 시각, 기한 전날 09:00)."""
    _require_open(task)
    now = _utc(now)
    if (preset is None) == (until is None):
        raise ActionError("나중에 볼 시각을 하나 고르세요.", 400)
    if preset is not None:
        target = _preset_until(preset, now, tz)
    else:
        target = _utc(until)
        if target <= now:
            raise ActionError("나중에 볼 시각은 지금보다 뒤여야 합니다.", 400)
        if target > now + SNOOZE_MAX:
            raise ActionError("나중에 볼 시각은 1년 안으로 고르세요.", 400)

    previous = task.snoozed_until
    task.snoozed_until = target
    wake = snooze_wake_at(task, tz)
    if wake is not None and wake <= now:
        task.snoozed_until = previous
        raise ActionError("기한이 가까워 지금은 숨길 수 없습니다.", 400)
    task.snooze_returned_at = None
    _log(task, "TASK_SNOOZED", {"until": _iso(target)}, "나중에 보기로 숨겼습니다")


def unsnooze(task: Task) -> None:
    """"지금 보기". 사용자가 직접 꺼낸 것이므로 '다시 표시됨'은 붙이지 않는다."""
    _require_open(task)
    if task.snoozed_until is None:
        return
    task.snoozed_until = None
    _log(task, "TASK_UNSNOOZED", {}, "나중에 보기를 해제했습니다")


# ---------------------------------------------------------------- 중요도

def set_priority(task: Task, priority: str) -> None:
    _require_open(task)
    if priority not in PRIORITIES:
        raise ActionError("중요도는 높음 · 보통 · 낮음 중 하나로 고르세요.", 400)
    before = task.priority
    task.priority = priority
    task.priority_source = "user"
    _log(task, "PRIORITY_CHANGED", {"from": before, "to": priority}, "사용자가 중요도를 바꿨습니다")


# ---------------------------------------------------------------- 기한

def _parse_day(text: str | None, today: _date) -> _date:
    text = (text or "").strip()
    if not _DATE_RE.fullmatch(text):
        raise ActionError("날짜 형식이 올바르지 않습니다. YYYY-MM-DD로 입력하세요.", 400)
    try:
        day = _date.fromisoformat(text)
    except ValueError:
        raise ActionError("없는 날짜입니다. 날짜를 다시 확인하세요.", 400)
    if abs((day - today).days) > MAX_AHEAD_DAYS:
        raise ActionError("기한은 오늘부터 앞뒤 2년 안의 날짜로 정하세요.", 400)
    return day


def _parse_time(text: str | None) -> _time | None:
    text = (text or "").strip()
    if not text:
        return None
    m = _TIME_RE.fullmatch(text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ActionError("시각 형식이 올바르지 않습니다. HH:MM(24시간제)으로 입력하세요.", 400)
    return _time(int(m.group(1)), int(m.group(2)))


def set_deadline(task: Task, *, date: str | None, time: str | None, clear: bool, confirm: bool, tz: str,
                 now: datetime | None = None) -> None:
    """기한을 정하거나(date[, time]) 없애거나(clear) 추정 기한을 확정한다(confirm). 하나만 한다."""
    _require_open(task)
    modes = int(bool(clear)) + int(bool(confirm)) + int(date is not None and date != "")
    if modes != 1:
        raise ActionError("기한을 정하거나, 없애거나, 확인하는 것 중 하나만 하세요.", 400)
    before = _iso(task.deadline_at)

    if confirm:
        if task.deadline_at is None or task.deadline_kind != "estimated":
            raise ActionError("확인할 추정 기한이 없습니다.", 409)
        task.deadline_kind = "explicit"  # 근거 문장(source)은 그대로 둔다
    elif clear:
        task.deadline_at, task.deadline_all_day, task.deadline_kind, task.deadline_source = None, False, "none", ""
    else:
        zone = ZoneInfo(tz)
        today = _utc(now or datetime.now(UTC)).astimezone(zone).date()
        day = _parse_day(date, today)
        at = _parse_time(time)
        task.deadline_at = datetime.combine(day, at or _time(0, 0), tzinfo=zone).astimezone(UTC)
        task.deadline_all_day = at is None
        task.deadline_kind = "explicit"
        task.deadline_source = USER_DEADLINE_SOURCE

    mark_calendar_dirty(task)
    _log(task, "DEADLINE_CHANGED", {"from": before, "to": _iso(task.deadline_at), "kind": task.deadline_kind},
         "사용자가 기한을 바꿨습니다")


# ---------------------------------------------------------------- 할 일

def _todo_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActionError("할 일 번호가 올바르지 않습니다.", 400)
    return value


def _title(value) -> str:
    title = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(title) <= TODO_TITLE_MAX:
        raise ActionError(f"할 일 제목은 1~{TODO_TITLE_MAX}자로 입력하세요.", 400)
    return title


def _memo(value) -> str:
    if not isinstance(value, str):
        raise ActionError("처리 메모가 올바르지 않습니다.", 400)
    memo = value.strip()
    if len(memo) > TODO_MEMO_MAX:
        raise ActionError(f"처리 메모는 {TODO_MEMO_MAX}자 안으로 입력하세요.", 400)
    return memo


def _find(plan: list, todo_id: int) -> int:
    for i, item in enumerate(plan):
        if isinstance(item, dict) and item.get("id") == todo_id:
            return i
    raise ActionError("할 일을 찾을 수 없습니다. 화면을 새로 고쳐 주세요.", 404)


def _clean_item(raw) -> dict:
    """되돌리기로 돌아오는 항목은 브라우저가 보낸 값이므로 아는 필드만 검증해 받는다."""
    if not isinstance(raw, dict):
        raise ActionError("되돌릴 할 일이 올바르지 않습니다.", 400)
    done = raw.get("done", False)
    due_at = raw.get("due_at")
    if not isinstance(done, bool) or not (due_at is None or (isinstance(due_at, str) and len(due_at) <= 40)):
        raise ActionError("되돌릴 할 일이 올바르지 않습니다.", 400)
    item = {"id": _todo_id(raw.get("id")), "title": _title(raw.get("title")), "done": done,
            "evidence": _memo(raw.get("evidence", "")), "due_at": due_at,
            "source": "agent" if raw.get("source") == "agent" else "user"}
    note = raw.get("due_note")
    if isinstance(note, str) and note.strip():
        item["due_note"] = note.strip()[:DUE_NOTE_MAX]
    return item


def edit_todos(task: Task, op: dict) -> dict:
    """할 일 하나를 바꾸고 바뀐 항목을 돌려준다. delete는 {"item", "index"}를 돌려주고 restore가 그것을 받는다."""
    _require_open(task)
    if not isinstance(op, dict):
        raise ActionError("할 일 작업이 올바르지 않습니다.", 400)
    kind = op.get("op")
    plan = [dict(p) if isinstance(p, dict) else p for p in (task.plan or [])]

    if kind == "add":
        ids = [p["id"] for p in plan if isinstance(p, dict) and isinstance(p.get("id"), int)]
        item = {"id": max(ids, default=0) + 1, "title": _title(op.get("title")), "done": False, "evidence": "",
                "due_at": None, "source": "user"}
        plan.append(item)
        result, todo_id = item, item["id"]
    elif kind == "toggle":
        done = op.get("done")
        if not isinstance(done, bool):
            raise ActionError("완료 여부가 올바르지 않습니다.", 400)
        i = _find(plan, _todo_id(op.get("id")))
        plan[i]["done"] = done  # 체크를 풀어도 처리 메모는 남긴다
        result, todo_id = plan[i], plan[i]["id"]
    elif kind == "memo":
        memo = _memo(op.get("memo"))
        i = _find(plan, _todo_id(op.get("id")))
        plan[i]["evidence"] = memo
        result, todo_id = plan[i], plan[i]["id"]
    elif kind == "delete":
        i = _find(plan, _todo_id(op.get("id")))
        removed = plan.pop(i)
        result, todo_id = {"item": removed, "index": i}, removed["id"]
    elif kind == "restore":
        item = _clean_item(op.get("item"))
        index = op.get("index", len(plan))
        if isinstance(index, bool) or not isinstance(index, int):
            raise ActionError("되돌릴 위치가 올바르지 않습니다.", 400)
        if any(isinstance(p, dict) and p.get("id") == item["id"] for p in plan):
            raise ActionError("이미 있는 할 일입니다.", 409)
        plan.insert(max(0, min(index, len(plan))), item)
        result, todo_id = item, item["id"]
    else:
        raise ActionError("알 수 없는 할 일 작업입니다.", 400)

    task.plan = plan  # 새 list를 넣어야 JSON 컬럼 변경이 감지된다
    mark_calendar_dirty(task)  # 일정 설명의 할 일 체크 표시를 다시 반영한다
    _log(task, "TODO_CHANGED", {"op": kind, "id": todo_id}, "사용자가 할 일을 바꿨습니다")
    return dict(result)


# ---------------------------------------------------------------- 닫기 · 다시 분석

def close_without_reply(db: Session, user: User, task: Task, reason: str) -> None:
    _require_open(task)
    reason = (reason or "").strip()[:CLOSE_REASON_MAX]
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(UTC)
    task.current_step = CLOSED_STEP
    task.next_action = ""
    task.waiting_for = ""
    task.snoozed_until = None
    mark_calendar_dirty(task)  # 일정은 지우지 않고 제목 앞에 ✓를 붙인다
    log_action(db, user_id=user.id, task_id=task.id, run_id=None, action_type="TASK_CLOSED",
               output_data={"reason": reason}, reason="답장 필요 없음으로 닫았습니다")


def ensure_reanalyzable(task: Task) -> None:
    _require_open(task)
    if (task.brief or {}).get("analysis") != "failed":
        raise ActionError("자동 분석에 실패한 업무만 다시 분석할 수 있습니다.", 409)


def request_reanalyze(task: Task) -> None:
    """시도 횟수를 0으로 하고 분석 대기로 되돌린다. 워커가 다음 주기에 분석한다(API가 깨운다)."""
    ensure_reanalyzable(task)
    task.brief = {**task.brief, "analysis": "pending", "attempts": 0}  # 새 dict
    task.current_step = intake.PENDING_STEP
    _log(task, "REANALYZE_REQUESTED", {}, "사용자가 다시 분석을 요청했습니다")


# ---------------------------------------------------------------- 업무로 처리

def ensure_promotable(user: User, email: Email) -> None:
    if email.user_id != user.id:
        raise ActionError("요청한 데이터를 찾을 수 없습니다.", 404)
    if not get_settings().mail_triage_enabled:
        raise ActionError("자동 판별이 꺼져 있어 분석할 수 없습니다.", 409)
    if (str(email.direction) != EmailDirection.INBOUND.value or not email.gmail_message_id
            or email.triage not in PROMOTABLE_TRIAGE or email.task_id is not None):
        raise ActionError("이미 업무로 처리된 메일입니다.", 409)


def promote_email(db: Session, user: User, email: Email) -> Task:
    """'업무 아님' · '자동 필터' 메일을 업무로 처리한다.

    같은 스레드에 업무가 있으면 그 업무에 붙이고(닫힌 업무면 다시 연다), 없으면 분석 대기 업무를 만든다.
    새 업무를 만드는 경우만 intake가 커밋한다. 그 밖에는 호출한 쪽이 커밋한다.
    """
    ensure_promotable(user, email)
    email_id = email.id
    task = intake._thread_task(db, user.id, email.gmail_thread_id)
    created = False
    if task is None:
        task = intake.create_pending_task(db, email)  # 커밋한다. 경합이면 그 스레드의 업무를 돌려준다
        if task is None:
            raise ActionError("업무를 만들지 못했습니다. 잠시 후 다시 시도하세요.", 409)
        created = email.task_id == task.id

    if not created:
        intake.link_email(db, task, email)
        if str(task.status) in _CLOSED:
            task.status = TaskStatus.ACTION_REQUIRED
            task.completed_at = None
            task.current_step = REOPENED_STEP
            mark_calendar_dirty(task)

    email.triage = "work"
    email.triage_reason = PROMOTED_REASON
    log_action(db, user_id=user.id, task_id=task.id, run_id=None, action_type="EMAIL_PROMOTED",
               output_data={"email_id": email_id}, reason="사용자가 메일을 업무로 처리했습니다")
    return task
