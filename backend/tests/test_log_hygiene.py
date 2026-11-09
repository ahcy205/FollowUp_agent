"""7단계 정리: 메일 내용(제목 · 본문 · 주소)이 로그와 처리 기록에 남지 않는다.

받은 메일 → 동기화 → 판별 → 접수 → 답장 초안 → 보내기를 가짜 Gmail · 가짜 LLM으로 한 번 돌리고,
그동안의 `logging` 기록과 AgentActionLog의 입력 · 결과에 테스트 메일의 고유 표지가 없는지 본다.
"""
import json
import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agent.llm import set_llm_override
from app.config import get_settings
from app.db import SessionLocal
from app.mail.sync import run_sync_cycle
from app.models import AgentActionLog, Email, EmailDirection, GoogleAccount, Task
from tests.conftest import ScriptedLLM
from tests.fake_google import make_raw
from tests.test_mail_pipeline import _add_account, register

SENDER = "leakmark-sender@partner.example"
SUBJECT = "LEAKMARK-SUBJ 견적 요청"
BODY = "LEAKMARK-BODY TS-200 100개 견적 부탁드립니다. 연락처 010-4321-8765"
MARKS = ("leakmark-sender", "LEAKMARK-SUBJ", "LEAKMARK-BODY", "4321-8765", "partner.example")


@pytest.fixture
def setup(client, google_fake, monkeypatch):
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    acc = _add_account(me["id"])
    with SessionLocal() as db:
        my = db.get(GoogleAccount, acc).google_email
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    google_fake.deliver(make_raw(sender=SENDER, to=my, subject=SUBJECT, body=BODY), labels=("INBOX",), internal_date_ms=now_ms)
    return me["id"]


def _analysis(prompt, schema):
    return {"summary": "TS-200 견적 요청", "counterpart": {"name": "김", "organization": "P"},
            "requests": ["견적"], "todos": [{"title": "견적 보내기", "due_text": "", "due_date": "", "due_time": ""}],
            "deadline": {"text": "", "date": "", "time": "", "kind": "none"},
            "priority": "HIGH", "needs_reply": True, "suspicious": []}


def _verdict(prompt, schema):
    return {"results": [{"key": "m1", "label": "work", "reason": "업무"}]}


def _draft(prompt, schema):
    return {"subject": "Re: " + SUBJECT, "body": "안녕하세요.\n" + BODY + "\n감사합니다.", "used_facts": [], "suspicious": []}


def test_whole_flow_keeps_mail_content_out_of_logs(client, setup, caplog):
    caplog.set_level(logging.DEBUG)
    set_llm_override(ScriptedLLM(structured_steps=[_verdict, _analysis, _draft]))

    assert run_sync_cycle() == 1
    with SessionLocal() as db:
        task = db.scalars(select(Task).where(Task.user_id == setup, Task.origin == "mail")).one()
    r = client.post(f"/api/tasks/{task.id}/reply-draft", json={"purpose": "reply"})
    assert r.status_code == 200, r.text
    draft = r.json()["mail"]["draft"]
    r = client.post(f"/api/tasks/{task.id}/reply-draft/send",
                    json={"expected_updated_at": draft["updated_at"], "unsupported_ack": True})
    assert r.status_code == 200, r.text

    for mark in MARKS:
        assert mark not in caplog.text, f"logging에 메일 내용이 남았다: {mark}"
    with SessionLocal() as db:
        logs = db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == task.id)).all()
        assert logs, "처리 기록이 남아야 한다"
        for row in logs:
            payload = json.dumps([row.input_data, row.output_data], ensure_ascii=False)
            for mark in MARKS:
                assert mark not in payload, f"{row.action_type} 처리 기록에 메일 내용이 남았다: {mark}"
                assert mark not in (row.reason or ""), f"{row.action_type} 처리 이유에 메일 내용이 남았다: {mark}"


def test_database_error_message_hides_saved_values(client):
    """DB 오류 메시지에 저장하려던 값(본문 · 주소)이 붙지 않는다. 처리 못 한 오류 로그로 새지 않게."""
    me = register(client)
    with SessionLocal() as db:
        for _ in range(2):
            db.add(Email(user_id=me["id"], sender=SENDER, receiver="me@gmail.com", subject=SUBJECT, content=BODY,
                         direction=EmailDirection.INBOUND, gmail_message_id="dup-1", gmail_thread_id="t-1", cc=[], gmail_meta={}))
        with pytest.raises(IntegrityError) as err:
            db.commit()
    text = str(err.value)
    for mark in MARKS:
        assert mark not in text
