"""RAG 1단계 Task 5: 답장 초안에 답장에 써도 되는 자료를 넣고, 인용한 줄로 다시 확인한다."""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.knowledge import search, store
from app.mail import reply_draft as rd
from app.models import Email, EmailDirection, GoogleAccount, Task, TaskStatus, User
from tests.conftest import ScriptedLLM

TZ = "Asia/Seoul"
UTC = timezone.utc
NOW = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)
ME = "me@gmail.com"
PRICE_CSV = "품번,단가,최소수량\nTS-200,12000,100\nTS-300,5000,50\n"
COST_CSV = "품번,원가\nTS-200,8800\n"


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    monkeypatch.setattr(get_settings(), "knowledge_enabled", True)


def _setup(db, *, mail="TS-200 100개 단가 알려 주세요.", memo="", requests=("TS-200 단가 알려 주기",)):
    u = User(email="u@example.com", password_hash="x")
    db.add(u)
    db.commit()
    db.add(GoogleAccount(user_id=u.id, google_email=ME, refresh_token_enc="enc", scopes=[]))
    t = Task(user_id=u.id, title="견적 요청", goal="견적서 송부", status=TaskStatus.ACTION_REQUIRED, origin="mail",
             gmail_thread_id="thr-1", priority="NORMAL",
             plan=[{"id": 1, "title": "단가 확인", "done": False, "evidence": memo, "due_at": None,
                    "source": "agent"}],
             brief={"analysis": "done", "summary": "TS-200 견적 요청", "needs_reply": True,
                    "requests": list(requests)})
    db.add(t)
    db.commit()
    db.add(Email(user_id=u.id, sender="kim@partner.co.kr", receiver=ME, subject="견적 문의", content=mail,
                 direction=EmailDirection.INBOUND, processed=True, received_at=NOW, gmail_message_id="m-1",
                 gmail_thread_id="thr-1", triage="work", task_id=t.id, cc=[], gmail_meta={}))
    db.commit()
    return u, t


def _doc(db, u, name, text, vis="shareable"):
    s = store.add_document(db, u, name, text.encode("utf-8"), vis)
    db.commit()
    return s


def _answer(body="안녕하세요.\nTS-200 단가는 개당 12,000원입니다.", facts=None):
    if facts is None:
        facts = [{"text": "개당 12,000원", "source": "doc", "chunk": "D1-0", "line": 2}]
    return {"subject": "Re: 견적 문의", "body": body, "used_facts": facts, "suspicious": []}


def _generate(db, t, u, *answers, purpose="reply", user_input=""):
    llm = ScriptedLLM(structured_steps=list(answers))
    draft = rd.generate_draft(db, t, u, llm, purpose=purpose, user_input=user_input, tz=TZ)
    db.commit()
    return draft, llm


def test_shareable_chunk_in_prompt_internal_never():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        _doc(db, u, "원가표.csv", COST_CSV, vis="internal")
        _, llm = _generate(db, t, u, _answer())
        prompt = llm.structured_prompts[0]
        assert "내 자료 (답장에 써도 되는 자료)" in prompt
        assert "[자료 D1-0] 단가표.csv" in prompt
        assert "2: 품번: TS-200 · 단가: 12000 · 최소수량: 100" in prompt
        at = prompt.index("품번: TS-200 · 단가: 12000")
        assert prompt.rfind("<untrusted_external_content", 0, at) > prompt.rfind("</untrusted_external_content>", 0, at)
        assert "8800" not in prompt and "원가표" not in prompt


def test_doc_fact_kept_with_line():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, _ = _generate(db, t, u, _answer())
        src = draft.sources
        assert [c["ref"] for c in src["chunks"]] == ["D1-0"]
        assert src["chunks"][0]["visibility"] == "shareable" and src["chunks"][0]["title"] == "단가표.csv"
        assert len(src["facts"]) == 1
        fact = src["facts"][0]
        assert fact["line_text"] == "품번: TS-200 · 단가: 12000 · 최소수량: 100"
        assert fact["checked"] == "numbers" and fact["ref"] == "D1-0" and fact["line"] == 2
        assert fact["other_item"] is False
        assert draft.warnings["unsupported"] == []
        assert draft.warnings["other_item"] == 0 and draft.warnings["internal_values"] == 0
        assert src["search_failed"] is False and src["sources_changed"] is False


@pytest.mark.parametrize("chunk,line", [("D9-0", 2), ("D1-0", 9), ("D1-0", 0), ("D1-0", "2")])
def test_doc_fact_wrong_chunk_or_line_dropped(chunk, line):
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        facts = [{"text": "개당 12,000원", "source": "doc", "chunk": chunk, "line": line}]
        draft, _ = _generate(db, t, u, _answer(facts=facts))
        assert draft.sources["facts"] == []
        assert "12000" in draft.warnings["unsupported"]  # 인용 줄이 없으니 근거도 없다


def test_doc_fact_number_not_in_line_dropped():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        facts = [{"text": "개당 12,000원", "source": "doc", "chunk": "D1-0", "line": 3}]  # TS-300 줄
        draft, _ = _generate(db, t, u, _answer(facts=facts))
        assert draft.sources["facts"] == []


def test_doc_fact_number_not_in_body_dropped():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\n단가는 [[단가 입력]]입니다."))
        assert draft.sources["facts"] == []


def test_other_item_warning_on_similar_code():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", "품번,단가\nTS-2000,12000\n")
        draft, _ = _generate(db, t, u, _answer())
        assert len(draft.sources["facts"]) == 1
        assert draft.sources["facts"][0]["other_item"] is True
        assert draft.warnings["other_item"] == 1


def test_non_numeric_fact_manual():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "조건.txt", "TS-200 주문 조건\nTS-200 무료 배송\n")
        facts = [{"text": "무료 배송", "source": "doc", "chunk": "D1-0", "line": 2}]
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\nTS-200은 무료 배송입니다.", facts=facts))
        assert len(draft.sources["facts"]) == 1
        assert draft.sources["facts"][0]["checked"] == "manual"
        assert draft.sources["facts"][0]["line_text"] == "TS-200 무료 배송"


def test_only_cited_line_counts_as_evidence():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        body = "안녕하세요.\nTS-200 단가는 개당 12,000원이고, 묶음 할인은 5,000원입니다."
        draft, _ = _generate(db, t, u, _answer(body=body))
        assert len(draft.sources["facts"]) == 1
        assert "5000" in draft.warnings["unsupported"]
        assert "12000" not in draft.warnings["unsupported"]


def test_internal_value_warned_even_from_memo():
    with SessionLocal() as db:
        u, t = _setup(db, memo="원가 8,000원")
        _doc(db, u, "원가표.txt", "TS-200 원가 8,000원", vis="internal")
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\n단가는 8,000원입니다.",
                                              facts=[{"text": "원가 8,000원", "source": "memo",
                                                      "chunk": "", "line": 0}]))
        assert draft.warnings["internal_values"] == 1
        assert draft.sources["internal_values"] == [{"value": "8000원", "source_ids": [1], "titles": ["원가표.txt"]}]
        assert "8000" not in draft.warnings["unsupported"]  # 메모에 있으니 근거는 있다


def test_internal_value_small_won_amount():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "수수료.txt", "카드 수수료 50원", vis="internal")
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\n수수료는 50 원입니다.", facts=[]))
        assert [v["value"] for v in draft.sources["internal_values"]] == ["50원"]
        draft2, _ = _generate(db, t, u, _answer(body="안녕하세요.\n수량은 50개입니다.", facts=[]))
        assert draft2.sources["internal_values"] == [] and draft2.warnings["internal_values"] == 0


def test_internal_value_in_thread_mail_not_warned():
    with SessionLocal() as db:
        u, t = _setup(db, mail="TS-200 단가를 8,000원으로 맞춰 주실 수 있나요?")
        _doc(db, u, "원가표.txt", "TS-200 원가 8,000원", vis="internal")
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\n8,000원은 어렵습니다.", facts=[]))
        assert draft.sources["internal_values"] == []


def test_followup_does_not_search(monkeypatch):
    calls = []
    monkeypatch.setattr(search, "search_shareable", lambda *a, **k: calls.append(1) or [])
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, llm = _generate(db, t, u, _answer(facts=[]), purpose="followup")
        assert calls == []
        assert "내 자료" not in llm.structured_prompts[0]
        assert draft.sources["chunks"] == []


def test_switch_off_no_search(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
    calls = []
    monkeypatch.setattr(search, "search_shareable", lambda *a, **k: calls.append(1) or [])
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, llm = _generate(db, t, u, _answer())
        assert calls == []
        assert "12000 · 최소수량" not in llm.structured_prompts[0]
        assert draft.sources["chunks"] == [] and draft.sources["facts"] == []


def test_search_error_still_creates_draft(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("index broken")
    monkeypatch.setattr(search, "search_shareable", boom)
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, _ = _generate(db, t, u, _answer())
        assert draft.status == "draft"
        assert draft.sources["search_failed"] is True
        assert draft.sources["chunks"] == [] and draft.sources["facts"] == []


def test_regenerate_overwrites_sources():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, _ = _generate(db, t, u, _answer())
        assert len(draft.sources["facts"]) == 1
        draft2, _ = _generate(db, t, u, _answer(body="안녕하세요.\n단가는 [[단가 입력]]입니다.", facts=[]))
        assert draft2.id == draft.id
        assert draft2.sources["facts"] == [] and [c["ref"] for c in draft2.sources["chunks"]] == ["D1-0"]
    with SessionLocal() as db:
        stored = db.get(type(draft), draft.id)
        assert stored.sources["facts"] == []


def test_edit_removes_stale_doc_fact():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, _ = _generate(db, t, u, _answer())
        assert len(draft.sources["facts"]) == 1
        rd.update_draft(draft, subject="Re: 견적 문의", body="안녕하세요.\nTS-200 단가는 개당 13,000원입니다.",
                        cc_selected=[])
        db.commit()
        assert draft.sources["facts"] == []
        assert "13000" in draft.warnings["unsupported"]
        assert [c["ref"] for c in draft.sources["chunks"]] == ["D1-0"]  # 사본은 남는다


def test_edit_keeps_doc_fact_and_rechecks_internal():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        _doc(db, u, "원가표.txt", "TS-200 원가 8,800원", vis="internal")
        draft, _ = _generate(db, t, u, _answer())
        assert draft.warnings["internal_values"] == 0
        rd.update_draft(draft, subject="Re: 견적 문의",
                        body="안녕하세요.\nTS-200 단가는 개당 12,000원입니다. 최저 8,800원까지 됩니다.", cc_selected=[])
        db.commit()
        assert len(draft.sources["facts"]) == 1
        assert draft.warnings["internal_values"] == 1
        assert draft.sources["internal_values"][0]["value"] == "8800원"


def test_check_facts_dates_and_no_task_ids():
    chunks = [{"ref": "D1-0", "lines": ["TS-300 납기 11월 15일"]}]
    facts = [{"text": "11월 15일 납품", "source": "doc", "chunk": "D1-0", "line": 1}]
    from app.knowledge.draft_sources import check_facts
    out = check_facts("납품은 11/15에 됩니다.", facts, chunks, set())
    assert len(out) == 1 and out[0]["other_item"] is False and out[0]["checked"] == "numbers"


def test_build_query_parts():
    from app.knowledge.draft_sources import build_query
    with SessionLocal() as db:
        u, t = _setup(db, mail="본문" + "가" * 600)
        emails = rd._emails(db, t)
        q = build_query(t, emails, "추가 입력")
        assert "TS-200 견적 요청" in q and "TS-200 단가 알려 주기" in q and "추가 입력" in q
        assert "가" * 498 in q and "가" * 499 not in q


def test_refresh_draft_checks_uses_stored_copy(monkeypatch):
    from app.knowledge.draft_sources import refresh_draft_checks
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, _ = _generate(db, t, u, _answer())
        before = draft.sources
        monkeypatch.setattr(search, "search_shareable", lambda *a, **k: pytest.fail("no search on refresh"))
        draft.body = "안녕하세요.\nTS-200 단가는 개당 13,000원입니다."
        refresh_draft_checks(db, draft)
        assert draft.sources is not before  # 새 객체 대입(JSON 변경 감지)
        assert draft.sources["facts"] == [] and "13000" in draft.warnings["unsupported"]
        assert [c["ref"] for c in draft.sources["chunks"]] == ["D1-0"]


def test_draft_out_includes_sources():
    from app.api.tasks import _draft_out
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        _generate(db, t, u, _answer())
        out = _draft_out(db, t, NOW)
        assert out.sources["facts"][0]["ref"] == "D1-0"
        assert out.warnings["internal_values"] == 0 and out.warnings["other_item"] == 0


def test_switch_off_no_internal_check(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
    calls = []
    monkeypatch.setattr(search, "internal_value_index", lambda *a, **k: calls.append(1) or {})
    with SessionLocal() as db:
        u, t = _setup(db, memo="원가 8,000원")
        _doc(db, u, "원가표.txt", "TS-200 원가 8,000원", vis="internal")
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\n단가는 8,000원입니다.", facts=[]))
        assert calls == []
        assert draft.sources["internal_values"] == [] and draft.warnings["internal_values"] == 0
        assert draft.warnings["other_item"] == 0


def test_switch_off_edit_stored_line_not_evidence(monkeypatch):
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "단가표.csv", PRICE_CSV)
        draft, _ = _generate(db, t, u, _answer())
        assert len(draft.sources["facts"]) == 1
        monkeypatch.setattr(get_settings(), "knowledge_enabled", False)
        monkeypatch.setattr(search, "internal_value_index", lambda *a, **k: pytest.fail("no index when off"))
        rd.update_draft(draft, subject="Re: 견적 문의", body="안녕하세요.\nTS-200 단가는 개당 12,000원입니다.",
                        cc_selected=[])
        db.commit()
        assert "12000" in draft.warnings["unsupported"]
        assert [c["ref"] for c in draft.sources["chunks"]] == ["D1-0"]  # 표시용 사본은 그대로
        assert draft.warnings["internal_values"] == 0


def test_manual_fact_line_not_evidence():
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "조건.txt", "TS-200 주문 조건\nTS-200 무료 배송, 반품비 5,000원\n")
        facts = [{"text": "무료 배송", "source": "doc", "chunk": "D1-0", "line": 2}]
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\nTS-200은 무료 배송이고 반품비는 5,000원입니다.",
                                              facts=facts))
        assert draft.sources["facts"][0]["checked"] == "manual"
        assert "5000" in draft.warnings["unsupported"]


def test_check_facts_day_only_matches_full_date_in_body():
    from app.knowledge.draft_sources import check_facts
    chunks = [{"ref": "D1-0", "lines": ["TS-200 납기 11월 15일"]}]
    facts = [{"text": "15일 납품", "source": "doc", "chunk": "D1-0", "line": 1}]
    out = check_facts("TS-200 납품은 11/15입니다.", facts, chunks, {"TS-200"})
    assert len(out) == 1 and out[0]["checked"] == "numbers" and out[0]["other_item"] is False
