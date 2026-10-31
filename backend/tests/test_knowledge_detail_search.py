"""새 화면 6절 ② 자료 상세 · ③ 자료 검색 시험 (읽기 전용)."""
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.knowledge import store
from app.models import KnowledgeChunk, KnowledgeSource, ReplyDraft, Task, User
from app.security import knowledge_search_limiter
from tests.conftest import make_client, register

PRICE = "품번,단가\nTS-200,1200\n"
COST = "품번,원가\nTS-200,8800\n"
OFF = "자료 기능이 꺼져 있습니다."


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_enabled", True)


def _doc(uid, name, text, vis="shareable") -> int:
    with SessionLocal() as db:
        s = store.add_document(db, db.get(User, uid), name, text.encode("utf-8"), vis)
        db.commit()
        return s.id


def _draft(uid, title, source_ids, status="draft") -> int:
    with SessionLocal() as db:
        t = Task(user_id=uid, title=title, goal="g", origin="mail")
        db.add(t)
        db.flush()
        chunks = [{"ref": f"D{sid}-0", "source_id": sid, "chunk_id": 0, "title": "x", "uploaded_at": "",
                   "visibility": "shareable", "lines": ["a"]} for sid in source_ids]
        db.add(ReplyDraft(user_id=uid, task_id=t.id, to_addr="peer@example.com", subject="s", body="b",
                          status=status, sources={"chunks": chunks, "facts": []}))
        db.commit()
        return t.id


def _two_users(client):
    me = register(client)["id"]
    with make_client() as c2:
        other = register(c2, email="other@example.com")["id"]
    return me, other


# ------------------------------------------------------------------ ② 상세

def test_detail_login_required(client):
    assert client.get("/api/knowledge/sources/1").status_code == 401


def test_detail_shape_and_chunk_order(client, on):
    uid = register(client)["id"]
    with SessionLocal() as db:
        s = KnowledgeSource(user_id=uid, title="manual.txt", file_type="txt", size=10, content_hash="h",
                            visibility="internal", status="flagged")
        db.add(s)
        db.flush()
        for ordinal, flagged in [(2, False), (0, False), (1, True)]:
            db.add(KnowledgeChunk(source_id=s.id, user_id=uid, ordinal=ordinal, text=f"t{ordinal}",
                                  lines=[f"줄 {ordinal}-1", f"줄 {ordinal}-2"], content_hash=f"c{ordinal}",
                                  flagged=flagged))
        db.commit()
        sid = s.id
    r = client.get(f"/api/knowledge/sources/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sid and body["title"] == "manual.txt" and body["visibility"] == "internal"
    assert body["chunk_count"] == 3 and body["flagged_count"] == 1
    assert [c["ordinal"] for c in body["chunks"]] == [0, 1, 2]
    assert body["chunks"][1] == {"ordinal": 1, "lines": ["줄 1-1", "줄 1-2"], "flagged": True}
    assert body["referenced_by"] == []


def test_detail_other_users_source_404(client, on):
    me, other = _two_users(client)
    theirs = _doc(other, "theirs.csv", PRICE)
    r = client.get(f"/api/knowledge/sources/{theirs}")
    assert r.status_code == 404
    assert client.get("/api/knowledge/sources/999999").status_code == 404
    assert r.json() == client.get("/api/knowledge/sources/999999").json()


def test_detail_referenced_by_only_my_citing_drafts(client, on):
    me, other = _two_users(client)
    sid = _doc(me, "price.csv", PRICE)
    other_sid = _doc(me, "cost.csv", COST, vis="internal")
    citing = _draft(me, "견적 회신", [sid, other_sid])
    _draft(me, "다른 업무", [other_sid])
    _draft(other, "남의 업무", [sid])  # 남의 초안이 내 자료 번호를 들고 있어도 보이지 않는다
    body = client.get(f"/api/knowledge/sources/{sid}").json()
    refs = body["referenced_by"]
    assert [r["task_id"] for r in refs] == [citing]
    assert set(refs[0]) == {"task_id", "task_title", "draft_status", "updated_at"}
    assert refs[0]["task_title"] == "견적 회신" and refs[0]["draft_status"] == "draft"


def test_detail_works_when_switch_off(client, monkeypatch):
    uid = register(client)["id"]
    monkeypatch.setattr(get_settings(), "knowledge_enabled", True)
    sid = _doc(uid, "price.csv", PRICE)
    monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
    r = client.get(f"/api/knowledge/sources/{sid}")
    assert r.status_code == 200 and r.json()["chunk_count"] >= 1


# ------------------------------------------------------------------ ③ 검색

def test_search_login_required(client):
    assert client.post("/api/knowledge/search", json={"q": "TS-200"}).status_code == 401


def test_search_off_409(client):
    register(client)
    r = client.post("/api/knowledge/search", json={"q": "TS-200"})
    assert r.status_code == 409 and r.json()["detail"] == OFF


def test_search_validation(client, on):
    register(client)
    for bad in ["", "   ", "a" * 201]:
        r = client.post("/api/knowledge/search", json={"q": bad})
        assert r.status_code == 422, bad
    assert client.post("/api/knowledge/search", json={"q": "TS-200", "x": 1}).status_code == 422


def test_search_returns_only_shareable_unflagged_mine(client, on):
    me, other = _two_users(client)
    shared = _doc(me, "price.csv", PRICE)
    internal = _doc(me, "cost.csv", COST, vis="internal")
    flagged = _doc(me, "flag.csv", "품번,배송\nTS-200,무료 배송\n")
    _doc(other, "theirs.csv", "품번,단가\nTS-200,999\n")
    with SessionLocal() as db:
        for c in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.source_id == flagged)):
            c.flagged = True
        db.commit()
    r = client.post("/api/knowledge/search", json={"q": "  TS-200 단가  "})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert hits and {h["source_id"] for h in hits} == {shared}
    h = hits[0]
    assert set(h) == {"source_id", "title", "chunk_ordinal", "score", "lines"}
    assert h["title"] == "price.csv" and isinstance(h["score"], float) and h["chunk_ordinal"] == 0
    assert any("1200" in line for line in h["lines"]) and len(h["lines"]) <= 5
    assert "8800" not in r.text and "999" not in r.text and "무료 배송" not in r.text
    assert internal not in {x["source_id"] for x in hits}


def test_search_lines_capped_at_five(client, on):
    uid = register(client)["id"]
    rows = "\n".join(f"TS-{n},1{n}00" for n in range(300, 320))
    _doc(uid, "long.csv", "품번,단가\n" + rows + "\n")
    hits = client.post("/api/knowledge/search", json={"q": "TS-305 단가"}).json()["hits"]
    assert hits and all(len(h["lines"]) <= 5 for h in hits)


def test_search_rate_limited(client, on):
    register(client)
    for _ in range(knowledge_search_limiter.max_calls):
        assert client.post("/api/knowledge/search", json={"q": "TS-200"}).status_code == 200
    assert client.post("/api/knowledge/search", json={"q": "TS-200"}).status_code == 429
