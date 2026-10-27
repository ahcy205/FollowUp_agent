"""합친 내용 분석 (설계 7.3).

판별이 켜진 채 업무 B를 A에 합치면 A.merge_analysis = {"state": "pending", "source_id", "email_ids", "at"}가 남는다.
워커가 동기화 주기마다(process_inbox, 접수 분석 뒤) 옮긴 받은 메일로 변경 분석(app.mail.changes)을 1회 돌려 A의
요청 · 기한 · 요약을 고친다. 결과:
- 반영 → state "done". 형식 오류 두 번 · 반영 중 오류 → 5단계 규칙(요청 합집합 · 이른 기한)으로 마무리, state "fallback".
- LLM 장애 → pending 그대로 두고 다음 주기에 다시 한다(재답장 대기열에는 넣지 않는다: 두 번 분석하지 않게).
- 분석 직전 · 직후에 업무를 다시 읽어, pending이 아니거나 원본이 더 이상 A에 합쳐져 있지 않으면(되돌려짐) 결과를 버린다.
- 판별이 꺼져 있으면(llm=None) LLM 없이 5단계 규칙으로 마무리한다.
- 합친 지 10분이 지나도 pending이면(LLM 장애가 이어지거나 설정되지 않음) 5단계 규칙으로 마무리한다. 동기화 주기가
  LLM 준비 여부와 관계없이 finish_stale_merges를 부른다.
- 대상 업무의 재답장 대기열은 합친 내용 분석이 끝날 때까지 미룬다(intake._change_tasks). 옛 메일 분석이 그 뒤에 온 새 메일의
  변경을 덮지 않게.
로그에는 업무 번호와 건수만 남긴다.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import LLMClient
from app.mail import changes
from app.mail.merge import _parse, finish_by_stage5_rules, note_analysis_state
from app.models import Email, EmailDirection, Task

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)


def _pending_tasks(db: Session, user_id: int) -> list[Task]:
    tasks = db.scalars(select(Task).where(Task.user_id == user_id, Task.origin == "mail",
                                          Task.merged_into_id.is_(None)).order_by(Task.id)).all()
    return [t for t in tasks if isinstance(t.merge_analysis, dict) and t.merge_analysis.get("state") == "pending"]


def _still_wanted(db: Session, task: Task, source_id) -> bool:
    """분석 결과를 써도 되는지: 아직 pending이고 같은 원본이며, 그 원본이 지금도 이 업무에 합쳐져 있다."""
    ma = task.merge_analysis if isinstance(task.merge_analysis, dict) else {}
    if ma.get("state") != "pending" or ma.get("source_id") != source_id or not isinstance(source_id, int):
        return False
    merged_into = db.scalar(select(Task.merged_into_id).where(Task.id == source_id, Task.user_id == task.user_id))
    return merged_into == task.id


def _finish(db: Session, task: Task, state: str) -> None:
    task.merge_analysis = {**(task.merge_analysis or {}), "state": state}
    db.commit()


def _fallback(db: Session, task: Task, source_id: int) -> None:
    finish_by_stage5_rules(db, task, db.get(Task, source_id))
    _finish(db, task, "fallback")


def finish_stale_merges(db: Session, user_id: int, now: datetime, everything: bool = False) -> int:
    """LLM 없이 마무리: 합친 지 10분이 지난 pending(everything이면 모든 pending)을 5단계 규칙으로. 마무리한 수."""
    finished = 0
    for task in _pending_tasks(db, user_id):
        source_id = task.merge_analysis.get("source_id")
        if not _still_wanted(db, task, source_id):
            task.merge_analysis = {}  # 원본이 없어졌거나 되돌려졌다
            db.commit()
            continue
        at = _parse(task.merge_analysis.get("at"))
        if everything or at is None or now - at > STALE_AFTER:
            _fallback(db, task, source_id)
            log.info("merge analysis for task %s finished by stage-5 rules without LLM", task.id)
            finished += 1
    return finished


def run_merge_analyses(db: Session, user_id: int, llm: LLMClient | None, tz: str,
                       now: datetime | None = None) -> int:
    """pending인 합치기를 마무리한다. 마무리한 업무 수(done · fallback)를 돌려준다. 업무마다 커밋한다."""
    now = now or datetime.now(UTC)
    finished = finish_stale_merges(db, user_id, now, everything=llm is None)
    if llm is None:  # 판별이 꺼졌다: 위에서 모두 5단계 규칙으로 마무리했다
        return finished
    for task in _pending_tasks(db, user_id):
        task_id = task.id
        source_id = task.merge_analysis.get("source_id")
        if not _still_wanted(db, task, source_id):
            task.merge_analysis = {}
            db.commit()
            continue
        ids = [i for i in task.merge_analysis.get("email_ids") or [] if isinstance(i, int) and not isinstance(i, bool)]
        emails = list(db.scalars(select(Email).where(
            Email.user_id == user_id, Email.task_id == task_id, Email.id.in_(ids),
            Email.direction == EmailDirection.INBOUND)).all()) if ids else []
        if not emails:  # 옮긴 받은 메일이 없다(보낸 메일뿐 등): 고칠 내용이 없다
            _finish(db, task, "done")
            finished += 1
            continue
        db.commit()  # 분석하는 동안 쓰기 트랜잭션을 열어 두지 않는다(사용자가 되돌릴 수 있게)
        outcome = changes.run_analysis(db, task, emails, llm, tz,
                                       still_wanted=lambda t: _still_wanted(db, t, source_id))
        if outcome == changes.OUTAGE:
            db.rollback()
            log.info("merge analysis for task %s postponed (LLM unavailable)", task_id)
            return finished  # 남은 업무도 다음 주기에
        task = db.get(Task, task_id)
        if outcome == changes.DROPPED or task is None:
            db.rollback()  # 되돌려졌거나 합쳐졌다: 아무것도 쓰지 않는다
            log.info("merge analysis for task %s dropped (merge undone or task merged)", task_id)
            continue
        if outcome == changes.APPLIED:
            note_analysis_state(task, source_id)
            _finish(db, task, "done")
        else:  # 형식 오류 두 번 · 반영 중 오류
            if not _still_wanted(db, task, source_id):
                db.rollback()
                continue
            _fallback(db, task, source_id)
            log.info("merge analysis for task %s fell back to stage-5 rules", task_id)
        finished += 1
    return finished
