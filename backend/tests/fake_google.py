"""In-memory Google stand-in for tests (implements the GoogleClient protocol)."""

import re
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

from app.google.client import (
    REQUIRED_SCOPES, AccessToken, CalendarEvent, GmailProfile, GoogleAuthError, GoogleConflict, GoogleError,
    GoogleHistoryGone, GoogleNotFound, GoogleTransientError, IdPage, RawMessage, SentMessage, TokenGrant,
)


def make_raw(*, sender, to, subject, body="", html=None, cc=None, message_id=None, headers=None,
             attachments=(), charset="utf-8") -> bytes:
    """Build an RFC 822 message. attachments: iterable of (filename, bytes, mimetype 'maintype/subtype')."""
    m = EmailMessage()
    m["From"] = sender
    m["To"] = to
    m["Subject"] = subject
    if cc:
        m["Cc"] = cc
    if message_id:
        m["Message-ID"] = message_id
    for k, v in (headers or {}).items():
        m[k] = v
    if body or html is None:
        m.set_content(body, charset=charset)
    if html is not None:
        if body or m.get_content_type() != "text/plain":
            m.add_alternative(html, subtype="html", charset=charset)
        else:
            m.set_content(html, subtype="html", charset=charset)
    for filename, data, mimetype in attachments:
        maintype, _, subtype = mimetype.partition("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    return m.as_bytes()


def _merge_patch(current: dict, patch: dict) -> dict:
    """Google PATCH처럼 합친다: 중첩 객체는 재귀로 합치고, None은 필드 삭제, 그 밖(배열 포함)은 교체."""
    out = dict(current)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict):
            base = out.get(key)
            out[key] = _merge_patch(base if isinstance(base, dict) else {}, value)
        else:
            out[key] = value
    return out


class FakeGoogle:
    def __init__(self, email: str = "me@gmail.com"):
        self.email = email
        self.mailbox: dict[str, RawMessage] = {}
        self.history_id: int = 1000
        self._history: list[tuple[int, str]] = []
        self.valid_refresh_tokens: set[str] = set()
        self.grant = TokenGrant(access_token="at-0", refresh_token="1//fake-refresh", expires_in=3600,
                                scopes=list(REQUIRED_SCOPES))
        self.history_gone = False
        self.revoked: list[str] = []
        self.revoke_fails = False
        self.get_calls: list[str] = []
        self.events: dict[str, dict] = {}
        self.deleted_events: dict[str, dict] = {}  # 사용자가 지운 일정. Google처럼 본문(표식 포함)을 남긴다
        self.calendar_fail: GoogleError | None = None
        self.calendar_fail_times: int = 0
        self.calendar_calls: list[tuple[str, str]] = []
        self.sent: list[tuple[bytes, str]] = []  # gmail_send 기록: (raw, thread_id)
        self.gmail_send_fail: Exception | None = None  # 설정하면 gmail_send가 보내지 않고 이 예외를 낸다
        self._seq = 0
        self._tokens = 0

    def deliver(self, raw: bytes, *, labels=("INBOX",), thread_id=None, internal_date_ms: int | None = None) -> str:
        self._seq += 1
        mid = f"msg{self._seq:04d}"
        ms = int(time.time() * 1000) if internal_date_ms is None else internal_date_ms
        self.mailbox[mid] = RawMessage(id=mid, thread_id=thread_id or mid, label_ids=list(labels),
                                       internal_date_ms=ms, raw=raw)
        self.history_id += 1
        self._history.append((self.history_id, mid))
        return mid

    # --- OAuth ---
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        q = urlencode({"state": state, "code_challenge": code_challenge, "redirect_uri": redirect_uri,
                       "scope": " ".join(REQUIRED_SCOPES)})
        return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenGrant:
        if self.grant.refresh_token is not None:
            self.valid_refresh_tokens.add(self.grant.refresh_token)
        return self.grant

    def refresh_access_token(self, refresh_token: str) -> AccessToken:
        if refresh_token not in self.valid_refresh_tokens:
            raise GoogleAuthError("invalid_grant")
        self._tokens += 1
        return AccessToken(f"at-{self._tokens}", datetime.now(timezone.utc) + timedelta(hours=1))

    def revoke(self, token: str) -> None:
        if self.revoke_fails:
            raise GoogleTransientError("revoke failed")
        self.revoked.append(token)

    # --- Gmail ---
    def gmail_profile(self, access_token: str) -> GmailProfile:
        return GmailProfile(email=self.email, history_id=str(self.history_id))

    def gmail_history(self, access_token: str, start_history_id: str, page_token: str | None) -> IdPage:
        if self.history_gone:
            raise GoogleHistoryGone("history gone")
        start = int(start_history_id)
        ids: list[str] = []
        for hid, mid in self._history:
            if hid > start and mid not in ids:
                ids.append(mid)
        return IdPage(message_ids=ids, next_page_token=None, history_id=str(self.history_id))

    def gmail_list(self, access_token: str, query: str, page_token: str | None, page_size: int = 100) -> IdPage:
        m = re.search(r"after:(\d+)", query)
        after_ms = int(m.group(1)) * 1000 if m else None
        ids = [r.id for r in self.mailbox.values() if after_ms is None or r.internal_date_ms >= after_ms]
        offset = int(page_token) if page_token else 0
        page = ids[offset:offset + page_size]
        nxt = str(offset + page_size) if offset + page_size < len(ids) else None
        return IdPage(message_ids=page, next_page_token=nxt)

    def gmail_get_raw(self, access_token: str, message_id: str) -> RawMessage:
        self.get_calls.append(message_id)
        if message_id not in self.mailbox:
            raise GoogleError(f"message not found: {message_id}")
        return self.mailbox[message_id]

    def gmail_send(self, access_token: str, raw_rfc822: bytes, thread_id: str) -> SentMessage:
        if self.gmail_send_fail is not None:
            raise self.gmail_send_fail
        mid = self.deliver(raw_rfc822, labels=("SENT",), thread_id=thread_id)
        self.sent.append((raw_rfc822, thread_id))
        return SentMessage(id=mid, thread_id=thread_id)

    # --- Calendar ---
    def _calendar_enter(self, method: str, event_id: str) -> None:
        self.calendar_calls.append((method, event_id))
        if self.calendar_fail is not None:
            err = self.calendar_fail
            if self.calendar_fail_times > 1:
                self.calendar_fail_times -= 1
            else:
                self.calendar_fail = None
                self.calendar_fail_times = 0
            raise err

    def _event(self, event_id: str, status: str = "confirmed", body: dict | None = None) -> CalendarEvent:
        private = ((body or {}).get("extendedProperties") or {}).get("private") or {}
        return CalendarEvent(id=event_id, html_link=f"https://calendar.example/event/{event_id}", status=status,
                             private=dict(private))

    def calendar_insert(self, access_token: str, body: dict) -> CalendarEvent:
        self._seq += 1
        event_id = body.get("id") or f"evt{self._seq:04d}"
        self._calendar_enter("insert", event_id)
        if event_id in self.events or event_id in self.deleted_events:
            raise GoogleConflict("event id already exists")
        self.events[event_id] = {**body, "id": event_id}
        return self._event(event_id, body=self.events[event_id])

    def calendar_get(self, access_token: str, event_id: str) -> CalendarEvent:
        self._calendar_enter("get", event_id)
        if event_id in self.deleted_events:
            return self._event(event_id, "cancelled", self.deleted_events[event_id])
        if event_id not in self.events:
            raise GoogleNotFound("event not found")
        ev = self.events[event_id]
        return self._event(event_id, ev.get("status", "confirmed"), ev)

    def calendar_patch(self, access_token: str, event_id: str, body: dict) -> CalendarEvent:
        self._calendar_enter("patch", event_id)
        if event_id in self.deleted_events and body.get("status") == "confirmed":
            # Google처럼 status=confirmed로 patch하면 취소된 일정이 되살아난다
            self.events[event_id] = self.deleted_events.pop(event_id)
        if event_id in self.deleted_events or event_id not in self.events:
            raise GoogleNotFound("event not found")
        merged = _merge_patch(self.events[event_id], body)
        for key in ("start", "end"):
            part = merged.get(key) or {}
            if "date" in part and "dateTime" in part:
                raise GoogleError("400 invalid start/end: both date and dateTime")
        self.events[event_id] = merged
        return self._event(event_id, merged.get("status", "confirmed"), merged)

    def calendar_delete(self, access_token: str, event_id: str) -> None:
        self._calendar_enter("delete", event_id)
        if event_id in self.deleted_events or event_id not in self.events:
            raise GoogleNotFound("event not found")
        self.deleted_events[event_id] = self.events.pop(event_id)

    def delete_event(self, event_id: str) -> None:
        """Simulate the user deleting the event in Google Calendar."""
        self.deleted_events[event_id] = self.events.pop(event_id, {"id": event_id})
