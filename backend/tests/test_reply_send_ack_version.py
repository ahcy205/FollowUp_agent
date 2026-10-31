"""RAG 1단계 최종 리뷰: '알고 보냅니다'를 확인한 그 초안(updated_at)에 묶고, 제목 속 내부용 값도 찾는다.
스위치가 꺼졌을 때 보내기 직전 확인이 저장된 경고를 쓰는 것도 확인한다 (설계 3.4 · 5.3)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.knowledge import draft_sources as ds
from app.knowledge import store
from app.mail import reply_draft as rd
from app.mail import reply_send as rs
from app.mail.errors import ActionError
from app.models import ReplyDraft, Task, User
from tests.conftest import register
from tests.test_reply_send import _setup, _user

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)
COST_8800 = "품번,원가\nTS-200,8800\n"
COST_1200 = "품번,원가\nTS-100,1200\n"
PLAIN_BODY = "안녕하세요.\n견적서를 보내 드립니다.\n감사합니다."
NEED_ACK = "내부용 자료의 값이 들어 있습니다. 내용을 확인하고 '알고 보냅니다'를 체크해 주세요."
CHANGED = "초안이 방금 바뀌었습니다. 내용을 다시 확인한 뒤 보내 주세요."


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_enabled", True)


@pytest.fixture
def fake(google_fake):
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    return google_fake


def _doc(db, uid, name, text, vis="internal"):
    s = store.add_document(db, db.get(User, uid), name, text.encode("utf-8"), vis)
    db.commit()
    return s


def _send(db, task_id, client, **kw):
    task = db.get(Task, task_id)
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.task_id == task_id))
    rs.send_draft(db, db.get(User, task.user_id), task, draft, client, NOW, **kw)
    return draft


def _draft_json(client, tid):
    return client.get(f"/api/tasks/{tid}").json()["mail"]["draft"]


# ---------------------------------------------------------------- 3. 확인을 초안 버전에 묶기

def test_api_send_with_stale_version_409(fake, client):
    me = register(client)
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"])
        _doc(db, me["id"], "원가표.csv", COST_1200)
        old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={"internal_ack": True, "expected_updated_at": old})
    assert (r.status_code, r.json()["detail"]) == (409, CHANGED)
    assert fake.sent == []
    with SessionLocal() as db:
        assert db.get(ReplyDraft, did).status == "draft"


def test_api_send_with_matching_version_sends(fake, client):
    me = register(client)
    with SessionLocal() as db:
        tid, _ = _setup(db, me["id"])
        _doc(db, me["id"], "원가표.csv", COST_1200)
    at = _draft_json(client, tid)["updated_at"]  # 화면이 받은 그대로(SQLite: 시간대 없는 UTC)
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={"internal_ack": True, "unsupported_ack": True, "expected_updated_at": at})
    assert r.status_code == 200, r.text
    assert r.json()["mail"]["draft"]["status"] == "sent" and len(fake.sent) == 1


def test_expected_version_compared_as_utc_instant(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        at = db.get(ReplyDraft, did).updated_at
        same_instant = at.replace(tzinfo=UTC).astimezone(timezone(timedelta(hours=9)))  # 같은 순간, 서울 시각
        assert _send(db, tid, fake, expected_updated_at=same_instant).status == "sent"


def test_send_without_expected_version_still_works(fake, client):
    """expected_updated_at은 선택이다(본문 없는 보내기 · 6단계 화면)."""
    me = register(client)
    with SessionLocal() as db:
        tid, _ = _setup(db, me["id"])
    assert client.post(f"/api/tasks/{tid}/reply-draft/send").status_code == 200


def test_edit_after_ack_check_blocks_old_version(fake, client):
    """카드에서 확인을 체크한 뒤 다른 창에서 고치면, 체크한 그 버전으로는 보내지 않는다."""
    me = register(client)
    with SessionLocal() as db:
        tid, _ = _setup(db, me["id"])
        _doc(db, me["id"], "원가표.csv", COST_1200)
    at = _draft_json(client, tid)["updated_at"]
    r = client.post(f"/api/tasks/{tid}/reply-draft/update", json={
        "subject": "Re: 견적 문의", "body": "안녕하세요.\n단가는 1200원입니다. 원가는 8800원입니다.", "cc_selected": []})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/tasks/{tid}/reply-draft/send", json={"internal_ack": True, "expected_updated_at": at})
    assert (r.status_code, r.json()["detail"]) == (409, CHANGED)
    assert fake.sent == []


# ---------------------------------------------------------------- 5. 제목도 내부용 값 비교에 넣기

def test_subject_internal_value_warned_on_edit(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=PLAIN_BODY)
        _doc(db, uid, "원가표.csv", COST_8800)
        d = db.get(ReplyDraft, did)
        rd.update_draft(d, subject="Re: 견적 문의 (원가 8800원)", body=PLAIN_BODY, cc_selected=[])
        db.commit()
        db.refresh(d)
        assert d.warnings["internal_values"] == 1
        assert d.sources["internal_values"][0]["value"] == "8800원"


def test_subject_internal_value_blocks_send_without_ack(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=PLAIN_BODY)
        db.execute(update(ReplyDraft).where(ReplyDraft.id == did).values(subject="Re: 견적 문의 8800원"))
        db.commit()
        _doc(db, uid, "원가표.csv", COST_8800)
        with pytest.raises(ActionError) as e:
            _send(db, tid, fake)
        assert (str(e.value), e.value.status) == (NEED_ACK, 409)
        assert fake.sent == []
        assert _send(db, tid, fake, internal_ack=True).status == "sent"


def test_thread_mail_subject_value_not_warned(fake):
    """상대 메일 제목에 있던 값은 내부용 값으로 경고하지 않는다(본문과 같은 기준: 스레드 메일은 허용 글)."""
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid, body=PLAIN_BODY)
        from app.models import Email
        db.execute(update(Email).where(Email.task_id == tid).values(subject="견적 문의 8800원 가능?"))
        db.commit()
        _doc(db, uid, "원가표.csv", COST_8800)
        d = db.get(ReplyDraft, did)
        rd.update_draft(d, subject="Re: 견적 문의 8800원 가능?", body=PLAIN_BODY, cc_selected=[])
        db.commit()
        db.refresh(d)
        assert d.warnings["internal_values"] == 0
        assert ds.internal_values_now(db, db.get(Task, tid), d) == []


# ---------------------------------------------------------------- 6. 스위치가 꺼졌을 때

def test_switch_off_send_ignores_internal_index(fake, monkeypatch):
    """스위치를 끄면 6단계와 같다: 내부용 자료에 본문 값이 있어도 저장된 경고가 없으면 확인 없이 보낸다."""
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)  # 본문에 1200원
        _doc(db, uid, "원가표.csv", COST_1200)
        d = db.get(ReplyDraft, did)
        monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
        ds.refresh_draft_checks(db, d)  # 꺼진 상태로 다시 계산: 내부용 값을 저장하지 않는다
        db.commit()
        assert d.warnings["internal_values"] == 0 and d.sources["internal_values"] == []
        monkeypatch.setattr(ds, "internal_values_now", lambda *a, **k: pytest.fail("no index when off"))
        assert _send(db, tid, fake, unsupported_ack=True).status == "sent" and len(fake.sent) == 1


def test_switch_off_stale_stored_warning_still_needs_ack(fake, monkeypatch):
    """켜져 있을 때 만든 초안에 내부용 값 경고가 저장돼 있으면, 끈 뒤에도 그 경고로 막는다(안전한 쪽).
    끈 뒤에 고치거나 다시 만들면 경고가 0으로 다시 계산돼 풀린다."""
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        _doc(db, uid, "원가표.csv", COST_1200)
        d = db.get(ReplyDraft, did)
        ds.refresh_draft_checks(db, d)
        db.commit()
        assert d.warnings["internal_values"] == 1
        monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
        with pytest.raises(ActionError) as e:
            _send(db, tid, fake)
        assert (str(e.value), e.value.status) == (NEED_ACK, 409)
        assert fake.sent == []
        assert _send(db, tid, fake, internal_ack=True, unsupported_ack=True).status == "sent" and len(fake.sent) == 1
