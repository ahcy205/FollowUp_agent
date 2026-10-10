"""A user closes a follow-up task the agent created; the event shows on both timelines."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Task, TaskStatus, User
from tests.conftest import register


def _make_followup(parent_id: int, title: str = "A회사 납기일 단축 가능 여부 확인") -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User))
        task = Task(user_id=user.id, parent_task_id=parent_id, title=title, goal="생산팀 확인", status=TaskStatus.PENDING,
                    origin="agent", agent_enabled=False, next_action="생산팀 확인")
        db.add(task)
        db.commit()
        return task.id


def _seed_task(status: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Task.id).where(Task.status == status, Task.origin == "seed"))


def test_user_completes_followup_with_note(client):
    register(client)
    parent_id = _seed_task(TaskStatus.WAITING)
    fid = _make_followup(parent_id)

    r = client.post(f"/api/tasks/{fid}/complete", json={"note": "10월 8일 납품 가능, 추가 비용 없음"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "COMPLETED"

    d = client.get(f"/api/tasks/{fid}").json()
    assert d["task"]["status"] == "COMPLETED"
    assert d["task"]["result_summary"] == "10월 8일 납품 가능, 추가 비용 없음"
    assert d["task"]["completed_at"]
    assert "USER_COMPLETED" in [l["action_type"] for l in d["logs"]]

    parent = client.get(f"/api/tasks/{parent_id}").json()
    parent_log = next(l for l in parent["logs"] if l["action_type"] == "FOLLOWUP_COMPLETED")
    assert "A회사 납기일 단축 가능 여부 확인" in parent_log["reason"]
    assert parent["followups"][0]["status"] == "COMPLETED"


def test_only_agent_created_followups_can_be_completed_by_user(client):
    register(client)
    agent_task = _seed_task(TaskStatus.PENDING)  # a normal agent-driven task
    assert client.post(f"/api/tasks/{agent_task}/complete", json={"note": ""}).status_code == 409

    fid = _make_followup(_seed_task(TaskStatus.WAITING))
    assert client.post(f"/api/tasks/{fid}/complete", json={"note": ""}).status_code == 200
    assert client.post(f"/api/tasks/{fid}/complete", json={"note": ""}).status_code == 409  # already done

    assert client.post("/api/tasks/999999/complete", json={"note": ""}).status_code == 404
