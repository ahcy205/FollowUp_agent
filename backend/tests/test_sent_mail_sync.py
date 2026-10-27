"""6단계 Task 4: Gmail에서 보낸 메일 중 업무 스레드의 것을 저장하고 '답장 보냄'으로 옮긴다."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.google.crypto import encrypt_token
from app.mail.sync import sync_account
from app.models import (
    AgentActionLog, Email, EmailDirection, GoogleAccount, GoogleAccountStatus, ReplyDraft, Task, TaskStatus,
)
from tests.conftest import register
from tests.fake_google import make_raw

NOW = datetime.now(timezone.utc)


def _ms(dt):
    return int(dt.timestamp() * 1000)


@pytest.fixture
def account(client, google_fake):
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    with SessionLocal() as db:
        acc = GoogleAccount(user_id=me["id"], google_email="me@gmail.com",
                            refresh_token_enc=encrypt_token("1//fake-refresh"),
                            scopes=["openid"], status=GoogleAccountStatus.CONNECTED)
        db.add(acc)
        db.commit()
        return acc.id, me["id"]


def _run(account_id, fake):
    with SessionLocal() as db:
        return sync_account(db, db.get(GoogleAccount, account_id), fake, now=NOW)


def _task(uid, thread="thr-1", **over):
    with SessionLocal() as db:
        f = dict(user_id=uid, title="견적 요청", goal="견적서", status=TaskStatus.ACTION_REQUIRED,
                 origin="mail", agent_enabled=False, gmail_thread_id=thread, priority="NORMAL", brief={})
        f.update(over)
        t = Task(**f)
        db.add(t)
        db.commit()
        return t.id


def _sent(fake, thread, subject="Re: 견적 요청", to="kim@partner.co.kr", when=None, labels=("SENT",)):
    raw = make_raw(sender="me@gmail.com", to=to, subject=subject, body="회신")
    return fake.deliver(raw, labels=labels, thread_id=thread, internal_date_ms=_ms(when or NOW))


def _rows():
    with SessionLocal() as db:
        return list(db.scalars(select(Email).where(Email.gmail_message_id.is_not(None)).order_by(Email.id)))


def _count_logs(tid, action="REPLY_SENT"):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(AgentActionLog).where(
            AgentActionLog.task_id == tid, AgentActionLog.action_type == action))


def test_sent_in_task_thread_marks_waiting(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    _sent(google_fake, "thr-1")
    r = _run(aid, google_fake)
    assert r.sent_stored == 1 and r.skipped_sent == 0
    rows = _rows()
    assert len(rows) == 1
    e = rows[0]
    assert e.direction == EmailDirection.OUTBOUND.value and e.sender == "me@gmail.com"
    assert e.task_id == tid and e.processed is True and e.triage == "sent"
    assert "kim@partner.co.kr" in e.receiver
    with SessionLocal() as db:
        t = db.get(Task, tid)
        assert t.status == TaskStatus.WAITING and t.reply_sent_at is not None
    assert _count_logs(tid) == 1


def test_sent_outside_task_thread_not_stored(account, google_fake):
    aid, uid = account
    _task(uid)
    _sent(google_fake, "other-thread")
    r = _run(aid, google_fake)
    assert r.skipped_sent == 1 and r.sent_stored == 0
    assert _rows() == []


def test_sent_in_merged_task_thread_attaches_to_target(account, google_fake):
    aid, uid = account
    target = _task(uid, thread="thr-a")
    _task(uid, thread="thr-b", status=TaskStatus.CANCELLED, merged_into_id=target)
    _sent(google_fake, "thr-b")
    _run(aid, google_fake)
    assert _rows()[0].task_id == target
    with SessionLocal() as db:
        assert db.get(Task, target).status == TaskStatus.WAITING


def test_sent_to_self_with_inbox_treated_as_sent(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    _sent(google_fake, "thr-1", labels=("INBOX", "SENT"))
    _run(aid, google_fake)
    assert _rows()[0].direction == EmailDirection.OUTBOUND.value
    with SessionLocal() as db:
        assert db.get(Task, tid).status == TaskStatus.WAITING


def test_app_sent_mail_not_duplicated(account, google_fake):
    """앱이 보낸 메일(Task 6이 저장함)이 동기화로 다시 와도 행 · 타임라인이 그대로다."""
    aid, uid = account
    tid = _task(uid)
    raw = make_raw(sender="me@gmail.com", to="kim@partner.co.kr", subject="Re: 견적 요청", body="회신")
    mid = google_fake.gmail_send("tok", raw, "thr-1").id
    with SessionLocal() as db:
        db.add(Email(user_id=uid, sender="me@gmail.com", receiver="kim@partner.co.kr", subject="Re: 견적 요청",
                     content="회신", direction=EmailDirection.OUTBOUND, processed=True, triage="sent",
                     task_id=tid, gmail_message_id=mid, gmail_thread_id="thr-1", gmail_meta={}))
        db.add(ReplyDraft(user_id=uid, task_id=tid, to_addr="kim@partner.co.kr", subject="Re: 견적 요청",
                          body="회신", status="sent", gmail_message_id=mid))
        t = db.get(Task, tid)
        t.status = TaskStatus.WAITING
        db.add(AgentActionLog(user_id=uid, task_id=tid, action_type="REPLY_SENT", output_data={}))
        db.commit()
    _run(aid, google_fake)
    assert len(_rows()) == 1
    assert _count_logs(tid) == 1


def test_unknown_draft_resolved_by_sync(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    start = NOW - timedelta(minutes=2)
    with SessionLocal() as db:
        db.add(ReplyDraft(user_id=uid, task_id=tid, to_addr="Kim@Partner.co.kr", subject="견적 요청",
                          body="회신", status="unknown", sending_at=start))
        db.commit()
    mid = _sent(google_fake, "thr-1", subject="RE: 견적 요청", to="kim@partner.co.kr", when=NOW)
    _run(aid, google_fake)
    with SessionLocal() as db:
        d = db.scalar(select(ReplyDraft).where(ReplyDraft.task_id == tid))
        assert d.status == "sent" and d.gmail_message_id == mid


def test_unknown_draft_not_resolved_when_recipient_differs(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    with SessionLocal() as db:
        db.add(ReplyDraft(user_id=uid, task_id=tid, to_addr="kim@partner.co.kr", subject="견적 요청",
                          body="x", status="unknown", sending_at=NOW - timedelta(minutes=1)))
        db.commit()
    _sent(google_fake, "thr-1", subject="Re: 견적 요청", to="someone@else.com")
    _run(aid, google_fake)
    with SessionLocal() as db:
        assert db.scalar(select(ReplyDraft.status).where(ReplyDraft.task_id == tid)) == "unknown"


def test_sent_on_closed_task_keeps_status(account, google_fake):
    aid, uid = account
    tid = _task(uid, status=TaskStatus.COMPLETED)
    _sent(google_fake, "thr-1")
    _run(aid, google_fake)
    assert len(_rows()) == 1
    with SessionLocal() as db:
        assert db.get(Task, tid).status == TaskStatus.COMPLETED
    assert _count_logs(tid) == 0
