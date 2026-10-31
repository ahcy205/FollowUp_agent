"""6단계 Task 3: 보낸 답장 처리 공통 · 상대 답장 없음 확인(영업일 3일)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.mail import inbox, lifecycle
from app.mail.errors import ActionError
from app.models import AgentActionLog, Email, EmailDirection, Task, TaskStatus, User
from tests.conftest import register

TZ = "Asia/Seoul"
UTC = timezone.utc
_seq = iter(range(1, 1_000_000))


def kst(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC) - timedelta(hours=9)


def _user(db, email="u@example.com") -> User:
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    return u


def _task(db, uid, **over) -> Task:
    n = next(_seq)
    fields = dict(user_id=uid, title="견적 요청", goal="견적서 송부", status=TaskStatus.ACTION_REQUIRED,
                  origin="mail", gmail_thread_id=f"thr-{n}", priority="NORMAL",
                  brief={"analysis": "done", "summary": "견적 요청", "needs_reply": True})
    fields.update(over)
    t = Task(**fields)
    db.add(t)
    db.commit()
    return t


def _inbound(db, task, at) -> Email:
    n = next(_seq)
    e = Email(user_id=task.user_id, sender="kim@partner.co.kr", receiver="me@example.com", subject="s",
              content="c", direction=EmailDirection.INBOUND, processed=False, received_at=at,
              gmail_message_id=f"m-{n}", gmail_thread_id=task.gmail_thread_id, triage="work",
              task_id=task.id, gmail_meta={})
    db.add(e)
    db.commit()
    return e


def _waiting(db, uid, sent_at, **over) -> Task:
    return _task(db, uid, status=TaskStatus.WAITING, reply_sent_at=sent_at, wait_from=sent_at, **over)


def test_business_days_friday_evening_kst():
    start = kst(2026, 10, 23, 17)  # 금
    assert lifecycle.add_business_days(start, 3, TZ) == kst(2026, 10, 28, 17)  # 수


def test_business_days_from_saturday():
    assert lifecycle.add_business_days(kst(2026, 10, 24, 10), 3, TZ) == kst(2026, 10, 29, 0)  # 목 00:00


def test_business_days_from_sunday():
    assert lifecycle.add_business_days(kst(2026, 10, 25, 23), 3, TZ) == kst(2026, 10, 29, 0)  # 목 00:00


def test_check_no_reply_moves_after_3_days():
    with SessionLocal() as db:
        u = _user(db)
        sent = kst(2026, 10, 26, 10)  # 월
        t = _waiting(db, u.id, sent)
        assert lifecycle.check_no_reply(db, u.id, kst(2026, 10, 28, 9), TZ) == 0  # 2영업일
        db.refresh(t)
        assert t.status == TaskStatus.WAITING and t.no_reply_asked_at is None
        now = kst(2026, 10, 29, 10)  # 3영업일
        assert lifecycle.check_no_reply(db, u.id, now, TZ) == 1
        db.refresh(t)
        assert t.status == TaskStatus.ACTION_REQUIRED and t.no_reply_asked_at is not None
        assert t.current_step == "상대 답장 없음 — 다시 연락할까요?"
        alerts = inbox.alerts_for(t, None, None, now, TZ)
        assert [a["kind"] for a in alerts] == ["no-reply"]
        assert alerts[0]["text"].startswith("[답 없음] ")
        assert alerts[0]["key"].startswith(f"no-reply:{t.id}:")
        assert lifecycle.check_no_reply(db, u.id, now, TZ) == 0  # 다시 돌려도 중복 없음


def test_check_no_reply_ignores_when_inbound_after():
    with SessionLocal() as db:
        u = _user(db)
        t = _waiting(db, u.id, kst(2026, 10, 26, 10))
        _inbound(db, t, kst(2026, 10, 27, 10))
        assert lifecycle.check_no_reply(db, u.id, kst(2026, 11, 5, 10), TZ) == 0
        db.refresh(t)
        assert t.status == TaskStatus.WAITING


def test_check_no_reply_ignores_old_inbound_and_other_users():
    with SessionLocal() as db:
        u = _user(db)
        other = _user(db, "o@example.com")
        sent = kst(2026, 10, 26, 10)
        t = _waiting(db, u.id, sent)
        _inbound(db, t, kst(2026, 10, 25, 10))  # 보내기 전 메일은 무관
        theirs = _waiting(db, other.id, sent)
        assert lifecycle.check_no_reply(db, u.id, kst(2026, 11, 5, 10), TZ) == 1
        db.refresh(theirs)
        assert theirs.status == TaskStatus.WAITING


def _asked(db, uid, now):
    t = _waiting(db, uid, now - timedelta(days=5))
    lifecycle.check_no_reply(db, uid, now, TZ)
    db.refresh(t)
    assert t.no_reply_asked_at is not None
    return t


def test_answer_wait_restarts_clock():
    with SessionLocal() as db:
        u = _user(db)
        now = kst(2026, 10, 29, 10)
        t = _asked(db, u.id, now)
        lifecycle.answer_no_reply(db, u, t, "wait", now)
        db.commit()
        assert t.status == TaskStatus.WAITING and t.no_reply_asked_at is None
        assert t.wait_from.replace(tzinfo=UTC) == now


def test_answer_close_completes():
    with SessionLocal() as db:
        u = _user(db)
        now = kst(2026, 10, 29, 10)
        t = _asked(db, u.id, now)
        lifecycle.answer_no_reply(db, u, t, "close", now)
        db.commit()
        assert t.status == TaskStatus.COMPLETED and t.completed_at is not None
        logs = db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == t.id,
                                                       AgentActionLog.action_type == "TASK_CLOSED")).all()
        assert logs and logs[0].output_data["reason"] == "상대 답장 없음으로 닫음"


def test_answer_followup_keeps_state():
    with SessionLocal() as db:
        u = _user(db)
        now = kst(2026, 10, 29, 10)
        t = _asked(db, u.id, now)
        lifecycle.answer_no_reply(db, u, t, "followup", now)
        assert t.status == TaskStatus.ACTION_REQUIRED and t.no_reply_asked_at is not None


def test_answer_without_question_409():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id)
        with pytest.raises(ActionError) as e:
            lifecycle.answer_no_reply(db, u, t, "wait", kst(2026, 10, 29, 10))
        assert e.value.status == 409 and "다시 연락할지 묻는 업무가 아닙니다" in str(e.value)


def test_mark_reply_sent_ends_nudge():
    with SessionLocal() as db:
        u = _user(db)
        now = kst(2026, 10, 29, 10)
        t = _task(db, u.id, snoozed_until=now + timedelta(days=1))
        mail = _inbound(db, t, now - timedelta(days=3))
        at = now - timedelta(days=3)
        t.snoozed_until = None
        assert inbox.nudge_days(t, at, now, TZ) == 3
        t.snoozed_until = now + timedelta(days=1)
        lifecycle.mark_reply_sent(db, t, mail, now, source="app")
        db.commit()
        assert inbox.tab_of(t) == "sent"
        assert inbox.nudge_days(t, at, now, TZ) is None
        assert t.status == TaskStatus.WAITING and t.waiting_for == "상대 회신"
        assert t.current_step == "답장 보냄" and t.snoozed_until is None
        assert t.reply_sent_at is not None and t.wait_from is not None
        logs = db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == t.id,
                                                       AgentActionLog.action_type == "REPLY_SENT")).all()
        assert len(logs) == 1


def test_mark_reply_sent_ignores_closed_and_merged():
    with SessionLocal() as db:
        u = _user(db)
        now = kst(2026, 10, 29, 10)
        closed = _task(db, u.id, status=TaskStatus.COMPLETED)
        target = _task(db, u.id)
        merged = _task(db, u.id, merged_into_id=target.id)
        for t in (closed, merged):
            lifecycle.mark_reply_sent(db, t, None, now, source="gmail")
            assert t.reply_sent_at is None
        assert closed.status == TaskStatus.COMPLETED


def test_clear_wait_on_inbound():
    t = Task(user_id=1, title="x", goal="x", no_reply_asked_at=kst(2026, 10, 29))
    lifecycle.clear_wait_on_inbound(t)
    assert t.no_reply_asked_at is None


def test_no_reply_api_validates_choice(client):
    me = register(client)
    with SessionLocal() as db:
        tid = _asked(db, me["id"], datetime.now(UTC)).id
        other = _user(db, "other@example.com")
        oid = _waiting(db, other.id, datetime.now(UTC)).id
    assert client.post(f"/api/tasks/{tid}/no-reply", json={"choice": "bogus"}).status_code == 422
    assert client.post(f"/api/tasks/{tid}/no-reply", json={"choice": "wait", "x": 1}).status_code == 422
    assert client.post(f"/api/tasks/{oid}/no-reply", json={"choice": "wait"}).status_code == 404
    r = client.post(f"/api/tasks/{tid}/no-reply", json={"choice": "wait"})
    assert r.status_code == 200 and r.json()["task"]["status"] == "WAITING"
    assert client.post(f"/api/tasks/{tid}/no-reply", json={"choice": "wait"}).status_code == 409


def test_detail_and_card_flags(client):
    me = register(client)
    with SessionLocal() as db:
        tid = _asked(db, me["id"], datetime.now(UTC)).id
    m = client.get(f"/api/tasks/{tid}").json()["mail"]
    assert m["no_reply"] is True and m["reply_sent_at"] is not None
    todo = client.get("/api/inbox").json()["tabs"]["todo"]
    cards = [c for k in ("now", "week", "later") for c in todo[k]]
    assert [c["no_reply"] for c in cards if c["task_id"] == tid] == [True]


def test_cycle_runs_no_reply_check_with_triage_off(client, google_fake, monkeypatch):
    from app.agent.llm import set_llm_override
    from app.config import get_settings
    from app.mail.sync import run_sync_cycle
    from tests.conftest import ScriptedLLM
    from tests.test_mail_pipeline import _add_account
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    _add_account(me["id"])
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", False)
    set_llm_override(ScriptedLLM(structured_steps=[AssertionError("no LLM")]))
    with SessionLocal() as db:
        tid = _waiting(db, me["id"], datetime.now(UTC) - timedelta(days=10)).id
    run_sync_cycle()
    with SessionLocal() as db:
        assert db.get(Task, tid).status == TaskStatus.ACTION_REQUIRED


def test_cycle_survives_no_reply_failure(client, google_fake, monkeypatch):
    from app.mail import sync
    from app.mail.sync import run_sync_cycle
    from tests.test_mail_pipeline import _add_account
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    _add_account(me["id"])

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(sync.lifecycle, "check_no_reply", boom)
    assert run_sync_cycle() == 1


def test_check_no_reply_sees_unattached_thread_reply():
    # 트리아지가 꺼져 intake가 안 돌면 상대 답장에 task_id가 없다. 같은 스레드면 답이 온 것으로 본다.
    with SessionLocal() as db:
        u = _user(db)
        t = _waiting(db, u.id, kst(2026, 10, 26, 10))
        n = next(_seq)
        db.add(Email(user_id=u.id, sender="kim@partner.co.kr", receiver="me@example.com", subject="s",
                     content="c", direction=EmailDirection.INBOUND, processed=False,
                     received_at=kst(2026, 10, 27, 10), gmail_message_id=f"m-{n}",
                     gmail_thread_id=t.gmail_thread_id, triage="pending", task_id=None, gmail_meta={}))
        db.commit()
        assert lifecycle.check_no_reply(db, u.id, kst(2026, 11, 5, 10), TZ) == 0
        db.refresh(t)
        assert t.status == TaskStatus.WAITING
