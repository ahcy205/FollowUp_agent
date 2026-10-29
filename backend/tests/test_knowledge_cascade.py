"""RAG 1단계 Task 6: 자료를 지우거나 내부용으로 바꾸면 초안에서 사본을 지우고, 보내기 직전에 다시 확인한다 (설계 5.3 · 5.4)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.api.tasks import task_fingerprint
from app.config import get_settings
from app.db import SessionLocal
from app.knowledge import draft_sources as ds
from app.knowledge import search, store
from app.mail import reply_send as rs
from app.mail.errors import ActionError
from app.models import AgentActionLog, Email, KnowledgeSource, ReplyDraft, Task, TaskStatus, User
from tests.conftest import register
from tests.test_reply_send import _setup, _user

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)
PRICE_CSV = "품번,단가\nTS-200,1200\n"
SHIP_CSV = "품번,배송\nTS-200,무료 배송\n"
CHANGED = "참고한 자료가 지워졌거나 내부용으로 바뀌었습니다. 초안을 다시 만들어 주세요."
NEED_ACK = "내부용 자료의 값이 들어 있습니다. 내용을 확인하고 '알고 보냅니다'를 체크해 주세요."


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_enabled", True)


@pytest.fixture
def fake(google_fake):
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    return google_fake


def _doc(db, uid, name, text, vis="shareable"):
    s = store.add_document(db, db.get(User, uid), name, text.encode("utf-8"), vis)
    db.commit()
    return s


def _cite(db, draft_id, *sources: KnowledgeSource):
    """초안이 이 자료들의 덩어리를 넣어 받아 썼다고 만든다(첫 번째 자료의 1200 줄을 인용)."""
    draft = db.get(ReplyDraft, draft_id)
    chunks = []
    for s in sources:
        hits = [h for h in search.search_shareable(db, draft.user_id, f"TS-200 {s.title}") if h.source_id == s.id]
        chunks += ds.chunk_entries(hits[:1])
    first = chunks[0]
    line = next(i for i, text in enumerate(first["lines"], start=1) if "1200" in text)
    draft.sources = {"chunks": chunks, "facts": [{"text": "단가는 1200원", "ref": first["ref"], "line": line}],
                     "internal_values": [], "search_failed": False, "sources_changed": False}
    ds.refresh_draft_checks(db, draft)
    db.commit()
    db.refresh(draft)
    assert len(draft.sources["facts"]) == 1 and draft.warnings["internal_values"] == 0
    return draft


def _old_updated_at(db, draft_id):
    at = NOW - timedelta(days=1)
    db.execute(update(ReplyDraft).where(ReplyDraft.id == draft_id).values(updated_at=at))
    db.commit()
    return at


def _send(db, task_id, client, **kw):
    task = db.get(Task, task_id)
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.task_id == task_id))
    rs.send_draft(db, db.get(User, task.user_id), task, draft, client, NOW, **kw)
    return draft


def test_delete_purges_copy_and_marks_changed(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        price, ship = _doc(db, uid, "단가표.csv", PRICE_CSV), _doc(db, uid, "배송.csv", SHIP_CSV)
        _cite(db, did, price, ship)
        # 다른 사용자의 초안은 같은 자료 번호를 담고 있어도 건드리지 않는다
        other = _user(db, "other@example.com")
        _, other_did = _setup(db, other)
        od = db.get(ReplyDraft, other_did)
        od.sources = {"chunks": [{"ref": f"D{price.id}-0", "source_id": price.id, "lines": ["x"]}], "facts": []}
        db.commit()
        old = _old_updated_at(db, did)

        store.delete_source(db, db.get(KnowledgeSource, price.id))
        db.commit()

        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert [c["source_id"] for c in d.sources["chunks"]] == [ship.id]
        assert d.sources["facts"] == []
        assert d.sources["sources_changed"] is True
        assert d.updated_at.replace(tzinfo=UTC) > old
        db.refresh(od)
        assert [c["source_id"] for c in od.sources["chunks"]] == [price.id]
        assert ds.purge_source_from_drafts(db, uid, price.id) == 0  # 이미 걷어냈다


def test_downgrade_purges_all_drafts_and_blocks_send(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid1, did1 = _setup(db, uid)
        # 두 번째 업무를 만들 수 있게 첫 업무의 스레드 · 메일 번호를 바꿔 둔다(사용자마다 고유)
        db.get(Task, tid1).gmail_thread_id = "thr-a"
        for e in db.scalars(select(Email).where(Email.task_id == tid1)):
            e.gmail_message_id = f"a-{e.gmail_message_id}"
        db.commit()
        tid2, did2 = _setup(db, uid, status="sent")
        db.get(Task, tid2).status = TaskStatus.COMPLETED  # 닫힌 업무의 초안도 걷어낸다
        db.commit()
        price = _doc(db, uid, "단가표.csv", PRICE_CSV)
        _cite(db, did1, price)
        _cite(db, did2, price)

        # 사본을 지우기 전 상태: 자료만 내부용으로 바뀌었다 → 보내기 직전 확인이 막는다
        db.execute(update(KnowledgeSource).where(KnowledgeSource.id == price.id).values(visibility="internal"))
        db.commit()
        with pytest.raises(ActionError) as e:
            _send(db, tid1, fake, internal_ack=True)
        assert (str(e.value), e.value.status) == (CHANGED, 409)
        db.execute(update(KnowledgeSource).where(KnowledgeSource.id == price.id).values(visibility="shareable"))
        db.commit()

        sent_before = db.get(ReplyDraft, did2).updated_at
        store.set_visibility(db, db.get(KnowledgeSource, price.id), "internal")
        db.commit()
        d1, d2 = db.get(ReplyDraft, did1), db.get(ReplyDraft, did2)
        db.refresh(d1)
        db.refresh(d2)
        assert d1.sources["chunks"] == [] and d1.sources["facts"] == []
        assert d2.sources["chunks"] == [] and d2.sources["facts"] == []
        assert d1.sources["sources_changed"] is True
        assert d2.sources.get("sources_changed") is False and d2.status == "sent"
        assert d2.updated_at == sent_before
        # 이제 내부용 자료가 된 값이 본문에 있다
        assert d1.warnings["internal_values"] == 1

        # 사본은 없어도 '자료가 바뀌었다'는 표시가 남아 있으면 막는다. 다시 만들기만 이 표시를 지운다.
        with pytest.raises(ActionError) as e:
            _send(db, tid1, fake, internal_ack=True)
        assert (str(e.value), e.value.status) == (CHANGED, 409)
        assert fake.sent == []


def test_send_requires_internal_ack(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        d = db.get(ReplyDraft, did)
        d.warnings = {"internal_values": 1}
        db.commit()
        with pytest.raises(ActionError) as e:
            _send(db, tid, fake)
        assert (str(e.value), e.value.status) == (NEED_ACK, 409)
        assert fake.sent == []
        d = _send(db, tid, fake, internal_ack=True)
        assert d.status == "sent" and len(fake.sent) == 1
        log = db.scalar(select(AgentActionLog).where(AgentActionLog.task_id == tid,
                                                     AgentActionLog.action_type == "REPLY_SENT"))
        assert log.output_data["internal_ack"] is True


def test_api_send_internal_ack(fake, client):
    me = register(client)
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"])
        d = db.get(ReplyDraft, did)
        d.warnings = {"internal_values": 2}
        db.commit()
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={})
    assert r.status_code == 409 and r.json()["detail"] == NEED_ACK
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={"internal_ack": True})
    assert r.status_code == 200, r.text
    assert r.json()["mail"]["draft"]["status"] == "sent"


def test_send_body_rejects_extra_fields(fake, client):
    me = register(client)
    with SessionLocal() as db:
        tid, _ = _setup(db, me["id"])
    for body in ({"internal_ack": True, "to": "evil@example.com"}, {"internal_ack": "yes"}):
        assert client.post(f"/api/tasks/{tid}/reply-draft/send", json=body).status_code == 422, body
    assert fake.sent == []


def test_upgrade_to_shareable_does_not_purge(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        price = _doc(db, uid, "단가표.csv", PRICE_CSV)
        cost = _doc(db, uid, "원가표.csv", "품번,원가\nTS-200,8800\n", vis="internal")
        _cite(db, did, price)
        before = dict(db.get(ReplyDraft, did).sources)
        old = _old_updated_at(db, did)
        store.set_visibility(db, db.get(KnowledgeSource, cost.id), "shareable")
        store.set_visibility(db, db.get(KnowledgeSource, price.id), "shareable")  # 같은 등급
        db.commit()
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.sources == before and d.sources["sources_changed"] is False
        assert d.updated_at.replace(tzinfo=UTC) == old


def test_fingerprint_changes_when_draft_updated(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        price = _doc(db, uid, "단가표.csv", PRICE_CSV)
        _cite(db, did, price)
        _old_updated_at(db, did)
        before = task_fingerprint(db, db.get(Task, tid))
        store.delete_source(db, db.get(KnowledgeSource, price.id))
        db.commit()
        assert task_fingerprint(db, db.get(Task, tid)) != before
