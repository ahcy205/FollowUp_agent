import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import KnowledgeChunk, KnowledgeSource, ReplyDraft, Task, User
from tests.conftest import make_client, register


def _src(uid, h="h1"):
    return KnowledgeSource(user_id=uid, title="t", file_type="txt", size=1, content_hash=h, status="indexed")


def test_source_hash_unique_per_user(client):
    register(client, "a@example.com")
    register(client, "b@example.com")
    with SessionLocal() as db:
        a, b = db.scalars(select(User.id).order_by(User.id)).all()
        db.add(_src(a))
        db.commit()
        db.add(_src(b))  # other user, same hash: allowed
        db.commit()
        db.add(_src(a))
        with pytest.raises(IntegrityError):
            db.commit()


def test_delete_source_cascades_chunks(client):
    register(client)
    with SessionLocal() as db:
        uid = db.scalars(select(User.id)).first()
        s = _src(uid)
        db.add(s)
        db.commit()
        db.add(KnowledgeChunk(source_id=s.id, user_id=uid, ordinal=0, text="x", lines=["x"], content_hash="c"))
        db.commit()
        assert s.visibility == "internal" and s.kind == "document" and s.notes == {}
        db.delete(s)
        db.commit()
        assert db.scalars(select(KnowledgeChunk)).all() == []


def test_reply_draft_sources_default_empty(client):
    register(client)
    with SessionLocal() as db:
        uid = db.scalars(select(User.id)).first()
        task = Task(user_id=uid, title="t", goal="g")
        db.add(task)
        db.commit()
        d = ReplyDraft(user_id=uid, task_id=task.id, purpose="reply", to_addr="a@example.com", subject="s", body="b")
        db.add(d)
        db.commit()
        assert d.sources == {}


def test_me_reports_knowledge_enabled(client):
    assert register(client)["knowledge_enabled"] is False
    assert client.get("/api/auth/me").json()["knowledge_enabled"] is False
