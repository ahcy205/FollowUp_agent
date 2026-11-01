"""숫자 단위(만 · 천 · 억)를 하나의 값으로 비교하고, 경고 값은 단위까지 보인다 (2026-11-01 설계)."""
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.mail import draft_checks as dc
from tests.test_reply_draft_sources import _answer, _doc, _generate, _setup


def _keys(text: str) -> list[str]:
    return [key for kind, key, _ in dc.tokens(text) if kind == "n"]


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("30만 원", "300000"),
        ("30만원", "300000"),
        ("1.5만", "15000"),
        ("3천", "3000"),
        ("5백", "500"),
        ("2억", "200000000"),
        ("8만 8천", "88000"),
        ("8만8천원", "88000"),
        ("1억 2천만", "120000000"),
        ("3백만", "3000000"),
        ("3만 5천 500", "35500"),
        ("1,200만", "12000000"),
    ],
)
def test_unit_number_is_one_value(text, key):
    assert _keys(text) == [key]


def test_not_integer_keeps_plain_number():
    assert _keys("1.23456만") == ["1.23456"]


def test_man_not_unit_when_word_continues():
    assert _keys("2만큼 더") == ["2"]
    assert _keys("10개만 주세요") == ["10"]


def test_dates_and_commas_unchanged():
    assert [k for _, k, _ in dc.tokens("11월 15일, 11/16, 2026-11-17")] == ["11/15", "11/16", "11/17"]
    assert _keys("선택지 1,2") == ["1", "2"]
    assert _keys("1,500원") == ["1500"]


def test_unit_and_plain_forms_support_each_other():
    assert dc.draft_warnings("무료 배송은 30만 원 이상입니다.", "메모: 300,000원 이상 무료", "")["unsupported"] == []
    assert dc.draft_warnings("무료 배송은 300,000원 이상입니다.", "메모: 30만 원 이상 무료", "")["unsupported"] == []
    assert dc.draft_warnings("원가는 8만 8천 원", "메모: 88000원", "")["unsupported"] == []


def test_shown_value_keeps_unit_words():
    w = dc.draft_warnings("배송: 30만 원 이상, 단가 1,500원, 수량 100개, 3개월 보증, 납기 4주, 할인 5%", "", "")
    assert w["unsupported"] == ["30만 원", "1,500원", "100개", "3개월", "4주", "5%"]


def test_same_value_different_forms_warned_once():
    w = dc.draft_warnings("30만 원 이상(300,000원)", "", "")
    assert w["unsupported"] == ["30만 원"]


@pytest.fixture
def _knowledge(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    monkeypatch.setattr(get_settings(), "knowledge_enabled", True)


def test_internal_value_caught_when_written_with_units(_knowledge):
    with SessionLocal() as db:
        u, t = _setup(db)
        _doc(db, u, "원가표.txt", "TS-200 원가 88000원", vis="internal")
        draft, _ = _generate(db, t, u, _answer(body="안녕하세요.\n단가는 8만 8천 원입니다.", facts=[]))
        assert [v["value"] for v in draft.sources["internal_values"]] == ["88000원"]


def test_doc_fact_with_units_matches_plain_line(_knowledge):
    with SessionLocal() as db:
        u, t = _setup(db, mail="무료 배송 기준 금액이 얼마인가요?", requests=("무료 배송 기준 금액 알려 주기",))
        _doc(db, u, "배송정책.txt", "무료 배송 기준 300000원 이상")
        draft, _ = _generate(
            db, t, u,
            _answer(body="안녕하세요.\n30만 원 이상 주문하시면 무료 배송입니다.",
                    facts=[{"text": "30만 원 이상 무료 배송", "source": "doc", "chunk": "D1-0", "line": 1}]),
        )
        assert draft.warnings["unsupported"] == []
        assert [f["checked"] for f in draft.sources["facts"]] == ["numbers"]
