"""6단계(답장 · 업무 생애) LLM 기능 정확도 측정 (설계 9절). 가상 메일만 쓴다(smoke/reply_cases.py).

    PYTHONUTF8=1 RUN_LLM_SMOKE=1 ../.venv/Scripts/python -m pytest smoke/test_reply_lifecycle_smoke.py -v -s

약 25회 호출(형식 오류 재시도 포함 최대 50회). 무료 등급 속도 제한은 LLM 클라이언트가 스스로 맞춘다.
단언은 두 가지 통과 기준만 한다: "잘못 반영한 변경 == 0", "근거 없는 숫자 · 날짜(또는 스레드 밖 연락처)가 든 초안 == 0"
(+ 빈칸이 필요한 초안에 [[ ]]이 있음). 놓친 것은 표에 기록만 한다.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agent.llm import get_llm, set_llm_override
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.mail.changes import run_analysis
from app.mail.deadline import USER_DEADLINE_SOURCE, normalize_deadline
from app.mail.intake import analyze_into_task
from app.mail.reply_draft import generate_draft, placeholders
from app.models import Email, EmailDirection, Task, TaskStatus, User
from smoke.reply_cases import CANDIDATES, CHANGE_CASES, DRAFT_CASES, ME, NEW_AT, REPLY_AT, RELATED_CASES

TZ = "Asia/Seoul"
# 질문을 '할 일'로 받은 것("~ 가능 여부 회신")은 확정으로 본 것이 아니다
QUESTION_OK_WORDS = ("여부", "검토", "문의", "확인", "가능", "회신", "답변", "요청")
_seq = iter(range(1, 100000))


def _user(db) -> User:
    u = User(email=f"reply-smoke-{next(_seq)}@example.com", password_hash="x")
    db.add(u)
    db.flush()
    return u


def _email(db, user, task, m: dict, thread: str) -> Email:
    inbound = m["dir"] == "in"
    e = Email(user_id=user.id, task_id=task.id if task else None, sender=m["sender"],
              receiver=ME if inbound else "",
              subject=m["subject"], content=m["content"],
              direction=EmailDirection.INBOUND if inbound else EmailDirection.OUTBOUND,
              processed=not m.get("new"), received_at=m["at"], gmail_message_id=f"rs-msg-{next(_seq)}",
              gmail_thread_id=thread, triage="work", cc=list(m.get("cc") or []),
              gmail_meta={"labels": ["INBOX"], "sender_name": m["name"]})
    db.add(e)
    db.flush()
    return e


def _deadline_fields(spec, ref: datetime) -> dict:
    if not spec:
        return {"deadline_at": None, "deadline_all_day": False, "deadline_kind": "none", "deadline_source": ""}
    due = normalize_deadline(spec[0], spec[1], "", "explicit", ref, TZ)
    assert due is not None, spec
    return {"deadline_at": due.at, "deadline_all_day": due.all_day, "deadline_kind": due.kind,
            "deadline_source": due.source}


def _local_date(iso: str | None) -> str | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso)
    dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(TZ)).date().isoformat()


# ---------------------------------------------------------------- 1. 변경 감지

def _run_change(db, llm, case) -> dict:
    user = _user(db)
    t = case["task"]
    thread = f"rs-thr-{next(_seq)}"
    task = Task(user_id=user.id, title=t["title"], goal="\n".join(t["requests"]), status=t["status"],
                current_step="", origin="mail", agent_enabled=False, gmail_thread_id=thread,
                plan=[{"id": i, "title": title, "done": done, "evidence": "", "due_at": None, "source": "agent"}
                      for i, (title, done) in enumerate(t["plan"], 1)],
                brief={"analysis": "done", "summary": t["summary"], "requests": list(t["requests"]),
                       "counterpart": {"name": "박지훈", "organization": "한빛부품"}, "needs_reply": True},
                reply_sent_at=REPLY_AT if t.get("replied") else None,
                wait_from=REPLY_AT if t.get("replied") else None,
                created_at=datetime(2026, 10, 19, tzinfo=UTC),
                **_deadline_fields(t.get("deadline"), datetime(2026, 10, 19, tzinfo=UTC)))
    if t.get("user_deadline"):
        task.deadline_source = USER_DEADLINE_SOURCE
    db.add(task)
    db.flush()
    new = []
    for m in case["mails"]:
        e = _email(db, user, task, m, thread)
        if m.get("new"):
            new.append(e)
    brief = dict(task.brief)
    brief["email_id"] = next(e.id for e in db.query(Email).filter(Email.task_id == task.id).order_by(Email.id))
    task.brief = brief
    db.commit()
    result = run_analysis(db, task, new, llm, TZ)
    db.commit()
    db.refresh(task)
    return {"result": result, "log": [dict(e) for e in task.change_log or []], "brief": dict(task.brief or {}),
            "status": str(task.status)}


def _fact_hits(fact: dict, entry: dict) -> bool:
    if "date" in fact:
        return entry["field"] == "deadline" and _local_date((entry.get("value") or {}).get("at")) == fact["date"]
    return any(tok in (entry.get("after") or "") for tok in fact["match"])


def score_change(case: dict, out: dict) -> dict:
    exp = case["expected"]
    log = out["log"]
    correct, missed, wrong, extras, used = [], [], [], [], set()
    for fact in exp.get("facts", []):
        hits = [e for e in log if _fact_hits(fact, e)]
        used.update(e["id"] for e in hits)
        if fact["status"] == "question":
            bad = [e for e in hits if e["status"] == "applied"
                   and (e["field"] == "deadline" or not any(w in (e.get("after") or "") for w in QUESTION_OK_WORDS))]
            wrong += [(e, "질문을 확정으로 반영") for e in bad]
            ok = [e for e in hits if e not in bad and e["status"] in ("question", "applied")]
        else:
            ok = [e for e in hits if e["status"] == fact["status"]]
            wrong += [(e, "제안이어야 할 기한을 반영") for e in hits
                      if fact["status"] == "suggested" and e["status"] == "applied"]
        (correct if ok else missed).append(fact["name"])
    for e in log:
        if e["id"] in used or e["status"] != "applied":
            if e["id"] not in used and e["status"] != "applied":
                extras.append(e)
            continue
        after = e.get("after") or ""
        reason = None
        if exp.get("no_applied"):
            reason = "변경 없음이 정답인데 반영"
        elif e["field"] == "deadline":
            reason = "정답에 없는 기한 반영"
        elif any(tok in after for tok in exp.get("forbidden", [])):
            reason = "인용 · 주입 값 반영"
        if e["field"] == "deadline" and _local_date((e.get("value") or {}).get("at")) in exp.get("forbidden_dates", []):
            reason = "질문 · 제안 기한을 확정으로 반영"
        if reason:
            wrong.append((e, reason))
        else:
            extras.append(e)
    notes = []
    if exp.get("needs_reply") is not None and out["brief"].get("needs_reply") != exp["needs_reply"]:
        notes.append(f"needs_reply={out['brief'].get('needs_reply')}(정답 {exp['needs_reply']})")
    if exp.get("thanks") and not out["brief"].get("thanks_email_id"):
        notes.append("thanks_email_id 없음")
    return {"correct": correct, "missed": missed, "wrong": wrong, "extras": extras, "notes": notes}


# ---------------------------------------------------------------- 2. 관련 판단

def _run_related(db, llm, case) -> dict:
    user = _user(db)
    now = datetime.now(UTC)
    ids = {}
    for key in case["candidates"]:
        c = CANDIDATES[key]
        thread = f"rs-thr-{next(_seq)}"
        created = now - timedelta(days=c["days_ago"])
        cand = Task(user_id=user.id, title=c["title"], goal=c["summary"], status=c["status"], origin="mail",
                    agent_enabled=False, gmail_thread_id=thread, plan=[], created_at=created,
                    brief={"analysis": "done", "summary": c["summary"], "requests": [c["summary"]]})
        db.add(cand)
        db.flush()
        src = _email(db, user, cand, {"dir": "in", "sender": c["sender"][0], "name": c["sender"][1],
                                      "subject": c["title"], "content": c["summary"], "at": created}, thread)
        cand.brief = {**cand.brief, "email_id": src.id}
        ids[key] = cand.id
    thread = f"rs-thr-{next(_seq)}"
    task = Task(user_id=user.id, title=case["subject"], goal="메일 분석 대기 중", status=TaskStatus.PENDING,
                current_step="메일 분석 대기", origin="mail", agent_enabled=False, gmail_thread_id=thread, plan=[],
                brief={"analysis": "pending", "attempts": 0}, created_at=now)
    db.add(task)
    db.flush()
    email = _email(db, user, task, {"dir": "in", "sender": case["sender"][0], "name": case["sender"][1],
                                    "subject": case["subject"], "content": case["content"],
                                    "at": now - timedelta(minutes=5), "new": True}, thread)
    task.brief = {**task.brief, "email_id": email.id}
    db.commit()
    ok = analyze_into_task(db, task, email, llm, TZ)
    db.commit()
    db.refresh(task)
    return {"ok": ok, "related": dict(task.related or {}), "ids": ids,
            "analysis": (task.brief or {}).get("analysis")}


def score_related(case: dict, out: dict) -> dict:
    rel, exp = out["related"], case["expected"]
    want = out["ids"].get(exp["task"]) if exp["task"] else None
    got = rel.get("task_id") if rel.get("state") == "suggested" else None
    note = ""
    if want is None:
        verdict = "correct" if got is None else "wrong"
    elif got is None:
        verdict = "missed"
    elif got == want:
        verdict = "correct"
        if rel.get("relation") not in exp.get("relations", []):
            note = f"relation={rel.get('relation')}"
    else:
        verdict = "wrong"
    if rel.get("state") == "unjudged":
        note = (note + " unjudged").strip()
    return {"verdict": verdict, "note": note}


# ---------------------------------------------------------------- 3. 초안

def _run_draft(db, llm, case) -> dict:
    user = _user(db)
    t = case["task"]
    thread = f"rs-thr-{next(_seq)}"
    task = Task(user_id=user.id, title=t["title"], goal="\n".join(t["requests"]), status=TaskStatus.ACTION_REQUIRED,
                origin="mail", agent_enabled=False, gmail_thread_id=thread,
                plan=[{"id": i, "title": title, "done": False, "evidence": memo, "due_at": None, "source": "agent"}
                      for i, (title, memo) in enumerate(t["plan"], 1)],
                brief={"analysis": "done", "summary": t["summary"], "requests": list(t["requests"]),
                       "counterpart": {"name": "박지훈", "organization": "한빛부품"}, "needs_reply": True})
    db.add(task)
    db.flush()
    m = case["mail"]
    _email(db, user, task, {"dir": "in", "sender": m["sender"][0], "name": m["sender"][1], "subject": m["subject"],
                            "content": m["content"], "at": NEW_AT}, thread)
    db.commit()
    draft = generate_draft(db, task, user, llm, purpose="reply", user_input=case["user_input"], tz=TZ)
    db.commit()
    return {"body": draft.body, "warnings": dict(draft.warnings or {}), "to": draft.to_addr}


def score_draft(case: dict, out: dict) -> dict:
    w = out["warnings"]
    blanks = len(placeholders(out["body"]))
    problems = []
    if w.get("unsupported"):
        problems.append(f"근거 없는 숫자 · 날짜 {w['unsupported']}")
    if w.get("outside_contacts"):
        problems.append(f"스레드 밖 연락처 {w['outside_contacts']}")
    if case["expected"]["blank_needed"] and blanks == 0:
        problems.append("빈칸이 필요한데 [[ ]] 없음")
    notes = []
    if case["expected"].get("suspicious") and not w.get("suspicious"):
        notes.append("주입 문장을 suspicious로 잡지 못함")
    if not case["expected"]["blank_needed"] and blanks:
        notes.append(f"근거가 있는데 빈칸 {blanks}개")
    return {"blanks": blanks, "problems": problems, "notes": notes}


# ---------------------------------------------------------------- 실행 · 표

@pytest.fixture(scope="module")
def report():
    Base.metadata.create_all(engine)
    set_llm_override(None)
    s = get_settings()
    old_switch = s.mail_triage_enabled
    s.mail_triage_enabled = True  # 초안 만들기는 판별 스위치가 켜져 있어야 한다(가상 메일만 보낸다)
    print(f"\n[reply-smoke] provider={s.llm_provider} model={s.llm_model} analysis_model={s.llm_analysis_model} "
          f"rpm={s.llm_rpm_limit}")
    llm = get_llm()
    out = {"change": [], "related": [], "draft": []}
    started = datetime.now()
    try:
        with SessionLocal() as db:
            for group, cases, run, score in (("change", CHANGE_CASES, _run_change, score_change),
                                             ("related", RELATED_CASES, _run_related, score_related),
                                             ("draft", DRAFT_CASES, _run_draft, score_draft)):
                for case in cases:
                    try:
                        raw = run(db, llm, case)
                        out[group].append({"case": case, "raw": raw, "score": score(case, raw)})
                    except Exception as e:  # noqa: BLE001 - 사례 하나의 장애가 나머지 측정을 막지 않게
                        db.rollback()
                        out[group].append({"case": case, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                    _print_case(group, out[group][-1])
    finally:
        s.mail_triage_enabled = old_switch
    _print_table(out, datetime.now() - started)
    yield out


def _print_case(group: str, r: dict) -> None:
    name = r["case"]["name"]
    if "error" in r:
        print(f"[{group}] {name}: 오류 {r['error']}")
        return
    sc = r["score"]
    if group == "change":
        print(f"[change] {name}: result={r['raw']['result']} 맞음={sc['correct']} 놓침={sc['missed']} "
              f"잘못={[(e['field'], e['after'], why) for e, why in sc['wrong']]} {sc['notes']}")
        for e in r["raw"]["log"]:
            print(f"    - {e['field']}/{e['status']}: {e.get('before')!r} → {e.get('after')!r} | 근거 {e.get('evidence')!r}")
    elif group == "related":
        print(f"[related] {name}: {sc['verdict']} related={r['raw']['related']} 후보={r['raw']['ids']} {sc['note']}")
    else:
        w = r["raw"]["warnings"]
        print(f"[draft] {name}: 빈칸={sc['blanks']} unsupported={w.get('unsupported')} "
              f"outside={w.get('outside_contacts')} suspicious={len(w.get('suspicious') or [])} "
              f"문제={sc['problems']} {sc['notes']}")
        print("    | " + r["raw"]["body"].replace("\n", "\n    | "))


def summarize(out: dict) -> dict:
    ch = [r for r in out["change"] if "error" not in r]
    rel = [r for r in out["related"] if "error" not in r]
    dr = [r for r in out["draft"] if "error" not in r]
    return {
        "change": {"cases": len(out["change"]), "errors": len(out["change"]) - len(ch),
                   "expected": sum(len(r["case"]["expected"].get("facts", [])) for r in ch),
                   "correct": sum(len(r["score"]["correct"]) for r in ch),
                   "missed": sum(len(r["score"]["missed"]) for r in ch),
                   "wrong": sum(len(r["score"]["wrong"]) for r in ch),
                   "notes": sum(len(r["score"]["notes"]) for r in ch)},
        "related": {"cases": len(out["related"]), "errors": len(out["related"]) - len(rel),
                    "correct": sum(r["score"]["verdict"] == "correct" for r in rel),
                    "missed": sum(r["score"]["verdict"] == "missed" for r in rel),
                    "wrong": sum(r["score"]["verdict"] == "wrong" for r in rel)},
        "draft": {"cases": len(out["draft"]), "errors": len(out["draft"]) - len(dr),
                  "unsupported": sum(bool(r["raw"]["warnings"].get("unsupported")) for r in dr),
                  "outside": sum(bool(r["raw"]["warnings"].get("outside_contacts")) for r in dr),
                  "blank_missing": sum(r["case"]["expected"]["blank_needed"] and r["score"]["blanks"] == 0 for r in dr),
                  "notes": sum(len(r["score"]["notes"]) for r in dr)},
    }


def _print_table(out: dict, took) -> None:
    s = summarize(out)
    c, r, d = s["change"], s["related"], s["draft"]
    print(f"\n[reply-smoke] 걸린 시간 {took}")
    print("| 묶음 | 사례 | 오류 | 정답 항목 | 맞음 | 놓침 | 잘못 | 기타 |")
    print("|---|---|---|---|---|---|---|---|")
    print(f"| 재답장 변경 감지 | {c['cases']} | {c['errors']} | {c['expected']} | {c['correct']} | {c['missed']} | "
          f"{c['wrong']} | 답장 필요 · 감사 표시 불일치 {c['notes']} |")
    print(f"| 다른 스레드 판단 | {r['cases']} | {r['errors']} | {r['cases'] - r['errors']} | {r['correct']} | "
          f"{r['missed']} | {r['wrong']} | |")
    print(f"| 답장 초안 | {d['cases']} | {d['errors']} | {d['cases'] - d['errors']} | "
          f"{d['cases'] - d['errors'] - len([x for x in out['draft'] if 'error' not in x and x['score']['problems']])} | "
          f"빈칸 누락 {d['blank_missing']} | 근거 없는 숫자 · 날짜 {d['unsupported']} · 밖 연락처 {d['outside']} | "
          f"참고 {d['notes']} |")


def test_every_case_ran(report):
    """장애(한도 · 스키마 거부)로 못 돈 사례가 있으면 측정 자체가 무효다."""
    errors = [(g, r["case"]["name"], r["error"]) for g in report for r in report[g] if "error" in r]
    assert not errors, errors


def test_no_wrongly_applied_change(report):
    wrong = [(r["case"]["name"], e["field"], e.get("after"), why)
             for r in report["change"] if "error" not in r for e, why in r["score"]["wrong"]]
    assert not wrong, wrong


def test_no_unsupported_fact_in_drafts(report):
    bad = [(r["case"]["name"], r["score"]["problems"]) for r in report["draft"]
           if "error" not in r and r["score"]["problems"]]
    assert not bad, bad
