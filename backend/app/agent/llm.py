"""Thin wrapper around the Anthropic Messages API (structured JSON calls), plus a protocol so tests can inject a scripted LLM."""

import json
import logging
from typing import Protocol

import anthropic

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """User-presentable LLM failure (message is Korean and includes a recovery hint).

    `retryable` marks failures that may clear by waiting (server errors, timeouts, per-minute limits).
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    def structured(self, system: str, prompt: str, schema: dict) -> dict: ...


class AnthropicLLM:
    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY가 설정되지 않았습니다. 서버 환경변수를 설정한 뒤 다시 시도해 주세요.")
        self.s = settings
        self.client = anthropic.Anthropic(
            api_key=settings.llm_api_key, timeout=settings.llm_request_timeout, max_retries=2
        )

    def _create(self, **kwargs):
        try:
            if self.s.llm_refusal_fallback:
                return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            return self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise LLMError("LLM API 키 인증에 실패했습니다. LLM_API_KEY 값을 확인하세요.")
        except anthropic.PermissionDeniedError:
            raise LLMError("LLM API 키에 모델 사용 권한이 없습니다. LLM_MODEL 설정을 확인하세요.")
        except anthropic.NotFoundError:
            raise LLMError(f"LLM 모델 '{self.s.llm_model}'을 찾을 수 없습니다. LLM_MODEL 설정을 확인하세요.")
        except anthropic.RateLimitError:
            raise LLMError("LLM API 사용량 한도에 도달했습니다. 1분 뒤 다시 시도해 주세요.", retryable=True)
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower():
                raise LLMError("Anthropic 계정의 API 크레딧이 부족합니다. console.anthropic.com → Plans & Billing에서 "
                               "크레딧을 충전한 뒤 다시 시도해 주세요.")
            log.exception("LLM bad request")
            raise LLMError(f"LLM 요청 오류: {e.message[:300]}")
        except anthropic.APIStatusError as e:
            raise LLMError(f"LLM 서버 오류({e.status_code})입니다. 잠시 후 다시 시도해 주세요.",
                           retryable=e.status_code >= 500)
        except anthropic.APITimeoutError:
            raise LLMError("LLM 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
        except anthropic.APIConnectionError:
            raise LLMError("LLM 서버에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 잠시 후 다시 시도해 주세요.", retryable=True)

    def structured(self, system: str, prompt: str, schema: dict) -> dict:
        resp = self._create(
            model=self.s.llm_analysis_model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "refusal":
            raise LLMError("LLM이 분석 요청을 거절했습니다.")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise LLMError("LLM 분석 결과를 해석할 수 없습니다.")


_override: LLMClient | None = None


def set_llm_override(llm: LLMClient | None) -> None:
    """Tests inject a scripted client here."""
    global _override
    _override = llm


def get_llm() -> LLMClient:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.llm_provider == "gemini":
        from app.agent.gemini_llm import GeminiLLM

        return GeminiLLM(settings)
    return AnthropicLLM(settings)
