from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.agent.llm import LLMError, set_llm_override
from app.config import get_settings
from app.db import SessionLocal
from app.google.crypto import encrypt_token
from app.mail import sync
from app.mail.sync import run_sync_cycle
from app.models import Email, GoogleAccount, GoogleAccountStatus, Task
from tests.conftest import ScriptedLLM, register
from tests.fake_google import make_raw


def _add_account(user_id: int) -> int:
    with SessionLocal() as db:
        acc = GoogleAccount(user_id=user_id, google_email=f"u{user_id}@gmail.com",
                            refresh_token_enc=encrypt_token("1//fake-refresh"),
                            scopes=["openid"], status=GoogleAccountStatus.CONNECTED)
        db.add(acc)
        db.commit()
        return acc.id


@pytest.fixture
def acct(client, google_fake, monkeypatch):
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    return _add_account(me["id"]), me["id"]


def _deliver(fake, subject, labels=("INBOX",)):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return fake.deliver(make_raw(sender="kim@partner.co.kr", to="me@gmail.com", subject=subject,
                                 body="견적서를 보내 주세요."), labels=labels, internal_date_ms=now_ms)


def _analysis():
    return {"summary": "견적 요청", "counterpart": {"name": "김", "organization": "P"},
            "requests": ["견적"], "todos": [{"title": "견적 보내기", "due_text": "", "due_date": "", "due_time": ""}],
            "deadline": {"text": "", "date": "", "time": "", "kind": "none"},
            "priority": "HIGH", "needs_reply": True, "suspicious": []}


def _work_verdict(prompt, schema):
    return {"results": [{"key": "m1", "label": "work", "reason": "업무"}]}


def test_cycle_runs_triage_and_intake_when_enabled(acct, google_fake):
    _, uid = acct
    _deliver(google_fake, "견적 요청")
    _deliver(google_fake, "특가 안내", labels=("INBOX", "CATEGORY_PROMOTIONS"))
    llm = ScriptedLLM(structured_steps=[_work_verdict, _analysis()])
    set_llm_override(llm)
    assert run_sync_cycle() == 1
    with SessionLocal() as db:
        assert len(db.scalars(select(Task).where(Task.user_id == uid, Task.origin == "mail")).all()) == 1
        states = sorted(e.triage for e in db.scalars(select(Email).where(Email.user_id == uid, Email.gmail_message_id.is_not(None))))
        assert states == ["filtered", "work"]


def test_disabled_never_calls_llm(acct, google_fake, monkeypatch):
    _, uid = acct
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", False)
    _deliver(google_fake, "견적 요청")
    llm = ScriptedLLM(structured_steps=[AssertionError("no LLM")])
    set_llm_override(llm)
    monkeypatch.setattr(sync, "get_llm", lambda: pytest.fail("get_llm must not be called"), raising=False)
    assert run_sync_cycle() == 1
    assert llm.structured_prompts == []
    with SessionLocal() as db:
        assert [e.triage for e in db.scalars(select(Email).where(Email.user_id == uid, Email.gmail_message_id.is_not(None)))] == ["pending"]


def test_sync_now_does_not_process(client, acct, google_fake, monkeypatch):
    _, uid = acct
    _deliver(google_fake, "견적 요청")
    llm = ScriptedLLM(structured_steps=[AssertionError("no LLM")])
    set_llm_override(llm)
    woke = []
    monkeypatch.setattr("app.mail.sync.wake", lambda: woke.append(1))
    r = client.post("/api/google/sync")
    assert r.status_code == 200
    assert llm.structured_prompts == []
    assert woke == [1]
    with SessionLocal() as db:
        mails = db.scalars(select(Email).where(Email.user_id == uid, Email.gmail_message_id.is_not(None))).all()
        assert len(mails) == 1 and mails[0].triage == "pending"


def test_llm_unavailable_skips_quietly(acct, google_fake, monkeypatch):
    aid, uid = acct
    _deliver(google_fake, "견적 요청")

    def boom():
        raise LLMError("키 없음")

    monkeypatch.setattr(sync, "get_llm", boom, raising=False)
    assert run_sync_cycle() == 1
    with SessionLocal() as db:
        assert [e.triage for e in db.scalars(select(Email).where(Email.user_id == uid, Email.gmail_message_id.is_not(None)))] == ["pending"]
        acc = db.get(GoogleAccount, aid)
        assert acc.last_sync_error == "" and acc.sync_lease_until is None


def test_processing_error_does_not_undo_sync(client, google_fake, monkeypatch):
    a = register(client, "a@example.com")
    client.post("/api/auth/logout")
    b = register(client, "b@example.com")
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    ida, idb = _add_account(a["id"]), _add_account(b["id"])
    _deliver(google_fake, "견적 요청")
    set_llm_override(ScriptedLLM())
    seen = []

    def bad_triage(db, user_id, llm, *, max_batches):
        seen.append(user_id)
        raise RuntimeError("boom")

    monkeypatch.setattr(sync, "triage_pending", bad_triage, raising=False)
    assert run_sync_cycle() == 2
    assert sorted(seen) == sorted([a["id"], b["id"]])
    with SessionLocal() as db:
        assert len(db.scalars(select(Email).where(Email.gmail_message_id.is_not(None))).all()) == 2  # stored for both users despite the failure
        for i in (ida, idb):
            acc = db.get(GoogleAccount, i)
            assert acc.last_sync_error == "" and acc.sync_lease_until is None
