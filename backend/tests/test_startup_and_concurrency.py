"""Startup demo-account refresh and concurrent run-start serialization."""

import threading

from sqlalchemy import func, select

from app.agent import orchestrator
from app.config import get_settings
from app.db import SessionLocal
from app.main import _ensure_demo_user
from app.models import AgentRun, Task, TaskStatus, User
from tests.conftest import register


def test_startup_refreshes_demo_user_password(client, monkeypatch):
    s = get_settings()
    register(client, s.demo_user_email, "old-password-1")
    client.post("/api/auth/logout")
    monkeypatch.setattr(s, "demo_user_password", "new-password-2")

    _ensure_demo_user()

    assert client.post("/api/auth/login", json={"email": s.demo_user_email, "password": "new-password-2"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": s.demo_user_email, "password": "old-password-1"}).status_code == 401


def test_concurrent_start_run_creates_only_one_run(client, monkeypatch):
    register(client)
    orchestrator.set_dispatcher(lambda run_id: None)  # keep the run RUNNING without executing it
    with SessionLocal() as db:
        task_id = db.scalar(select(Task.id).where(Task.status == TaskStatus.PENDING, Task.agent_enabled.is_(True)))

    # The first caller lingers inside the "is a run active?" check so a second caller can race it.
    arrived: list[int] = []
    gate = threading.Event()
    real_active_run = orchestrator.active_run

    def slow_active_run(db, task):
        arrived.append(1)
        if len(arrived) == 1:
            gate.wait(0.5)
        else:
            gate.set()
        return real_active_run(db, task)

    monkeypatch.setattr(orchestrator, "active_run", slow_active_run)
    errors: list[Exception] = []

    def start():
        with SessionLocal() as db:
            user = db.scalar(select(User))
            task = db.get(Task, task_id)
            try:
                orchestrator.start_run(db, user, task, "manual_retry")
            except orchestrator.AgentBusyError as e:
                errors.append(e)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with SessionLocal() as db:
        runs = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.task_id == task_id))
    assert runs == 1
    assert len(errors) == 1
