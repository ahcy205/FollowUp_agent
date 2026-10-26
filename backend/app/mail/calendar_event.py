"""업무(Task)를 Google 캘린더 일정 본문으로 바꾼다. 순수 함수만 둔다 (DB · 네트워크 없음)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Task, TaskStatus

_CLOSED = {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}
_TITLE_MAX = 200
_DESC_MAX = 4000
_TIMED_DURATION = timedelta(minutes=30)


def event_id_for(task: Task, salt: str) -> str:
    """결정적 일정 ID. 캘린더 규칙(a-v, 0-9, 5자 이상)을 만족한다.

    salt는 Google 계정 연결마다 만드는 무작위 값이다. DB를 새로 만들거나 다른 인스턴스가 같은 계정을
    연결해도 업무 번호만으로 남의 일정과 번호가 겹치지 않게 한다.
    """
    return "fu" + salt + f"{task.id:08x}"


def private_marker(task: Task, salt: str) -> str:
    """일정의 extendedProperties.private.followupTask 값. 409 때 우리 일정인지 확인하는 데 쓴다."""
    return f"{salt}:{task.id}"


def _local_deadline(task: Task, tz: str) -> datetime:
    at = task.deadline_at
    if at is None:
        raise ValueError("deadline_at is required to build a calendar event")
    if at.tzinfo is None:  # SQLite는 UTC를 naive로 돌려준다
        at = at.replace(tzinfo=UTC)
    return at.astimezone(ZoneInfo(tz))


def _title(task: Task) -> str:
    brief = task.brief or {}
    summary = " ".join(str(brief.get("summary") or task.title or "").split())
    prefix = ("✓ " if str(task.status) in _CLOSED else "") + (
        "(확인 필요) " if task.deadline_kind == "estimated" else ""
    )
    return (prefix + "[업무] " + summary)[:_TITLE_MAX]


def _description(task: Task, public_url: str) -> str:
    brief = task.brief or {}
    lines: list[str] = []
    cp = brief.get("counterpart") or {}
    name = str(cp.get("name") or "").strip()
    org = str(cp.get("organization") or "").strip()
    if name or org:
        lines.append("상대: " + (f"{name} ({org})" if name and org else name or org))
    requests = [str(r).strip() for r in (brief.get("requests") or []) if str(r).strip()]
    if requests:
        lines += ["요청:"] + [f"- {r}" for r in requests]
    plan = [p for p in (task.plan or []) if isinstance(p, dict) and str(p.get("title") or "").strip()]
    if plan:
        lines += ["할 일:"] + [f"{'☑' if p.get('done') else '☐'} {str(p['title']).strip()}" for p in plan]
    source = (task.deadline_source or "").strip()
    if source:
        lines.append(f"기한 근거: {source}")
    link = f"앱에서 보기: {public_url.rstrip('/')}/tasks/{task.id}"
    body = "\n".join(lines)
    room = _DESC_MAX - len(link) - 2
    if len(body) > room:
        body = body[: room - 1].rstrip() + "…"
    return f"{body}\n\n{link}" if body else link


def build_event(task: Task, *, tz: str, public_url: str, salt: str) -> dict:
    local = _local_deadline(task, tz)
    if task.deadline_all_day:
        day: date = local.date()
        start = {"date": day.isoformat()}
        end = {"date": (day + timedelta(days=1)).isoformat()}
        overrides = [{"method": "popup", "minutes": 900}]
    else:
        start = {"dateTime": local.isoformat(timespec="seconds"), "timeZone": tz}
        end = {"dateTime": (local + _TIMED_DURATION).isoformat(timespec="seconds"), "timeZone": tz}
        overrides = [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 60}]
    return {
        "id": event_id_for(task, salt),
        "summary": _title(task),
        "description": _description(task, public_url),
        "start": start,
        "end": end,
        "reminders": {"useDefault": False, "overrides": overrides},
        "extendedProperties": {"private": {"followupTask": private_marker(task, salt)}},
    }
