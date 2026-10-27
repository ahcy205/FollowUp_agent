"""6단계 Task 4: Gmail에서 보낸 메일 중 업무 스레드의 것을 저장하고 '답장 보냄'으로 옮긴다."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.google.crypto import encrypt_token
from app.mail.sync import sync_account
from app.models import (
    AgentActionLog, Email, EmailDirection, GoogleAccount, GoogleAccountStatus, ReplyDraft, Task, TaskStatus,
)
from tests.conftest import ScriptedLLM, register
from tests.fake_google import make_raw

NOW = datetime.now(timezone.utc)


def _ms(dt):
    return int(dt.timestamp() * 1000)


@pytest.fixture
def account(client, google_fake):
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    with SessionLocal() as db:
        acc = GoogleAccount(user_id=me["id"], google_email="me@gmail.com",
                            refresh_token_enc=encrypt_token("1//fake-refresh"),
                            scopes=["openid"], status=GoogleAccountStatus.CONNECTED)
        db.add(acc)
        db.commit()
        return acc.id, me["id"]


def _run(account_id, fake):
    with SessionLocal() as db:
        return sync_account(db, db.get(GoogleAccount, account_id), fake, now=NOW)


def _task(uid, thread="thr-1", **over):
    with SessionLocal() as db:
        f = dict(user_id=uid, title="견적 요청", goal="견적서", status=TaskStatus.ACTION_REQUIRED,
                 origin="mail", agent_enabled=False, gmail_thread_id=thread, priority="NORMAL", brief={})
        f.update(over)
        t = Task(**f)
        db.add(t)
        db.commit()
        return t.id


def _sent(fake, thread, subject="Re: 견적 요청", to="kim@partner.co.kr", when=None, labels=("SENT",)):
    raw = make_raw(sender="me@gmail.com", to=to, subject=subject, body="회신")
    return fake.deliver(raw, labels=labels, thread_id=thread, internal_date_ms=_ms(when or NOW))


def _rows():
    with SessionLocal() as db:
        return list(db.scalars(select(Email).where(Email.gmail_message_id.is_not(None)).order_by(Email.id)))


def _count_logs(tid, action="REPLY_SENT"):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(AgentActionLog).where(
            AgentActionLog.task_id == tid, AgentActionLog.action_type == action))


def test_sent_in_task_thread_marks_waiting(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    _sent(google_fake, "thr-1")
    r = _run(aid, google_fake)
    assert r.sent_stored == 1 and r.skipped_sent == 0
    rows = _rows()
    assert len(rows) == 1
    e = rows[0]
    assert e.direction == EmailDirection.OUTBOUND.value and e.sender == "me@gmail.com"
    assert e.task_id == tid and e.processed is True and e.triage == "sent"
    assert "kim@partner.co.kr" in e.receiver
    with SessionLocal() as db:
        t = db.get(Task, tid)
        assert t.status == TaskStatus.WAITING and t.reply_sent_at is not None
    assert _count_logs(tid) == 1


def test_sent_outside_task_thread_not_stored(account, google_fake):
    aid, uid = account
    _task(uid)
    _sent(google_fake, "other-thread")
    r = _run(aid, google_fake)
    assert r.skipped_sent == 1 and r.sent_stored == 0
    assert _rows() == []


def test_sent_in_merged_task_thread_attaches_to_target(account, google_fake):
    aid, uid = account
    target = _task(uid, thread="thr-a")
    _task(uid, thread="thr-b", status=TaskStatus.CANCELLED, merged_into_id=target)
    _sent(google_fake, "thr-b")
    _run(aid, google_fake)
    assert _rows()[0].task_id == target
    with SessionLocal() as db:
        assert db.get(Task, target).status == TaskStatus.WAITING


def test_sent_to_self_with_inbox_treated_as_sent(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    _sent(google_fake, "thr-1", labels=("INBOX", "SENT"))
    _run(aid, google_fake)
    assert _rows()[0].direction == EmailDirection.OUTBOUND.value
    with SessionLocal() as db:
        assert db.get(Task, tid).status == TaskStatus.WAITING


def test_app_sent_mail_not_duplicated(account, google_fake):
    """앱이 보낸 메일(Task 6이 저장함)이 동기화로 다시 와도 행 · 타임라인이 그대로다."""
    aid, uid = account
    tid = _task(uid)
    raw = make_raw(sender="me@gmail.com", to="kim@partner.co.kr", subject="Re: 견적 요청", body="회신")
    mid = google_fake.gmail_send("tok", raw, "thr-1").id
    with SessionLocal() as db:
        db.add(Email(user_id=uid, sender="me@gmail.com", receiver="kim@partner.co.kr", subject="Re: 견적 요청",
                     content="회신", direction=EmailDirection.OUTBOUND, processed=True, triage="sent",
                     task_id=tid, gmail_message_id=mid, gmail_thread_id="thr-1", gmail_meta={}))
        db.add(ReplyDraft(user_id=uid, task_id=tid, to_addr="kim@partner.co.kr", subject="Re: 견적 요청",
                          body="회신", status="sent", gmail_message_id=mid))
        t = db.get(Task, tid)
        t.status = TaskStatus.WAITING
        db.add(AgentActionLog(user_id=uid, task_id=tid, action_type="REPLY_SENT", output_data={}))
        db.commit()
    _run(aid, google_fake)
    assert len(_rows()) == 1
    assert _count_logs(tid) == 1


def test_unknown_draft_resolved_by_sync(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    start = NOW - timedelta(minutes=2)
    with SessionLocal() as db:
        db.add(ReplyDraft(user_id=uid, task_id=tid, to_addr="Kim@Partner.co.kr", subject="견적 요청",
                          body="회신", status="unknown", sending_at=start))
        db.commit()
    mid = _sent(google_fake, "thr-1", subject="RE: 견적 요청", to="kim@partner.co.kr", when=NOW)
    _run(aid, google_fake)
    with SessionLocal() as db:
        d = db.scalar(select(ReplyDraft).where(ReplyDraft.task_id == tid))
        assert d.status == "sent" and d.gmail_message_id == mid


def test_unknown_draft_not_resolved_when_recipient_differs(account, google_fake):
    aid, uid = account
    tid = _task(uid)
    with SessionLocal() as db:
        db.add(ReplyDraft(user_id=uid, task_id=tid, to_addr="kim@partner.co.kr", subject="견적 요청",
                          body="x", status="unknown", sending_at=NOW - timedelta(minutes=1)))
        db.commit()
    _sent(google_fake, "thr-1", subject="Re: 견적 요청", to="someone@else.com")
    _run(aid, google_fake)
    with SessionLocal() as db:
        assert db.scalar(select(ReplyDraft.status).where(ReplyDraft.task_id == tid)) == "unknown"


def test_sent_on_closed_task_keeps_status(account, google_fake):
    aid, uid = account
    tid = _task(uid, status=TaskStatus.COMPLETED)
    _sent(google_fake, "thr-1")
    _run(aid, google_fake)
    assert len(_rows()) == 1
    with SessionLocal() as db:
        assert db.get(Task, tid).status == TaskStatus.COMPLETED
    assert _count_logs(tid) == 0


# ---------------------------------------------------------------- 최종 리뷰: 업무가 생기기 전 보낸 답장 · 보낸 시각

TZ = "Asia/Seoul"
MON = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)  # 월 10:00 KST
TUE = MON + timedelta(days=1)
THU = MON + timedelta(days=3, hours=4)
FRI = MON + timedelta(days=4)


def _inbound(fake, thread, when, subject="견적 요청"):
    raw = make_raw(sender="kim@partner.co.kr", to="me@gmail.com", subject=subject, body="견적서를 보내 주세요.")
    return fake.deliver(raw, labels=("INBOX",), thread_id=thread, internal_date_ms=_ms(when))


def _sync_at(account_id, fake, now):
    with SessionLocal() as db:
        return sync_account(db, db.get(GoogleAccount, account_id), fake, now=now)


def _prime(account_id, fake):
    """첫 동기화로 history 지점을 잡는다(이후 메일은 history로 온다)."""
    _sync_at(account_id, fake, MON - timedelta(days=1))


def _analysis(needs_reply=True):
    return {"summary": "견적 요청", "counterpart": {"name": "김", "organization": "P"},
            "requests": ["견적"], "todos": [{"title": "견적 보내기", "due_text": "", "due_date": "", "due_time": ""}],
            "deadline": {"text": "", "date": "", "time": "", "kind": "none"},
            "priority": "NORMAL", "needs_reply": needs_reply, "suspicious": []}


def _intake(uid, llm):
    from app.mail.intake import intake_work_mails
    with SessionLocal() as db:
        for e in db.scalars(select(Email).where(Email.user_id == uid, Email.direction == EmailDirection.INBOUND)):
            e.triage = "work"
        db.commit()
        return intake_work_mails(db, uid, llm, max_mails=5, tz=TZ)


def _utc_(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@pytest.mark.parametrize("sent_first", [False, True])
def test_sent_before_task_is_stored_and_intake_marks_waiting(account, google_fake, sent_first):
    """앱이 꺼진 동안 받은 메일(09:00)에 폰으로 답장(09:10)했다. 한 묶음으로 동기화돼도 보낸 답장을 버리지 않고,
    접수가 업무를 만들 때 '답장 보냄'으로 둔다. 목록 순서(보낸 메일이 먼저)와 관계없다."""
    aid, uid = account
    _prime(aid, google_fake)
    t_in, t_out = MON, MON + timedelta(minutes=10)
    if sent_first:
        _sent(google_fake, "thr-x", when=t_out)
        _inbound(google_fake, "thr-x", t_in)
    else:
        _inbound(google_fake, "thr-x", t_in)
        _sent(google_fake, "thr-x", when=t_out)
    r = _sync_at(aid, google_fake, MON + timedelta(hours=1))
    assert r.stored == 1 and r.sent_stored == 1 and r.skipped_sent == 0
    out = [e for e in _rows() if e.direction == EmailDirection.OUTBOUND.value]
    assert len(out) == 1 and out[0].task_id is None and out[0].triage == "sent" and out[0].processed is True

    _intake(uid, ScriptedLLM(structured_steps=[_analysis()]))
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.user_id == uid, Task.gmail_thread_id == "thr-x"))
        assert task.status == TaskStatus.WAITING and task.current_step == "답장 보냄"
        assert _utc_(task.reply_sent_at) == t_out and _utc_(task.wait_from) == t_out
        assert db.scalar(select(Email.task_id).where(Email.user_id == uid, Email.gmail_message_id.is_not(None),
                                                     Email.direction == EmailDirection.OUTBOUND)) == task.id
        assert task.brief["analysis"] == "done" and task.brief["summary"] == "견적 요청"


def test_sent_older_than_inbound_does_not_mark_waiting_at_intake(account, google_fake):
    """보낸 답장 뒤에 상대가 또 보냈으면(받은 메일이 더 최신): 첫 메일 분석은 답장 보냄으로 두고, 뒤에 온 메일은
    변경 분석에 맡긴다. 다음 주기 변경 분석이 답장이 필요하다고 보면 처리할 일로 올린다."""
    aid, uid = account
    _prime(aid, google_fake)
    _inbound(google_fake, "thr-x", MON)
    _sent(google_fake, "thr-x", when=MON + timedelta(minutes=10))
    _inbound(google_fake, "thr-x", MON + timedelta(minutes=30), subject="Re: 견적 요청")
    _sync_at(aid, google_fake, MON + timedelta(hours=1))
    _intake(uid, ScriptedLLM(structured_steps=[_analysis()]))
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.user_id == uid, Task.gmail_thread_id == "thr-x"))
        assert task.status == TaskStatus.WAITING and len(task.brief["changes_pending_email_ids"]) == 1
    _intake(uid, ScriptedLLM(structured_steps=[{"changes": [], "needs_reply": True, "suspicious": []}]))
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.user_id == uid, Task.gmail_thread_id == "thr-x"))
        assert task.status == TaskStatus.ACTION_REQUIRED


def test_sent_on_unknown_thread_still_skipped_with_other_inbound(account, google_fake):
    """받은 메일이 있는 스레드가 아니면(다른 스레드에만 받은 메일) 보낸 메일은 여전히 저장하지 않는다."""
    aid, uid = account
    _prime(aid, google_fake)
    _inbound(google_fake, "thr-x", MON)
    _sent(google_fake, "thr-y", when=MON + timedelta(minutes=10))
    r = _sync_at(aid, google_fake, MON + timedelta(hours=1))
    assert r.sent_stored == 0 and r.skipped_sent == 1
    assert all(e.direction == EmailDirection.INBOUND.value for e in _rows())


def test_late_analysis_keeps_waiting_when_reply_sent_after_inbound(account, google_fake):
    """분석이 늦게 끝나도(분석 대기 중 답장을 보냄) '답장 보냄'을 처리할 일로 덮지 않는다. 요약 · 할 일은 채운다."""
    aid, uid = account
    _prime(aid, google_fake)
    _inbound(google_fake, "thr-x", MON)
    _sync_at(aid, google_fake, MON + timedelta(minutes=5))
    _intake(uid, ScriptedLLM(structured_steps=[RuntimeError("bad")]))  # 형식 오류: 분석 대기로 남는다
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.user_id == uid, Task.gmail_thread_id == "thr-x"))
        assert task.brief["analysis"] == "pending"
    _sent(google_fake, "thr-x", when=MON + timedelta(minutes=10))
    _sync_at(aid, google_fake, MON + timedelta(minutes=20))
    _intake(uid, ScriptedLLM(structured_steps=[_analysis()]))
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.user_id == uid, Task.gmail_thread_id == "thr-x"))
        assert task.brief["analysis"] == "done" and task.brief["summary"] == "견적 요청"
        assert task.status == TaskStatus.WAITING and task.current_step == "답장 보냄"
        assert [p["title"] for p in task.plan] == ["견적 보내기"]


def test_gmail_sent_uses_mail_time_not_sync_time(account, google_fake):
    """월요일에 답장, 화요일에 상대 회신, 목요일에 앱을 켜 동기화: 기다림 시계는 월요일부터, '답 없음'을 묻지 않는다."""
    from app.mail.lifecycle import check_no_reply
    aid, uid = account
    _prime(aid, google_fake)
    tid = _task(uid)
    _sent(google_fake, "thr-1", when=MON)
    _inbound(google_fake, "thr-1", TUE, subject="Re: 견적 요청")
    _sync_at(aid, google_fake, THU)
    with SessionLocal() as db:
        t = db.get(Task, tid)
        assert _utc_(t.reply_sent_at) == MON and _utc_(t.wait_from) == MON
        assert check_no_reply(db, uid, THU, TZ) == 0
        assert db.get(Task, tid).no_reply_asked_at is None


def test_gmail_sent_without_answer_asks_on_time(account, google_fake):
    """월요일에 답장하고 상대가 답이 없으면, 금요일에 처음 동기화해도 바로 '답 없음'을 묻는다(동기화 시각부터 세지 않는다)."""
    from app.mail.lifecycle import check_no_reply
    aid, uid = account
    _prime(aid, google_fake)
    tid = _task(uid)
    _sent(google_fake, "thr-1", when=MON)
    _sync_at(aid, google_fake, FRI)
    with SessionLocal() as db:
        assert check_no_reply(db, uid, FRI, TZ) == 1
        assert db.get(Task, tid).no_reply_asked_at is not None


def test_older_gmail_sent_does_not_move_reply_time_back(account, google_fake):
    """이미 더 늦은 답장 보냄 시각이 있으면, 늦게 발견된 옛 보낸 메일이 시각을 되돌리지 않는다."""
    aid, uid = account
    _prime(aid, google_fake)
    tid = _task(uid, status=TaskStatus.WAITING, reply_sent_at=TUE, wait_from=TUE)
    _sent(google_fake, "thr-1", when=MON)
    _sync_at(aid, google_fake, THU)
    with SessionLocal() as db:
        t = db.get(Task, tid)
        assert _utc_(t.reply_sent_at) == TUE and _utc_(t.wait_from) == TUE
    assert _count_logs(tid) == 0


# ---------------------------------------------------------------- 최종 리뷰: 결과 불분명 답장과 두 번 발송

def _draft(uid, tid, status, sending_at, to="kim@partner.co.kr"):
    with SessionLocal() as db:
        d = ReplyDraft(user_id=uid, task_id=tid, to_addr=to, subject="Re: 견적 요청", body="회신",
                       status=status, sending_at=sending_at)
        db.add(d)
        db.commit()
        return d.id


def test_unknown_draft_resolved_when_gmail_time_slightly_before_sending(account, google_fake):
    """Gmail 시각(internalDate)이 서버 시계보다 1초 앞서도 결과 불분명 초안과 맞춘다(앞쪽 2분 허용)."""
    aid, uid = account
    tid = _task(uid)
    start = NOW - timedelta(minutes=6)
    did = _draft(uid, tid, "unknown", start)
    mid = _sent(google_fake, "thr-1", when=start - timedelta(seconds=1))
    _run(aid, google_fake)
    with SessionLocal() as db:
        d = db.get(ReplyDraft, did)
        assert d.status == "sent" and d.gmail_message_id == mid


def test_sending_draft_resolved_by_match(account, google_fake):
    """발송 중(sending)에 멈춘 초안도 동기화가 찾은 보낸 메일과 맞으면 보냄으로 바뀐다."""
    aid, uid = account
    tid = _task(uid)
    start = NOW - timedelta(minutes=1)
    did = _draft(uid, tid, "sending", start)
    mid = _sent(google_fake, "thr-1", when=start + timedelta(seconds=3))
    _run(aid, google_fake)
    with SessionLocal() as db:
        d = db.get(ReplyDraft, did)
        assert d.status == "sent" and d.gmail_message_id == mid


def test_match_window_rejects_mail_long_before_sending(account, google_fake):
    """보내기 3분 전에 보낸 메일은 이 초안이 보낸 것이 아니다."""
    aid, uid = account
    tid = _task(uid)
    start = NOW - timedelta(minutes=1)
    did = _draft(uid, tid, "unknown", start)
    _sent(google_fake, "thr-1", when=start - timedelta(minutes=3))
    _run(aid, google_fake)
    with SessionLocal() as db:
        assert db.get(ReplyDraft, did).status == "unknown"


def test_not_sent_answer_refused_when_sync_found_reply(account, google_fake):
    """동기화가 보낸 답장을 확인했지만 초안과 맞추지 못한 경우(받는 사람이 다름): '안 보내졌어요'는 409로 막는다.
    초안으로 되돌리면 updated_at이 늦어져 다시 보내기 막기가 풀리기 때문이다."""
    from app.mail import reply_send as rs
    from app.mail.errors import ActionError
    from app.models import User
    aid, uid = account
    tid = _task(uid)
    start = NOW - timedelta(minutes=6)
    did = _draft(uid, tid, "unknown", start)
    _sent(google_fake, "thr-1", to="someone@else.com", when=start + timedelta(seconds=2))
    _run(aid, google_fake)
    with SessionLocal() as db:
        task, d = db.get(Task, tid), db.get(ReplyDraft, did)
        assert d.status == "unknown" and task.reply_sent_at is not None
        with pytest.raises(ActionError) as e:
            rs.confirm_unknown(db, db.get(User, uid), task, d, sent=False, now=NOW)
        assert e.value.status == 409
        assert e.value.args[0] == "Gmail에서 이 답장이 이미 보내진 것으로 확인됩니다. 보낸편지함을 확인해 주세요."
        db.rollback()
        assert db.get(ReplyDraft, did).status == "unknown"


def test_not_sent_answer_allowed_when_reply_was_before_send(account, google_fake):
    """보내기 훨씬 전에 보낸 답장(이전 답장)은 '안 보내졌어요'를 막지 않는다."""
    from app.mail import reply_send as rs
    from app.models import User
    aid, uid = account
    tid = _task(uid, status=TaskStatus.WAITING, reply_sent_at=NOW - timedelta(days=1))
    did = _draft(uid, tid, "unknown", NOW - timedelta(minutes=6))
    with SessionLocal() as db:
        rs.confirm_unknown(db, db.get(User, uid), db.get(Task, tid), db.get(ReplyDraft, did), sent=False, now=NOW)
        db.commit()
        assert db.get(ReplyDraft, did).status == "draft"
