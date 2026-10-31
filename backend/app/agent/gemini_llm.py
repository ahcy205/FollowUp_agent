"""Google Gemini provider: structured JSON calls with client-side pacing and 429 retry handling."""

import json
import logging
import re
import threading
import time
from collections import deque

import httpx
from google import genai
from google.genai import errors, types

from app.agent.llm import LLMError
from app.config import Settings

log = logging.getLogger(__name__)

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
    waiting a minute cannot help.
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


class GeminiLLM:
    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY가 설정되지 않았습니다. 서버 환경변수에 Gemini API 키를 설정한 뒤 다시 시도해 주세요.")
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
                    raise LLMError(f"Gemini 무료 등급 일일 한도(모델 {model})를 모두 사용했습니다. Google AI Studio에서 결제를 활성화하거나 LLM_MODEL을 다른 모델로 바꾼 뒤 다시 시도해 주세요.")
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    break
                log.warning("Gemini 429, waiting %.0fs (attempt %d)", delay, attempt + 1)
                self._wait(min(delay + 1, MAX_RATE_LIMIT_WAIT), "Gemini 분당 사용 한도 초과 — 자동 재시도 대기")
        raise LLMError("Gemini API 사용 한도(분당 요청 수)를 계속 초과하고 있습니다. 1분 뒤 다시 시도해 주세요.", retryable=True)

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
            raise LLMError(f"Gemini 서버 오류({e.code})입니다. 잠시 후 다시 시도해 주세요.", retryable=True)
        except httpx.TimeoutException:
            raise LLMError("Gemini 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
        except httpx.HTTPError:
            raise LLMError("Gemini 서버에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 잠시 후 다시 시도해 주세요.", retryable=True)

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
