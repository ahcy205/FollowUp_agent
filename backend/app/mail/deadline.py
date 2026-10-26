"""LLM이 추출한 기한 표현을 코드로 검증해 Deadline으로 만든다.

지어낸 날짜(과거, 2년 초과, 형식 오류, 원문 없음)는 None으로 걸러낸다.
"""
import re
from dataclasses import dataclass
from datetime import date as _date, datetime, time as _time, timedelta, timezone
from zoneinfo import ZoneInfo

MAX_AHEAD_DAYS = 730
SOURCE_MAX = 300
USER_DEADLINE_SOURCE = "사용자가 직접 정함"  # 사용자가 정한 기한의 근거. 다시 분석해도 덮지 않는다
_KINDS = ("explicit", "estimated")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_RE = re.compile(r"(\d{2}):(\d{2})")


@dataclass(frozen=True)
class Deadline:
    at: datetime  # UTC aware
    all_day: bool
    kind: str  # "explicit" | "estimated"
    source: str


def normalize_deadline(
    text: str, date: str, time: str, kind: str, received_at: datetime, tz: str
) -> Deadline | None:
    # 잘못된 tz 이름은 설정 오류이므로 ZoneInfoNotFoundError를 그대로 올린다.
    zone = ZoneInfo(tz)
    text = (text or "").strip()
    if not text or kind not in _KINDS:
        return None
    date = (date or "").strip()
    if not _DATE_RE.fullmatch(date):
        return None
    try:
        day = _date.fromisoformat(date)
    except ValueError:
        return None

    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    recv_day = received_at.astimezone(zone).date()
    if day < recv_day or day > recv_day + timedelta(days=MAX_AHEAD_DAYS):
        return None

    m = _TIME_RE.fullmatch((time or "").strip())
    hour = minute = 0
    all_day = True
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        hour, minute = int(m.group(1)), int(m.group(2))
        all_day = False

    local = datetime.combine(day, _time(hour, minute), tzinfo=zone)
    return Deadline(
        at=local.astimezone(timezone.utc),
        all_day=all_day,
        kind=kind,
        source=text[:SOURCE_MAX],
    )


def earliest(deadlines: list[Deadline]) -> Deadline | None:
    return min(deadlines, key=lambda d: d.at) if deadlines else None
