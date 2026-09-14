"""Thin wrapper around the Anthropic Messages API, plus a protocol so tests can inject a scripted LLM."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """User-presentable LLM failure (message is Korean and includes a recovery hint)."""


@dataclass
class LLMResponse:
    stop_reason: str
    content: list[dict[str, Any]]
    request_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class LLMClient(Protocol):
    def agent_turn(self, system: str, tools: list[dict], messages: list[dict]) -> LLMResponse: ...

    def structured(self, system: str, prompt: str, schema: dict) -> dict: ...


def _sanitize_content(blocks: list[dict]) -> list[dict]:
    """Prepare assistant content for replay.

    After a server-side fallback, model-internal blocks (thinking, tool_use) that precede the final
    `fallback` marker must not be echoed back; the marker itself is dropped as an audit block.
    """
    last_fb = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    out = []
    for i, b in enumerate(blocks):
        t = b.get("type")
        if t == "fallback":
            continue
        if i < last_fb and t not in ("text",):
            continue
        out.append(b)
    return out


def _strip_provider_fields(messages: list[dict]) -> list[dict]:
    """Drop fields another provider stored on blocks (e.g. gemini_thought_signature)."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = [{k: v for k, v in b.items() if not k.startswith("gemini_")} for b in content]
        out.append({**m, "content": content})
    return out


class AnthropicLLM:
    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY가 설정되지 않았습니다. 서버 환경변수를 설정한 뒤 'Agent 재시도'를 눌러주세요.")
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
            raise LLMError("LLM API 사용량 한도에 도달했습니다. 1분 뒤 'Agent 재시도'를 눌러주세요.")
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower():
                raise LLMError("Anthropic 계정의 API 크레딧이 부족합니다. console.anthropic.com → Plans & Billing에서 "
                               "크레딧을 충전한 뒤 'Agent 재시도'를 눌러주세요.")
            log.exception("LLM bad request")
            raise LLMError(f"LLM 요청 오류: {e.message[:300]}")
        except anthropic.APIStatusError as e:
            raise LLMError(f"LLM 서버 오류({e.status_code})입니다. 잠시 후 'Agent 재시도'를 눌러주세요.")
        except anthropic.APITimeoutError:
            raise LLMError("LLM 응답 시간이 초과되었습니다. 'Agent 재시도'를 눌러주세요.")
        except anthropic.APIConnectionError:
            raise LLMError("LLM 서버에 연결할 수 없습니다. 네트워크 상태 확인 후 'Agent 재시도'를 눌러주세요.")

    def agent_turn(self, system: str, tools: list[dict], messages: list[dict]) -> LLMResponse:
        resp = self._create(
            model=self.s.llm_model,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=_strip_provider_fields(messages),
            output_config={"effort": self.s.llm_effort},
            cache_control={"type": "ephemeral"},
        )
        if resp.stop_reason == "refusal":
            raise LLMError("LLM이 안전 정책에 따라 요청 처리를 거절했습니다. 업무 목표 문구를 확인해주세요.")
        content = _sanitize_content([b.to_dict(exclude_none=True) for b in resp.content])
        usage = resp.usage.to_dict() if resp.usage else {}
        return LLMResponse(stop_reason=resp.stop_reason or "", content=content,
                           request_id=getattr(resp, "_request_id", "") or "", usage=usage)

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
