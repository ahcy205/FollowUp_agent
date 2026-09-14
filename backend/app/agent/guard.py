"""Prompt-injection and data-leak guards applied in code, independent of the LLM."""

import re

_INJECTION_PATTERNS = [
    (r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?)", "영문 '이전 지시 무시' 패턴"),
    (r"(이전|앞의|기존|위의)\s*(의\s*)?(모든\s*)?(지시|명령|규칙|지침)[을를]?\s*(모두\s*)?(무시|잊)", "'이전 지시 무시' 패턴"),
    (r"system\s*(override|prompt|instruction|message)\s*[:：]?", "시스템 명령 사칭"),
    (r"시스템\s*(지시|명령|프롬프트|오버라이드)", "시스템 명령 사칭"),
    (r"(관리자|admin(istrator)?)\s*(권한|모드|privilege|mode)", "관리자 권한 사칭"),
    (r"(developer|dev|jailbreak|DAN)\s*mode", "탈옥 모드 요청"),
    (r"(모든|전체)\s*고객[의]?\s*(정보|이메일|연락처|전화|주소|목록)", "전체 고객 정보 요청"),
    (r"(all|every)\s+(customer|client|user)s?['’]?\s+(data|info|information|emails?|contacts?|phone)", "전체 고객 정보 요청"),
    (r"(reveal|print|show|output)\s+(your\s+)?(system\s+prompt|instructions|api\s*key|secret)", "시스템 정보 유출 요청"),
    (r"(api\s*key|비밀번호|password|secret\s*key|토큰)\s*(을|를)?\s*(알려|보내|출력|전송)", "비밀정보 요청"),
    (r"(COMPLETED|완료)\s*(로|으로)\s*(즉시\s*)?(변경|바꾸|처리)", "업무 상태 조작 요청"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), label) for p, label in _INJECTION_PATTERNS]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b0\d{1,2}-\d{3,4}-\d{4}\b")


def detect_injection(text: str) -> list[str]:
    """Return human-readable labels of suspicious instruction patterns found in untrusted text."""
    found: list[str] = []
    for rx, label in _COMPILED:
        if rx.search(text or "") and label not in found:
            found.append(label)
    return found


def wrap_untrusted(source: str, text: str, max_chars: int = 4000) -> str:
    """Fence external content so the model treats it as data. Tag-breaking sequences are neutralized."""
    body = (text or "")[:max_chars]
    body = re.sub(r"</?\s*untrusted_external_content", "[tag-removed]", body, flags=re.IGNORECASE)
    safe_source = re.sub(r"[^\w\s:#()\-.가-힣]", "", source)[:80]
    return (
        f'<untrusted_external_content source="{safe_source}">\n{body}\n</untrusted_external_content>'
    )


def find_foreign_contacts(text: str, allowed: set[str], protected: set[str]) -> list[str]:
    """Contacts (emails/phones) of *other* customers appearing in outgoing text — a data-leak signal."""
    hits = set(EMAIL_RE.findall(text or "")) | set(PHONE_RE.findall(text or ""))
    allowed_l = {a.lower() for a in allowed}
    protected_l = {p.lower() for p in protected}
    return sorted(h for h in hits if h.lower() in protected_l and h.lower() not in allowed_l)
