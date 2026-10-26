import re
from datetime import UTC, datetime

import pytest

from app.mail.calendar_event import build_event, event_id_for
from app.models import Task, TaskStatus

TZ = "Asia/Seoul"
URL = "http://localhost:8000/"
SALT = "abc12345"


def make_task(**kw) -> Task:
    base = dict(
        id=31,
        user_id=2,
        title="제목 대체",
        status=TaskStatus.IN_PROGRESS,
        brief={"summary": "견적서 회신", "requests": ["단가표 첨부", "납기 확인"], "counterpart": {"name": "김민수", "organization": "한빛상사"}},
        plan=[{"id": 1, "title": "견적 작성", "done": True}, {"id": 2, "title": "회신", "done": False}],
        deadline_at=datetime(2026, 10, 30, 5, 0, tzinfo=UTC),
        deadline_all_day=False,
        deadline_kind="explicit",
        deadline_source="금요일 오후 2시까지 부탁드립니다",
    )
    base.update(kw)
    return Task(**base)


def test_event_id_format():
    eid = event_id_for(make_task(), SALT)
    assert eid == "fu" + SALT + "0000001f"
    assert re.fullmatch(r"^[a-v0-9]{5,1024}$", eid)
    # 다른 salt(다른 DB · 다른 인스턴스)면 같은 업무 번호라도 다른 일정 번호다
    assert event_id_for(make_task(), "0123456v") != eid


def test_private_marker():
    ev = build_event(make_task(), tz=TZ, public_url=URL, salt=SALT)
    assert ev["extendedProperties"] == {"private": {"followupTask": "abc12345:31"}}


def test_all_day_dates_in_user_tz():
    ev = build_event(make_task(deadline_at=datetime(2026, 10, 29, 15, 0, tzinfo=UTC), deadline_all_day=True), tz=TZ, public_url=URL, salt=SALT)
    assert ev["start"] == {"date": "2026-10-30"}
    assert ev["end"] == {"date": "2026-10-31"}


def test_all_day_naive_datetime_treated_as_utc():
    ev = build_event(make_task(deadline_at=datetime(2026, 10, 29, 15, 0), deadline_all_day=True), tz=TZ, public_url=URL, salt=SALT)
    assert ev["start"] == {"date": "2026-10-30"}


def test_timed_event():
    ev = build_event(make_task(), tz=TZ, public_url=URL, salt=SALT)
    assert ev["start"] == {"dateTime": "2026-10-30T14:00:00+09:00", "timeZone": TZ}
    assert ev["end"] == {"dateTime": "2026-10-30T14:30:00+09:00", "timeZone": TZ}
    assert ev["id"] == event_id_for(make_task(), SALT)


def test_reminders():
    timed = build_event(make_task(), tz=TZ, public_url=URL, salt=SALT)["reminders"]
    assert timed == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 60}]}
    day = build_event(make_task(deadline_all_day=True), tz=TZ, public_url=URL, salt=SALT)["reminders"]
    assert day == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 900}]}


def test_event_is_private():
    # Shared calendars must not expose mail-derived details to other viewers.
    for task in (make_task(), make_task(deadline_all_day=True)):
        assert build_event(task, tz=TZ, public_url=URL, salt=SALT)["visibility"] == "private"


def test_title_variants():
    assert build_event(make_task(), tz=TZ, public_url=URL, salt=SALT)["summary"] == "[업무] 견적서 회신"
    assert build_event(make_task(deadline_kind="estimated"), tz=TZ, public_url=URL, salt=SALT)["summary"] == "(확인 필요) [업무] 견적서 회신"
    for st in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        assert build_event(make_task(status=st), tz=TZ, public_url=URL, salt=SALT)["summary"] == "✓ [업무] 견적서 회신"
    both = build_event(make_task(status=TaskStatus.COMPLETED, deadline_kind="estimated"), tz=TZ, public_url=URL, salt=SALT)
    assert both["summary"] == "✓ (확인 필요) [업무] 견적서 회신"
    fallback = build_event(make_task(brief={}), tz=TZ, public_url=URL, salt=SALT)
    assert fallback["summary"] == "[업무] 제목 대체"
    long = build_event(make_task(brief={"summary": "가" * 500}), tz=TZ, public_url=URL, salt=SALT)["summary"]
    assert len(long) == 200 and long.startswith("[업무] ")
    multi = build_event(make_task(brief={"summary": "첫줄\n둘째줄"}), tz=TZ, public_url=URL, salt=SALT)["summary"]
    assert "\n" not in multi


def test_description_lines():
    d = build_event(make_task(), tz=TZ, public_url=URL, salt=SALT)["description"]
    assert "상대: 김민수 (한빛상사)" in d
    assert "요청:\n- 단가표 첨부\n- 납기 확인" in d
    assert "할 일:\n☑ 견적 작성\n☐ 회신" in d
    assert "기한 근거: 금요일 오후 2시까지 부탁드립니다" in d
    assert "앱에서 보기: http://localhost:8000/tasks/31" in d
    assert "//tasks" not in d


def test_description_omits_empty_and_caps():
    d = build_event(make_task(brief={}, plan=[], deadline_source=""), tz=TZ, public_url=URL, salt=SALT)["description"]
    assert d == "앱에서 보기: http://localhost:8000/tasks/31"
    big = build_event(make_task(brief={"requests": ["x" * 100] * 100}), tz=TZ, public_url=URL, salt=SALT)["description"]
    assert len(big) <= 4000 and big.endswith("/tasks/31")


def test_no_deadline_raises():
    with pytest.raises(ValueError):
        build_event(make_task(deadline_at=None), tz=TZ, public_url=URL, salt=SALT)
