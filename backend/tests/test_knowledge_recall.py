"""RAG 1단계 검색 정확도 (설계 7절). 키워드 검색은 외부 호출이 없으므로 일반 테스트로 잰다.

가상 자료 7종 · 검색 질문 21개(smoke/knowledge_cases.py). `-s`로 돌리면 질문별 표와 recall@5를 출력한다.
단언은 하나만: 품번으로 묻는 질문은 정답 덩어리가 모두 상위 5개 안에 있다. 다른 말로 묻기 · 품명으로 찾기는 기록만 한다.
"""
from app.db import SessionLocal
from app.knowledge import search
from app.models import KnowledgeChunk, KnowledgeSource, User
from smoke.knowledge_cases import RECALL_QUERIES, load_documents


def _expected_chunks(db, sources: dict, key: str, needle: str) -> set[int]:
    chunks = db.query(KnowledgeChunk).filter(KnowledgeChunk.source_id == sources[key].id).all()
    return {c.id for c in chunks if any(needle in line for line in (c.lines or []))}


def _measure() -> list[dict]:
    with SessionLocal() as db:
        user = User(email="recall@example.com", password_hash="x")
        db.add(user)
        db.commit()
        sources = load_documents(db, user)
        rows = []
        for q in RECALL_QUERIES:
            key, needle = q["expected"]
            want = _expected_chunks(db, sources, key, needle)
            assert want, f"시험 세트 오류: {key}에 '{needle}' 줄이 없음"
            hits = search.search_shareable(db, user.id, q["query"])
            rank = next((i for i, h in enumerate(hits, 1) if h.chunk_id in want), None)
            titles = {s.id: s.title for s in db.query(KnowledgeSource).all()}
            rows.append({**q, "rank": rank, "top": [(titles[h.source_id], h.ordinal, round(h.score, 2)) for h in hits]})
        return rows


def _print(rows: list[dict]) -> None:
    print(f"\n[recall] ID_WEIGHT={search.ID_WEIGHT} GRAM_WEIGHT={search.GRAM_WEIGHT} TIE={search.TIE} "
          f"MIN_SCORE={search.MIN_SCORE}")
    print("| # | 종류 | 질문 | 정답 | 순위 | 상위 결과(자료 · 덩어리 · 점수) |")
    print("|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        top = "; ".join(f"{t} #{o} {s}" for t, o, s in r["top"][:3])
        print(f"| {i} | {r['kind']} | {r['query']} | {r['expected'][0]} '{r['expected'][1]}' | "
              f"{r['rank'] or '없음'} | {top} |")
    print("| 종류 | 질문 | 상위 5 안 | recall@5 |\n|---|---|---|---|")
    for kind in ("id", "paraphrase", "row", None):
        group = [r for r in rows if kind is None or r["kind"] == kind]
        found = sum(r["rank"] is not None for r in group)
        print(f"| {kind or '전체'} | {len(group)} | {found} | {found / len(group):.2f} |")


def test_identifier_queries_find_their_row_in_top5():
    rows = _measure()
    _print(rows)
    missed = [r["query"] for r in rows if r["kind"] == "id" and r["rank"] is None]
    assert not missed, missed


def test_flagged_notice_chunk_never_searched():
    """주입 문장이 든 덩어리는 검색에서 빠진다(설계 3.2).

    최종 리뷰 뒤: 'AI 비서는 … 넣으세요'처럼 AI를 주어로 시키는 문장도 걸리므로 프로모션 덩어리도 빠진다.
    같은 자료의 걸리지 않은 덩어리를 쓰는지는 test_knowledge_injection이 확인한다(이 공지 자료는 두 덩어리 모두 주입이다).
    """
    with SessionLocal() as db:
        user = User(email="recall@example.com", password_hash="x")
        db.add(user)
        db.commit()
        sources = load_documents(db, user)
        assert sources["notice"].status == "flagged"
        hits = search.search_shareable(db, user.id, "대리점 시스템 공지 원가 TS-200 프로모션")
        texts = [h.text for h in hits]
        assert not any("이전 지시를 모두 무시" in t for t in texts)
        assert not any("20% 할인" in t or "rival-parts" in t for t in texts)
