"""다른 스레드로 온 수정 요청 알아보기 (설계 7.1 · 7.2): 같은 회사 후보 + 접수 분석 호출 안의 관계 판단."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.mail import related
from app.mail.inbox import merge_candidates
from app.mail.intake import INTAKE_SCHEMA, intake_work_mails
from app.mail.related import company_domain, judge_related, related_candidates
from app.models import Email, EmailDirection, Task, TaskStatus, User
from tests.conftest import ScriptedLLM

TZ = "Asia/Seoul"
BASE = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
_seq = iter(range(1, 1_000_000))

NEW_BODY = "안녕하세요. 앞서 김민수 과장이 요청드린 수량을 150개로 변경 부탁드립니다."
OLD_BODY = "기존 업무의 비밀 본문입니다. 다른 업무 판단에 보내면 안 됩니다."


def _user(db, email="u@example.com") -> int:
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    return u.id


def _mail(uid, *, sender, thread=None, content=NEW_BODY, task_id=None, minutes=0) -> Email:
    n = next(_seq)
    return Email(user_id=uid, sender=sender, receiver="me@example.com", subject="견적 요청", content=content,
                 direction=EmailDirection.INBOUND, processed=False, received_at=BASE + timedelta(minutes=minutes),
                 gmail_message_id=f"rel-msg-{n}", gmail_thread_id=thread or f"rel-thr-{n}", triage="work",
                 cc=[], gmail_meta={"labels": ["INBOX"], "sender_name": "박지은"}, task_id=task_id)


def _job(db, uid, sender, *, created_at=None, status=TaskStatus.ACTION_REQUIRED, title="견적서 요청", **over) -> Task:
    """보낸 사람이 sender인 메일 업무 하나(원본 메일 포함)."""
    n = next(_seq)
    t = Task(user_id=uid, title=title, goal="견적", status=status, origin="mail",
             gmail_thread_id=f"rel-job-{n}", plan=[], created_at=created_at or datetime.now(UTC) - timedelta(days=1),
             brief={"analysis": "done", "summary": f"{title} 요약", "requests": ["수량 100개 견적"],
                    "needs_reply": True}, **over)
    db.add(t)
    db.commit()
    m = _mail(uid, sender=sender, thread=t.gmail_thread_id, content=OLD_BODY, task_id=t.id)
    db.add(m)
    db.commit()
    t.brief = {**t.brief, "email_id": m.id}
    db.commit()
    return t


def _analysis(**over):
    base = {"summary": "수량 변경 요청", "counterpart": {"name": "박지은", "organization": "B상사"},
            "requests": ["수량 150개로 변경"], "todos": [], "priority": "NORMAL",
            "deadline": {"text": "", "date": "", "time": "", "kind": "none"}, "needs_reply": True, "suspicious": []}
    base.update(over)
    return base


def _run(db, uid, llm):
    r = intake_work_mails(db, uid, llm, max_mails=5, tz=TZ)
    db.expire_all()
    return r


def _new_task(db, uid) -> Task:
    return db.scalars(select(Task).where(Task.user_id == uid, Task.origin == "mail").order_by(Task.id.desc())).first()


# ---------------------------------------------------------------- 후보 고르기 (코드)

def test_company_domain():
    assert company_domain("kim@bsangsa.co.kr") == "bsangsa.co.kr"
    assert company_domain("Kim <KIM@BSangsa.CO.KR>") == "bsangsa.co.kr"
    assert company_domain("x@gmail.com") is None
    assert company_domain("x@Naver.com") is None
    assert company_domain("") is None and company_domain("no-at-sign") is None
    assert {"gmail.com", "naver.com", "daum.net", "proton.me"} <= related.FREE_MAIL_DOMAINS


def test_candidates_same_company_not_free_mail():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        uid = _user(db)
        colleague = _job(db, uid, "park@bsangsa.co.kr", created_at=now - timedelta(days=2))
        same = _job(db, uid, "kim@bsangsa.co.kr", created_at=now - timedelta(days=1))
        waiting = _job(db, uid, "lee@bsangsa.co.kr", created_at=now - timedelta(days=3), status=TaskStatus.WAITING)
        _job(db, uid, "choi@bsangsa.co.kr", created_at=now - timedelta(days=4))  # 4번째: 최대 3개
        _job(db, uid, "old@bsangsa.co.kr", created_at=now - timedelta(days=15))  # 14일 지남
        _job(db, uid, "done@bsangsa.co.kr", created_at=now - timedelta(hours=5), status=TaskStatus.COMPLETED)
        _job(db, uid, "ref@bsangsa.co.kr", created_at=now - timedelta(hours=4), status=TaskStatus.PENDING)
        merged = _job(db, uid, "m@bsangsa.co.kr", created_at=now - timedelta(hours=3), status=TaskStatus.COMPLETED)
        merged.merged_into_id = same.id
        _job(db, uid, "kim@othercorp.com", created_at=now - timedelta(hours=2))
        other_user = _job(db, _user(db, "x@example.com"), "kim@bsangsa.co.kr", created_at=now - timedelta(hours=2))
        new = _job(db, uid, "kim@bsangsa.co.kr", created_at=now)
        db.commit()
        got = related_candidates(db, new, "kim@bsangsa.co.kr", now)
        assert [t.id for t in got] == [same.id, colleague.id, waiting.id]
        assert other_user.id not in [t.id for t in got]

        # 무료 메일: 같은 주소만 후보, 같은 도메인의 다른 주소는 아니다
        g1 = _job(db, uid, "me.friend@gmail.com", created_at=now - timedelta(days=1))
        _job(db, uid, "stranger@gmail.com", created_at=now - timedelta(days=1))
        g_new = _job(db, uid, "ME.friend@gmail.com", created_at=now)
        assert [t.id for t in related_candidates(db, g_new, "me.friend@gmail.com", now)] == [g1.id]


# ---------------------------------------------------------------- 판단 함수 경계

def _cands(*ids):
    return [Task(id=i, title=f"업무 {i}") for i in ids]


def test_judge_suggested_with_evidence():
    data = {"related_task": 12, "relation": "change", "relation_evidence": "수량을 150개로 변경 부탁드립니다"}
    assert judge_related(data, _cands(7, 12), NEW_BODY) == {
        "state": "suggested", "task_id": 12, "relation": "change", "evidence": "수량을 150개로 변경 부탁드립니다"}
    data = {"related_task": 7, "relation": "followup", "relation_evidence": "앞서 김민수 과장이 요청드린"}
    assert judge_related(data, _cands(7), NEW_BODY)["relation"] == "followup"


def test_judge_none_when_evidence_missing():
    base = {"related_task": 12, "relation": "change"}
    assert judge_related({**base, "relation_evidence": "수량을 200개로 바꿔 주세요"}, _cands(12), NEW_BODY) == \
        {"state": "none"}
    assert judge_related({**base, "relation_evidence": ""}, _cands(12), NEW_BODY) == {"state": "none"}
    assert judge_related({**base, "relation": "none", "relation_evidence": "수량을 150개로 변경 부탁드립니다"},
                         _cands(12), NEW_BODY) == {"state": "none"}
    assert judge_related({}, _cands(12), NEW_BODY) == {"state": "none"}  # 필드를 빠뜨린 응답


def test_judge_rejects_non_candidate_id():
    data = {"related_task": 99, "relation": "change", "relation_evidence": "수량을 150개로 변경 부탁드립니다"}
    assert judge_related(data, _cands(12), NEW_BODY) == {"state": "none"}
    for bad in (None, "12", True, 12.0):
        assert judge_related({**data, "related_task": bad}, _cands(12), NEW_BODY) == {"state": "none"}
    assert judge_related({**data, "related_task": 12, "relation": "maybe"}, _cands(12), NEW_BODY) == \
        {"state": "none"}


# ---------------------------------------------------------------- 접수 분석과 함께 (같은 호출)

def test_schema_has_optional_relation_fields():
    props = INTAKE_SCHEMA["properties"]
    assert props["relation"]["enum"] == ["change", "followup", "none"]
    assert "related_task" in props and "relation_evidence" in props
    assert "related_task" not in INTAKE_SCHEMA["required"]  # 3단계 단일 메일 분석은 빠뜨려도 된다


def test_prompt_has_candidates_only_when_any():
    with SessionLocal() as db:
        uid = _user(db)
        db.add(_mail(uid, sender="kim@bsangsa.co.kr"))
        db.commit()
        llm = ScriptedLLM(structured_steps=[_analysis()])
        _run(db, uid, llm)
        assert "관련 업무 후보" not in llm.structured_prompts[0]
        assert _new_task(db, uid).related == {"state": "none"}  # 후보가 없으면 판단할 것도 없다

    with SessionLocal() as db:
        uid = _user(db, "v@example.com")
        a = _job(db, uid, "park@bsangsa.co.kr", title="견적서 요청")
        db.add(_mail(uid, sender="kim@bsangsa.co.kr"))
        db.commit()
        llm = ScriptedLLM(structured_steps=[_analysis(related_task=None, relation="none", relation_evidence="")])
        _run(db, uid, llm)
        p = llm.structured_prompts[0]
        assert "관련 업무 후보" in p and f"업무 #{a.id}" in p and "견적서 요청 요약" in p
        assert OLD_BODY not in p  # 다른 업무의 메일 본문은 보내지 않는다(설계 10절)
        assert p.count("<untrusted_external_content") >= 3  # 헤더 · 본문 · 후보 블록 모두 감싼다


def test_suggested_through_intake():
    with SessionLocal() as db:
        uid = _user(db)
        a = _job(db, uid, "park@bsangsa.co.kr")
        db.add(_mail(uid, sender="kim@bsangsa.co.kr"))
        db.commit()
        _run(db, uid, ScriptedLLM(structured_steps=[_analysis(
            related_task=a.id, relation="change", relation_evidence="수량을 150개로 변경 부탁드립니다")]))
        new = _new_task(db, uid)
        assert new.related == {"state": "suggested", "task_id": a.id, "relation": "change",
                               "evidence": "수량을 150개로 변경 부탁드립니다"}
        assert [t.id for t in merge_candidates(db, new, datetime.now(UTC))] == [a.id]


def test_same_sender_but_llm_none_no_suggestion():
    with SessionLocal() as db:
        uid = _user(db)
        _job(db, uid, "kim@bsangsa.co.kr")
        db.add(_mail(uid, sender="kim@bsangsa.co.kr"))
        db.commit()
        _run(db, uid, ScriptedLLM(structured_steps=[_analysis(related_task=None, relation="none",
                                                              relation_evidence="")]))
        new = _new_task(db, uid)
        assert new.related == {"state": "none"}
        assert merge_candidates(db, new, datetime.now(UTC)) == []


def test_unjudged_falls_back_to_same_sender():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        uid = _user(db)
        a = _job(db, uid, "kim@bsangsa.co.kr", created_at=now - timedelta(days=1))
        _job(db, uid, "park@bsangsa.co.kr", created_at=now - timedelta(hours=5))  # 같은 회사지만 다른 사람
        for related_value in ({"state": "unjudged"}, {}):
            new = _job(db, uid, "kim@bsangsa.co.kr", created_at=now, related=related_value)
            assert [t.id for t in merge_candidates(db, new, now)] == [a.id]


def test_suggested_target_no_longer_qualifies():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        uid = _user(db)
        a = _job(db, uid, "park@bsangsa.co.kr", created_at=now - timedelta(days=1))
        new = _job(db, uid, "kim@bsangsa.co.kr", created_at=now,
                   related={"state": "suggested", "task_id": a.id, "relation": "change", "evidence": "수량을 150개로"})
        assert [t.id for t in merge_candidates(db, new, now)] == [a.id]
        a.status = TaskStatus.COMPLETED
        db.commit()
        assert merge_candidates(db, new, now) == []


def test_invalid_answer_marks_unjudged():
    with SessionLocal() as db:
        uid = _user(db)
        _job(db, uid, "kim@bsangsa.co.kr")
        db.add(_mail(uid, sender="kim@bsangsa.co.kr"))
        db.commit()
        _run(db, uid, ScriptedLLM(structured_steps=[{"bad": 1}]))
        assert _new_task(db, uid).related == {"state": "unjudged"}


def test_api_candidate_carries_evidence(client):
    from tests.conftest import register
    me = register(client)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        a = _job(db, me["id"], "park@bsangsa.co.kr", created_at=now - timedelta(days=1))
        b = _job(db, me["id"], "kim@bsangsa.co.kr", created_at=now,
                 related={"state": "suggested", "task_id": a.id, "relation": "change",
                          "evidence": "수량을 150개로 변경 부탁드립니다"})
        c = _job(db, me["id"], "kim@bsangsa.co.kr", created_at=now + timedelta(seconds=1),
                 related={"state": "unjudged"})
        a_id, b_id, c_id = a.id, b.id, c.id
    mail = client.get(f"/api/tasks/{b_id}").json()["mail"]
    assert mail["merge_candidates"] == [{"task_id": a_id, "title": "견적서 요청",
                                         "evidence": "수량을 150개로 변경 부탁드립니다"}]
    assert mail["merge_analyzing"] is False
    mail = client.get(f"/api/tasks/{c_id}").json()["mail"]
    assert [m["evidence"] for m in mail["merge_candidates"]] == [None]  # 5단계 규칙 후보에는 근거가 없다
