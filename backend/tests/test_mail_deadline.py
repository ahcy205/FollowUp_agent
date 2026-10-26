from datetime import datetime, timezone

from app.mail.deadline import Deadline, earliest, normalize_deadline

RECV = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)  # KST 2026-10-26 18:00
TZ = "Asia/Seoul"


def _n(date, time="", kind="explicit", text="10월 30일까지", recv=RECV):
    return normalize_deadline(text, date, time, kind, recv, TZ)


def test_all_day():
    d = _n("2026-10-30")
    assert d.at == datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)
    assert d.all_day is True and d.kind == "explicit"


def test_with_time():
    d = _n("2026-10-30", "14:00")
    assert d.at == datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc)
    assert d.all_day is False


def test_same_day_ok():
    assert _n("2026-10-26") is not None


def test_naive_received_treated_as_utc():
    assert _n("2026-10-26", recv=RECV.replace(tzinfo=None)) is not None
    assert _n("2026-10-25", recv=RECV.replace(tzinfo=None)) is None


def test_past_rejected():
    assert _n("2026-10-25") is None


def test_too_far_rejected():
    assert _n("2028-12-01") is None
    assert _n("2028-10-25") is not None  # +730일 경계 (2028 윤년)
    assert _n("2028-10-26") is None


def test_bad_format():
    for bad in ("2026-13-40", "10/30", "", "2026-1-5"):
        assert _n(bad) is None


def test_empty_text_rejected():
    assert _n("2026-10-30", text="") is None
    assert _n("2026-10-30", text="   ") is None


def test_bad_time_falls_back_all_day():
    d = _n("2026-10-30", "25:99")
    assert d.all_day is True
    assert d.at == datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)


def test_kind_validated():
    assert _n("2026-10-30", kind="maybe") is None
    assert _n("2026-10-30", kind="estimated").kind == "estimated"


def test_tz_boundary():
    recv = datetime(2026, 10, 26, 16, 30, tzinfo=timezone.utc)  # KST 27일 01:30
    assert _n("2026-10-26", recv=recv) is None
    assert _n("2026-10-27", recv=recv) is not None


def test_earliest():
    a = _n("2026-11-05")
    b = _n("2026-10-30", "14:00", kind="estimated")
    c = _n("2026-12-01")
    assert earliest([a, b, c]) is b
    assert earliest([]) is None


def test_source_truncated():
    d = _n("2026-10-30", text="가" * 500)
    assert len(d.source) == 300
