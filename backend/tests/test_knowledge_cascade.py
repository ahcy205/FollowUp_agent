"""RAG 1단계 Task 6: 자료를 지우거나 내부용으로 바꾸면 초안에서 사본을 지우고, 보내기 직전에 다시 확인한다 (설계 5.3 · 5.4)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.api.tasks import _draft_out, task_fingerprint
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
COST_1200 = "품번,원가\nTS-100,1200\n"
COST_8800 = "품번,원가\nTS-200,8800\n"
MEMO_BODY = "안녕하세요.\n원가는 8,800원입니다.\n감사합니다."  # 메모에서 온 값(인용 줄 · 상대 메일에 없음)
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
        _doc(db, uid, "원가표.csv", COST_1200, vis="internal")  # 본문의 1200원이 내부용 자료에 있다
        with pytest.raises(ActionError) as e:
            _send(db, tid, fake)
        assert (str(e.value), e.value.status) == (NEED_ACK, 409)
        assert fake.sent == []
        d = _send(db, tid, fake, internal_ack=True, unsupported_ack=True)
        assert d.status == "sent" and len(fake.sent) == 1
        log = db.scalar(select(AgentActionLog).where(AgentActionLog.task_id == tid,
                                                     AgentActionLog.action_type == "REPLY_SENT"))
        assert log.output_data["internal_ack"] is True


def test_api_send_internal_ack(fake, client):
    me = register(client)
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"])
        _doc(db, me["id"], "원가표.csv", COST_1200, vis="internal")
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={})
    assert r.status_code == 409 and r.json()["detail"] == NEED_ACK
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={"internal_ack": True, "unsupported_ack": True})
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


# ---------------------------------------------------------------- 고침 1차

def test_unknown_draft_purged_then_not_sent_blocks(fake):
    """결과 불분명 초안도 '자료가 바뀌었다'를 켠다: '안 보내졌어요'로 초안에 돌아와도 내부용이 된 값을 보내지 않는다."""
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, status="unknown")
        price = _doc(db, uid, "단가표.csv", PRICE_CSV)
        _cite(db, did, price)
        d = db.get(ReplyDraft, did)
        d.sending_at = NOW - timedelta(minutes=6)
        db.commit()
        before = db.get(ReplyDraft, did).updated_at
        store.set_visibility(db, db.get(KnowledgeSource, price.id), "internal")
        db.commit()
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.sources["chunks"] == [] and d.sources["sources_changed"] is True
        assert d.updated_at.replace(tzinfo=UTC) == before.replace(tzinfo=UTC)
        rs.confirm_unknown(db, db.get(User, uid), db.get(Task, tid), d, sent=False, now=NOW)
        db.commit()
        assert d.status == "draft"
        with pytest.raises(ActionError) as e:
            _send(db, tid, fake, internal_ack=True)
        assert (str(e.value), e.value.status) == (CHANGED, 409)
        assert fake.sent == []


def test_claim_requires_draft_unchanged_since_check(fake, monkeypatch):
    """확인과 선점 사이(토큰 갱신)에 초안이 바뀌면 선점하지 않는다."""
    original = rs.access_token_for

    def edit_meanwhile(db, account, client):
        with SessionLocal() as db2:
            db2.execute(update(ReplyDraft).where(ReplyDraft.id == did)
                        .values(body="바뀐 본문 1200원", updated_at=datetime.now(UTC)))
            db2.commit()
        return original(db, account, client)

    monkeypatch.setattr(rs, "access_token_for", edit_meanwhile)
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        with pytest.raises(ActionError) as e:
            _send(db, tid, fake)
        assert (str(e.value), e.value.status) == ("초안이 방금 바뀌었습니다. 내용을 다시 확인한 뒤 보내 주세요.", 409)
        assert fake.sent == [] and db.get(ReplyDraft, did).status == "draft"


def test_send_rechecks_internal_values_from_body(fake):
    """초안을 만든 뒤 올린 내부용 자료의 값도 보내기 직전에 잡는다(저장된 경고를 믿지 않는다)."""
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=MEMO_BODY)
        d = db.get(ReplyDraft, did)
        ds.refresh_draft_checks(db, d)
        db.commit()
        assert d.warnings["internal_values"] == 0
        _doc(db, uid, "원가표.csv", COST_8800, vis="internal")
        # 올릴 때 저장된 경고도 다시 계산하지만(아래 테스트), 보내기 직전 확인은 저장된 경고를 믿지 않는다:
        # 저장된 경고를 일부러 낡게 만들어도 막아야 한다.
        stale = {**d.warnings, "internal_values": 0}
        db.execute(update(ReplyDraft).where(ReplyDraft.id == did)
                   .values(warnings=stale, sources={**d.sources, "internal_values": []}))
        db.commit()
        db.expire_all()
        assert db.get(ReplyDraft, did).warnings["internal_values"] == 0
        with pytest.raises(ActionError) as e:
            _send(db, tid, fake)
        assert (str(e.value), e.value.status) == (NEED_ACK, 409)
        assert fake.sent == []
        assert _send(db, tid, fake, internal_ack=True, unsupported_ack=True).status == "sent" and len(fake.sent) == 1


@pytest.mark.parametrize("change", ["shareable", "delete"])
def test_internal_source_change_refreshes_warnings(fake, change):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=MEMO_BODY)
        cost = _doc(db, uid, "원가표.csv", COST_8800, vis="internal")
        d = db.get(ReplyDraft, did)
        ds.refresh_draft_checks(db, d)
        db.commit()
        assert d.warnings["internal_values"] == 1 and d.sources["internal_values"][0]["source_ids"] == [cost.id]
        old = _old_updated_at(db, did)
        source = db.get(KnowledgeSource, cost.id)
        if change == "shareable":
            store.set_visibility(db, source, "shareable")
        else:
            store.delete_source(db, source)
        db.commit()
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.warnings["internal_values"] == 0 and d.sources["internal_values"] == []
        assert not d.sources.get("sources_changed")
        assert d.updated_at.replace(tzinfo=UTC) == old  # 발송 판단 기준 시각은 그대로


def test_draft_out_cannot_send_when_sources_changed(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        price = _doc(db, uid, "단가표.csv", PRICE_CSV)
        _cite(db, did, price)
        assert _draft_out(db, db.get(Task, tid), NOW).can_send is True
        store.delete_source(db, db.get(KnowledgeSource, price.id))
        db.commit()
        assert _draft_out(db, db.get(Task, tid), NOW).can_send is False


def test_new_internal_upload_refreshes_draft_warnings(fake):
    """초안을 만든 뒤 내부용 자료를 올리면 보내기 전 초안의 내부용 값 경고를 다시 계산한다(카드에 '알고 보냅니다'가 보이게).
    updated_at은 그대로 둔다(두 번 보내기 방지 기준)."""
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=MEMO_BODY)
        d = db.get(ReplyDraft, did)
        ds.refresh_draft_checks(db, d)
        db.commit()
        assert d.warnings["internal_values"] == 0
        old = _old_updated_at(db, did)
        fp = task_fingerprint(db, db.get(Task, tid))
        cost = _doc(db, uid, "원가표.csv", COST_8800, vis="internal")
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.warnings["internal_values"] == 1
        assert [v["source_ids"] for v in d.sources["internal_values"]] == [[cost.id]]
        assert d.updated_at.replace(tzinfo=UTC) == old
        assert task_fingerprint(db, db.get(Task, tid)) != fp  # 열린 화면이 새 경고를 받는다


def test_shareable_upload_does_not_flag(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=MEMO_BODY)
        ds.refresh_draft_checks(db, db.get(ReplyDraft, did))
        db.commit()
        _doc(db, uid, "원가표.csv", COST_8800, vis="shareable")
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.warnings["internal_values"] == 0


def test_downgrade_uncited_source_refreshes_draft_warnings(fake):
    """인용하지 않은 공유 자료를 내부용으로 바꾸면, 그 값을 쓴 보내기 전 초안에 내부용 값 경고가 생긴다."""
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=MEMO_BODY)
        cost = _doc(db, uid, "원가표.csv", COST_8800, vis="shareable")
        d = db.get(ReplyDraft, did)
        ds.refresh_draft_checks(db, d)
        db.commit()
        assert d.warnings["internal_values"] == 0 and not d.sources.get("chunks")
        old = _old_updated_at(db, did)
        store.set_visibility(db, db.get(KnowledgeSource, cost.id), "internal")
        db.commit()
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.warnings["internal_values"] == 1
        assert [v["source_ids"] for v in d.sources["internal_values"]] == [[cost.id]]
        assert not d.sources.get("sources_changed")
        assert d.updated_at.replace(tzinfo=UTC) == old


def test_internal_upload_with_switch_off_leaves_drafts(fake, monkeypatch):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=MEMO_BODY)
        ds.refresh_draft_checks(db, db.get(ReplyDraft, did))
        db.commit()
        before = dict(db.get(ReplyDraft, did).warnings)
        monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
        _doc(db, uid, "원가표.csv", COST_8800, vis="internal")
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.warnings == before
