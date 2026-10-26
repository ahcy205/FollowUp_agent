"""데모 모드 분리: DEMO_MODE가 꺼져 있으면 데모 흔적이 없고, 켜져 있어도 Reset이 메일 업무를 지키지 않는다면 안 된다."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.agent.audit import log_action
from app.config import get_settings
from app.db import SessionLocal
from app.main import _ensure_demo_user
from app.models import Customer, Document, Email, EmailDirection, Task, TaskStatus, User
from tests.conftest import register


def _off(monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_mode", False)


def _count(db, model, uid):
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == uid))


def test_off_register_creates_empty_account(client, monkeypatch):
    _off(monkeypatch)
    me = register(client)
    assert me["demo_mode"] is False
    assert client.get("/api/auth/me").json()["demo_mode"] is False
    with SessionLocal() as db:
        assert _count(db, Task, me["id"]) == 0
        assert _count(db, Customer, me["id"]) == 0
        assert _count(db, Document, me["id"]) == 0
        assert _count(db, Email, me["id"]) == 0


def test_on_register_reports_demo_mode(client):
    me = register(client)
    assert me["demo_mode"] is True
    with SessionLocal() as db:
        assert _count(db, Task, me["id"]) > 0


def test_off_demo_endpoints_404(client, monkeypatch):
    me = register(client)  # 켜진 상태로 가입해 업무를 만든 뒤 끈다
    with SessionLocal() as db:
        tid = db.scalar(select(Task.id).where(Task.user_id == me["id"]).limit(1))
    _off(monkeypatch)
    assert client.post("/api/demo/reset").status_code == 404
    r = client.post(f"/api/demo/tasks/{tid}/simulate-reply", json={"preset": "accept"})
    assert r.status_code == 404


def test_off_no_simulate_flag(client, monkeypatch):
    me = register(client)
    with SessionLocal() as db:
        t = db.scalar(select(Task).where(Task.user_id == me["id"], Task.agent_enabled.is_(True),
                                         Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.CANCELLED])).limit(1))
        db.add(Email(user_id=me["id"], task_id=t.id, sender=me["email"], receiver="x@partner.co.kr", subject="s",
                     content="c", direction=EmailDirection.OUTBOUND, sent_at=datetime.now(UTC)))
        db.commit()
        tid = t.id
    assert client.get(f"/api/tasks/{tid}").json()["can_simulate_reply"] is True
    _off(monkeypatch)
    assert client.get(f"/api/tasks/{tid}").json()["can_simulate_reply"] is False


def test_off_no_demo_user_on_startup(monkeypatch):
    _off(monkeypatch)
    monkeypatch.setattr(get_settings(), "demo_user_password", "demo-pass-123")
    _ensure_demo_user()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_on_reset_keeps_mail_tasks(client):
    me = register(client)
    uid = me["id"]
    with SessionLocal() as db:
        cust = db.scalar(select(Customer).where(Customer.user_id == uid).limit(1))
        doc = db.scalar(select(Document).where(Document.user_id == uid).limit(1))
        mail_task = Task(user_id=uid, title="견적 요청", goal="견적서 송부", status=TaskStatus.ACTION_REQUIRED,
                         origin="mail", agent_enabled=False, gmail_thread_id="thr-1", customer_id=cust.id,
                         deadline_at=datetime.now(UTC) + timedelta(days=3),
                         plan=[{"id": 1, "title": "견적서 보내기", "done": False, "evidence": "", "due_at": None,
                                "source": "user"}],
                         brief={"analysis": "done", "summary": "견적 요청"})
        db.add(mail_task)
        db.flush()
        gmail = Email(user_id=uid, task_id=mail_task.id, customer_id=cust.id, document_id=doc.id,
                      sender="kim@partner.co.kr", receiver=me["email"], subject="s", content="c",
                      direction=EmailDirection.INBOUND, gmail_message_id="g-1", gmail_thread_id="thr-1")
        db.add(gmail)
        demo_task = db.scalar(select(Task).where(Task.user_id == uid, Task.origin != "mail").limit(1))
        demo_task.parent_task_id = mail_task.id
        mail_task.parent_task_id = demo_task.id  # 메일 업무가 지워질 데모 업무를 가리켜도 정리돼야 한다
        log_action(db, user_id=uid, task_id=mail_task.id, run_id=None, action_type="DEADLINE_CHANGED",
                   reason="사용자가 기한을 바꿨습니다")
        db.commit()
        mail_id, gmail_id = mail_task.id, gmail.id
        demo_emails_before = db.scalar(select(func.count()).select_from(Email).where(
            Email.user_id == uid, Email.gmail_message_id.is_(None)))

    assert client.post("/api/demo/reset").status_code == 204

    with SessionLocal() as db:
        kept = db.get(Task, mail_id)
        assert kept is not None and kept.plan[0]["title"] == "견적서 보내기" and kept.deadline_at is not None
        assert kept.customer_id is None and kept.parent_task_id is None
        g = db.get(Email, gmail_id)
        assert g is not None and g.task_id == mail_id and g.customer_id is None and g.document_id is None
        from app.models import AgentActionLog
        assert db.scalar(select(func.count()).select_from(AgentActionLog).where(
            AgentActionLog.task_id == mail_id)) == 1
        assert db.scalar(select(func.count()).select_from(Task).where(
            Task.user_id == uid, Task.origin != "mail")) > 0  # 데모 업무는 다시 생긴다
        assert db.scalar(select(func.count()).select_from(Email).where(
            Email.user_id == uid, Email.gmail_message_id.is_(None))) == demo_emails_before
