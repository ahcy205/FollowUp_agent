"""Gmail sync for one account: backfill, incremental history, 404 fallback, lease, dedupe.

Logs carry Gmail message ids and counts only: never subjects, bodies, addresses or tokens.
The background worker (wake) arrives in a later task.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.google.client import (
    GoogleAuthError, GoogleClient, GoogleError, GoogleHistoryGone, GoogleTransientError,
)
from app.google.oauth import access_token_for, forget_access_token
from app.mail.parse import parse_raw
from app.models import Email, EmailDirection, GoogleAccount

log = logging.getLogger(__name__)

TRANSIENT_MESSAGE = "Gmail 연결이 잠시 불안정합니다. 잠시 후 다시 시도합니다."
GMAIL_AUTH_MESSAGE = "Gmail 인증이 거부되었습니다. 다음 동기화에서 다시 시도합니다."
ERROR_MESSAGE = "Gmail 동기화 중 오류가 발생했습니다."
ADDRESS_MAX = 255
_ID_CHUNK = 500


@dataclass
class SyncResult:
    listed: int
    stored: int
    skipped_sent: int
    complete: bool


def _utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes (stored as UTC); PostgreSQL returns aware ones. Normalize both."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# --- lease ---

def acquire_lease(db: Session, account_id: int, now: datetime, seconds: int = 300) -> bool:
    """Take the sync lease only if it is free or expired (single conditional UPDATE). True if taken.

    Datetimes are normalized to UTC so the comparison is by instant on both SQLite (stores naive UTC
    text) and PostgreSQL (timestamptz).
    """
    now = _utc(now)
    res = db.execute(
        update(GoogleAccount)
        .where(GoogleAccount.id == account_id,
               or_(GoogleAccount.sync_lease_until.is_(None), GoogleAccount.sync_lease_until < now))
        .values(sync_lease_until=now + timedelta(seconds=seconds))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return res.rowcount == 1


def release_lease(db: Session, account_id: int) -> None:
    db.execute(
        update(GoogleAccount).where(GoogleAccount.id == account_id).values(sync_lease_until=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()


# --- ids fetched but deliberately not stored (sent/draft/vanished/unparseable) ---
# Without this, such ids are never in the DB, stay at the front of the list every cycle and, once there
# are >= cap of them, the cycle never completes. In memory only: after a restart each cycle re-adds what
# it fetches, so progress is still guaranteed. Cleared when the account's history point advances.

_skipped: dict[int, set[str]] = {}
_skipped_lock = threading.Lock()


def _skipped_ids(account_id: int) -> set[str]:
    with _skipped_lock:
        return set(_skipped.get(account_id, ()))


def _mark_skipped(account_id: int, message_id: str) -> None:
    with _skipped_lock:
        _skipped.setdefault(account_id, set()).add(message_id)


def clear_skipped(account_id: int | None = None) -> None:
    with _skipped_lock:
        if account_id is None:
            _skipped.clear()
        else:
            _skipped.pop(account_id, None)


# --- id listing ---

def _list_all(client: GoogleClient, token: str, query: str) -> list[str]:
    ids: list[str] = []
    page: str | None = None
    while True:
        p = client.gmail_list(token, query, page)
        ids.extend(p.message_ids)
        page = p.next_page_token
        if not page:
            return ids


def _history_all(client: GoogleClient, token: str, start: str) -> tuple[list[str], str]:
    ids: list[str] = []
    page: str | None = None
    hid = start
    while True:
        p = client.gmail_history(token, start, page)
        ids.extend(p.message_ids)
        if p.history_id:
            hid = p.history_id
        page = p.next_page_token
        if not page:
            return ids, hid


def _backfill(client: GoogleClient, token: str, since: datetime) -> tuple[list[str], str]:
    hid = client.gmail_profile(token).history_id  # take the point first so nothing arriving now is missed
    return _list_all(client, token, f"after:{int(since.timestamp())} in:inbox -in:spam -in:trash"), hid


def _collect_ids(account: GoogleAccount, client: GoogleClient, token: str,
                 now: datetime) -> tuple[list[str], str]:
    backfill_start = now - timedelta(days=get_settings().mail_backfill_days)
    if not account.history_id:
        return _backfill(client, token, backfill_start)
    try:
        return _history_all(client, token, account.history_id)
    except GoogleHistoryGone:
        log.info("gmail history expired for account %s; re-listing by date", account.id)
        base = _utc(account.last_synced_at) if account.last_synced_at else backfill_start
        return _backfill(client, token, base - timedelta(days=1))


def _already_stored(db: Session, user_id: int, ids: list[str]) -> set[str]:
    have: set[str] = set()
    for i in range(0, len(ids), _ID_CHUNK):
        chunk = ids[i:i + _ID_CHUNK]
        have.update(db.scalars(select(Email.gmail_message_id)
                               .where(Email.user_id == user_id, Email.gmail_message_id.in_(chunk))))
    return have


# --- one message ---

def _build_email(user_id: int, msg) -> Email:
    p = parse_raw(msg.raw)
    received = datetime.fromtimestamp(msg.internal_date_ms / 1000, tz=timezone.utc)
    return Email(
        user_id=user_id, customer_id=None, task_id=None,
        sender=p.sender_email[:ADDRESS_MAX],
        receiver=", ".join(p.to)[:ADDRESS_MAX],
        subject=p.subject, content=p.text,
        direction=EmailDirection.INBOUND, processed=False,
        sent_at=p.sent_at or received, received_at=received,
        gmail_message_id=msg.id, gmail_thread_id=msg.thread_id,
        rfc_message_id=p.rfc_message_id[:500] if p.rfc_message_id else None,
        cc=p.cc, attachments=p.attachments, triage="pending",
        gmail_meta={"labels": list(msg.label_ids), "list_unsubscribe": p.list_unsubscribe,
                    "precedence": p.precedence, "sender_name": p.sender_name},
    )


def _store(db: Session, user_id: int, msg) -> bool:
    """Insert one mail inside a savepoint. False if it was skipped (unparseable, or a concurrent sync
    stored it first). Other database errors propagate: the cycle must stop without advancing history."""
    try:
        row = _build_email(user_id, msg)
    except Exception as e:  # noqa: BLE001 - one unparseable mail must not stop the cycle
        log.warning("gmail message %s skipped: %s", msg.id, type(e).__name__)
        return False
    try:
        with db.begin_nested():  # on error only this savepoint is rolled back
            db.add(row)
    except IntegrityError as e:  # unique (user_id, gmail_message_id): a concurrent sync won the race
        log.info("gmail message %s skipped: %s", msg.id, type(e).__name__)
        return False
    db.commit()  # commit per mail so an interrupted cycle keeps its progress
    return True


# --- sync ---

def _fail(db: Session, account: GoogleAccount, message: str, result: SyncResult) -> SyncResult:
    result.complete = False
    try:
        db.rollback()
        account.last_sync_error = message
        db.commit()
    except SQLAlchemyError as e:  # the database itself is unavailable; nothing more to record
        db.rollback()
        log.warning("gmail sync for account %s: could not record error: %s", account.id, type(e).__name__)
    return result


def sync_account(db: Session, account: GoogleAccount, client: GoogleClient,
                 now: datetime | None = None) -> SyncResult:
    """Run one sync cycle for one account. Never raises Google errors: records state and returns."""
    now = _utc(now) if now else datetime.now(timezone.utc)
    result = SyncResult(listed=0, stored=0, skipped_sent=0, complete=False)
    try:
        token = access_token_for(db, account, client)
    except GoogleAuthError:
        return result  # access_token_for already marked the account reauth_required
    except GoogleTransientError:
        log.info("gmail sync for account %s: token refresh failed (transient)", account.id)
        return _fail(db, account, TRANSIENT_MESSAGE, result)
    except GoogleError as e:
        log.warning("gmail sync for account %s: token refresh failed: %s", account.id, type(e).__name__)
        return _fail(db, account, ERROR_MESSAGE, result)
    try:
        ids, new_hid = _collect_ids(account, client, token, now)
        ids = list(dict.fromkeys(ids))
        result.listed = len(ids)
        have = _already_stored(db, account.user_id, ids)
        skipped = _skipped_ids(account.id)
        remaining = [i for i in ids if i not in have and i not in skipped]
        cap = max(1, get_settings().mail_sync_max_per_cycle)
        batch, deferred = remaining[:cap], remaining[cap:]
        for mid in batch:
            try:
                msg = client.gmail_get_raw(token, mid)
            except (GoogleTransientError, GoogleAuthError):
                raise
            except GoogleError:
                log.info("gmail message %s could not be fetched; skipped", mid)
                _mark_skipped(account.id, mid)
                continue
            if "INBOX" not in msg.label_ids or "SENT" in msg.label_ids:
                result.skipped_sent += 1
                _mark_skipped(account.id, mid)
                continue
            if _store(db, account.user_id, msg):
                result.stored += 1
            else:
                _mark_skipped(account.id, mid)
    except GoogleTransientError:
        log.info("gmail sync for account %s interrupted (transient); stored %d", account.id, result.stored)
        return _fail(db, account, TRANSIENT_MESSAGE, result)
    except GoogleAuthError:
        forget_access_token(account.user_id)  # the next cycle refreshes; a revoked grant is caught there
        log.info("gmail sync for account %s: access token rejected; stored %d", account.id, result.stored)
        return _fail(db, account, GMAIL_AUTH_MESSAGE, result)
    except GoogleError as e:
        log.warning("gmail sync for account %s failed: %s", account.id, type(e).__name__)
        return _fail(db, account, ERROR_MESSAGE, result)
    except SQLAlchemyError as e:  # e.g. database locked / connection lost: keep history so nothing is lost
        log.warning("gmail sync for account %s stopped by database error: %s; stored %d",
                    account.id, type(e).__name__, result.stored)
        return _fail(db, account, ERROR_MESSAGE, result)

    account.last_sync_error = ""
    if not deferred:
        account.history_id = new_hid
        account.last_synced_at = now
        result.complete = True
    db.commit()
    if result.complete:
        clear_skipped(account.id)
    log.info("gmail sync for account %s: listed %d, stored %d, skipped %d, deferred %d",
             account.id, result.listed, result.stored, result.skipped_sent, len(deferred))
    return result


def wake() -> None:
    """Wake the background sync worker. Implemented in a later task; a no-op for now."""
