import pytest

from app.google.client import GoogleNotFound, GoogleTransientError
from tests.fake_google import FakeGoogle


def test_insert_without_id_generates_one_and_get_patch():
    f = FakeGoogle()
    ev = f.calendar_insert("t", {"summary": "a"})
    assert ev.id and ev.id in f.events and ev.status == "confirmed"
    p = f.calendar_patch("t", ev.id, {"summary": "b"})
    assert f.events[ev.id]["summary"] == "b" and p.id == ev.id
    assert f.calendar_get("t", ev.id).id == ev.id
    assert f.calendar_calls == [("insert", ev.id), ("patch", ev.id), ("get", ev.id)]


def test_get_unknown_not_found():
    with pytest.raises(GoogleNotFound):
        FakeGoogle().calendar_get("t", "nope")


def test_calendar_fail_once():
    f = FakeGoogle()
    f.calendar_fail = GoogleTransientError("x")
    with pytest.raises(GoogleTransientError):
        f.calendar_insert("t", {"id": "e1"})
    assert f.calendar_fail is None
    f.calendar_insert("t", {"id": "e1"})


def test_calendar_fail_times():
    f = FakeGoogle()
    f.calendar_fail = GoogleTransientError("x")
    f.calendar_fail_times = 2
    for _ in range(2):
        with pytest.raises(GoogleTransientError):
            f.calendar_get("t", "e1")
    assert f.calendar_fail is None and f.calendar_fail_times == 0
    with pytest.raises(GoogleNotFound):
        f.calendar_get("t", "e1")
