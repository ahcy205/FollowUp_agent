"""Google Gemini provider.

The orchestrator persists conversations in one provider-neutral shape (text / tool_use / tool_result
blocks). This module converts that history to Gemini `Content`s on every call and converts the reply
back, keeping Gemini's `thought_signature` on the blocks so multi-turn function calling stays valid.
"""

import base64
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from typing import Callable

import httpx
from google import genai
from google.genai import errors, types

from app.agent.llm import LLMError, LLMResponse
from app.config import Settings

log = logging.getLogger(__name__)

REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
RETRYABLE_REASONS = {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}
MAX_FORMAT_RETRIES = 2
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_WAIT = 65.0


class RequestPacer:
    """Process-wide sliding-window pacing so free-tier requests-per-minute quotas are not exceeded."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sent: dict[str, deque[float]] = {}

    def reserve(self, key: str, rpm: int) -> float:
        """Reserve a slot; returns how many seconds the caller must sleep before sending."""
        if rpm <= 0:
            return 0.0
        with self._lock:
            now = time.monotonic()
            sent = self._sent.setdefault(key, deque())
            while sent and now - sent[0] >= 60:
                sent.popleft()
            start = now if len(sent) < rpm else sent[-rpm] + 60.5
            start = max(start, sent[-1] + 0.2 if sent else now)
            sent.append(start)
            return max(0.0, start - now)


pacer = RequestPacer()


def _retry_delay(e: errors.APIError) -> float | None:
    """Seconds to wait before retrying a 429, or None when a daily quota is exhausted.

    Google attaches a short RetryInfo even to per-day violations, so any PerDay violation wins:
    waiting a minute cannot help and would only burn the agent's time budget.
    """
    details = e.details if isinstance(e.details, dict) else {}
    items = [i for i in (details.get("error") or {}).get("details") or [] if isinstance(i, dict)]
    quota_ids = [str(v.get("quotaId", "")) for i in items for v in i.get("violations", []) if isinstance(v, dict)]
    if any("PerDay" in q for q in quota_ids):
        return None
    for item in items:
        if str(item.get("@type", "")).endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(item.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return 30.0


def _sig_out(sig: bytes | None) -> dict:
    return {"gemini_thought_signature": base64.b64encode(sig).decode("ascii")} if sig else {}


def _sig_in(block: dict) -> bytes | None:
    raw = block.get("gemini_thought_signature")
    return base64.b64decode(raw) if raw else None


def _tool_response_payload(block: dict) -> dict:
    content = block.get("content", "")
    if isinstance(content, list):  # [{"type": "text", "text": ...}]
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    if block.get("is_error"):
        return {"error": str(content)}
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {"result": str(content)}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def to_gemini_contents(messages: list[dict]) -> list[types.Content]:
    calls: dict[str, tuple[str, str | None]] = {}  # our tool_use id -> (function name, gemini call id)
    contents: list[types.Content] = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
        parts: list[types.Part] = []
        for b in blocks:
            kind = b.get("type")
            if kind == "text":
                sig = _sig_in(b)
                if b.get("text") or sig:
                    parts.append(types.Part(text=b.get("text", ""), thought_signature=sig))
            elif kind == "tool_use":
                calls[b["id"]] = (b["name"], b.get("gemini_call_id"))
                parts.append(types.Part(
                    function_call=types.FunctionCall(id=b.get("gemini_call_id"), name=b["name"], args=b.get("input") or {}),
                    thought_signature=_sig_in(b),
                ))
            elif kind == "tool_result":
                name, call_id = calls.get(b["tool_use_id"], ("unknown_tool", None))
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=call_id, name=name, response=_tool_response_payload(b))))
            # other block kinds (e.g. Anthropic thinking) have no Gemini equivalent and are skipped
        if parts:
            contents.append(types.Content(role=role, parts=parts))
    return contents


def from_gemini_parts(parts: list[types.Part]) -> list[dict]:
    blocks: list[dict] = []
    for p in parts:
        if p.thought:
            continue
        if p.function_call:
            fc = p.function_call
            blocks.append({
                "type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:16]}", "name": fc.name or "",
                "input": dict(fc.args or {}), **({"gemini_call_id": fc.id} if fc.id else {}), **_sig_out(p.thought_signature),
            })
        elif p.text is not None or p.thought_signature:
            blocks.append({"type": "text", "text": p.text or "", **_sig_out(p.thought_signature)})
    return blocks


class GeminiLLM:
    on_wait: Callable[[float, str], None] | None = None  # the orchestrator shows waits in the timeline

    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY가 설정되지 않았습니다. 서버 환경변수에 Gemini API 키를 설정한 뒤 'Agent 재시도'를 눌러주세요.")
        self.s = settings
        self.client = genai.Client(
            api_key=settings.llm_api_key,
            http_options=types.HttpOptions(
                timeout=int(settings.llm_request_timeout * 1000),
                # 429 is handled below using the server's retryDelay instead of short SDK backoff.
                retry_options=types.HttpRetryOptions(attempts=3, http_status_codes=[408, 500, 502, 503, 504]),
            ),
        )

    def _wait(self, seconds: float, reason: str) -> None:
        if seconds <= 0:
            return
        if seconds >= 2 and self.on_wait:
            self.on_wait(seconds, reason)
        time.sleep(seconds)

    def _generate(self, contents, config: types.GenerateContentConfig, model: str | None = None) -> types.GenerateContentResponse:
        model = model or self.s.llm_model
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            self._wait(pacer.reserve(model, self.s.llm_rpm_limit), "요청 속도 조절(분당 한도)")
            try:
                return self._generate_once(model, contents, config)
            except errors.ClientError as e:
                if e.code != 429:
                    raise
                log.warning("Gemini 429 details: %s", json.dumps(e.details, ensure_ascii=False)[:1500])
                delay = _retry_delay(e)
                if delay is None:
                    raise LLMError(f"Gemini 무료 등급 일일 한도(모델 {model})를 모두 사용했습니다. Google AI Studio에서 결제를 활성화하거나 LLM_MODEL을 다른 모델로 바꾼 뒤 'Agent 재시도'를 눌러주세요.")
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    break
                log.warning("Gemini 429, waiting %.0fs (attempt %d)", delay, attempt + 1)
                self._wait(min(delay + 1, MAX_RATE_LIMIT_WAIT), "Gemini 분당 사용 한도 초과 — 자동 재시도 대기")
        raise LLMError("Gemini API 사용 한도(분당 요청 수)를 계속 초과하고 있습니다. 1분 뒤 'Agent 재시도'를 눌러주세요.", retryable=True)

    def _generate_once(self, model: str, contents, config: types.GenerateContentConfig) -> types.GenerateContentResponse:
        try:
            return self.client.models.generate_content(model=model, contents=contents, config=config)
        except errors.ClientError as e:
            if e.code == 429:
                raise
            msg = (e.message or "").lower()
            if e.code == 400 and ("api key" in msg or "api_key" in msg):
                raise LLMError("Gemini API 키가 올바르지 않습니다. LLM_API_KEY 값을 확인하세요.")
            if e.code in (401, 403):
                raise LLMError("Gemini API 키에 권한이 없습니다. Google AI Studio에서 키와 프로젝트 설정을 확인하세요.")
            if e.code == 404:
                raise LLMError(f"Gemini 모델 '{model}'을 찾을 수 없습니다. LLM_MODEL / LLM_ANALYSIS_MODEL 설정을 확인하세요.")
            log.exception("Gemini client error")
            raise LLMError(f"Gemini 요청 오류({e.code}): {(e.message or '')[:300]}")
        except errors.ServerError as e:
            raise LLMError(f"Gemini 서버 오류({e.code})입니다. 잠시 후 'Agent 재시도'를 눌러주세요.", retryable=True)
        except httpx.TimeoutException:
            raise LLMError("Gemini 응답 시간이 초과되었습니다. 'Agent 재시도'를 눌러주세요.", retryable=True)
        except httpx.HTTPError:
            raise LLMError("Gemini 서버에 연결할 수 없습니다. 네트워크 상태 확인 후 'Agent 재시도'를 눌러주세요.", retryable=True)

    def agent_turn(self, system: str, tools: list[dict], messages: list[dict]) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=t["input_schema"])
                for t in tools
            ])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=16000,
        )
        contents = to_gemini_contents(messages)
        for attempt in range(MAX_FORMAT_RETRIES + 1):
            resp = self._generate(contents, config)
            if not resp.candidates:
                reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
                raise LLMError(f"Gemini가 요청을 차단했습니다({reason}). 업무 목표 문구를 확인해주세요.")
            cand = resp.candidates[0]
            finish = cand.finish_reason.name if cand.finish_reason else "STOP"
            if finish in REFUSAL_REASONS:
                raise LLMError(f"Gemini가 안전 정책({finish})에 따라 응답을 거절했습니다.")
            blocks = from_gemini_parts(cand.content.parts if cand.content and cand.content.parts else [])
            has_tool = any(b["type"] == "tool_use" for b in blocks)
            has_text = any(b["type"] == "text" and b["text"].strip() for b in blocks)
            if finish in RETRYABLE_REASONS or not (has_tool or has_text):
                log.warning("Gemini returned %s / empty output (attempt %d)", finish, attempt + 1)
                continue
            if has_tool:
                stop = "tool_use"
            elif finish == "MAX_TOKENS":
                stop = "max_tokens"
            else:
                stop = "end_turn"
            usage = resp.usage_metadata.model_dump(exclude_none=True) if resp.usage_metadata else {}
            return LLMResponse(stop_reason=stop, content=blocks, request_id=resp.response_id or "", usage=usage)
        raise LLMError("Gemini가 올바른 도구 호출을 만들지 못했습니다. 'Agent 재시도'를 눌러주세요.", retryable=True)

    def structured(self, system: str, prompt: str, schema: dict) -> dict:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=8000,
        )
        resp = self._generate(prompt, config, model=self.s.llm_analysis_model)
        text = resp.text or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise LLMError("Gemini 분석 결과를 해석할 수 없습니다.")
        if not isinstance(data, dict):
            raise LLMError("Gemini 분석 결과 형식이 올바르지 않습니다.")
        return data
