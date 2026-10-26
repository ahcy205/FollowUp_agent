"""목표 입력 Agent 도구는 가상 메일만 읽는다. 실제 Gmail 메일은 메일 판별 스위치 밖에서 LLM으로 가면 안 된다."""
import pytest

from app.agent.tools import AnalyzeEmailInput, CheckEmailInput, ToolContext, ToolError, analyze_email, check_email
from app.db import SessionLocal
from app.models import Email, EmailDirection, Task, TaskStatus, User


def _setup(db):
    u = User(email="u@example.com", password_hash="x")
    db.add(u)
    db.flush()
    t = Task(user_id=u.id, title="목표", goal="A회사 견적 후속", status=TaskStatus.IN_PROGRESS, origin="user")
    db.add(t)
    db.flush()
    gmail = Email(user_id=u.id, sender="kim@partner.co.kr", receiver=u.email, subject="s", content="실제 메일",
                  direction=EmailDirection.INBOUND, gmail_message_id="g-1", triage="not_work")
    linked = Email(user_id=u.id, task_id=t.id, sender="kim@partner.co.kr", receiver=u.email, subject="s",
                   content="실제 메일", direction=EmailDirection.INBOUND, gmail_message_id="g-2")
    db.add_all([gmail, linked])
    db.commit()
    return ToolContext(db=db, user=u, task=t, run=None, llm=None), gmail.id, linked.id


def test_analyze_email_refuses_gmail_mail():
    with SessionLocal() as db:
        ctx, gmail_id, linked_id = _setup(db)
        for eid in (gmail_id, linked_id):
            with pytest.raises(ToolError):
                analyze_email(ctx, AnalyzeEmailInput(email_id=eid, reason="확인"))


def test_check_email_skips_gmail_mail():
    with SessionLocal() as db:
        ctx, _, _ = _setup(db)
        out = check_email(ctx, CheckEmailInput(include_read=True, reason="확인"))
        assert all("실제 메일" not in str(item) for item in out.get("emails", out.get("items", [])))
        assert "실제 메일" not in str(out)
