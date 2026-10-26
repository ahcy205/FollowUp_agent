"""RFC 822 raw message -> ParsedMail (stdlib only). Never raises on input the email parser accepts."""

import email
import email.policy
import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

SUBJECT_MAX = 300
TEXT_MAX = 20_000


@dataclass
class ParsedMail:
    sender_email: str = ""
    sender_name: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    subject: str = ""
    text: str = ""
    rfc_message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    list_unsubscribe: bool = False
    precedence: str = ""
    attachments: list[str] = field(default_factory=list)
    sent_at: datetime | None = None


_BLOCK_TAGS = {"br", "p", "div", "li", "tr"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = htmllib.unescape("".join(p.parts)).replace("\xa0", " ")
    return "\n".join(ln for ln in (s.strip() for s in text.splitlines()) if ln)


_QUOTE_STARTS = [
    re.compile(r"^On .+wrote:$"),
    re.compile(r"^\d{4}년 \d{1,2}월 \d{1,2}일 .+(님이 작성|작성):$"),
    re.compile(r"^-{2,}\s*(Original Message|원본 메시지)\s*-{2,}"),
]
_FROM = re.compile(r"^From: .+")
_FROM_NEXT = re.compile(r"^(Sent|Date|보낸 날짜):")


def strip_quoted(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if any(p.match(s) for p in _QUOTE_STARTS):
            break
        if _FROM.match(s) and i + 1 < len(lines) and _FROM_NEXT.match(lines[i + 1].strip()):
            break
        kept.append(line)
    return "\n".join(ln for ln in kept if not ln.lstrip().startswith(">")).strip()


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = (part.get_content_charset() or "utf-8").lower()
    if charset.replace("_", "-") in ("ks-c-5601-1987", "ksc5601", "euc-kr", "euckr", "cp949", "ms949"):
        charset = "cp949"  # superset of EUC-KR; Outlook/Naver send cp949 chars under these labels
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", errors="replace")


def _body_text(msg: Message) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        raw = _decode_payload(part)
    except Exception:
        return ""
    return html_to_text(raw) if part.get_content_type() == "text/html" else raw


def _hdr(msg: Message, name: str) -> str:
    try:
        v = msg.get(name)
        return "" if v is None else str(v).strip()
    except Exception:
        return ""


def _addresses(msg: Message, name: str) -> list[tuple[str, str]]:
    try:
        values = [str(v) for v in (msg.get_all(name) or [])]
    except Exception:
        return []
    return [(n.strip(), a.strip().lower()) for n, a in getaddresses(values) if a.strip()]


def _sent_at(msg: Message) -> datetime | None:
    v = _hdr(msg, "Date")
    if not v:
        return None
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt


def parse_raw(raw: bytes) -> ParsedMail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    senders = _addresses(msg, "From")
    name, addr = senders[0] if senders else ("", "")
    attachments: list[str] = []
    try:
        for part in msg.walk():
            fn = part.get_filename()
            if fn:
                attachments.append(str(fn))
    except Exception:
        pass
    text = strip_quoted(_body_text(msg))[:TEXT_MAX].strip()
    return ParsedMail(
        sender_email=addr,
        sender_name=name,
        to=[a for _, a in _addresses(msg, "To")],
        cc=[a for _, a in _addresses(msg, "Cc")],
        subject=" ".join(_hdr(msg, "Subject").split())[:SUBJECT_MAX],
        text=text,
        rfc_message_id=_hdr(msg, "Message-ID") or None,
        in_reply_to=_hdr(msg, "In-Reply-To") or None,
        references=_hdr(msg, "References").split(),
        list_unsubscribe=bool(_hdr(msg, "List-Unsubscribe")),
        precedence=_hdr(msg, "Precedence").lower(),
        attachments=attachments,
        sent_at=_sent_at(msg),
    )
