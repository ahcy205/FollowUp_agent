"""LLM provider errors are classified as retryable (outage / limit) or permanent (configuration)."""

import httpx
import pytest

from app.agent.llm import LLMError
from app.config import get_settings


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
