"""답장 초안 경고를 코드로 다시 확인한다 (설계 2.4). 막지 않고 경고만 한다.

- 숫자 · 날짜: 초안과 근거 글(메모 · 사용자 입력 · 스레드 본문)을 같은 방식으로 토큰으로 나눠 집합으로 비교한다.
  부분 문자열로 비교하면 "15일"이 "2015"나 "150개" 속 숫자로 근거가 있는 것처럼 보이기 때문이다.
  - 날짜는 한 형태(M/D)로 맞춘다: "11월 15일", "11/15", "11.15", "2026-11-15", "2026.11.15".
  - "15일"(일만)은 근거에 "15일"이나 15일인 날짜가 있어야 하고, "11월"(월만)도 같다.
  - 쉼표는 천 단위 구분(1,500)일 때만 지우고, 그 밖의 쉼표는 숫자를 나눈다("1,2" → 1과 2).
- 연락처: 이 업무 메일의 보낸 사람 · 받는 사람 · 참조 머리글에 있는 주소 · 전화만 근거로 본다. 본문에만 있는 주소는
  상대(또는 공격자)가 심어 둔 것일 수 있으므로 스레드 밖 연락처로 경고한다.
"""
import re

from app.agent.guard import EMAIL_RE

PLACEHOLDER_RE = re.compile(r"\[\[[^\[\]]{1,80}\]\]")
# 한국 전화: 010-9999-8888, 010 9999 8888, 01099998888, 02 123 4567, 02.123.4567
PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)")
_TOKEN_RE = re.compile(
    r"(?P<ymd>\d{4})[-./](?P<ymd_m>\d{1,2})[-./](?P<ymd_d>\d{1,2})(?!\d)"
    r"|(?P<kmd>\d{1,2})\s*월\s*(?P<kmd_d>\d{1,2})\s*일"
    r"|(?<![\d.,/])(?P<smd>\d{1,2})[/.](?P<smd_d>\d{1,2})(?![\d.,/])"
    r"|(?P<day>\d{1,2})일"
    r"|(?P<month>\d{1,2})월"
    r"|(?P<num>\d[\d,.:/-]*\d|\d)"
)
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
MAX_ITEMS = 20


def placeholders(text: str) -> list[str]:
    return PLACEHOLDER_RE.findall(text or "")


def _md(month: str, day: str) -> str | None:
    m, d = int(month), int(day)
    return f"{m}/{d}" if 1 <= m <= 12 and 1 <= d <= 31 else None


def _numbers(raw: str) -> list[str]:
    if _THOUSANDS_RE.fullmatch(raw):
        return [raw.replace(",", "")]
    return [p for p in raw.split(",") if p]


def tokens(text: str) -> list[tuple[str, str, str]]:
    """(종류, 비교 키, 보일 글). 종류: md(날짜) · d(일만) · m(월만) · n(숫자)."""
    out: list[tuple[str, str, str]] = []
    for mt in _TOKEN_RE.finditer(text or ""):
        g, shown = mt.groupdict(), mt.group(0).strip()
        if g["ymd"] or g["kmd"] or g["smd"]:
            month, day = ((g["ymd_m"], g["ymd_d"]) if g["ymd"] else (g["kmd"], g["kmd_d"]) if g["kmd"]
                          else (g["smd"], g["smd_d"]))
            key = _md(month, day)
            if key:
                out.append(("md", key, shown))
            else:  # 날짜가 될 수 없는 값(13.5 등)은 숫자로 본다
                out.extend(("n", n, n) for n in _numbers(shown.replace(" ", "")))
        elif g["day"]:
            out.append(("d", str(int(g["day"])), shown))
        elif g["month"]:
            out.append(("m", str(int(g["month"])), shown))
        else:
            out.extend(("n", n, n) for n in _numbers(g["num"]))
    return out


def _supported_keys(sources: str) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for kind, key, _ in tokens(sources):
        keys.add((kind, key))
        if kind == "md":
            month, day = key.split("/")
            keys.update({("m", month), ("d", day)})
    return keys


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def draft_warnings(draft_body: str, sources: str, header_text: str) -> dict:
    """header_text = 이 업무 메일의 보낸 사람 · 받는 사람 · 참조 머리글(연락처 근거)."""
    body = draft_body or ""
    known = _supported_keys(sources)
    unsupported: list[str] = []
    for kind, key, shown in tokens(body):
        if (kind, key) not in known and shown not in unsupported:
            unsupported.append(shown)
    emails = {e.lower() for e in EMAIL_RE.findall(header_text or "")}
    phones = {_digits(p) for p in PHONE_RE.findall(header_text or "")}
    outside: list[str] = []
    for c in EMAIL_RE.findall(body):
        if c.lower() not in emails and c not in outside:
            outside.append(c)
    for c in PHONE_RE.findall(body):
        if _digits(c) not in phones and c not in outside:
            outside.append(c.strip())
    return {"placeholders": len(placeholders(body)), "unsupported": unsupported[:MAX_ITEMS],
            "outside_contacts": outside[:MAX_ITEMS], "suspicious": []}
