"""RAG 1단계 Task 7: 자료실 API (목록 · 올리기 · 공유 등급 · 지우기)."""
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import KnowledgeChunk, KnowledgeSource
from app.security import upload_limiter
from tests.conftest import make_client, register

CSV = "품번,단가\nTS-200,1200\n".encode("utf-8")
TXT = "안녕하세요. 자료입니다.\n둘째 줄입니다.".encode("utf-8")


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_enabled", True)


def _up(client, files, **data):
    return client.post("/api/knowledge/sources", files=[("files", f) for f in files], data=data)


def test_upload_multiple_files_partial_failure(client):
    register(client)
    r = _up(client, [("price.csv", CSV, "text/csv"), ("bad.exe", b"x", "application/octet-stream"),
                     ("memo.txt", TXT, "text/plain")])
    assert r.status_code == 200
    res = r.json()["results"]
    assert [x["ok"] for x in res] == [True, False, True]
    assert res[1]["source"] is None and "지원하지 않는 형식" in res[1]["error"]
    assert res[0]["source"]["chunk_count"] >= 1 and res[0]["source"]["title"] == "price.csv"
    lst = client.get("/api/knowledge/sources").json()
    assert lst["enabled"] is True and len(lst["sources"]) == 2
    assert all(s["chunk_count"] >= 1 and s["flagged_count"] == 0 for s in lst["sources"])


def test_upload_default_visibility_internal(client):
    register(client)
    res = _up(client, [("a.txt", TXT, "text/plain")]).json()["results"]
    assert res[0]["source"]["visibility"] == "internal"
    res = _up(client, [("b.txt", TXT + b"2", "text/plain")], visibility="shareable").json()["results"]
    assert res[0]["source"]["visibility"] == "shareable"


def test_upload_basename_and_duplicate(client):
    register(client)
    res = _up(client, [(r"C:\x\..\dir/a.txt", TXT, "text/plain")]).json()["results"]
    assert res[0]["source"]["title"] == "a.txt"
    res = _up(client, [("a2.txt", TXT, "text/plain")]).json()["results"]
    assert not res[0]["ok"] and "이미 올린 자료" in res[0]["error"]


def test_upload_oversize_file_fails_only_that_file(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_max_file_mb", 1)
    register(client)
    res = _up(client, [("big.txt", b"a" * (1024 * 1024 + 5), "text/plain"), ("ok.txt", TXT, "text/plain")]).json()["results"]
    assert [x["ok"] for x in res] == [False, True]
    assert "너무 큽니다" in res[0]["error"]


def test_upload_integrity_error_reported_as_duplicate(client, monkeypatch):
    from app.api import knowledge as api
    from sqlalchemy.exc import IntegrityError
    register(client)
    real = api.store.add_document
    calls = {"n": 0}

    def flaky(db, user, filename, data, visibility):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("x", {}, Exception("dup"))
        return real(db, user, filename, data, visibility)

    monkeypatch.setattr(api.store, "add_document", flaky)
    res = _up(client, [("a.txt", TXT, "text/plain"), ("b.txt", TXT + b"1", "text/plain")]).json()["results"]
    assert res[0]["ok"] is False and res[0]["error"] == "이미 올린 자료입니다."
    assert res[1]["ok"] is True


def test_switch_off_upload_409_list_and_delete_ok(client, monkeypatch):
    register(client)
    sid = _up(client, [("a.txt", TXT, "text/plain")]).json()["results"][0]["source"]["id"]
    monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
    r = _up(client, [("b.txt", TXT + b"1", "text/plain")])
    assert r.status_code == 409 and r.json()["detail"] == "자료 기능이 꺼져 있습니다."
    r = client.post(f"/api/knowledge/sources/{sid}/visibility", json={"visibility": "shareable"})
    assert r.status_code == 409 and r.json()["detail"] == "자료 기능이 꺼져 있습니다."
    lst = client.get("/api/knowledge/sources").json()
    assert lst["enabled"] is False and len(lst["sources"]) == 1
    assert client.post(f"/api/knowledge/sources/{sid}/delete").json() == {"ok": True}
    assert client.get("/api/knowledge/sources").json()["sources"] == []
    with SessionLocal() as db:
        assert db.scalar(select(KnowledgeChunk.id).limit(1)) is None


def test_visibility_change_and_validation(client):
    register(client)
    sid = _up(client, [("a.txt", TXT, "text/plain")]).json()["results"][0]["source"]["id"]
    r = client.post(f"/api/knowledge/sources/{sid}/visibility", json={"visibility": "shareable"})
    assert r.status_code == 200 and r.json()["visibility"] == "shareable"
    assert client.post(f"/api/knowledge/sources/{sid}/visibility", json={"visibility": "x"}).status_code == 422


def test_other_users_source_404_on_visibility_and_delete(client):
    register(client, "a@example.com")
    sid = _up(client, [("a.txt", TXT, "text/plain")]).json()["results"][0]["source"]["id"]
    other = make_client()
    register(other, "b@example.com")
    assert other.post(f"/api/knowledge/sources/{sid}/visibility", json={"visibility": "shareable"}).status_code == 404
    assert other.post(f"/api/knowledge/sources/{sid}/delete").status_code == 404
    assert other.get("/api/knowledge/sources").json()["sources"] == []
    with SessionLocal() as db:
        assert db.get(KnowledgeSource, sid) is not None


def test_requires_login():
    c = make_client()
    assert c.get("/api/knowledge/sources").status_code == 401
    assert c.post("/api/knowledge/sources", files=[("files", ("a.txt", TXT, "text/plain"))]).status_code == 401


def test_upload_rate_limited(client):
    register(client)
    for i in range(5):
        assert _up(client, [(f"{i}.txt", TXT + str(i).encode(), "text/plain")]).status_code == 200
    assert _up(client, [("z.txt", TXT + b"z", "text/plain")]).status_code == 429
    upload_limiter.reset()


def test_upload_requires_csrf_header(client):
    from fastapi.testclient import TestClient
    from app.main import app
    register(client)
    bare = TestClient(app)
    bare.cookies.update(client.cookies)
    r = bare.post("/api/knowledge/sources", files=[("files", ("a.txt", TXT, "text/plain"))])
    assert r.status_code == 403


def test_upload_unexpected_extract_error_saved_as_failed(client, monkeypatch):
    """설계 3.3: 추출 중 예상하지 못한 오류가 나도 그 파일만 실패한 자료로 남고 나머지는 올라간다."""
    from app.knowledge import extract

    real = extract._text_blocks

    def flaky(text):
        if "boom" in text:
            raise RuntimeError("iteration broke")
        return real(text)

    monkeypatch.setattr(extract, "_text_blocks", flaky)
    register(client)
    r = _up(client, [("a.txt", TXT, "text/plain"), ("b.txt", b"boom", "text/plain")])
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0]["ok"] is True and res[0]["source"]["status"] == "indexed"
    assert res[1]["ok"] is True and res[1]["source"]["status"] == "failed"
    assert res[1]["source"]["error"] == "파일을 읽는 중 문제가 생겼습니다. 다른 이름으로 저장해 다시 올려 주세요."


def test_upload_last_resort_error_only_that_file(client, monkeypatch, caplog):
    """저장 단계에서 어떤 오류가 나도 그 파일만 실패로 알리고 다음 파일을 계속 올린다. 로그에는 예외 타입 이름만."""
    from app.api import knowledge as api
    register(client)
    real = api.store.add_document

    def flaky(db, user, filename, data, visibility):
        if filename == "b.txt":
            raise RuntimeError("secret-content-should-not-be-logged")
        return real(db, user, filename, data, visibility)

    monkeypatch.setattr(api.store, "add_document", flaky)
    with caplog.at_level("WARNING"):
        r = _up(client, [("b.txt", TXT + b"1", "text/plain"), ("a.txt", TXT, "text/plain")])
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0] == {"filename": "b.txt", "ok": False, "error": "파일을 처리하지 못했습니다. 잠시 후 다시 올려 주세요.",
                      "source": None}
    assert res[1]["ok"] is True
    assert "secret-content" not in caplog.text and "RuntimeError" in caplog.text
    assert len(client.get("/api/knowledge/sources").json()["sources"]) == 1
