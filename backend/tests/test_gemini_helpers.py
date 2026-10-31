"""Gemini provider helpers — offline: request pacing and 429 retry-delay parsing."""

def test_pacer_spaces_requests_to_rpm_limit():
    from app.agent.gemini_llm import RequestPacer

    pacer = RequestPacer()
    waits = [pacer.reserve("m", 5) for _ in range(6)]
    assert all(w < 2 for w in waits[:5])
    assert 59 < waits[5] <= 61
    assert pacer.reserve("other-model", 5) < 1
    assert RequestPacer().reserve("m", 0) == 0


def test_retry_delay_parsing():
    from google.genai import errors

    from app.agent.gemini_llm import _retry_delay

    per_minute = {"error": {"code": 429, "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "49s"}]}}
    per_day = {"error": {"code": 429, "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
    assert _retry_delay(errors.ClientError(429, per_minute)) == 49.0
    assert _retry_delay(errors.ClientError(429, per_day)) is None


def test_daily_violation_wins_over_retry_info():
    from google.genai import errors

    from app.agent.gemini_llm import _retry_delay

    mixed = {"error": {"code": 429, "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"},
            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}]}}
    assert _retry_delay(errors.ClientError(429, mixed)) is None
