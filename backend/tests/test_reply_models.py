import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import ReplyDraft, Task, User
from tests.conftest import register
from tests.fake_google import FakeGoogle, make_raw


def _uid(db):
    return db.scalars(select(User.id)).first()


def _draft(uid, tid):
    return ReplyDraft(user_id=uid, task_id=tid, purpose="reply", to_addr="a@example.com", subject="s", body="b")


def test_reply_draft_unique_per_task(client):
    register(client)
    with SessionLocal() as db:
        uid = _uid(db)
        task = Task(user_id=uid, title="t", goal="g")
        db.add(task)
        db.commit()
        db.add(_draft(uid, task.id))
        db.commit()
        db.add(_draft(uid, task.id))
        with pytest.raises(IntegrityError):
            db.commit()


def test_reply_draft_defaults(client):
    register(client)
    with SessionLocal() as db:
        uid = _uid(db)
        task = Task(user_id=uid, title="t", goal="g")
        db.add(task)
        db.commit()
        d = _draft(uid, task.id)
        db.add(d)
        db.commit()
        db.refresh(d)
        assert d.status == "draft"
        assert d.cc_candidates == [] and d.cc_selected == [] and d.warnings == {}
        assert d.user_input == "" and d.gmail_message_id is None and d.sending_at is None
        assert d.created_at is not None and d.updated_at is not None


def test_task_new_columns_defaults(client):
    register(client)
    with SessionLocal() as db:
        task = Task(user_id=_uid(db), title="t", goal="g")
        db.add(task)
        db.commit()
        db.refresh(task)
        assert task.change_log == []
        assert task.related == {}
        assert task.merge_analysis == {}
        assert task.changed_unseen is False
        assert task.reply_sent_at is None
        assert task.wait_from is None
        assert task.no_reply_asked_at is None


def test_fake_gmail_send_adds_sent_message():
    fake = FakeGoogle()
    raw = make_raw(sender="me@gmail.com", to="a@example.com", subject="Re: x", body="hi")
    start = fake.history_id
    sent = fake.gmail_send("tok", raw, "thr-1")
    assert sent.thread_id == "thr-1"
    assert fake.mailbox[sent.id].label_ids == ["SENT"]
    assert fake.mailbox[sent.id].thread_id == "thr-1"
    assert fake.mailbox[sent.id].raw == raw
    assert fake.gmail_history("tok", str(start), None).message_ids == [sent.id]
    assert fake.sent == [(raw, "thr-1")]


def test_fake_calendar_delete():
    fake = FakeGoogle()
    fake.events["e1"] = {"id": "e1"}
    fake.calendar_delete("tok", "e1")
    assert "e1" not in fake.events and "e1" in fake.deleted_events
