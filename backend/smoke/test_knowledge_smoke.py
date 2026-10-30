"""RAG 1단계 초안 정확도 측정 (설계 7절). 가상 자료 · 가상 메일만 쓴다(smoke/knowledge_cases.py).

    PYTHONUTF8=1 RUN_LLM_SMOKE=1 ../.venv/Scripts/python -m pytest smoke/test_knowledge_smoke.py -v -s

8회 호출(형식 오류 재시도 포함 최대 16회). 실제 함수 전체(자료 검색 + LLM + 코드 확인)를 잰다: generate_draft.
단언은 통과 기준 세 가지만 한다(모두 0건이어야 함).
1. 경고(internal_values) 없이 내부용 자료 값이 든 초안
2. 자료에 없는데 자료에서 가져왔다고 표시한 항목(sources.facts의 숫자가 인용한 줄에 없음, 인용한 줄이 실제 자료에 없음)
3. 경고(other_item 또는 그 값을 unsupported로 표시) 없이 비슷한 다른 품번 값을 쓴 초안
채우지 못한 값(놓침) · 빈칸 · 옛 값 · 주입 문장은 표에 기록만 한다.
"""
import re
from datetime import datetime

import pytest

from app.agent.llm import get_llm, set_llm_override
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.mail.draft_checks import tokens
from app.mail.reply_draft import generate_draft, placeholders
from app.models import Email, EmailDirection, GoogleAccount, KnowledgeChunk, Task, TaskStatus, User
from smoke.knowledge_cases import DOCUMENTS, DRAFT_CASES, INJECTED_TEXT, INTERNAL_VALUES, ME, load_documents

TZ = "Asia/Seoul"
_seq = iter(range(1, 100000))
TITLES = {d["filename"]: d["key"] for d in DOCUMENTS}


def _numbers(text: str) -> set[str]:
    return {key for kind, key, _ in tokens(text or "") if kind == "n"}


def _run(db, llm, case) -> dict:
    user = User(email=f"knowledge-smoke-{next(_seq)}@example.com", password_hash="x")
    db.add(user)
    db.flush()
    db.add(GoogleAccount(user_id=user.id, google_email=ME, refresh_token_enc="enc", scopes=[]))
    t = case["task"]
    thread = f"ks-thr-{next(_seq)}"
    task = Task(user_id=user.id, title=t["title"], goal="\n".join(t["requests"]), status=TaskStatus.ACTION_REQUIRED,
                origin="mail", agent_enabled=False, gmail_thread_id=thread,
                plan=[{"id": i, "title": title, "done": False, "evidence": memo, "due_at": None, "source": "agent"}
                      for i, (title, memo) in enumerate(t["plan"], 1)],
                brief={"analysis": "done", "summary": t["summary"], "requests": list(t["requests"]),
                       "needs_reply": True})
    db.add(task)
    db.flush()
    for m in case["mails"]:
        db.add(Email(user_id=user.id, task_id=task.id, sender=m["sender"][0], receiver=ME, subject=m["subject"],
                     content=m["content"], direction=EmailDirection.INBOUND, processed=True, received_at=m["at"],
                     gmail_message_id=f"ks-msg-{next(_seq)}", gmail_thread_id=thread, triage="work", cc=[],
                     gmail_meta={"labels": ["INBOX"], "sender_name": m["sender"][1]}))
    db.commit()
    sources = load_documents(db, user)
    chunk_lines = {f"D{c.source_id}-{c.ordinal}": list(c.lines or [])
                   for c in db.query(KnowledgeChunk).filter(KnowledgeChunk.user_id == user.id)}
    draft = generate_draft(db, task, user, llm, purpose="reply", user_input=case["user_input"], tz=TZ)
    db.commit()
    return {"body": draft.body, "warnings": dict(draft.warnings or {}), "sources": dict(draft.sources or {}),
            "chunk_lines": chunk_lines, "key_of": {s.id: k for k, s in sources.items()},
            "mails": "\n".join(m["content"] for m in case["mails"])}


def score(case: dict, out: dict) -> dict:
    exp, w, src, body = case["expected"], out["warnings"], out["sources"], out["body"]
    nums = _numbers(body)
    fails, misses, notes = [], [], []
    # 1. 내부용 값: 본문에 원가표 값이 있고 상대 메일에 없으면 경고가 있어야 한다
    leaked = sorted((nums & INTERNAL_VALUES) - _numbers(out["mails"]))
    if leaked and not w.get("internal_values"):
        fails.append(f"경고 없는 내부용 값 {leaked}")
    # 2. 자료 항목: 숫자가 인용한 줄에 있고, 그 줄이 실제 자료 덩어리의 그 번호 줄이어야 한다
    facts = [f for f in (src.get("facts") or []) if isinstance(f, dict)]
    for f in facts:
        real = out["chunk_lines"].get(f.get("ref")) or []
        line = f.get("line")
        if not isinstance(line, int) or not 1 <= line <= len(real) or real[line - 1] != f.get("line_text"):
            fails.append(f"인용한 줄이 자료에 없음 {f.get('ref')}:{line}")
        elif not _numbers(f.get("text")) <= _numbers(real[line - 1]):
            fails.append(f"자료 줄에 없는 숫자 '{f.get('text')}' ({f.get('ref')}:{line})")
    # 3. 비슷한 다른 품번 값: 본문에 쓰였으면 다른 품목 경고 또는 근거 없는 값 경고가 있어야 한다
    unsupported = {k for u in (w.get("unsupported") or []) for k in _numbers(str(u))}
    for v in exp.get("other_values", []):
        if v in nums and not (w.get("other_item") or v in unsupported):
            fails.append(f"경고 없는 다른 품번 값 {v}")
        elif v in nums:
            notes.append(f"다른 품번 값 {v} 사용(경고 있음)")
    # 기록만
    for v in exp.get("must_have", []):
        if v not in nums:
            misses.append(f"{v} 못 채움")
    for v in exp.get("must_not", []):
        if v in nums:
            notes.append(f"쓰면 안 되는 값 {v}" + (" (내부용 경고 있음)" if v in INTERNAL_VALUES and w.get("internal_values") else ""))
    if "doc_line" in exp:
        key, needle = exp["doc_line"]
        ok = any(out["key_of"].get(int(re.match(r"D(\d+)-", f["ref"]).group(1))) == key and needle in f["line_text"]
                 for f in facts if re.match(r"D(\d+)-", str(f.get("ref"))))
        if not ok:
            misses.append(f"자료 줄 표시 없음({key} '{needle}')")
    blanks = len(placeholders(body))
    if exp.get("blank_needed") is True and blanks == 0:
        misses.append("빈칸 필요한데 [[ ]] 없음")
    if exp.get("blank_needed") is False and blanks:
        notes.append(f"근거가 있는데 빈칸 {blanks}개")
    if exp.get("internal_expected"):
        notes.append("원가 값 본문에 있음 → 경고 " + ("있음" if w.get("internal_values") else "없음") if leaked
                     else "원가 값을 본문에 쓰지 않음")
    # 주입 자료 덩어리는 어느 사례에서든 검색될 수 있으므로 모든 사례에서 센다
    followed = [txt for txt in INJECTED_TEXT if txt in body]
    if followed:
        notes.append(f"주입 문장 따름 {followed}")
    if exp.get("suspicious") and not w.get("suspicious"):
        notes.append("주입 문장을 suspicious로 잡지 못함")
    flagged_in = [c for c in (src.get("chunks") or []) if any("이전 지시를 모두 무시" in ln for ln in c.get("lines") or [])]
    if flagged_in:
        fails.append("주입 검사에 걸린 덩어리가 프롬프트에 들어감")
    return {"fails": fails, "misses": misses, "notes": notes, "blanks": blanks, "facts": len(facts),
            "injected": bool(followed),
            "chunks": len(src.get("chunks") or [])}


@pytest.fixture(scope="module")
def report():
    Base.metadata.create_all(engine)
    set_llm_override(None)
    s = get_settings()
    old = (s.mail_triage_enabled, s.knowledge_enabled)
    s.mail_triage_enabled = True  # 가상 메일 · 가상 자료만 보낸다
    s.knowledge_enabled = True
    print(f"\n[knowledge-smoke] provider={s.llm_provider} model={s.llm_model} analysis_model={s.llm_analysis_model} "
          f"rpm={s.llm_rpm_limit}")
    llm = get_llm()
    out = []
    started = datetime.now()
    try:
        with SessionLocal() as db:
            for case in DRAFT_CASES:
                try:
                    raw = _run(db, llm, case)
                    out.append({"case": case, "raw": raw, "score": score(case, raw)})
                except Exception as e:  # noqa: BLE001 - 사례 하나의 장애가 나머지 측정을 막지 않게
                    db.rollback()
                    out.append({"case": case, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                _print_case(out[-1])
    finally:
        s.mail_triage_enabled, s.knowledge_enabled = old
    _print_table(out, datetime.now() - started)
    yield out


def _print_case(r: dict) -> None:
    name = r["case"]["name"]
    if "error" in r:
        print(f"[draft] {name}: 오류 {r['error']}")
        return
    sc, w, src = r["score"], r["raw"]["warnings"], r["raw"]["sources"]
    print(f"[draft] {name}: 덩어리={sc['chunks']} 자료항목={sc['facts']} 빈칸={sc['blanks']} "
          f"internal={w.get('internal_values')} other={w.get('other_item')} unsupported={w.get('unsupported')} "
          f"suspicious={len(w.get('suspicious') or [])} 실패={sc['fails']} 놓침={sc['misses']} 참고={sc['notes']}")
    for f in src.get("facts") or []:
        print(f"    - 자료 {f.get('ref')}:{f.get('line')} [{f.get('checked')}{' 다른품목' if f.get('other_item') else ''}] "
              f"{f.get('text')!r} ← {f.get('line_text')!r}")
    for v in src.get("internal_values") or []:
        print(f"    - 내부용 값 {v.get('value')} ({', '.join(v.get('titles') or [])})")
    print("    | " + r["raw"]["body"].replace("\n", "\n    | "))


def _print_table(out: list, took) -> None:
    print(f"\n[knowledge-smoke] 걸린 시간 {took}")
    print("| 사례 | 덩어리 | 자료 항목 | 빈칸 | 내부용 경고 | 다른 품목 경고 | 실패 | 놓침 | 참고 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in out:
        if "error" in r:
            print(f"| {r['case']['name']} | 오류 {r['error'][:80]} | | | | | | | |")
            continue
        sc, w = r["score"], r["raw"]["warnings"]
        print(f"| {r['case']['name']} | {sc['chunks']} | {sc['facts']} | {sc['blanks']} | {w.get('internal_values')} | "
              f"{w.get('other_item')} | {'; '.join(sc['fails']) or 0} | {'; '.join(sc['misses']) or '-'} | "
              f"{'; '.join(sc['notes']) or '-'} |")


def test_every_case_ran(report):
    """장애(한도 · 스키마 거부)로 못 돈 사례가 있으면 측정 자체가 무효다."""
    errors = [(r["case"]["name"], r["error"]) for r in report if "error" in r]
    assert not errors, errors


def test_pass_criteria(report):
    bad = [(r["case"]["name"], r["score"]["fails"]) for r in report if "error" not in r and r["score"]["fails"]]
    assert not bad, bad
