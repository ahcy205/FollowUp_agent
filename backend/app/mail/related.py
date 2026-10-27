"""다른 스레드로 온 수정 요청 알아보기 (설계 7.1 · 7.2).

- 후보 고르기(코드): 보낸 사람 주소가 같거나 같은 회사 도메인(무료 메일 제외)이고, 처리할 일 · 답장 보냄 상태이며,
  14일 안에 만들어진 더 먼저 생긴 다른 메일 업무. 최근 것부터 최대 3개.
- 내용 판단(LLM): 접수 분석과 같은 호출에 후보의 번호 · 제목 · 한 줄 요약만 준다. 다른 업무의 메일 본문은 보내지 않는다.
- judge_related가 응답에서 판단을 만든다. 나중에 다른 판단 모델로 바꿀 때 이 함수만 바꾼다.

task.related 형식:
  {"state": "suggested", "task_id", "relation": "change"|"followup", "evidence"} / {"state": "none"} /
  {"state": "unjudged"}(분석 실패 · 판별 꺼짐 — 5단계 "같은 보낸 사람" 규칙으로 제안)
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.guard import wrap_untrusted
from app.mail.inbox import _source_sender
from app.models import Task, TaskStatus

FREE_MAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "naver.com", "daum.net", "hanmail.net", "kakao.com", "nate.com",
    "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.co.kr", "icloud.com", "me.com",
    "proton.me", "protonmail.com"})
WINDOW = timedelta(days=14)
MAX_CANDIDATES = 3
RELATIONS = ("change", "followup")
CANDIDATE_STATUSES = (TaskStatus.ACTION_REQUIRED.value, TaskStatus.IN_PROGRESS.value,
                      TaskStatus.BLOCKED.value, TaskStatus.WAITING.value)
SUMMARY_CHARS = 120
NONE = {"state": "none"}
UNJUDGED = {"state": "unjudged"}


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _address(addr: str | None) -> str:
    """'이름 <a@b>' 또는 'a@b'에서 소문자 주소."""
    text = (addr or "").strip()
    if "<" in text and ">" in text:
        text = text[text.rfind("<") + 1:text.rfind(">")]
    return text.strip().lower()


def company_domain(addr: str) -> str | None:
    """소문자 도메인. 무료 메일이거나 주소가 아니면 None."""
    address = _address(addr)
    if address.count("@") != 1:
        return None
    domain = address.split("@")[1].strip(". ")
    if not domain or "." not in domain or domain in FREE_MAIL_DOMAINS:
        return None
    return domain


def qualifies(task: Task, cand: Task, now: datetime) -> bool:
    """cand가 task의 합치기 후보 조건(상태 · 14일 · 더 먼저 생김 · 같은 사용자)을 지금도 만족하는지. 보낸 사람은 보지 않는다."""
    created, cand_created = _utc(task.created_at), _utc(cand.created_at)
    return (cand.id != task.id and cand.user_id == task.user_id and cand.origin == "mail"
            and cand.merged_into_id is None and str(cand.status) in CANDIDATE_STATUSES
            and cand_created is not None and cand_created > _utc(now) - WINDOW
            and (created is None or cand_created < created))


def related_candidates(db: Session, task: Task, sender: str, now: datetime) -> list[Task]:
    """설계 7.1 후보. 최근 것부터 최대 3개."""
    address, domain = _address(sender), company_domain(sender)
    if not address:
        return []
    query = (select(Task).where(Task.user_id == task.user_id, Task.id != task.id, Task.origin == "mail",
                                Task.merged_into_id.is_(None), Task.status.in_(CANDIDATE_STATUSES),
                                Task.created_at > _utc(now) - WINDOW)
             .order_by(Task.created_at.desc(), Task.id.desc()))
    if task.created_at is not None:
        query = query.where(Task.created_at < task.created_at)
    out: list[Task] = []
    for cand in db.scalars(query).all():
        other = _source_sender(db, cand)
        if other and (other == address or (domain is not None and company_domain(other) == domain)):
            out.append(cand)
            if len(out) == MAX_CANDIDATES:
                break
    return out


def candidates_block(candidates: list[Task]) -> str:
    """접수 분석 프롬프트에 덧붙이는 후보 블록. 후보가 없으면 ""(프롬프트를 바꾸지 않는다)."""
    if not candidates:
        return ""
    lines = []
    for c in candidates:
        summary = " ".join(str((c.brief or {}).get("summary") or "").split())[:SUMMARY_CHARS]
        title = " ".join(str(c.title or "").split())[:SUMMARY_CHARS]
        lines.append(f"- 업무 #{c.id}: {title} / 한 줄 요약: {summary or '없음'}")
    text = "\n".join(lines)
    return ("\n\n## 관련 업무 후보\n같은 상대(또는 같은 회사)의 진행 중인 업무입니다. 이 메일이 그중 하나의 수정 · 추가 요청이면 "
            "relation=\"change\", 같은 건의 이어지는 연락이면 relation=\"followup\"으로 하고 related_task에 그 번호, "
            "relation_evidence에 이 메일 본문에서 그대로 옮긴 근거 문장을 적으세요. 관계가 없거나 확실하지 않으면, "
            "또는 메일이 후보와 별개의 건이라고 밝히거나 다른 품목 · 다른 일을 새로 요청하면(후보를 언급하더라도) "
            "related_task=null, relation=\"none\", relation_evidence=\"\"입니다.\n"
            + wrap_untrusted("related task candidates", text, max_chars=len(text)))


def judge_related(data: dict, candidates: list[Task], body: str) -> dict:
    """분석 응답에서 관계 판단을 만든다. 후보 밖 번호 · relation none · 근거가 본문에 없으면 {"state": "none"}."""
    from app.mail.change_prompt import evidence_in  # change_prompt → intake → (지연) related 순환을 피한다
    if not isinstance(data, dict):
        return dict(NONE)
    task_id, relation = data.get("related_task"), data.get("relation")
    evidence = data.get("relation_evidence")
    if (not isinstance(task_id, int) or isinstance(task_id, bool) or relation not in RELATIONS
            or task_id not in {c.id for c in candidates} or not isinstance(evidence, str)):
        return dict(NONE)
    evidence = " ".join(evidence.split())[:500]
    if not evidence_in(evidence, body):
        return dict(NONE)
    return {"state": "suggested", "task_id": task_id, "relation": relation, "evidence": evidence}
