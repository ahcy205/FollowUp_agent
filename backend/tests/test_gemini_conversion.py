"""Gemini history conversion — offline. Signatures and call/response pairing must survive a round trip."""

from google.genai import types

from app.agent.gemini_llm import from_gemini_parts, to_gemini_contents
from app.agent.llm import _strip_provider_fields


def test_function_calls_round_trip_with_thought_signature():
    parts = [
        types.Part(text="고객과 문서를 조회하겠습니다."),
        types.Part(function_call=types.FunctionCall(name="search_customer", args={"customer_name": "A회사", "reason": "조회"}),
                   thought_signature=b"\x01sig-bytes"),
        types.Part(function_call=types.FunctionCall(id="call-2", name="search_document", args={"keyword": "견적서", "reason": "조회"})),
    ]
    blocks = from_gemini_parts(parts)
    assert [b["type"] for b in blocks] == ["text", "tool_use", "tool_use"]
    assert blocks[1]["input"]["customer_name"] == "A회사"
    assert blocks[1]["gemini_thought_signature"]
    assert blocks[2]["gemini_call_id"] == "call-2"

    history = [
        {"role": "user", "content": "<user_goal>A회사 견적</user_goal>"},
        {"role": "assistant", "content": blocks},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": blocks[1]["id"], "content": '{"count": 1}'},
            {"type": "tool_result", "tool_use_id": blocks[2]["id"], "content": "오류: 없음", "is_error": True},
        ]},
    ]
    contents = to_gemini_contents(history)
    assert [c.role for c in contents] == ["user", "model", "user"]
    model_parts = contents[1].parts
    assert model_parts[1].function_call.name == "search_customer"
    assert model_parts[1].thought_signature == b"\x01sig-bytes"
    responses = [p.function_response for p in contents[2].parts]
    assert responses[0].name == "search_customer" and responses[0].response == {"count": 1}
    assert responses[1].name == "search_document" and responses[1].id == "call-2"
    assert responses[1].response == {"error": "오류: 없음"}


def test_signature_only_text_part_is_kept_and_thoughts_skipped():
    blocks = from_gemini_parts([types.Part(text="생각", thought=True), types.Part(text="", thought_signature=b"s")])
    assert blocks == [{"type": "text", "text": "", "gemini_thought_signature": "cw=="}]
    assert to_gemini_contents([{"role": "assistant", "content": blocks}])[0].parts[0].thought_signature == b"s"


def test_anthropic_path_strips_gemini_fields():
    msgs = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "x", "input": {},
                                               "gemini_thought_signature": "cw==", "gemini_call_id": "c"}]}]
    assert _strip_provider_fields(msgs)[0]["content"][0] == {"type": "tool_use", "id": "t", "name": "x", "input": {}}


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
