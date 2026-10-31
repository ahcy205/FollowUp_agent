"""근거 없는 값 확인 (2026-10-31 사용자 결정): 초안에 '근거를 찾지 못한 값'이 있으면
'확인했어요'(unsupported_ack)를 받아야 보낸다. 6단계 설계 2.4의 "경고만, 막지는 않는다"를 바꾼다."""
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import AgentActionLog, ReplyDraft
from tests.conftest import register
from tests.test_reply_send import _setup

NEED_UNSUPPORTED_ACK = "근거를 찾지 못한 값이 있습니다. 값이 맞는지 확인하고 '확인했어요'를 체크해 주세요."


def _with_unsupported(db, did, values=("30",)):
    db.execute(update(ReplyDraft).where(ReplyDraft.id == did).values(warnings={"unsupported": list(values)}))
    db.commit()


def _connect(google_fake):
    google_fake.valid_refresh_tokens.add("1//fake-refresh")


def test_unsupported_without_ack_409(google_fake, client):
    _connect(google_fake)
    me = register(client)
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"])
        _with_unsupported(db, did)
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={})
    assert (r.status_code, r.json()["detail"]) == (409, NEED_UNSUPPORTED_ACK)
    assert google_fake.sent == []
    with SessionLocal() as db:
        assert db.get(ReplyDraft, did).status == "draft"


def test_unsupported_with_ack_sends_and_logs(google_fake, client):
    _connect(google_fake)
    me = register(client)
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"])
        _with_unsupported(db, did)
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={"unsupported_ack": True})
    assert r.status_code == 200, r.text
    assert r.json()["mail"]["draft"]["status"] == "sent" and len(google_fake.sent) == 1
    with SessionLocal() as db:
        log = db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == tid,
                                                      AgentActionLog.action_type == "REPLY_SENT")).one()
        assert log.output_data.get("unsupported_ack") is True


def test_no_unsupported_needs_no_ack(google_fake, client):
    _connect(google_fake)
    me = register(client)
    with SessionLocal() as db:
        tid, _ = _setup(db, me["id"])
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={})
    assert r.status_code == 200, r.text
    assert len(google_fake.sent) == 1
