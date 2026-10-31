"""6단계 Task 6: 승인한 답장 초안을 원래 Gmail 스레드로 보낸다. 두 번 발송 방지 · 결과 불분명 확인."""
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.google.client import GoogleAuthError, GoogleError, GoogleTransientError
from app.google.crypto import encrypt_token
from app.mail import reply_send as rs
from app.mail.errors import ActionError
from app.models import (AgentActionLog, Email, EmailDirection, GoogleAccount, GoogleAccountStatus, ReplyDraft, Task,
                        TaskStatus, User)
from tests.conftest import make_client, register

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)
ME = "me@gmail.com"
BODY = "안녕하세요.\n단가는 1200원, 납기는 11/3입니다.\n감사합니다."


def _parse(raw: bytes):
    return BytesParser(policy=policy.default).parsebytes(raw)


def _setup(db, uid: int, *, body=BODY, cc_selected=None, status="draft") -> tuple[int, int]:
    """연결된 Google 계정, 받은 메일 2통(같은 스레드)이 있는 업무, 초안. (task_id, draft_id)."""
    if db.scalar(select(GoogleAccount).where(GoogleAccount.user_id == uid)) is None:
        db.add(GoogleAccount(user_id=uid, google_email=ME, refresh_token_enc=encrypt_token("1//fake-refresh"),
                             scopes=["openid"], status=GoogleAccountStatus.CONNECTED))
    t = Task(user_id=uid, title="견적 요청", goal="견적서 송부", status=TaskStatus.ACTION_REQUIRED, origin="mail",
             gmail_thread_id="thr-1", priority="NORMAL",
             plan=[{"id": 1, "title": "단가 확인", "done": True, "evidence": "단가 1200원"},
                   {"id": 2, "title": "납기 회신", "done": False, "evidence": ""}],
             brief={"analysis": "done", "summary": "견적 요청", "needs_reply": True})
    db.add(t)
    db.flush()
    for n, (sender, at, rfc) in enumerate([("kim@partner.co.kr", NOW - timedelta(days=2), "<a1@partner.co.kr>"),
                                            ("park@partner.co.kr", NOW, "<a2@partner.co.kr>")]):
        db.add(Email(user_id=uid, sender=sender, receiver=ME, subject="견적 문의", content="견적 부탁드립니다.",
                     direction=EmailDirection.INBOUND, processed=True, received_at=at, gmail_message_id=f"in-{n}",
                     gmail_thread_id="thr-1", rfc_message_id=rfc, triage="work", task_id=t.id,
                     cc=["kim@partner.co.kr"] if n else [], gmail_meta={}))
    d = ReplyDraft(user_id=uid, task_id=t.id, purpose="reply", to_addr="park@partner.co.kr",
                   cc_candidates=["kim@partner.co.kr"], cc_selected=cc_selected or [], subject="Re: 견적 문의",
                   body=body, status=status, warnings={}, user_input="")
    db.add(d)
    db.commit()
    return t.id, d.id


def _user(db, email="u@example.com") -> int:
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    return u.id


@pytest.fixture
def fake(google_fake):
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    return google_fake


def _send(db, task_id, now=NOW, client=None, **kw):
    task = db.get(Task, task_id)
    draft = db.scalar(select(ReplyDraft).where(ReplyDraft.task_id == task_id))
    user = db.get(User, task.user_id)
    rs.send_draft(db, user, task, draft, client, now, **kw)
    return task, draft


# ---------------------------------------------------------------- MIME

def test_mime_headers():
    raw = rs.build_mime(from_addr=ME, to_addr="park@partner.co.kr", cc=["kim@partner.co.kr"], subject="견적 문의",
                       body=BODY, in_reply_to="<a2@partner.co.kr>", references=["<a1@partner.co.kr>"])
    m = _parse(raw)
    assert m["From"] == ME and m["To"] == "park@partner.co.kr" and m["Cc"] == "kim@partner.co.kr"
    assert m["Subject"] == "Re: 견적 문의"
    assert m["In-Reply-To"] == "<a2@partner.co.kr>"
    assert m["References"].split() == ["<a1@partner.co.kr>", "<a2@partner.co.kr>"]
    assert m.get_content_type() == "text/plain" and m.get_content_charset() == "utf-8"
    assert m.get_content().strip() == BODY
    # 이미 Re:가 있으면(대소문자 무시) 다시 붙이지 않는다. 참조가 없으면 Cc 머리글도 없다.
    m2 = _parse(rs.build_mime(from_addr=ME, to_addr="a@x.com", cc=[], subject="RE: 견적", body="b",
                              in_reply_to=None, references=[]))
    assert m2["Subject"] == "RE: 견적" and m2["Cc"] is None and m2["In-Reply-To"] is None and m2["References"] is None


def test_mime_rejects_header_injection():
    with pytest.raises(ValueError):
        rs.build_mime(from_addr=ME, to_addr="a@x.com\r\nBcc: evil@x.com", cc=[], subject="s", body="b",
                      in_reply_to=None, references=[])


# ---------------------------------------------------------------- 보내기

def test_send_success_waiting(fake):
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db), cc_selected=["kim@partner.co.kr"])
        task, draft = _send(db, tid, client=fake)
        assert len(fake.sent) == 1
        raw, thread = fake.sent[0]
        assert thread == "thr-1"
        m = _parse(raw)
        assert m["To"] == "park@partner.co.kr" and m["Cc"] == "kim@partner.co.kr" and m["From"] == ME
        assert m["In-Reply-To"] == "<a2@partner.co.kr>"
        assert m["References"].split() == ["<a1@partner.co.kr>", "<a2@partner.co.kr>"]
        db.refresh(task)
        db.refresh(draft)
        assert task.status == TaskStatus.WAITING and task.reply_sent_at is not None
        assert draft.status == "sent" and draft.gmail_message_id
        out = db.scalars(select(Email).where(Email.task_id == tid, Email.direction == EmailDirection.OUTBOUND)).all()
        assert len(out) == 1
        assert out[0].gmail_message_id == draft.gmail_message_id and out[0].gmail_thread_id == "thr-1"
        assert out[0].rfc_message_id == m["Message-ID"] and out[0].receiver == "park@partner.co.kr"
        logs = db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == tid,
                                                       AgentActionLog.action_type == "REPLY_SENT")).all()
        assert len(logs) == 1 and logs[0].reason == "답장을 보냈습니다 (받는 사람 1명, 참조 1명)"
        assert "park" not in str(logs[0].output_data) and "1200" not in str(logs[0].output_data)


def test_double_send_second_409(fake):
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        _send(db, tid, client=fake)
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409 and e.value.args[0] == "이미 보내는 중이거나 보낸 초안입니다."
        assert len(fake.sent) == 1


def test_concurrent_send_claims_once(fake):
    """보내는 중에 같은 초안으로 두 번째 요청이 오면(다른 세션) 두 번째는 409, 발송은 1번."""
    errors = []
    original = fake.gmail_send

    def reentrant(token, raw, thread_id):
        with SessionLocal() as db2:
            try:
                _send(db2, tid, client=fake)
            except ActionError as e:
                errors.append(e.status)
        return original(token, raw, thread_id)

    fake.gmail_send = reentrant
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        _send(db, tid, client=fake)
    assert errors == [409] and len(fake.sent) == 1


def test_send_blocked_by_placeholder(fake):
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db), body="납기는 [[납기 일정 입력]]입니다.")
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409 and fake.sent == []
        assert db.get(ReplyDraft, did).status == "draft"


def test_send_recipient_changed_409(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        db.add(Email(user_id=uid, sender="choi@partner.co.kr", receiver=ME, subject="견적 문의", content="추가",
                     direction=EmailDirection.INBOUND, processed=True, received_at=NOW + timedelta(hours=1),
                     gmail_message_id="in-new", gmail_thread_id="thr-1", triage="work", task_id=tid, cc=[],
                     gmail_meta={}))
        db.commit()
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409 and e.value.args[0] == "받는 사람이 바뀌었습니다. 초안을 다시 만들어 주세요."
        assert fake.sent == [] and db.get(ReplyDraft, did).status == "draft"


def test_send_cc_outside_candidates_409(fake):
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db), cc_selected=["evil@x.com"])
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409 and fake.sent == []


def test_send_requires_google_connection(fake):
    with SessionLocal() as db:
        uid = _user(db)
        tid, did = _setup(db, uid)
        acc = db.scalar(select(GoogleAccount).where(GoogleAccount.user_id == uid))
        acc.status = GoogleAccountStatus.REAUTH_REQUIRED
        db.commit()
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409 and "Google" in e.value.args[0] and fake.sent == []
        assert db.get(ReplyDraft, did).status == "draft"


def test_transient_error_unknown(fake):
    fake.gmail_send_fail = GoogleTransientError("timeout")
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        task, draft = _send(db, tid, client=fake)
        db.refresh(draft)
        db.refresh(task)
        assert draft.status == "unknown" and draft.sending_at is not None
        assert task.status == TaskStatus.ACTION_REQUIRED
        assert db.scalars(select(Email).where(Email.task_id == tid,
                                              Email.direction == EmailDirection.OUTBOUND)).all() == []
        # 확인 중에는 다시 보내지 못한다
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409


def test_unexpected_error_unknown(fake):
    fake.gmail_send_fail = KeyError("id")  # 응답을 읽지 못함: 나갔을 수도 있다
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        _send(db, tid, client=fake)
        assert db.get(ReplyDraft, did).status == "unknown"


def _unknown(db, minutes_ago: int) -> tuple[int, int]:
    tid, did = _setup(db, _user(db), status="unknown")
    d = db.get(ReplyDraft, did)
    d.sending_at = NOW - timedelta(minutes=minutes_ago)
    db.commit()
    return tid, did


def _confirm(db, tid, did, sent, now=NOW):
    task = db.get(Task, tid)
    rs.confirm_unknown(db, db.get(User, task.user_id), task, db.get(ReplyDraft, did), sent=sent, now=now)


def test_confirm_before_5min_409(fake):
    with SessionLocal() as db:
        tid, did = _unknown(db, 4)
        with pytest.raises(ActionError) as e:
            _confirm(db, tid, did, True)
        assert e.value.status == 409
        assert e.value.args[0] == "아직 발송 여부를 확인하는 중입니다. 잠시 후 다시 확인해 주세요."
        assert db.get(ReplyDraft, did).status == "unknown"


def test_confirm_not_sent_back_to_draft(fake):
    with SessionLocal() as db:
        tid, did = _unknown(db, 5)
        _confirm(db, tid, did, False)
        db.commit()
        d = db.get(ReplyDraft, did)
        assert d.status == "draft" and d.sending_at is None
        assert db.get(Task, tid).status == TaskStatus.ACTION_REQUIRED


def test_confirm_sent_marks_waiting(fake):
    with SessionLocal() as db:
        tid, did = _unknown(db, 10)
        _confirm(db, tid, did, True)
        db.commit()
        assert db.get(ReplyDraft, did).status == "sent"
        task = db.get(Task, tid)
        assert task.status == TaskStatus.WAITING and task.reply_sent_at is not None
        assert len(db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == tid,
                                                           AgentActionLog.action_type == "REPLY_SENT")).all()) == 1


def test_confirm_only_unknown(fake):
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        with pytest.raises(ActionError) as e:
            _confirm(db, tid, did, True)
        assert e.value.status == 409


def test_auth_error_back_to_draft_with_message(fake):
    fake.gmail_send_fail = GoogleAuthError("401")
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409 and "Google 연결을 다시 해 주세요" in e.value.args[0]
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.status == "draft" and d.sending_at is None


def test_rejected_error_back_to_draft(fake):
    fake.gmail_send_fail = GoogleError("400")
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        with pytest.raises(ActionError) as e:
            _send(db, tid, client=fake)
        assert e.value.status == 409
        d = db.get(ReplyDraft, did)
        db.refresh(d)
        assert d.status == "draft"


def test_sync_stored_sent_copy_first_no_duplicate(fake):
    """발송 응답을 받기 전에 동기화가 같은 메일을 먼저 저장하고 초안을 sent로 바꿨어도 중복 저장 · 중복 기록이 없다."""
    from app.mail import lifecycle
    original = fake.gmail_send

    def sync_first(token, raw, thread_id):
        sent = original(token, raw, thread_id)
        with SessionLocal() as db2:
            uid = db2.get(Task, tid).user_id
            row = Email(user_id=uid, sender=ME, receiver="park@partner.co.kr", subject="Re: 견적 문의", content="x",
                        direction=EmailDirection.OUTBOUND, processed=False, received_at=datetime.now(UTC),
                        gmail_message_id=sent.id, gmail_thread_id="thr-1", cc=[], gmail_meta={})
            db2.add(row)
            db2.flush()
            lifecycle.record_sent_mail(db2, uid, row, datetime.now(UTC))
            db2.commit()
        return sent

    fake.gmail_send = sync_first
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        _send(db, tid, now=datetime.now(UTC), client=fake)
        db.expire_all()
        assert db.get(ReplyDraft, did).status == "sent"
        assert len(db.scalars(select(Email).where(Email.task_id == tid,
                                                  Email.direction == EmailDirection.OUTBOUND)).all()) == 1
        assert len(db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == tid,
                                                           AgentActionLog.action_type == "REPLY_SENT")).all()) == 1


# ---------------------------------------------------------------- 남은 할 일

def test_complete_all_todos(fake):
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        task = db.get(Task, tid)
        assert rs.complete_remaining_todos(task) == 1
        db.commit()
        db.refresh(task)
        assert all(p["done"] for p in task.plan)
        assert rs.complete_remaining_todos(task) == 0


# ---------------------------------------------------------------- API

def test_api_send_confirm_and_todos(fake, client):
    me = register(client)
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"])
    r = client.post(f"/api/tasks/{tid}/reply-draft/send")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task"]["status"] == "WAITING" and data["mail"]["draft"]["status"] == "sent"
    assert data["mail"]["remaining_todos_prompt"] is True
    assert client.post(f"/api/tasks/{tid}/reply-draft/send").status_code == 409
    assert len(fake.sent) == 1

    with make_client() as other:
        register(other, "other@example.com")
        for p, body in (("reply-draft/send", None), ("reply-draft/confirm", {"sent": True}),
                        ("todos/complete-all", None)):
            assert other.post(f"/api/tasks/{tid}/{p}", json=body).status_code == 404, p

    r = client.post(f"/api/tasks/{tid}/todos/complete-all")
    assert r.status_code == 200 and r.json()["mail"]["remaining_todos_prompt"] is False
    assert all(p["done"] for p in r.json()["task"]["plan"])


def test_api_unknown_then_confirm(fake, client):
    me = register(client)
    fake.gmail_send_fail = GoogleTransientError("timeout")
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"])
    r = client.post(f"/api/tasks/{tid}/reply-draft/send")
    assert r.status_code == 200, r.text
    draft = r.json()["mail"]["draft"]
    assert draft["status"] == "unknown" and draft["unknown_since"] and draft["can_send"] is False
    r = client.post(f"/api/tasks/{tid}/reply-draft/confirm", json={"sent": False})
    assert r.status_code == 409 and r.json()["detail"] == "아직 발송 여부를 확인하는 중입니다. 잠시 후 다시 확인해 주세요."
    with SessionLocal() as db:
        d = db.get(ReplyDraft, did)
        d.sending_at = datetime.now(UTC) - timedelta(minutes=6)
        db.commit()
    r = client.post(f"/api/tasks/{tid}/reply-draft/confirm", json={"sent": False})
    assert r.status_code == 200 and r.json()["mail"]["draft"]["status"] == "draft"
    assert client.post(f"/api/tasks/{tid}/reply-draft/confirm", json={"sent": "yes"}).status_code == 422


def test_api_send_rate_limited(fake, client):
    me = register(client)
    with SessionLocal() as db:
        tid, did = _setup(db, me["id"], body="[[빈칸]]")
    # 확인에서 거절된 요청(빈칸)은 한도를 쓰지 않는다
    assert [client.post(f"/api/tasks/{tid}/reply-draft/send").status_code for _ in range(6)] == [409] * 6
    codes = []
    for _ in range(6):
        with SessionLocal() as db:
            d = db.get(ReplyDraft, did)
            d.status, d.body, d.sending_at = "draft", BODY, None
            db.commit()
        codes.append(client.post(f"/api/tasks/{tid}/reply-draft/send").status_code)
    assert codes == [200] * 5 + [429] and len(fake.sent) == 5


def test_stale_sending_can_be_confirmed(fake):
    """발송 중 서버가 멈춰 sending에 5분 넘게 남은 초안도 사용자가 확인할 수 있다."""
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db), status="sending")
        d = db.get(ReplyDraft, did)
        d.sending_at = NOW - timedelta(minutes=2)
        db.commit()
        assert not rs.is_unknown(d, NOW)
        with pytest.raises(ActionError) as e:
            _confirm(db, tid, did, False)
        assert e.value.status == 409
        assert rs.is_unknown(d, NOW + timedelta(minutes=3))
        _confirm(db, tid, did, False, now=NOW + timedelta(minutes=3))
        db.commit()
        assert db.get(ReplyDraft, did).status == "draft"


def test_send_blocked_when_reply_found_after_not_sent(fake):
    """'안 보내졌어요'로 되돌린 뒤 동기화가 실제로 보낸 메일을 찾으면(reply_sent_at이 초안보다 늦음) 다시 보내지 않는다."""
    from app.mail import lifecycle
    start = datetime.now(UTC) - timedelta(minutes=10)
    fake.gmail_send_fail = GoogleTransientError("timeout")
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        _send(db, tid, now=start, client=fake)
        assert db.get(ReplyDraft, did).status == "unknown"
        now = datetime.now(UTC)
        _confirm(db, tid, did, False, now=now)
        db.commit()
        d = db.get(ReplyDraft, did)
        assert d.status == "draft" and rs._utc(d.updated_at) >= rs._utc(now) - timedelta(seconds=1)
        uid = db.get(Task, tid).user_id
        row = Email(user_id=uid, sender=ME, receiver="park@partner.co.kr", subject="Re: 견적 문의", content="x",
                    direction=EmailDirection.OUTBOUND, processed=False, received_at=start,
                    gmail_message_id="sent-elsewhere", gmail_thread_id="thr-1", cc=[], gmail_meta={})
        db.add(row)
        db.flush()
        lifecycle.record_sent_mail(db, uid, row, now + timedelta(seconds=30))
        db.commit()
        assert db.get(ReplyDraft, did).status == "draft"  # sending_at이 없어 초안과 맞춰 보지 못한다
        fake.gmail_send_fail = None
        sent_before = len(fake.sent)
        with pytest.raises(ActionError) as e:
            _send(db, tid, now=now + timedelta(minutes=1), client=fake)
        assert e.value.status == 409
        assert e.value.args[0] == "이미 답장을 보낸 것으로 확인됐습니다. 초안을 지우거나 다시 만들어 주세요."
        assert len(fake.sent) == sent_before
        assert db.get(ReplyDraft, did).status == "draft"


def test_draft_edit_after_reply_can_send(fake):
    """답장을 보낸 뒤 새로 고친 초안(updated_at이 더 늦음)은 보낼 수 있다."""
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        task = db.get(Task, tid)
        task.reply_sent_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
        from app.mail import reply_draft
        reply_draft.update_draft(db.get(ReplyDraft, did), subject="Re: 견적 문의", body=BODY, cc_selected=[])
        db.commit()
        _send(db, tid, now=datetime.now(UTC), client=fake, unsupported_ack=True)
        assert db.get(ReplyDraft, did).status == "sent"


def test_mime_has_message_id_with_sender_domain():
    m = _parse(rs.build_mime(from_addr=ME, to_addr="a@x.com", cc=[], subject="s", body="b",
                             in_reply_to=None, references=[]))
    assert m["Message-ID"] and m["Message-ID"].strip().endswith("@gmail.com>")
    m2 = _parse(rs.build_mime(from_addr=ME, to_addr="a@x.com", cc=[], subject="s", body="b", in_reply_to=None,
                              references=[], message_id="<fixed@gmail.com>"))
    assert m2["Message-ID"] == "<fixed@gmail.com>"


def test_sent_copy_keeps_message_id_for_later_references(fake):
    """앱이 보낸 사본에 Message-ID를 저장해 다음 답장의 References에 들어가게 한다."""
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db))
        _send(db, tid, client=fake)
        header = _parse(fake.sent[0][0])["Message-ID"]
        out = db.scalar(select(Email).where(Email.task_id == tid, Email.direction == EmailDirection.OUTBOUND))
        assert header and out.rfc_message_id == header
        _thread, _in_reply_to, refs = rs._reply_target(db, db.get(Task, tid), ME)
        assert header in refs


def test_draft_out_shows_reply_found_after_not_sent(fake):
    """Gmail 보낸 메일의 시각이 초안을 되돌린 시각보다 일러도, 그 뒤에 확인됐으면 화면에 '이미 답장' 안내를 띄운다."""
    from app.api.tasks import _draft_out
    from app.mail import lifecycle
    start = datetime.now(UTC) - timedelta(minutes=10)
    with SessionLocal() as db:
        tid, did = _setup(db, _user(db), status="unknown")
        d = db.get(ReplyDraft, did)
        d.sending_at = start
        db.commit()
        _confirm(db, tid, did, False, now=datetime.now(UTC))
        db.commit()
        assert _draft_out(db, db.get(Task, tid), datetime.now(UTC)).replied_after is False
        uid = db.get(Task, tid).user_id
        row = Email(user_id=uid, sender=ME, receiver="park@partner.co.kr", subject="Re: 견적 문의", content="x",
                    direction=EmailDirection.OUTBOUND, processed=False, received_at=start,
                    gmail_message_id="sent-elsewhere", gmail_thread_id="thr-1", cc=[], gmail_meta={})
        db.add(row)
        db.flush()
        lifecycle.record_sent_mail(db, uid, row, datetime.now(UTC))
        db.commit()
        task = db.get(Task, tid)
        assert rs._utc(task.reply_sent_at) == start  # 메일 자체의 시각
        assert _draft_out(db, task, datetime.now(UTC)).replied_after is True
