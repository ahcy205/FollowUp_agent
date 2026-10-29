from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.knowledge import search, store
from app.models import KnowledgeChunk, User
from tests.conftest import register


def _uid(client, email="a@example.com"):
    register(client, email)
    with SessionLocal() as db:
        return db.scalars(select(User).where(User.email == email)).one().id


def _add(db, uid, name, text, vis="shareable"):
    s = store.add_document(db, db.get(User, uid), name, text.encode("utf-8"), vis)
    db.commit()
    return s


def test_identifiers_whole_token_uppercase():
    assert search.identifiers("ts-200 단가, AB12 와 TS-2000 x1 abc 123") == {"TS-200", "AB12", "TS-2000", "X1"}
    assert search.identifiers("TS-200단가") == {"TS-200"}


def test_identifier_match_ranks_row_first(client):
    uid = _uid(client)
    with SessionLocal() as db:
        _add(db, uid, "a.txt", "배송 안내\n납기는 평균 3일입니다\n\n\nTS-200 단가는 개당 8,000원입니다")
        hits = search.search_shareable(db, uid, "TS-200 단가 문의")
        assert hits and "TS-200" in hits[0].text and hits[0].title == "a.txt"


def test_identifier_match_is_whole_token(client):
    uid = _uid(client)
    with SessionLocal() as db:
        _add(db, uid, "a.txt", "TS-2000 단가는 다릅니다")
        hits = search.search_shareable(db, uid, "TS-200")
        assert all(h.score < search.ID_WEIGHT for h in hits)  # 식별자 점수 0, 조각 겹침만
        assert search.identifiers("TS-2000") & search.identifiers("TS-200") == set()


def test_internal_and_flagged_never_returned(client):
    uid = _uid(client)
    with SessionLocal() as db:
        _add(db, uid, "in.txt", "TS-200 단가표 내부용", vis="internal")
        s = _add(db, uid, "ok.txt", "TS-200 단가표 공유용")
        db.add(KnowledgeChunk(source_id=s.id, user_id=uid, ordinal=9, text="TS-200 단가표 의심", lines=[],
                              content_hash="x" * 64, flagged=True))
        db.commit()
        hits = search.search_shareable(db, uid, "TS-200 단가표")
        assert hits and all(h.title == "ok.txt" and "의심" not in h.text and "내부" not in h.text for h in hits)


def test_other_users_chunks_never_returned(client):
    a = _uid(client, "a@example.com")
    b = _uid(client, "b@example.com")
    with SessionLocal() as db:
        _add(db, a, "a.txt", "TS-200 단가표")
        assert search.search_shareable(db, b, "TS-200 단가표") == []
        assert search.internal_value_index(db, b) == {}


def test_limit_and_per_source_caps(client):
    uid = _uid(client)
    with SessionLocal() as db:
        for n in range(2):
            s = _add(db, uid, f"s{n}.txt", f"seed {n}")
            for i in range(5):
                db.add(KnowledgeChunk(source_id=s.id, user_id=uid, ordinal=10 + i, text=f"TS-200 단가 안내 {n}{i}",
                                      lines=[], content_hash=f"{n}{i}".ljust(64, "0"), flagged=False))
        db.commit()
        hits = search.search_shareable(db, uid, "TS-200 단가 안내")
        assert len(hits) == 5
        per = {}
        for h in hits:
            per[h.source_id] = per.get(h.source_id, 0) + 1
        assert max(per.values()) <= 3
        assert len(search.search_shareable(db, uid, "TS-200 단가 안내", limit=2)) == 2


def test_newer_source_first_on_tie(client):
    uid = _uid(client)
    with SessionLocal() as db:
        old = _add(db, uid, "old.txt", "TS-200 단가는 8,000원")
        new = _add(db, uid, "new.txt", "TS-200 단가는 8,000원이다")
        old.created_at = datetime.now(timezone.utc) - timedelta(days=30)
        new.created_at = datetime.now(timezone.utc)
        db.commit()
        hits = search.search_shareable(db, uid, "TS-200 단가는 8,000원")
        assert [h.title for h in hits][:2] == ["new.txt", "old.txt"]


def test_below_min_score_dropped_returns_empty(client):
    uid = _uid(client)
    with SessionLocal() as db:
        _add(db, uid, "a.txt", "회의실 예약 방법 안내")
        assert search.search_shareable(db, uid, "zzz qqq 완전히 다른 주제") == []
        assert search.search_shareable(db, uid, "") == []


def test_internal_value_index_skips_small_numbers(client):
    uid = _uid(client)
    with SessionLocal() as db:
        s = _add(db, uid, "원가표.txt", "원가 8,000원, 수량 12개", vis="internal")
        _add(db, uid, "pub.txt", "공개 9,999원", vis="shareable")
        idx = search.internal_value_index(db, uid)
        assert idx["8000"] == [(s.id, "원가표.txt")]
        assert "9999" not in idx
        assert [k for k in idx if search.is_internal_candidate(k, k)] == ["8000"]


def test_is_internal_candidate_contract():
    assert search.is_internal_candidate("8000", "8000")
    assert search.is_internal_candidate("1.250", "1.250")
    assert not search.is_internal_candidate("12", "12")
    assert search.is_internal_candidate("12", "12원")
    assert not search.is_internal_candidate("12", "12개")
