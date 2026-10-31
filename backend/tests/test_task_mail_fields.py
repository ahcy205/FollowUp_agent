import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.models import Task, User
from tests.conftest import register


def _user_id(db) -> int:
    return db.scalars(select(User.id)).first()


def _task(uid, thread_id):
    return Task(user_id=uid, title="t", goal="g", gmail_thread_id=thread_id)


def test_thread_unique_per_user(client):
    register(client)
    with SessionLocal() as db:
        uid = _user_id(db)
        db.add(_task(uid, "thr-1"))
        db.commit()
        db.add(_task(uid, "thr-1"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_thread_null_allowed_many(client):
    register(client)
    with SessionLocal() as db:
        uid = _user_id(db)
        db.add_all([_task(uid, None), _task(uid, None), _task(uid, None)])
        db.commit()


def test_task_brief_exposes_mail_fields(client):
    me = register(client)
    with SessionLocal() as db:  # 예전에는 데모 가상 업무를 썼다: 메일 업무를 직접 만든다
        t = Task(user_id=me["id"], title="견적 요청", goal="견적서 송부", origin="mail")
        db.add(t)
        db.commit()
        task_id = t.id
    r = client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["brief"] == {}
    assert task["deadline_kind"] == "none"
    assert task["deadline_at"] is None
    assert task["deadline_all_day"] is False
    assert task["deadline_source"] == ""


def test_triage_disabled_by_default():
    s = get_settings()
    assert s.mail_triage_enabled is False
    assert s.mail_triage_batches_per_cycle == 3
    assert s.mail_intake_per_cycle == 5
