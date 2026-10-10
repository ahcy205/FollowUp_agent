"""The user may rewrite the agent's draft while approving; the edited version is what gets sent."""

import json

from app.agent.llm import set_llm_override
from tests.conftest import ScriptedLLM, register, tool_call
from tests.test_demo_scenario import GOAL, _after_approval_steps, _first_run_steps


def _pause_for_approval(client):
    llm = ScriptedLLM(_first_run_steps() + _after_approval_steps())
    set_llm_override(llm)
    task_id = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    approval = client.get(f"/api/tasks/{task_id}").json()["approvals"][0]
    return llm, task_id, approval


def test_edited_draft_is_sent_and_agent_is_told(client):
    register(client)
    llm, task_id, approval = _pause_for_approval(client)
    original_subject = approval["email"]["subject"]

    r = client.post(f"/api/approvals/{approval['id']}/decide", json={
        "approved": True, "subject": "[견적] ICB-200 견적서 (수정본)", "body": "김민준 과장님, 수정된 견적서를 보내드립니다. 납기일은 10월 15일입니다. 감사합니다."})
    assert r.status_code == 200, r.text

    d = client.get(f"/api/tasks/{task_id}").json()
    sent = next(e for e in d["emails"] if e["direction"] == "OUTBOUND")
    assert sent["subject"] == "[견적] ICB-200 견적서 (수정본)"
    assert sent["body" if "body" in sent else "content"].startswith("김민준 과장님, 수정된")
    assert sent["receiver"] == "minjun.kim@a-company.example"  # recipient is never user-editable

    # The agent sees that what went out is the user's wording, not its own.
    result = json.loads(llm.seen_messages[5][-1]["content"][-1]["content"])
    assert result["status"] == "sent" and result["edited_by_user"] is True
    assert result["subject"] == "[견적] ICB-200 견적서 (수정본)"

    decided = next(l for l in d["logs"] if l["action_type"] == "APPROVAL_DECIDED")
    assert decided["output_data"]["edited"] is True
    assert decided["output_data"]["original"]["subject"] == original_subject


def test_rejecting_ignores_edits(client):
    register(client)
    llm = ScriptedLLM(_first_run_steps() + [
        lambda m: tool_call(("update_task_status", {"reason": "취소", "status": "ACTION_REQUIRED", "current_step": "취소됨"}))])
    set_llm_override(llm)
    task_id = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    approval = client.get(f"/api/tasks/{task_id}").json()["approvals"][0]

    r = client.post(f"/api/approvals/{approval['id']}/decide", json={"approved": False, "subject": "바뀐 제목", "body": "바뀐 본문입니다. 열 자 이상."})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/tasks/{task_id}").json()
    draft = next(e for e in d["emails"] if e["direction"] == "DRAFT")
    assert draft["subject"] == approval["email"]["subject"]
    assert draft["content"] == approval["email"]["content"]
