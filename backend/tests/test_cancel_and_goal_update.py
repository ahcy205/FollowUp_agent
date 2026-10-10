"""Cancelling a task (keeps history, stops the agent) and rewriting its goal (re-plans)."""

from sqlalchemy import select

from app.agent import orchestrator
from app.agent.llm import set_llm_override
from app.db import SessionLocal
from app.models import Task, User
from tests.conftest import ScriptedLLM, register, tool_call
from tests.test_demo_scenario import GOAL, _first_run_steps

SETTLE = ("update_task_status", {"reason": "확인", "status": "ACTION_REQUIRED", "current_step": "조회 완료"})


def test_cancel_while_awaiting_approval(client):
    register(client)
    set_llm_override(ScriptedLLM(_first_run_steps()))
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    approval = client.get(f"/api/tasks/{tid}").json()["approvals"][0]

    r = client.post(f"/api/tasks/{tid}/cancel", json={"reason": "고객이 구매를 보류함"})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/tasks/{tid}").json()
    assert d["task"]["status"] == "CANCELLED"
    assert d["active_run"] is None and d["runs"][0]["status"] == "STOPPED"
    assert d["approvals"][0]["status"] == "CANCELLED"
    cancelled = next(l for l in d["logs"] if l["action_type"] == "TASK_CANCELLED")
    assert "고객이 구매를 보류함" in cancelled["reason"]
    assert not any(e["direction"] == "OUTBOUND" for e in d["emails"])

    # Nothing can revive it.
    assert client.post(f"/api/approvals/{approval['id']}/decide", json={"approved": True}).status_code == 409
    assert client.post(f"/api/tasks/{tid}/agent/retry").status_code == 409
    assert client.post(f"/api/tasks/{tid}/cancel", json={}).status_code == 409
    assert client.post(f"/api/tasks/{tid}/goal", json={"goal": "다른 목표로 바꿔줘"}).status_code == 409
    assert client.get("/api/dashboard").json()["counts"]["CANCELLED"] == 1


def test_cancel_during_a_run_stops_the_loop_before_its_tools_run(client):
    register(client)

    def cancel_mid_turn(_):
        with SessionLocal() as db:
            user = db.scalar(select(User))
            task = db.scalar(select(Task).where(Task.origin == "user"))
            orchestrator.cancel_task(db, user, task, "급한 일이 생김")
        return tool_call(("update_task_status", {"reason": "대기", "status": "WAITING", "current_step": "x", "waiting_for": "고객"}))

    llm = ScriptedLLM([cancel_mid_turn, lambda m: tool_call(SETTLE)])
    set_llm_override(llm)
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    d = client.get(f"/api/tasks/{tid}").json()
    assert d["task"]["status"] == "CANCELLED"  # the turn's update_task_status was discarded
    assert d["runs"][0]["status"] == "STOPPED"
    assert "update_task_status" not in [l["tool_name"] for l in d["logs"] if l["action_type"] == "TOOL_CALL"]
    assert len(llm.steps) == 1


def test_completed_task_cannot_be_cancelled_or_edited(client):
    register(client)
    done = next(t for t in client.get("/api/dashboard").json()["tasks"] if t["status"] == "COMPLETED")
    assert client.post(f"/api/tasks/{done['id']}/cancel", json={}).status_code == 409
    assert client.post(f"/api/tasks/{done['id']}/goal", json={"goal": "완료된 업무 목표 변경 시도"}).status_code == 409


def test_goal_update_logs_and_restarts_the_agent_with_the_new_goal(client):
    register(client)
    llm = ScriptedLLM([lambda m: tool_call(SETTLE), lambda m: tool_call(SETTLE)])
    set_llm_override(llm)
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]

    r = client.post(f"/api/tasks/{tid}/goal", json={"goal": "A회사에 견적서를 보내고 발주 확정까지 받아줘."})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/tasks/{tid}").json()
    assert d["task"]["goal"] == "A회사에 견적서를 보내고 발주 확정까지 받아줘."
    updated = next(l for l in d["logs"] if l["action_type"] == "GOAL_UPDATED")
    assert updated["input_data"]["from"] == GOAL
    assert len(d["runs"]) == 2 and d["runs"][0]["trigger"] == "goal_updated"
    first_message = llm.seen_messages[1][0]["content"]
    assert "목표가 변경" in first_message and "발주 확정까지" in first_message
    assert not llm.steps


def test_goal_update_is_refused_while_the_agent_is_busy(client):
    register(client)
    set_llm_override(ScriptedLLM(_first_run_steps()))
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]  # paused for approval
    r = client.post(f"/api/tasks/{tid}/goal", json={"goal": "승인 대기 중 목표 변경 시도"})
    assert r.status_code == 409
    assert "실행이 끝난 뒤" in r.json()["detail"]


def test_followup_goal_update_only_rewrites_text(client):
    register(client)
    with SessionLocal() as db:
        user = db.scalar(select(User))
        parent = db.scalar(select(Task.id).where(Task.origin == "seed"))
        f = Task(user_id=user.id, parent_task_id=parent, title="후속", goal="원래 설명", origin="agent", agent_enabled=False)
        db.add(f)
        db.commit()
        fid = f.id
    set_llm_override(ScriptedLLM([]))  # any LLM call would blow up
    r = client.post(f"/api/tasks/{fid}/goal", json={"goal": "수정된 설명입니다"})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/tasks/{fid}").json()
    assert d["task"]["goal"] == "수정된 설명입니다" and d["runs"] == []
