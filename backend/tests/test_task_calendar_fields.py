from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Task, User
from tests.conftest import register


def test_defaults(client):
    register(client)
    with SessionLocal() as db:
        uid = db.scalars(select(User.id)).first()
        task = Task(user_id=uid, title="t", goal="g")
        db.add(task)
        db.commit()
        db.refresh(task)
        assert task.calendar_event_id is None
        assert task.calendar_event_url == ""
        assert task.calendar_state == "none"
        assert task.calendar_error == ""
        assert task.calendar_attempts == 0


def test_task_api_exposes_calendar(client):
    me = register(client)
    with SessionLocal() as db:  # 예전에는 데모 가상 업무를 썼다: 메일 업무를 직접 만든다
        t = Task(user_id=me["id"], title="견적 요청", goal="견적서 송부", origin="mail")
        db.add(t)
        db.commit()
        task_id = t.id
    r = client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["calendar_state"] == "none"
    assert task["calendar_event_url"] == ""
    assert task["calendar_error"] == ""


def test_setting_default():
    assert get_settings().calendar_sync_per_cycle == 10
