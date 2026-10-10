"""Server-sent task updates: a change fingerprint and the SSE stream built on it."""

import asyncio
import json

from sqlalchemy import select

from app.agent.audit import log_action
from app.api.tasks import task_event_frames, task_fingerprint
from app.db import SessionLocal
from app.models import AgentRun, RunStatus, Task, TaskStatus, User
from tests.conftest import make_client, register


def _task_id(client) -> int:
    return client.get("/api/dashboard").json()["tasks"][0]["id"]


def test_fingerprint_changes_only_when_the_task_view_changes(client):
    register(client)
    tid = _task_id(client)
    with SessionLocal() as db:
        task = db.get(Task, tid)
        user = db.scalar(select(User))
        f0 = task_fingerprint(db, task)
        assert task_fingerprint(db, task) == f0  # stable while nothing happens

        log_action(db, user_id=user.id, task_id=tid, run_id=None, action_type="EVENT_RECEIVED", reason="x")
        db.commit()
        f1 = task_fingerprint(db, task)
        assert f1 != f0

        run = AgentRun(user_id=user.id, task_id=tid, trigger="goal_submitted", status=RunStatus.RUNNING, messages=[])
        db.add(run)
        db.commit()
        f2 = task_fingerprint(db, task)
        assert f2 != f1

        run.steps += 1
        db.commit()
        f3 = task_fingerprint(db, task)
        assert f3 != f2

        task.status = TaskStatus.BLOCKED
        db.commit()
        assert task_fingerprint(db, task) != f3


def test_event_stream_sends_the_current_snapshot_first(client):
    register(client)
    tid = _task_id(client)
    uid = client.get("/api/auth/me").json()["id"]
    checks = 0

    async def disconnected_after_one_tick():
        nonlocal checks
        checks += 1
        return checks > 1

    async def collect():
        return [frame async for frame in task_event_frames(tid, uid, disconnected_after_one_tick)]

    frames = asyncio.run(collect())
    assert len(frames) == 1
    lines = frames[0].split(chr(10))
    assert lines[0] == "event: task"
    payload = json.loads(lines[1].removeprefix("data: "))
    assert payload["task"]["id"] == tid
    assert "logs" in payload and "active_run" in payload
    assert frames[0].endswith(chr(10) * 2)


def test_event_stream_skips_pushes_while_nothing_changes(client):
    register(client)
    tid = _task_id(client)
    uid = client.get("/api/auth/me").json()["id"]
    checks = 0

    async def disconnected_after_three_ticks():
        nonlocal checks
        checks += 1
        return checks > 3

    async def collect():
        return [frame async for frame in task_event_frames(tid, uid, disconnected_after_three_ticks)]

    assert len(asyncio.run(collect())) == 1  # the initial snapshot only; no repeats for an idle task


def test_event_stream_hides_other_users_tasks():
    with make_client() as alice, make_client() as bob:
        register(alice, "alice@example.com")
        register(bob, "bob@example.com")
        tid = _task_id(alice)
        assert bob.get(f"/api/tasks/{tid}/events").status_code == 404
        anon = make_client()
        assert anon.get(f"/api/tasks/{tid}/events").status_code == 401
