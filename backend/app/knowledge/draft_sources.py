"""답장 초안이 쓰는 자료 (RAG 설계 5절).

- 검색은 답장(reply) 초안일 때만, 답장에 써도 되는(shareable) 자료만 한다. 내부용 자료는 프롬프트에 넣지 않고
  내부용 값 경고(5.3) 비교에만 쓴다. 비교는 이 PC 안에서만 한다.
- LLM이 자료에서 가져왔다는 사실(used_facts, source "doc")은 넣어 준 덩어리 · 줄로 코드가 다시 확인한다(5.2).
  근거 확인에는 그 항목이 가리키는 **그 줄만** 더한다. 덩어리 전체를 더하면 지어낸 숫자도 근거가 있어 보인다.
- 넣어 준 덩어리 본문 사본을 초안(`sources`)에 둔다. 고칠 때 외부 호출 없이 같은 근거로 다시 확인하기 위해서다.
- 로그에는 건수 · 예외 타입 이름만 남긴다.
"""
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.guard import wrap_untrusted
from app.config import get_settings
from app.knowledge import search
from app.mail.draft_checks import draft_warnings, supported_keys, token_spans, tokens
from app.models import Email, EmailDirection, ReplyDraft, Task

log = logging.getLogger(__name__)

QUERY_MAIL_CHARS = 500
ITEM_CHARS = 300
MAX_FACTS = 20
_WON_RE = re.compile(r"\s*원")


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _latest_inbound(emails: list[Email]) -> Email | None:
    """emails는 최근 것부터(reply_draft._emails)."""
    return next((e for e in emails if e.direction == EmailDirection.INBOUND), None)


def _requests(task: Task) -> list[str]:
    brief = task.brief if isinstance(task.brief, dict) else {}
    return [r for r in (brief.get("requests") or []) if isinstance(r, str)]


def build_query(task: Task, emails: list[Email], user_input: str) -> str:
    """업무 한 줄 요약 + 상대 요청 + 가장 최근 받은 메일 본문 앞 500자 + 답장에 넣을 내용 (설계 4.2)."""
    brief = task.brief if isinstance(task.brief, dict) else {}
    latest = _latest_inbound(emails)
    parts = [str(brief.get("summary") or ""), *_requests(task),
             (latest.content or "")[:QUERY_MAIL_CHARS] if latest else "", user_input or ""]
    return "\n".join(p for p in parts if p)


def task_identifiers(task: Task, emails: list[Email]) -> set[str]:
    """상대 요청 · 가장 최근 받은 메일의 품번 같은 식별자 (5.2-3 다른 품목 경고용)."""
    latest = _latest_inbound(emails)
    return search.identifiers("\n".join([*_requests(task), (latest.content or "") if latest else ""]))


def chunk_entries(hits: list[search.Hit]) -> list[dict]:
    return [{"ref": f"D{h.source_id}-{h.ordinal}", "source_id": h.source_id, "chunk_id": h.chunk_id,
             "title": h.title, "uploaded_at": _utc(h.uploaded_at).isoformat(), "visibility": "shareable",
             "lines": list(h.lines)} for h in hits]


def find_chunks(db: Session, user_id: int, query: str) -> tuple[list[dict], bool]:
    """(덩어리 사본, 검색 실패 여부). 검색이 실패해도 초안 만들기는 실패하지 않는다."""
    try:
        return chunk_entries(search.search_shareable(db, user_id, query)), False
    except Exception as e:  # noqa: BLE001 - 어떤 오류든 자료 없이 진행한다
        log.warning("reply draft: knowledge search failed: %s", type(e).__name__)
        return [], True


def _uploaded_md(value: str) -> str:
    try:
        local = _utc(datetime.fromisoformat(value)).astimezone(ZoneInfo(get_settings().user_timezone))
    except (TypeError, ValueError):
        return "날짜 모름"
    return f"{local.month}/{local.day}"


def prompt_block(chunks: list[dict]) -> str:
    """'내 자료 (답장에 써도 되는 자료)' 블록. 자료 이름도 사용자가 올린 글이므로 머리글까지 함께 감싼다."""
    if not chunks:
        return ""
    parts = []
    for c in chunks:
        head = f"[자료 {c['ref']}] {c['title']} ({_uploaded_md(c.get('uploaded_at'))} 올림)"
        lines = "\n".join(f"{i}: {line}" for i, line in enumerate(c.get("lines") or [], start=1))
        text = f"{head}\n{lines}"
        parts.append(wrap_untrusted(f"doc#{c['ref']}", text, max_chars=len(text) + 2))
    return "내 자료 (답장에 써도 되는 자료):\n" + "\n\n".join(parts)


def _keys(text: str) -> set[tuple[str, str]]:
    return {(kind, key) for kind, key, _ in tokens(text)}


def check_facts(body: str, used_facts: list[dict], chunks: list[dict], task_ids: set[str]) -> list[dict]:
    """자료에서 가져온 항목을 넣어 준 덩어리 · 줄로 다시 확인한다 (설계 5.2 1~4). 통과한 항목만 돌려준다."""
    by_ref = {c.get("ref"): c for c in chunks if isinstance(c, dict)}
    body_keys = supported_keys(body)  # 근거 글과 같은 펼침: 항목 "15일"은 본문 "11/15"로도 맞는다
    out: list[dict] = []
    seen: set[tuple] = set()
    for f in used_facts:
        if not isinstance(f, dict) or f.get("source") != "doc":
            continue
        text = f.get("text").strip()[:ITEM_CHARS] if isinstance(f.get("text"), str) else ""
        ref, line = f.get("chunk"), f.get("line")
        chunk = by_ref.get(ref)
        lines = list(chunk.get("lines") or []) if chunk else []
        # 1. 넣어 준 덩어리 · 그 덩어리에 있는 줄 (줄 번호는 1부터)
        if not text or not lines or isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= len(lines):
            continue
        line_text = str(lines[line - 1])
        # 2. 숫자 · 날짜 토큰이 그 줄과 본문에 모두 있어야 한다. 4. 토큰이 없으면 직접 확인
        fact_keys = _keys(text)
        if fact_keys and not (fact_keys <= supported_keys(line_text) and fact_keys <= body_keys):
            continue
        key = (text, ref, line)
        if key in seen:
            continue
        seen.add(key)
        # 3. 메일 · 업무의 식별자가 그 줄에 하나도 없으면 다른 품목의 값일 수 있다
        other = bool(task_ids) and not (search.identifiers(line_text) & task_ids)
        out.append({"text": text, "ref": ref, "line": line, "line_text": line_text,
                    "checked": "numbers" if fact_keys else "manual", "other_item": other})
    return out[:MAX_FACTS]


def evidence_lines(facts: list[dict]) -> str:
    """숫자로 확인을 통과한 항목이 가리키는 줄만 이은 글 (숫자 · 날짜 근거 확인에 더한다).
    직접 확인(manual) 항목의 줄은 더하지 않는다: 숫자가 없는 사실이 그 줄의 숫자에 근거를 빌려주면 안 된다."""
    return "\n".join(str(f.get("line_text") or "") for f in facts if f.get("checked") == "numbers")


def internal_values(db: Session, user_id: int, body: str, allowed_text: str) -> list[dict]:
    """초안 속 금액 · 세 자리 이상 숫자 중 내부용 자료에 있고 allowed_text(인용 줄 + 스레드 메일)에 없는 값 (5.3).

    메모 · 사용자 입력은 allowed_text에 넣지 않는다: 원가를 메모에 적어 둔 경우가 현실적인 유출 경로다.
    """
    candidates = []
    for kind, key, shown, end in token_spans(body):
        if kind != "n":
            continue
        if _WON_RE.match(body or "", end):
            shown = f"{shown}원"
        if search.is_internal_candidate(key, shown):
            candidates.append((key, shown))
    if not candidates:
        return []
    index = search.internal_value_index(db, user_id)
    allowed = {key for kind, key, _ in tokens(allowed_text) if kind == "n"}
    out: list[dict] = []
    seen: set[str] = set()
    for key, shown in candidates:
        if key in seen or key in allowed or key not in index:
            continue
        seen.add(key)
        out.append({"value": shown, "source_ids": [sid for sid, _ in index[key]],
                    "titles": [title for _, title in index[key]]})
    return out[:MAX_FACTS]


def compute_checks(db: Session, task: Task, body: str, user_input: str, sources: dict,
                   suspicious: list[str]) -> tuple[dict, dict]:
    """(warnings, sources). 저장된 덩어리 사본으로 자료 항목 · 근거 · 내부용 값을 다시 계산한다. 외부 호출 없음.

    sources["facts"]의 항목(text · ref · line)을 후보로 다시 확인한다. 만들 때는 LLM의 doc 항목을 이 형태로 넣어 부른다.
    """
    from app.mail.reply_draft import _context_texts, _emails  # 순환 import 피하기

    sources = sources if isinstance(sources, dict) else {}
    if not get_settings().knowledge_enabled:
        # 스위치가 꺼지면 6단계와 같다(설계 3.4): 자료 줄을 근거로 쓰지 않고 내부용 색인도 보지 않는다.
        # 저장된 사본은 화면 표시를 위해 그대로 둔다.
        context, header_text = _context_texts(db, task, user_input)
        warnings = draft_warnings(body, context, header_text)
        warnings["suspicious"] = list(suspicious or [])
        warnings["internal_values"], warnings["other_item"] = 0, 0
        kept = {"chunks": [], "facts": [], "search_failed": False, "sources_changed": False}
        kept.update({k: v for k, v in sources.items() if k in kept})
        kept["internal_values"] = []
        return warnings, kept
    chunks = [c for c in (sources.get("chunks") or []) if isinstance(c, dict)]
    candidates = [{"text": f.get("text"), "source": "doc", "chunk": f.get("ref"), "line": f.get("line")}
                  for f in (sources.get("facts") or []) if isinstance(f, dict)]
    emails = _emails(db, task)
    facts = check_facts(body, candidates, chunks, task_identifiers(task, emails))
    context, header_text = _context_texts(db, task, user_input)
    warnings = draft_warnings(body, "\n".join([context, evidence_lines(facts)]), header_text)
    warnings["suspicious"] = list(suspicious or [])
    allowed = "\n".join([evidence_lines(facts), *[e.content or "" for e in emails]])
    found = internal_values(db, task.user_id, body, allowed)
    warnings["internal_values"] = len(found)
    warnings["other_item"] = sum(1 for f in facts if f["other_item"])
    new_sources = {"chunks": chunks, "facts": facts, "internal_values": found,
                   "search_failed": bool(sources.get("search_failed")),
                   "sources_changed": bool(sources.get("sources_changed"))}
    return warnings, new_sources


def refresh_draft_checks(db: Session, draft: ReplyDraft) -> None:
    """저장된 사본으로 이 초안의 자료 항목 · 경고를 다시 계산해 새 객체로 대입한다(JSON 변경 감지). 커밋은 호출한 쪽이."""
    task = db.get(Task, draft.task_id)
    suspicious = (draft.warnings or {}).get("suspicious") if isinstance(draft.warnings, dict) else []
    warnings, sources = compute_checks(db, task, draft.body, draft.user_input, draft.sources, suspicious or [])
    draft.warnings, draft.sources = warnings, sources


def _cites(sources, source_id: int) -> bool:
    return isinstance(sources, dict) and any(
        isinstance(c, dict) and c.get("source_id") == source_id for c in (sources.get("chunks") or []))


def _warns_internal(sources, source_id: int) -> bool:
    return isinstance(sources, dict) and any(
        isinstance(v, dict) and source_id in (v.get("source_ids") or []) for v in (sources.get("internal_values") or []))


def _update_keep_time(db: Session, draft: ReplyDraft, **values) -> None:
    """updated_at을 그대로 둔 채 바꾼다. ORM으로 바꾸면 onupdate가 updated_at을 새로 쓴다.

    updated_at은 '초안을 만들거나 고친 뒤 보낸 답장이 있는가'(replied_after_draft)의 기준이라,
    자료 변경 같은 사용자 아닌 변경으로 앞당기면 이미 보낸 답장을 놓쳐 두 번 보낼 수 있다.
    """
    db.execute(update(ReplyDraft).where(ReplyDraft.id == draft.id)
               .values(**values, updated_at=ReplyDraft.updated_at).execution_options(synchronize_session=False))
    db.expire(draft, list(values))


def _refresh_keep_time(db: Session, draft: ReplyDraft) -> None:
    task = db.get(Task, draft.task_id)
    suspicious = (draft.warnings or {}).get("suspicious") if isinstance(draft.warnings, dict) else []
    warnings, sources = compute_checks(db, task, draft.body, draft.user_input, draft.sources, suspicious or [])
    _update_keep_time(db, draft, warnings=warnings, sources=sources)


def refresh_internal_warnings(db: Session, user_id: int, source_id: int, drafts: list[ReplyDraft] | None = None) -> int:
    """내부용 자료를 지우거나 공유 가능으로 바꿀 때, 그 자료의 값을 경고하던 보내기 전(draft) 초안의 경고를 다시 계산한다.

    화면에 낡은 경고가 남지 않게 하기 위해서다. 보내기 직전 확인은 어차피 본문으로 다시 계산한다(reply_send).
    sources_changed는 켜지 않는다(인용한 자료가 아니다). 커밋은 호출한 쪽이.
    """
    db.flush()
    if drafts is None:
        drafts = db.scalars(select(ReplyDraft).where(ReplyDraft.user_id == user_id)).all()
    count = 0
    for draft in drafts:
        if draft.status == "draft" and _warns_internal(draft.sources, source_id):
            _refresh_keep_time(db, draft)
            count += 1
    return count


def purge_source_from_drafts(db: Session, user_id: int, source_id: int) -> int:
    """자료를 지우거나 내부용으로 바꿀 때 그 사용자의 모든 초안(업무 · 초안 상태 무관)에서 그 자료의 사본 · 항목을 걷어낸다 (5.4).

    - 보내기 전(draft) 초안: 남은 사본으로 경고를 다시 계산하고 '참고한 자료가 바뀌었다'(sources_changed)를 켠다.
    - 보내는 중 · 결과 불분명 초안: 사본을 지우고 sources_changed를 켠다. '안 보내졌어요'나 거절로 초안에 돌아오면
      보내기 직전 확인이 막아야 하기 때문이다. updated_at은 그대로 둔다(발송 상태 판단의 기준).
    - 보낸 초안: 사본만 지운다(이미 나간 본문은 그대로다).
    그 자료의 값을 내부용 값 경고로 보이던 보내기 전 초안도 경고를 다시 계산한다(지운 내부용 자료).
    바뀐(사본을 지운) 초안 수를 돌려준다. 커밋은 호출한 쪽이.
    sources는 JSON이라 그 사용자의 초안을 한 번에 읽어 파이썬에서 거른다(개인 규모).
    """
    db.flush()  # 세션은 autoflush가 꺼져 있다: 방금 바꾼 공유 등급 · 지운 자료가 내부용 값 색인에 보이게 한다
    drafts = db.scalars(select(ReplyDraft).where(ReplyDraft.user_id == user_id)).all()
    changed, rest = 0, []
    for draft in drafts:
        if not _cites(draft.sources, source_id):
            rest.append(draft)
            continue
        chunks = list(draft.sources.get("chunks") or [])
        gone = {c.get("ref") for c in chunks if isinstance(c, dict) and c.get("source_id") == source_id}
        kept = [c for c in chunks if not (isinstance(c, dict) and c.get("source_id") == source_id)]
        facts = [f for f in (draft.sources.get("facts") or []) if not (isinstance(f, dict) and f.get("ref") in gone)]
        new_sources = {**draft.sources, "chunks": kept, "facts": facts}  # 새 객체: JSON 변경 감지
        if draft.status == "draft":
            draft.sources = {**new_sources, "sources_changed": True}
            refresh_draft_checks(db, draft)  # 저장된 sources_changed를 그대로 옮긴다
            draft.updated_at = datetime.now(timezone.utc)
        elif draft.status == "sent":
            _update_keep_time(db, draft, sources=new_sources)
        else:  # sending · unknown
            _update_keep_time(db, draft, sources={**new_sources, "sources_changed": True})
        changed += 1
    refreshed = refresh_internal_warnings(db, user_id, source_id, rest)
    if changed or refreshed:
        log.info("knowledge source %s: purged from %d drafts, internal warnings refreshed in %d",
                 source_id, changed, refreshed)
    return changed


def internal_values_now(db: Session, task: Task, draft: ReplyDraft) -> list[dict]:
    """보내기 직전: 지금 본문으로 내부용 값을 다시 찾는다(저장된 경고를 믿지 않는다, 5.3). 외부 호출 없음.

    허용 글은 만들 때와 같다: 확인을 통과한 인용 줄 + 스레드 메일. 메모 · 입력은 넣지 않는다.
    """
    from app.mail.reply_draft import _emails  # 순환 import 피하기

    sources = draft.sources if isinstance(draft.sources, dict) else {}
    facts = [f for f in (sources.get("facts") or []) if isinstance(f, dict)]
    allowed = "\n".join([evidence_lines(facts), *[e.content or "" for e in _emails(db, task)]])
    return internal_values(db, task.user_id, draft.body, allowed)
