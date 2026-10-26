import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import SessionLocal, engine
from app.google.client import AccessToken, GoogleTransientError
from app.google.crypto import encrypt_token
from app.mail import sync
from app.mail.sync import SyncResult, acquire_lease, release_lease, sync_account
from app.models import Email, EmailDirection, GoogleAccount, GoogleAccountStatus
from tests.conftest import register
from tests.fake_google import make_raw

DAY_MS = 86_400_000


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture
def account(client, google_fake) -> int:
    """A registered user with a connected GoogleAccount row. Returns the account id."""
    me = register(client)
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    with SessionLocal() as db:
        acc = GoogleAccount(user_id=me["id"], google_email="me@gmail.com",
                            refresh_token_enc=encrypt_token("1//fake-refresh"),
                            scopes=["openid"], status=GoogleAccountStatus.CONNECTED)
        db.add(acc)
        db.commit()
        return acc.id


def _run(account_id: int, fake, now: datetime | None = None) -> SyncResult:
    with SessionLocal() as db:
        acc = db.get(GoogleAccount, account_id)
        return sync_account(db, acc, fake, now=now)


def _acc(account_id: int) -> GoogleAccount:
    with SessionLocal() as db:
        return db.get(GoogleAccount, account_id)


def _gmail_rows() -> list[Email]:
    with SessionLocal() as db:
        return list(db.scalars(select(Email).where(Email.gmail_message_id.is_not(None)).order_by(Email.id)))


def _mail(fake, subject="견적 문의", *, labels=("INBOX",), when: datetime | None = None, **kw) -> str:
    kw.setdefault("sender", "홍길동 <hong@acme.co.kr>")
    kw.setdefault("to", "me@gmail.com")
    raw = make_raw(subject=subject, body=kw.pop("body", "본문"), **kw)
    when = when or datetime.now(timezone.utc)
    return fake.deliver(raw, labels=labels, internal_date_ms=_ms(when))


def test_first_sync_backfills_recent_inbox(account, google_fake):
    now = datetime.now(timezone.utc)
    recent = _mail(google_fake, "최근", when=now - timedelta(days=2))
    _mail(google_fake, "오래됨", when=now - timedelta(days=5))
    res = _run(account, google_fake, now=now)
    rows = _gmail_rows()
    assert [r.gmail_message_id for r in rows] == [recent]
    assert rows[0].triage == "pending"
    assert rows[0].direction == EmailDirection.INBOUND
    assert rows[0].processed is False
    assert res.stored == 1 and res.complete is True
    assert _acc(account).history_id == str(google_fake.history_id)


def test_saved_fields(account, google_fake):
    when = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
    mid = google_fake.deliver(
        make_raw(sender="홍길동 <Hong@Acme.co.kr>", to="me@gmail.com, b@x.com", cc="c@x.com", subject="견적",
                 body="안녕하세요", message_id="<abc@acme.co.kr>",
                 headers={"List-Unsubscribe": "<mailto:u@x.com>", "Precedence": "bulk"},
                 attachments=[("견적서.pdf", b"%PDF", "application/pdf")]),
        labels=("INBOX", "UNREAD"), thread_id="thr1", internal_date_ms=_ms(when))
    _run(account, google_fake, now=when + timedelta(hours=1))
    [e] = _gmail_rows()
    assert e.gmail_message_id == mid
    assert e.gmail_thread_id == "thr1"
    assert e.rfc_message_id == "<abc@acme.co.kr>"
    assert e.sender == "hong@acme.co.kr"
    assert e.receiver == "me@gmail.com, b@x.com"
    assert e.cc == ["c@x.com"]
    assert e.subject == "견적"
    assert e.content == "안녕하세요"
    assert e.received_at.replace(tzinfo=timezone.utc) == when
    assert e.attachments == ["견적서.pdf"]
    assert e.gmail_meta == {"labels": ["INBOX", "UNREAD"], "list_unsubscribe": True,
                            "precedence": "bulk", "sender_name": "홍길동"}
    assert e.customer_id is None and e.task_id is None


def test_receiver_truncated_to_255(account, google_fake):
    to = ", ".join(f"person{i:03d}@example-company.co.kr" for i in range(20))
    _mail(google_fake, to=to)
    _run(account, google_fake)
    [e] = _gmail_rows()
    assert len(e.receiver) == 255


def test_incremental_uses_history(account, google_fake, monkeypatch):
    _mail(google_fake, "첫 메일")
    _run(account, google_fake)
    second = _mail(google_fake, "새 메일")
    calls = []
    real_list = google_fake.gmail_list
    monkeypatch.setattr(google_fake, "gmail_list", lambda *a, **k: calls.append("list") or real_list(*a, **k))
    real_history = google_fake.gmail_history
    monkeypatch.setattr(google_fake, "gmail_history", lambda *a, **k: calls.append("history") or real_history(*a, **k))
    res = _run(account, google_fake)
    assert calls == ["history"]
    assert res.stored == 1
    assert _gmail_rows()[-1].gmail_message_id == second
    assert _acc(account).history_id == str(google_fake.history_id)


def test_sent_and_non_inbox_skipped(account, google_fake):
    _mail(google_fake, "보낸", labels=("SENT",))
    _mail(google_fake, "보낸+받은", labels=("INBOX", "SENT"))
    _mail(google_fake, "임시", labels=("DRAFT",))
    ok = _mail(google_fake, "받은")
    res = _run(account, google_fake)
    assert [e.gmail_message_id for e in _gmail_rows()] == [ok]
    assert res.skipped_sent == 3 and res.stored == 1 and res.listed == 4
    assert res.complete is True


def test_dedupe_no_refetch(account, google_fake):
    now = datetime.now(timezone.utc)
    ids = [_mail(google_fake, f"m{i}", when=now - timedelta(hours=i)) for i in range(3)]
    _run(account, google_fake, now=now)
    assert len(_gmail_rows()) == 3
    google_fake.get_calls.clear()
    with SessionLocal() as db:  # restart: forget the history point so the full backfill runs again
        db.get(GoogleAccount, account).history_id = None
        db.commit()
    res = _run(account, google_fake, now=now)
    assert google_fake.get_calls == []
    assert len(_gmail_rows()) == 3
    assert res.listed == 3 and res.stored == 0
    assert sorted(e.gmail_message_id for e in _gmail_rows()) == sorted(ids)


def test_concurrent_insert_skipped_by_unique_constraint(account, google_fake, monkeypatch, caplog):
    """Another sync stores the same mail between our id check and our insert: no duplicate, no crash."""
    a = _mail(google_fake, "a")
    b = _mail(google_fake, "b")
    real_get = google_fake.gmail_get_raw

    def racing_get(token, mid):
        msg = real_get(token, mid)
        if mid == a:
            with SessionLocal() as other:
                uid = other.get(GoogleAccount, account).user_id
                other.add(Email(user_id=uid, sender="x@y.com", receiver="me@gmail.com", subject="other",
                                content="", direction="INBOUND", gmail_message_id=a))
                other.commit()
        return msg

    monkeypatch.setattr(google_fake, "gmail_get_raw", racing_get)
    res = _run(account, google_fake)
    rows = _gmail_rows()
    assert sorted(e.gmail_message_id for e in rows) == sorted([a, b])
    assert res.stored == 1 and res.complete is True


def test_history_gone_falls_back_to_query(account, google_fake, monkeypatch):
    now = datetime.now(timezone.utc)
    old = _mail(google_fake, "기존", when=now - timedelta(hours=2))
    _run(account, google_fake, now=now - timedelta(hours=1))
    synced_at = _acc(account).last_synced_at.replace(tzinfo=timezone.utc)
    new = _mail(google_fake, "새", when=now)
    google_fake.history_gone = True
    queries = []
    real_list = google_fake.gmail_list
    monkeypatch.setattr(google_fake, "gmail_list", lambda t, q, p: queries.append(q) or real_list(t, q, p))
    google_fake.get_calls.clear()
    res = _run(account, google_fake, now=now)
    expected_after = int((synced_at - timedelta(days=1)).timestamp())
    assert queries == [f"after:{expected_after} in:inbox -in:spam -in:trash"]
    assert google_fake.get_calls == [new]
    assert sorted(e.gmail_message_id for e in _gmail_rows()) == sorted([old, new])
    assert res.complete is True
    assert _acc(account).history_id == str(google_fake.history_id)


def test_cap_defers_history_advance(account, google_fake, monkeypatch):
    _run(account, google_fake)  # empty mailbox: sets the history point
    hid = _acc(account).history_id
    for i in range(3):
        _mail(google_fake, f"m{i}")
    monkeypatch.setattr(get_settings(), "mail_sync_max_per_cycle", 2)
    r1 = _run(account, google_fake)
    assert (r1.stored, r1.complete) == (2, False)
    assert _acc(account).history_id == hid
    r2 = _run(account, google_fake)
    assert (r2.stored, r2.complete) == (1, True)
    assert _acc(account).history_id == str(google_fake.history_id) != hid
    assert len(_gmail_rows()) == 3


def test_auth_error_marks_reauth(account, google_fake):
    _mail(google_fake)
    google_fake.valid_refresh_tokens.clear()
    res = _run(account, google_fake)
    assert res.stored == 0 and res.complete is False
    acc = _acc(account)
    assert acc.status == GoogleAccountStatus.REAUTH_REQUIRED
    assert _gmail_rows() == []


def test_transient_error_records_message(account, google_fake, monkeypatch):
    _run(account, google_fake)
    hid = _acc(account).history_id
    first = _mail(google_fake, "a")
    second = _mail(google_fake, "b")
    real_get = google_fake.gmail_get_raw

    def flaky(token, mid):
        if mid == second:
            raise GoogleTransientError("Google 일시 오류(503)")
        return real_get(token, mid)

    monkeypatch.setattr(google_fake, "gmail_get_raw", flaky)
    res = _run(account, google_fake)
    acc = _acc(account)
    assert res.complete is False and res.stored == 1
    assert [e.gmail_message_id for e in _gmail_rows()] == [first]  # progress so far is kept
    assert acc.last_sync_error
    assert acc.history_id == hid


def test_transient_error_while_listing(account, google_fake, monkeypatch):
    def down(*a, **k):
        raise GoogleTransientError("x")

    monkeypatch.setattr(google_fake, "gmail_profile", down)
    res = _run(account, google_fake)
    assert res.complete is False and res.stored == 0
    acc = _acc(account)
    assert acc.last_sync_error and acc.history_id is None and acc.last_synced_at is None


def test_success_clears_error_and_sets_synced_at(account, google_fake):
    with SessionLocal() as db:
        db.get(GoogleAccount, account).last_sync_error = "이전 오류"
        db.commit()
    now = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
    res = _run(account, google_fake, now=now)
    acc = _acc(account)
    assert res.complete is True
    assert acc.last_sync_error == ""
    assert acc.last_synced_at.replace(tzinfo=timezone.utc) == now


def test_vanished_message_skipped(account, google_fake, caplog):
    gone = _mail(google_fake, "삭제됨")
    ok = _mail(google_fake, "정상")
    real_list = google_fake.gmail_list

    def list_then_delete(t, q, p):
        page = real_list(t, q, p)
        google_fake.mailbox.pop(gone)  # deleted between list and get
        return page

    google_fake.gmail_list = list_then_delete
    with caplog.at_level(logging.INFO, logger="app.mail.sync"):
        res = _run(account, google_fake)
    assert [e.gmail_message_id for e in _gmail_rows()] == [ok]
    assert res.complete is True
    assert gone in caplog.text


def test_lease_blocks_second_holder(account):
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        assert acquire_lease(db, account, now) is True
    with SessionLocal() as db:
        assert acquire_lease(db, account, now + timedelta(seconds=10)) is False
    with SessionLocal() as db:
        assert acquire_lease(db, account, now + timedelta(seconds=301)) is True
    with SessionLocal() as db:
        release_lease(db, account)
    with SessionLocal() as db:
        assert acquire_lease(db, account, now + timedelta(seconds=302)) is True


def test_lease_compares_across_timezones(account):
    """A lease taken with a non-UTC aware datetime is compared by instant, not by wall clock."""
    kst = timezone(timedelta(hours=9))
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        assert acquire_lease(db, account, now.astimezone(kst)) is True
    with SessionLocal() as db:
        assert acquire_lease(db, account, now + timedelta(seconds=60)) is False
    with SessionLocal() as db:  # expired by instant (would look 9h in the future if stored as KST wall clock)
        assert acquire_lease(db, account, now + timedelta(seconds=400)) is True


def test_parse_failure_skips_one_mail(account, google_fake, monkeypatch, caplog):
    bad = _mail(google_fake, "깨진 메일 제목", body="비밀 본문", message_id="<broken@acme.co.kr>")
    good = _mail(google_fake, "정상")
    real_parse = sync.parse_raw

    def parse(raw):
        if b"<broken@acme.co.kr>" in raw:  # the one message whose parse fails
            raise ValueError("broken")
        return real_parse(raw)

    monkeypatch.setattr(sync, "parse_raw", parse)
    with caplog.at_level(logging.INFO, logger="app.mail.sync"):
        res = _run(account, google_fake)
    assert [e.gmail_message_id for e in _gmail_rows()] == [good]
    assert res.complete is True
    assert bad in caplog.text
    assert "깨진" not in caplog.text and "비밀" not in caplog.text and "hong@acme" not in caplog.text


# --- fix round 1 ---

def test_transient_token_refresh_recorded(account, google_fake, monkeypatch):
    """No cached token and the token endpoint is down: recorded, not raised."""
    def down(refresh_token: str) -> AccessToken:
        raise GoogleTransientError("Google 일시 오류(503)")

    monkeypatch.setattr(google_fake, "refresh_access_token", down)
    res = _run(account, google_fake)
    acc = _acc(account)
    assert res.complete is False and res.stored == 0
    assert acc.last_sync_error == sync.TRANSIENT_MESSAGE
    assert acc.status == GoogleAccountStatus.CONNECTED
    assert acc.history_id is None


def test_skipped_ids_do_not_block_cap(account, google_fake, monkeypatch):
    """cap=2 with 2 SENT mails listed ahead of 1 INBOX mail: the cycle still completes."""
    _run(account, google_fake)  # empty mailbox: sets the history point
    _mail(google_fake, "s1", labels=("SENT",))
    _mail(google_fake, "s2", labels=("SENT",))
    ok = _mail(google_fake, "inbox")
    monkeypatch.setattr(get_settings(), "mail_sync_max_per_cycle", 2)
    google_fake.get_calls.clear()
    results = []
    for _ in range(3):
        results.append(_run(account, google_fake))
        if results[-1].complete:
            break
    assert results[-1].complete is True
    assert [e.gmail_message_id for e in _gmail_rows()] == [ok]
    assert len(google_fake.get_calls) == len(set(google_fake.get_calls))  # nothing fetched twice
    assert _acc(account).history_id == str(google_fake.history_id)


def test_capped_first_backfill_completes_later(account, google_fake, monkeypatch):
    for i in range(3):
        _mail(google_fake, f"m{i}")
    monkeypatch.setattr(get_settings(), "mail_sync_max_per_cycle", 2)
    r1 = _run(account, google_fake)
    assert (r1.stored, r1.complete) == (2, False)
    assert _acc(account).history_id is None
    r2 = _run(account, google_fake)
    assert (r2.stored, r2.complete) == (1, True)
    assert _acc(account).history_id == str(google_fake.history_id)
    assert len(_gmail_rows()) == 3


def test_database_error_stops_cycle_without_advancing(account, google_fake):
    _run(account, google_fake)
    hid = _acc(account).history_id
    first = _mail(google_fake, "a")
    locked = _mail(google_fake, "b")

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO emails") and locked in str(parameters):
            raise OperationalError(statement, parameters, Exception("database is locked"))

    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        res = _run(account, google_fake)
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)
    acc = _acc(account)
    assert res.complete is False and res.stored == 1
    assert acc.history_id == hid
    assert acc.last_sync_error == sync.ERROR_MESSAGE
    assert [e.gmail_message_id for e in _gmail_rows()] == [first]
    res2 = _run(account, google_fake)  # the lost insert is retried, not skipped forever
    assert res2.complete is True and res2.stored == 1
    assert sorted(e.gmail_message_id for e in _gmail_rows()) == sorted([first, locked])


def test_demo_reset_keeps_gmail_mail_and_connection(client, account, google_fake):
    mid = _mail(google_fake, "실제 메일")
    _run(account, google_fake)
    assert [r.gmail_message_id for r in _gmail_rows()] == [mid]
    seed_count = len([e for e in client.get("/api/emails").json()]) - 1
    assert client.post("/api/demo/reset").status_code == 204
    assert [r.gmail_message_id for r in _gmail_rows()] == [mid]
    acc = _acc(account)
    assert acc is not None and acc.status == GoogleAccountStatus.CONNECTED and acc.history_id
    with SessionLocal() as db:
        seed_rows = list(db.scalars(select(Email).where(Email.gmail_message_id.is_(None))))
    assert len(seed_rows) == seed_count == 8
