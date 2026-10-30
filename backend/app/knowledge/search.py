"""답장에 써도 되는 자료 덩어리 찾기(외부 호출 없음)와 내부용 값 색인."""
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mail.draft_checks import tokens
from app.models import KnowledgeChunk, KnowledgeSource

log = logging.getLogger(__name__)

# Task 9 측정(docs/evals/2026-10-29-rag-documents.md)으로 정한 값.
ID_WEIGHT = 3.0
# 자료 이름(파일 이름)에만 있는 식별자. 사양서 표처럼 품번이 제목에만 있고 덩어리에는 없는 경우를 찾기 위해서다.
# 덩어리에 그대로 든 식별자보다 낮게 둬서, 그 품번 줄이 있는 덩어리를 밀어내지 않게 한다.
TITLE_ID_WEIGHT = 1.5
GRAM_WEIGHT = 1.0
TIE = 0.05
MIN_SCORE = 0.15

_ID_RE = re.compile(r"(?<![A-Za-z0-9-])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9-])")


@dataclass
class Hit:
    chunk_id: int
    source_id: int
    title: str
    uploaded_at: datetime
    ordinal: int
    text: str
    lines: list[str]
    score: float


def identifiers(text: str) -> set[str]:
    """영문과 숫자가 함께 든 낱말(하이픈 허용)을 대문자로. 앞뒤가 영숫자 · 하이픈이 아닌 낱말 단위."""
    out = set()
    for m in _ID_RE.finditer(text or ""):
        w = m.group(0)
        if re.search(r"[A-Za-z]", w) and re.search(r"\d", w):
            out.add(w.upper())
    return out


def _grams(text: str) -> set[str]:
    s = re.sub(r"[\W_]+", "", (text or "").lower())
    return {s[i : i + 2] for i in range(len(s) - 1)}


def search_shareable(db: Session, user_id: int, query: str, limit: int = 5, per_source: int = 3) -> list[Hit]:
    q_ids, q_grams = identifiers(query), _grams(query)
    if not q_ids and not q_grams:
        return []
    rows = db.execute(
        select(KnowledgeChunk, KnowledgeSource)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
        .where(
            KnowledgeChunk.user_id == user_id,
            KnowledgeSource.user_id == user_id,
            KnowledgeSource.visibility == "shareable",
            KnowledgeSource.status.in_(("indexed", "flagged")),
            KnowledgeChunk.flagged.is_(False),
        )
    ).all()
    scored: list[Hit] = []
    for chunk, source in rows:
        chunk_ids = q_ids & identifiers(chunk.text)
        title_ids = (q_ids & identifiers(source.title)) - chunk_ids
        id_score = len(chunk_ids) * ID_WEIGHT + len(title_ids) * TITLE_ID_WEIGHT
        # 덩어리가 질의보다 훨씬 길어 Jaccard는 너무 작아지므로 질의 기준 겹침 비율을 쓴다.
        gram_score = (len(q_grams & _grams(chunk.text)) / len(q_grams)) * GRAM_WEIGHT if q_grams else 0.0
        score = id_score + gram_score
        if score < MIN_SCORE:
            continue
        scored.append(
            Hit(chunk.id, source.id, source.title, source.created_at, chunk.ordinal, chunk.text,
                list(chunk.lines or []), score)
        )
    # 점수를 TIE 단위로 내려 같은 구간이면 최근 자료 먼저.
    scored.sort(key=lambda h: (-int(h.score / TIE), -h.uploaded_at.timestamp(), -h.score, h.source_id, h.ordinal))
    hits: list[Hit] = []
    counts: dict[int, int] = {}
    for h in scored:
        if counts.get(h.source_id, 0) >= per_source:
            continue
        counts[h.source_id] = counts.get(h.source_id, 0) + 1
        hits.append(h)
        if len(hits) >= limit:
            break
    log.info("자료 검색: 덩어리 %d개 중 %d개 선택, 최고 점수 %.2f", len(rows), len(hits),
             hits[0].score if hits else 0.0)
    return hits


def internal_value_index(db: Session, user_id: int) -> dict[str, list[tuple[int, str]]]:
    """내부용 자료의 숫자 키 -> [(자료 번호, 제목)]. 유출 경고용."""
    rows = db.execute(
        select(KnowledgeChunk.text, KnowledgeSource.id, KnowledgeSource.title)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
        .where(
            KnowledgeChunk.user_id == user_id,
            KnowledgeSource.user_id == user_id,
            KnowledgeSource.visibility == "internal",
            KnowledgeChunk.flagged.is_(False),
        )
    ).all()
    index: dict[str, list[tuple[int, str]]] = {}
    for text, sid, title in rows:
        for kind, key, _ in tokens(text):
            if kind != "n":
                continue
            entry = (sid, title)
            if entry not in index.setdefault(key, []):
                index[key].append(entry)
    return index


def is_internal_candidate(key: str, shown: str) -> bool:
    """숫자 키가 세 자리 이상(소수점 제외)이거나 금액이면 참.

    금액 판단은 `shown`에 달렸다. 호출하는 쪽은 본문 속 값 바로 뒤에 '원'이 붙어 있으면
    `shown`에 '원'을 포함해 넘긴다(예: "12원").
    """
    return len(key.replace(".", "")) >= 3 or shown.rstrip().endswith("원")
