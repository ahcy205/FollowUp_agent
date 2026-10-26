"""4단계 Task 5: 워커 연결과 다시 시도 API (FakeGoogle)."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.agent.llm import set_llm_override
from app.config import get_settings
from app.db import SessionLocal
from app.google.client import GoogleError
from app.mail import sync
from app.mail.sync import run_sync_cycle
from app.models import Email, Task, TaskStatus
from tests.conftest import ScriptedLLM, make_client, register
from tests.test_mail_pipeline import _add_account, _deliver

DEADLINE = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def acct(client, google_fake):
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    return _add_account(me["id"]), me["id"]


def _task(uid, *, state="none", deadline=DEADLINE, origin="mail", error="") -> int:
    with SessionLocal() as db:
        t = Task(user_id=uid, title="견적 요청", goal="견적서 송부", status=TaskStatus.ACTION_REQUIRED,
                 origin=origin, agent_enabled=False, deadline_at=deadline, deadline_all_day=True,
                 deadline_kind="explicit", calendar_state=state, calendar_error=error, calendar_attempts=5)
        db.add(t)
        db.commit()
        return t.id


def _analysis_with_deadline():
    return {"summary": "견적 요청", "counterpart": {"name": "김", "organization": "P"},
            "requests": ["견적"], "todos": [{"title": "견적 보내기", "due_text": "", "due_date": "", "due_time": ""}],
            "deadline": {"text": "10월 30일까지", "date": "2026-10-30", "time": "", "kind": "explicit"},
            "priority": "HIGH", "needs_reply": True, "suspicious": []}


def _work_verdict(prompt, schema):
    return {"results": [{"key": "m1", "label": "work", "reason": "업무"}]}


def test_cycle_syncs_calendar_after_intake(acct, google_fake, monkeypatch):
    _, uid = acct
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    _deliver(google_fake, "견적 요청")
    set_llm_override(ScriptedLLM(structured_steps=[_work_verdict, _analysis_with_deadline()]))
    run_sync_cycle()
    with SessionLocal() as db:
        t = db.scalars(select(Task).where(Task.user_id == uid, Task.origin == "mail")).one()
        assert t.calendar_state == "synced"
    assert len([c for c in google_fake.calendar_calls if c[0] == "insert"]) == 1


def test_calendar_runs_when_triage_off(acct, google_fake, monkeypatch):
    _, uid = acct
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", False)
    llm = ScriptedLLM(structured_steps=[AssertionError("no LLM")])
    set_llm_override(llm)
    tid = _task(uid)
    run_sync_cycle()
    assert llm.structured_prompts == [] and llm.calls == 0
    with SessionLocal() as db:
        assert db.get(Task, tid).calendar_state == "synced"
    assert len([c for c in google_fake.calendar_calls if c[0] == "insert"]) == 1


def test_sync_now_skips_calendar(client, acct, google_fake, monkeypatch):
    _, uid = acct
    _task(uid)
    monkeypatch.setattr("app.mail.sync.wake", lambda: None)
    r = client.post("/api/google/sync")
    assert r.status_code == 200
    assert google_fake.calendar_calls == []


def test_calendar_error_does_not_block_mail(acct, google_fake, monkeypatch):
    _, uid = acct
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    google_fake.calendar_fail = GoogleError("boom")
    _deliver(google_fake, "견적 요청")
    set_llm_override(ScriptedLLM(structured_steps=[_work_verdict, _analysis_with_deadline()]))
    run_sync_cycle()
    with SessionLocal() as db:
        mails = db.scalars(select(Email).where(Email.user_id == uid, Email.gmail_message_id.is_not(None))).all()
        assert len(mails) == 1 and mails[0].triage == "work"
        t = db.scalars(select(Task).where(Task.user_id == uid, Task.origin == "mail")).one()
        assert t.calendar_state == "error" and t.calendar_error


def test_calendar_exception_isolated(acct, google_fake, monkeypatch):
    _, uid = acct
    _deliver(google_fake, "견적 요청")

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(sync, "sync_calendars", boom)
    assert run_sync_cycle() == 1
    with SessionLocal() as db:
        assert len(db.scalars(select(Email).where(Email.user_id == uid, Email.gmail_message_id.is_not(None))).all()) == 1


def test_retry_endpoint(client, acct, google_fake, monkeypatch):
    _, uid = acct
    woke = []
    monkeypatch.setattr("app.mail.sync.wake", lambda: woke.append(1))
    tid = _task(uid, state="error", error="Google 캘린더에 일정을 반영하지 못했습니다.")

    r = client.post(f"/api/tasks/{tid}/calendar/retry")
    assert r.status_code == 200, r.text
    assert r.json()["task"]["calendar_state"] == "pending"
    assert r.json()["task"]["calendar_error"] == ""
    assert woke == [1]
    with SessionLocal() as db:
        t = db.get(Task, tid)
        assert t.calendar_state == "pending" and t.calendar_attempts == 0

    # another user gets 404
    with make_client() as other:
        register(other, "other@example.com")
        assert other.post(f"/api/tasks/{tid}/calendar/retry").status_code == 404

    # not a mail task
    uid_task = _task(uid, origin="user")
    assert client.post(f"/api/tasks/{uid_task}/calendar/retry").status_code == 409

    # deleted by user
    dead = _task(uid, state="deleted_by_user")
    r = client.post(f"/api/tasks/{dead}/calendar/retry")
    assert r.status_code == 409
    assert r.json()["detail"] == "캘린더에서 삭제된 일정은 다시 만들지 않습니다."
    with SessionLocal() as db:
        assert db.get(Task, dead).calendar_state == "deleted_by_user"


def test_retry_rate_limit(client, acct, monkeypatch):
    _, uid = acct
    monkeypatch.setattr("app.mail.sync.wake", lambda: None)
    tid = _task(uid, state="error")
    # rejected calls (409) do not consume quota
    dead = _task(uid, state="deleted_by_user")
    for _ in range(8):
        assert client.post(f"/api/tasks/{dead}/calendar/retry").status_code == 409
    for _ in range(5):
        assert client.post(f"/api/tasks/{tid}/calendar/retry").status_code == 200
    assert client.post(f"/api/tasks/{tid}/calendar/retry").status_code == 429
