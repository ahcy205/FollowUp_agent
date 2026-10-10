"""Token usage: provider dicts normalize to one shape, and runs accumulate it turn by turn."""

from app.agent.llm import normalize_usage, set_llm_override
from tests.conftest import ScriptedLLM, register, tool_call

GOAL = "A회사에 견적서를 보내고 고객 답변이 오면 내용을 확인해서 내가 해야 할 일을 정리해줘."


def test_anthropic_usage_normalizes():
    raw = {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100,
           "server_tool_use": None}
    assert normalize_usage(raw) == {"input": 2100, "output": 300, "cache_read": 800, "total": 2400}


def test_gemini_usage_normalizes():
    raw = {"prompt_token_count": 1500, "candidates_token_count": 200, "thoughts_token_count": 400, "cached_content_token_count": 900,
           "total_token_count": 2100}
    assert normalize_usage(raw) == {"input": 1500, "output": 600, "cache_read": 900, "total": 2100}


def test_empty_usage_is_zero():
    assert normalize_usage({}) == {"input": 0, "output": 0, "cache_read": 0, "total": 0}
    assert normalize_usage(None) == {"input": 0, "output": 0, "cache_read": 0, "total": 0}


def _with_usage(resp, **usage):
    resp.usage = usage
    return resp


def test_run_accumulates_usage_per_turn(client):
    register(client)
    llm = ScriptedLLM([
        lambda m: _with_usage(tool_call(("search_customer", {"reason": "조회", "customer_name": "A회사"})),
                              input_tokens=1000, output_tokens=50),
        lambda m: _with_usage(tool_call(("update_task_status", {"reason": "확인", "status": "ACTION_REQUIRED", "current_step": "조회 완료"})),
                              input_tokens=1200, output_tokens=80, cache_read_input_tokens=900),
    ])
    set_llm_override(llm)
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    run = client.get(f"/api/tasks/{tid}").json()["runs"][0]
    assert run["usage"]["input"] == 1000 + 1200 + 900
    assert run["usage"]["output"] == 130
    assert run["usage"]["cache_read"] == 900
    assert run["usage"]["total"] == 1000 + 50 + 1200 + 900 + 80
    assert run["usage"]["turns"] == 2
