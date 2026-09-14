"""End-to-end: goal → tools → approval → WAITING → customer reply event → re-run → follow-up → COMPLETED."""

from app.agent.llm import set_llm_override
from tests.conftest import ScriptedLLM, final, last_results, register, tool_call

GOAL = "A회사에 견적서를 보내고 고객 답변이 오면 내용을 확인해서 내가 해야 할 일을 정리해줘."


def _first_run_steps():
    state = {}

    def plan(_):
        return tool_call(("plan_goal", {"reason": "목표를 하위 목표로 분해", "task_title": "A회사 견적 업무", "subgoals": [
            "견적서 확인", "견적서 전송", "고객 답변 수신", "고객 요청 분석", "후속 업무 생성"]}))

    def customer(_):
        return tool_call(("search_customer", {"reason": "A회사 담당자 확인", "customer_name": "A회사"}))

    def document(messages):
        state["customer_id"] = last_results(messages)[0]["matches"][0]["customer_id"]
        return tool_call(("search_document", {"reason": "A회사 견적서 조회", "keyword": "A회사 견적서"}))

    def draft(messages):
        docs = last_results(messages)[0]["documents"]
        state["document_id"] = docs[0]["document_id"]
        assert "untrusted_external_content" in docs[0]["content"]
        return tool_call(
            ("complete_plan_step", {"reason": "견적서 확인", "step_id": 1, "evidence": f"document_id={docs[0]['document_id']}"}),
            ("draft_email", {"reason": "견적서 송부 메일 작성", "customer_id": state["customer_id"], "document_id": state["document_id"],
                             "subject": "[견적] 산업용 제어 보드 ICB-200 견적서 송부", "body": "김민준 과장님, 요청하신 견적서를 송부드립니다. 납기일은 10월 15일입니다."}),
            text="견적서를 확인했으니 메일 초안을 작성하겠습니다.",
        )

    def send(messages):
        draft_id = last_results(messages)[1]["draft_id"]
        return tool_call(("send_email", {"reason": "견적서 메일 발송", "draft_id": draft_id}))

    return [plan, customer, document, draft, send]


def _after_approval_steps():
    return [
        lambda m: tool_call(("complete_plan_step", {"reason": "발송 확인", "step_id": 2, "evidence": "메일 발송 완료"})),
        lambda m: tool_call(("update_task_status", {"reason": "고객 답변 대기", "status": "WAITING", "current_step": "견적서 전송 완료",
                                                     "waiting_for": "고객 답변", "next_action": "답변이 오면 분석"})),
    ]


def _reply_steps():
    state = {}

    def check(_):
        return tool_call(("check_email", {"reason": "새 답변 확인"}))

    def analyze(messages):
        r = last_results(messages)[0]
        assert r["new_count"] == 1
        state["email_id"] = r["emails"][0]["email_id"]
        return tool_call(("analyze_email", {"reason": "고객 요청 분석", "email_id": state["email_id"]}))

    def premature_complete(_):
        return tool_call(("update_task_status", {"reason": "완료 시도", "status": "COMPLETED", "current_step": "완료 시도", "result_summary": "완료"}))

    def followup(messages):
        err = messages[-1]["content"][0]
        assert err.get("is_error") and "완료 불가" in err["content"]
        return tool_call(
            ("create_followup_task", {"reason": "납기 단축 확인 필요", "title": "A회사 납기일 단축 가능 여부 확인",
                                      "description": "생산팀에 10월 8일 납품 가능 여부와 추가 비용 확인", "priority": "HIGH"}),
            ("complete_plan_step", {"reason": "수신", "step_id": 3, "evidence": f"email_id={state['email_id']}"}),
            ("complete_plan_step", {"reason": "분석", "step_id": 4, "evidence": "납기 단축 요청"}),
        )

    def complete(_):
        return tool_call(
            ("complete_plan_step", {"reason": "후속 업무 생성", "step_id": 5, "evidence": "후속 업무 1건"}),
            ("update_task_status", {"reason": "모든 하위 목표 달성", "status": "COMPLETED", "current_step": "후속 업무 생성 완료",
                                    "result_summary": "고객이 납기 단축을 요청해 후속 업무를 생성했습니다."}),
        )

    return [check, analyze, premature_complete, followup, complete]


ANALYSIS = {
    "summary": "납기일 단축 요청", "customer_intent": "change_request", "customer_requests": ["납기 10월 8일로 단축"],
    "goal_assessment": "답변 수신, 후속 조치 필요", "followup_actions": [
        {"title": "A회사 납기일 단축 가능 여부 확인", "description": "생산팀 확인", "priority": "HIGH"}],
    "requires_user_decision": True, "suspicious_instructions": [],
}


def test_full_follow_up_scenario(client):
    register(client)
    llm = ScriptedLLM(_first_run_steps() + _after_approval_steps() + _reply_steps(), analysis=ANALYSIS)
    set_llm_override(llm)

    r = client.post("/api/tasks", json={"goal": GOAL})
    assert r.status_code == 201, r.text
    task_id = r.json()["id"]

    # Paused for approval: nothing is sent yet.
    d = client.get(f"/api/tasks/{task_id}").json()
    assert d["task"]["status"] == "ACTION_REQUIRED"
    assert d["task"]["title"] == "A회사 견적 업무"
    assert d["active_run"]["status"] == "AWAITING_APPROVAL"
    approval = d["approvals"][0]
    assert approval["status"] == "PENDING" and approval["email"]["direction"] == "DRAFT"
    assert approval["email"]["receiver"] == "minjun.kim@a-company.example"
    assert not d["can_simulate_reply"]
    assert [l["tool_name"] for l in d["logs"] if l["action_type"] == "TOOL_CALL"] == [
        "plan_goal", "search_customer", "search_document", "complete_plan_step", "draft_email"]

    # Approve → email sent, agent resumes and decides to wait.
    r = client.post(f"/api/approvals/{approval['id']}/decide", json={"approved": True})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/tasks/{task_id}").json()
    assert d["task"]["status"] == "WAITING"
    assert d["task"]["waiting_for"] == "고객 답변"
    assert any(e["direction"] == "OUTBOUND" for e in d["emails"])
    assert d["can_simulate_reply"]
    # The resumed conversation carried the send_email tool_result.
    resumed = llm.seen_messages[5][-1]["content"]
    assert any('"status": "sent"' in b["content"] for b in resumed)

    # Customer reply event → agent re-runs automatically.
    r = client.post(f"/api/demo/tasks/{task_id}/simulate-reply", json={"preset": "delivery"})
    assert r.status_code == 201, r.text
    d = client.get(f"/api/tasks/{task_id}").json()
    assert d["task"]["status"] == "COMPLETED", d["logs"][-3:]
    assert d["task"]["completed_at"]
    assert all(p["done"] for p in d["task"]["plan"])
    assert [f["title"] for f in d["followups"]] == ["A회사 납기일 단축 가능 여부 확인"]
    assert d["followups"][0]["status"] == "PENDING"
    types = [l["action_type"] for l in d["logs"]]
    for t in ["AGENT_STARTED", "APPROVAL_REQUESTED", "APPROVAL_DECIDED", "EVENT_RECEIVED", "STATUS_CHANGED", "AGENT_FINISHED"]:
        assert t in types
    assert len(d["runs"]) == 2
    assert not llm.steps

    dash = client.get("/api/dashboard").json()
    assert dash["counts"]["COMPLETED"] >= 3


def test_rejected_email_is_not_sent(client):
    register(client)
    steps = _first_run_steps() + [
        lambda m: tool_call(("update_task_status", {"reason": "사용자가 발송 취소", "status": "ACTION_REQUIRED",
                                                     "current_step": "발송 취소됨", "next_action": "메일 내용 수정 필요"})),
    ]
    llm = ScriptedLLM(steps)
    set_llm_override(llm)
    task_id = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    approval = client.get(f"/api/tasks/{task_id}").json()["approvals"][0]
    client.post(f"/api/approvals/{approval['id']}/decide", json={"approved": False, "comment": "금액 재확인 필요"})
    d = client.get(f"/api/tasks/{task_id}").json()
    assert d["task"]["status"] == "ACTION_REQUIRED"
    assert not any(e["direction"] == "OUTBOUND" for e in d["emails"])
    assert '"rejected_by_user"' in llm.seen_messages[5][-1]["content"][-1]["content"]
    # Approval cannot be replayed.
    r = client.post(f"/api/approvals/{approval['id']}/decide", json={"approved": True})
    assert r.status_code == 409


def test_agent_is_reminded_when_it_ends_without_a_status(client):
    register(client)
    llm = ScriptedLLM([
        lambda m: final("고객 정보를 확인했습니다."),
        lambda m: tool_call(("update_task_status", {"reason": "사용자 결정 필요", "status": "ACTION_REQUIRED",
                                                     "current_step": "고객 확인", "next_action": "견적 금액 확인 필요"})),
    ])
    set_llm_override(llm)
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    assert "[시스템 확인]" in llm.seen_messages[1][-1]["content"]
    d = client.get(f"/api/tasks/{tid}").json()
    assert d["task"]["status"] == "ACTION_REQUIRED"
    assert d["runs"][0]["status"] == "FINISHED" and "견적 금액 확인 필요" in d["runs"][0]["final_message"]
