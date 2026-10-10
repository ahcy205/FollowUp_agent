"""Transient LLM failures are retried inside the run before the user is asked to intervene."""

import httpx
import pytest

from app.agent import orchestrator
from app.agent.llm import LLMError, set_llm_override
from app.config import get_settings
from tests.conftest import ScriptedLLM, register, tool_call

GOAL = "A회사에 견적서를 보내고 고객 답변이 오면 내용을 확인해서 내가 해야 할 일을 정리해줘."
SETTLE = ("update_task_status", {"reason": "확인", "status": "ACTION_REQUIRED", "current_step": "조회 완료"})


def _raise(message: str, retryable: bool):
    def step(_):
        raise LLMError(message, retryable=retryable)
    return step


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(orchestrator, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))


def test_transient_error_is_retried_and_run_finishes(client):
    register(client)
    llm = ScriptedLLM([
        lambda m: tool_call(("search_customer", {"reason": "조회", "customer_name": "A회사"})),
        _raise("LLM 서버 오류(503)입니다.", retryable=True),
        lambda m: tool_call(SETTLE),
    ])
    set_llm_override(llm)
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    d = client.get(f"/api/tasks/{tid}").json()
    assert d["runs"][0]["status"] == "FINISHED"
    assert d["runs"][0]["steps"] == 2  # the failed attempt did not consume a step
    retry = next(l for l in d["logs"] if l["action_type"] == "LLM_RETRY")
    assert "503" in retry["reason"] and "자동 재시도" in retry["reason"]
    assert d["task"]["status"] == "ACTION_REQUIRED"


def test_permanent_error_fails_immediately(client):
    register(client)
    set_llm_override(ScriptedLLM([_raise("LLM API 키 인증에 실패했습니다.", retryable=False), lambda m: tool_call(SETTLE)]))
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    d = client.get(f"/api/tasks/{tid}").json()
    assert d["runs"][0]["status"] == "FAILED"
    assert "인증" in d["runs"][0]["error"]
    assert "LLM_RETRY" not in [l["action_type"] for l in d["logs"]]


def test_retries_are_bounded(client, monkeypatch):
    register(client)
    monkeypatch.setattr(get_settings(), "llm_transient_retries", 2)
    llm = ScriptedLLM([_raise("LLM 응답 시간이 초과되었습니다.", retryable=True) for _ in range(3)] + [lambda m: tool_call(SETTLE)])
    set_llm_override(llm)
    tid = client.post("/api/tasks", json={"goal": GOAL}).json()["id"]
    d = client.get(f"/api/tasks/{tid}").json()
    assert d["runs"][0]["status"] == "FAILED"
    assert "2회 자동 재시도" in d["runs"][0]["error"]
    assert len([l for l in d["logs"] if l["action_type"] == "LLM_RETRY"]) == 2
    assert len(llm.steps) == 1  # the settle step was never reached


# ---------------------------------------------------------------- provider classification

def _anthropic_llm(raise_exc):
    import anthropic

    from app.agent.llm import AnthropicLLM

    s = get_settings().model_copy(update={"llm_api_key": "test-key", "llm_refusal_fallback": False})
    llm = AnthropicLLM(s)

    def create(**kwargs):
        raise raise_exc
    llm.client.messages.create = create
    return llm, anthropic


def _http(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://api.example/v1/messages"))


def test_anthropic_errors_are_classified():
    import anthropic

    cases = [
        (anthropic.InternalServerError("boom", response=_http(500), body=None), True),
        (anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.example")), True),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example")), True),
        (anthropic.RateLimitError("slow down", response=_http(429), body=None), True),
        (anthropic.AuthenticationError("bad key", response=_http(401), body=None), False),
        (anthropic.BadRequestError("bad", response=_http(400), body=None), False),
    ]
    for exc, expected in cases:
        llm, _ = _anthropic_llm(exc)
        with pytest.raises(LLMError) as info:
            llm._create(model="m", max_tokens=1, messages=[])
        assert info.value.retryable is expected, type(exc).__name__


def test_gemini_errors_are_classified():
    from google.genai import errors

    from app.agent.gemini_llm import GeminiLLM

    s = get_settings().model_copy(update={"llm_api_key": "test-key", "llm_rpm_limit": 0})
    llm = GeminiLLM(s)
    cases = [
        (errors.ServerError(503, {"error": {"message": "unavailable"}}), True),
        (httpx.ReadTimeout("slow"), True),
        (httpx.ConnectError("down"), True),
        (errors.ClientError(403, {"error": {"message": "forbidden"}}), False),
        (errors.ClientError(400, {"error": {"message": "API key not valid"}}), False),
    ]
    for exc, expected in cases:
        def generate(**kwargs):
            raise exc
        llm.client.models.generate_content = generate
        with pytest.raises(LLMError) as info:
            llm._generate_once("m", "hi", None)
        assert info.value.retryable is expected, type(exc).__name__
