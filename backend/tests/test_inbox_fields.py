from sqlalchemy import select

from app.db import SessionLocal
from app.models import Task, User
from tests.conftest import register


def _uid(db):
    return db.scalars(select(User.id)).first()


def test_defaults(client):
    register(client)
    with SessionLocal() as db:
        task = Task(user_id=_uid(db), title="t", goal="g")
        db.add(task)
        db.commit()
        db.refresh(task)
        assert task.snoozed_until is None
        assert task.snooze_returned_at is None
        assert task.priority_source == "llm"
        assert task.merged_into_id is None
        assert task.merge_dismissed is False
        assert task.merge_undo == {}


def test_merged_into_fk(client):
    register(client)
    with SessionLocal() as db:
        uid = _uid(db)
        a = Task(user_id=uid, title="a", goal="g")
        b = Task(user_id=uid, title="b", goal="g")
        db.add_all([a, b])
        db.commit()
        b.merged_into_id = a.id
        db.commit()
        db.refresh(b)
        assert b.merged_into_id == a.id
        bid = b.id
        db.delete(a)
        db.commit()
        db.expire_all()
        assert db.get(Task, bid).merged_into_id is None


def test_api_exposes(client):
    register(client)
    with SessionLocal() as db:
        task_id = db.scalars(select(Task.id)).first()
    r = client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["snoozed_until"] is None
    assert task["priority_source"] == "llm"
    assert task["merged_into_id"] is None
