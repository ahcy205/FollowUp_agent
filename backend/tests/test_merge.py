"""5단계 Task 4: 같은 상대 업무 합치기와 되돌리기."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.mail import intake, merge, task_actions
from app.mail.inbox import merge_candidates
from app.mail.task_actions import ActionError
from app.models import Email, Task, TaskStatus
from tests.conftest import ScriptedLLM, make_client, register
from tests.test_task_actions import NOW, TZ, UTC, _FixedDatetime, _kst, _logs, _mail, _task, _user


def _todo(i, title, done=False, evidence=""):
    return {"id": i, "title": title, "done": done, "evidence": evidence, "due_at": None, "source": "agent"}


def _pair(db, uid):
    """A(기존 업무)와 B(같은 상대의 새 업무). B 메일 2통."""
    a = _task(db, uid, created_at=NOW - timedelta(days=3), priority="NORMAL", priority_source="llm",
              deadline_at=_kst(2026, 11, 5), deadline_all_day=True, deadline_kind="estimated",
              deadline_source="다음 주쯤", calendar_event_id="ev-a", calendar_state="synced",
              brief={"analysis": "done", "summary": "견적", "needs_reply": True, "requests": ["견적서 송부"]})
    b = _task(db, uid, created_at=NOW - timedelta(hours=2), priority="HIGH", priority_source="user",
              deadline_at=_kst(2026, 10, 30, 15), deadline_all_day=False, deadline_kind="explicit",
              deadline_source="30일 오후 3시까지", calendar_event_id="ev-b", calendar_state="synced",
              snoozed_until=NOW + timedelta(days=1), next_action="회신하기",
              plan=[_todo(1, "견적서 보내기 "), _todo(2, "샘플 발송", done=True, evidence="택배 보냄"),
                    _todo(5, "납기 확인")],
              brief={"analysis": "done", "summary": "추가 요청", "needs_reply": True,
                     "requests": ["견적서 송부", "샘플 요청"]})
    mails = [_mail(uid, thread=a.gmail_thread_id, task_id=a.id)] + \
            [_mail(uid, thread=b.gmail_thread_id, task_id=b.id) for _ in range(2)]
    db.add_all(mails)
    db.commit()
    return a, b, [m.id for m in mails[1:]]


def _thread_mails(db, task_id):
    return sorted(db.scalars(select(Email.id).where(Email.task_id == task_id)).all())


def test_merge_moves_everything():
    with SessionLocal() as db:
        u = _user(db)
        a, b, b_mails = _pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        db.commit()

        assert set(b_mails) <= set(_thread_mails(db, a.id))
        assert _thread_mails(db, b.id) == []
        # 할 일: A의 "견적서 보내기"와 같은 제목(공백 무시)은 건너뛴다, 새 번호는 A 최대값 + 1부터
        assert [(p["id"], p["title"], p["done"]) for p in a.plan] == [
            (1, "견적서 보내기", False), (3, "납기 회신", False), (4, "샘플 발송", True), (5, "납기 확인", False)]
        assert a.plan[2]["evidence"] == "택배 보냄"
        assert a.brief["requests"] == ["견적서 송부", "샘플 요청"]
        # B 기한이 더 이르다 → A 기한 교체
        assert a.deadline_at.astimezone(UTC) == _kst(2026, 10, 30, 15)
        assert (a.deadline_all_day, a.deadline_kind, a.deadline_source) == (False, "explicit", "30일 오후 3시까지")
        assert a.priority == "HIGH" and a.priority_source == "user"
        # B
        assert b.merged_into_id == a.id and b.status == TaskStatus.COMPLETED
        assert b.completed_at is not None and b.current_step == f"업무 #{a.id}에 합쳐짐"
        assert b.snoozed_until is None
        assert a.calendar_state == "pending" and b.calendar_state == "pending"
        for t in (a, b):
            logs = _logs(db, t.id, "TASK_MERGED")
            assert len(logs) == 1 and logs[0].output_data == {"source_id": b.id, "target_id": a.id}
        rec = a.merge_undo[str(b.id)]
        assert rec["source_id"] == b.id and rec["at"] == NOW.isoformat()
        assert sorted(rec["moved_email_ids"]) == b_mails
        assert rec["added_todo_ids"] == [4, 5] and rec["added_requests"] == ["샘플 요청"]
        assert rec["before"]["priority"] == "NORMAL" and rec["after"]["priority"] == "HIGH"
        assert rec["source_before"]["status"] == "ACTION_REQUIRED"


def test_merge_keeps_later_deadline_and_higher_priority():
    with SessionLocal() as db:
        u = _user(db)
        a = _task(db, u.id, priority="HIGH", deadline_at=_kst(2026, 10, 28), deadline_kind="explicit")
        b = _task(db, u.id, priority="LOW", deadline_at=_kst(2026, 11, 28), deadline_kind="explicit")
        merge.merge(db, u, b, a, NOW)
        assert a.priority == "HIGH" and a.deadline_at.astimezone(UTC) == _kst(2026, 10, 28)
        a2 = _task(db, u.id)  # A에 기한 없음 → B 기한
        b2 = _task(db, u.id, deadline_at=_kst(2026, 11, 28), deadline_kind="estimated")
        merge.merge(db, u, b2, a2, NOW)
        assert a2.deadline_at.astimezone(UTC) == _kst(2026, 11, 28) and a2.deadline_kind == "estimated"


def test_merge_rejects(client):
    with SessionLocal() as db:
        u = _user(db)
        a = _task(db, u.id)
        b = _task(db, u.id)
        merged = _task(db, u.id, status=TaskStatus.COMPLETED, merged_into_id=a.id)
        closed = _task(db, u.id, status=TaskStatus.CANCELLED)
        pending = _task(db, u.id, status=TaskStatus.PENDING, brief={"analysis": "pending"})
        agent = _task(db, u.id, origin="user", gmail_thread_id=None)
        other = _task(db, _user(db, "x@example.com").id)
        for src, dst in ((a, a), (merged, a), (b, merged), (closed, a), (b, closed), (b, pending),
                         (agent, a), (b, agent), (b, other)):
            with pytest.raises(ActionError) as e:
                merge.merge(db, u, src, dst, NOW)
            assert e.value.status == 409 and str(e.value) == "합칠 수 없는 업무입니다."
        assert b.merged_into_id is None and b.status == TaskStatus.ACTION_REQUIRED

    me = register(client)
    with SessionLocal() as db:
        a_id, b_id = _task(db, me["id"]).id, _task(db, me["id"]).id
        agent_id = _task(db, me["id"], origin="user", gmail_thread_id=None).id
    with make_client() as other:
        oid = register(other, "other@example.com")["id"]
        with SessionLocal() as db:
            foreign = _task(db, oid).id
        assert other.post(f"/api/tasks/{b_id}/merge", json={"into_task_id": a_id}).status_code == 404
        assert other.post(f"/api/tasks/{a_id}/merge/undo", json={"source_id": b_id}).status_code == 404
        assert other.post(f"/api/tasks/{a_id}/merge/dismiss").status_code == 404
    assert client.post(f"/api/tasks/{b_id}/merge", json={"into_task_id": foreign}).status_code == 404
    assert client.post(f"/api/tasks/{agent_id}/merge", json={"into_task_id": a_id}).status_code == 409
    assert client.post(f"/api/tasks/{b_id}/merge", json={"into_task_id": agent_id}).status_code == 409
    assert client.post(f"/api/tasks/{b_id}/merge", json={"into_task_id": a_id, "x": 1}).status_code == 422

    r = client.post(f"/api/tasks/{b_id}/merge", json={"into_task_id": a_id})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["id"] == a_id
    assert client.post(f"/api/tasks/{b_id}/merge", json={"into_task_id": a_id}).status_code == 409
    r = client.post(f"/api/tasks/{a_id}/merge/undo", json={"source_id": b_id})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["id"] == a_id
    r = client.post(f"/api/tasks/{a_id}/merge/undo", json={"source_id": b_id})
    assert r.status_code == 409 and r.json()["detail"] == "되돌릴 수 있는 합치기가 없습니다."
    r = client.post(f"/api/tasks/{b_id}/merge/dismiss")
    assert r.status_code == 200 and r.json()["task"]["id"] == b_id
    assert client.post(f"/api/tasks/{agent_id}/merge/dismiss").status_code == 409


def test_followup_mail_goes_to_target(monkeypatch):
    monkeypatch.setattr(intake, "datetime", _FixedDatetime)
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        a.snoozed_until = NOW + timedelta(days=2)
        db.commit()
        m = _mail(u.id, thread=b.gmail_thread_id)
        db.add(m)
        db.commit()
        r = intake.intake_work_mails(db, u.id, ScriptedLLM(), max_mails=5, tz=TZ)
        assert r.attached == 1 and r.created == 0
        db.expire_all()
        assert db.get(Email, m.id).task_id == a.id
        assert db.get(Task, a.id).snoozed_until is None
        b2 = db.get(Task, b.id)
        assert b2.status == TaskStatus.COMPLETED and b2.merged_into_id == a.id


def _snapshot(t: Task):
    return (t.deadline_at.astimezone(UTC) if t.deadline_at else None, t.deadline_all_day, t.deadline_kind,
            t.deadline_source, t.priority, t.priority_source)


def test_undo_restores():
    with SessionLocal() as db:
        u = _user(db)
        a, b, b_mails = _pair(db, u.id)
        a_before, b_plan = _snapshot(a), [dict(p) for p in b.plan]
        a_plan, a_brief = [dict(p) for p in a.plan], dict(a.brief)
        b_completed = b.completed_at
        merge.merge(db, u, b, a, NOW)
        db.commit()
        a.calendar_state = b.calendar_state = "synced"
        got = merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=10))
        db.commit()
        assert got.id == a.id
        assert _thread_mails(db, b.id) == b_mails and not set(b_mails) & set(_thread_mails(db, a.id))
        assert a.plan == a_plan and a.brief == a_brief and _snapshot(a) == a_before
        assert b.plan == b_plan  # 원래 번호 그대로 돌아온다
        assert b.merged_into_id is None and b.status == TaskStatus.ACTION_REQUIRED
        assert b.completed_at == b_completed and b.current_step == ""
        assert b.snoozed_until.astimezone(UTC) == NOW + timedelta(days=1) and b.next_action == "회신하기"
        assert a.merge_undo == {}
        assert a.calendar_state == "pending" and b.calendar_state == "pending"
        assert len(_logs(db, a.id, "TASK_UNMERGED")) == 1 and len(_logs(db, b.id, "TASK_UNMERGED")) == 1
        with pytest.raises(ActionError) as e:
            merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=11))
        assert e.value.status == 409


def test_undo_keeps_user_changes(monkeypatch):
    monkeypatch.setattr(intake, "datetime", _FixedDatetime)
    with SessionLocal() as db:
        u = _user(db)
        a, b, b_mails = _pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        db.commit()
        # 합친 뒤: 사용자가 A 기한을 바꾸고, 옮겨 온 할 일(5 납기 확인)을 체크, B 스레드로 새 메일
        task_actions.set_deadline(a, date="2026-11-20", time=None, clear=False, confirm=False, tz=TZ, now=NOW)
        task_actions.edit_todos(a, {"op": "toggle", "id": 5, "done": True})
        task_actions.edit_todos(a, {"op": "memo", "id": 5, "memo": "확인함"})
        m = _mail(u.id, thread=b.gmail_thread_id)
        db.add(m)
        db.commit()
        intake.intake_work_mails(db, u.id, ScriptedLLM(), max_mails=5, tz=TZ)
        assert db.get(Email, m.id).task_id == a.id

        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=5))
        db.commit()
        db.expire_all()
        a, b = db.get(Task, a.id), db.get(Task, b.id)
        # 사용자가 정한 기한은 그대로, 중요도는 사용자가 안 바꿨으니 이전 값
        assert a.deadline_at.replace(tzinfo=UTC) == _kst(2026, 11, 20) and a.deadline_source == "사용자가 직접 정함"
        assert a.priority == "NORMAL" and a.priority_source == "llm"
        moved = {p["id"]: p for p in b.plan}
        assert moved[5]["done"] is True and moved[5]["evidence"] == "확인함"
        assert [p["id"] for p in a.plan] == [1, 3]
        assert db.get(Email, m.id).task_id == b.id
        assert set(_thread_mails(db, b.id)) == set(b_mails) | {m.id}


def test_undo_keeps_user_priority():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        task_actions.set_priority(a, "LOW")
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=1))
        assert a.priority == "LOW" and a.priority_source == "user"
        # 기한은 사용자가 안 바꿨으니 이전 값(추정)으로
        assert a.deadline_kind == "estimated" and a.deadline_at.astimezone(UTC) == _kst(2026, 11, 5)


def test_undo_todo_id_collision():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        # 합친 사이 B에 2번이 생겼다 → 돌아오는 2번(샘플 발송)은 새 번호를 받는다
        b.plan = b.plan + [_todo(2, "다른 일")]
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=1))
        ids = [p["id"] for p in b.plan]
        assert len(ids) == len(set(ids))
        assert {p["title"] for p in b.plan} >= {"샘플 발송", "납기 확인", "다른 일"}


def test_undo_window():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        with pytest.raises(ActionError) as e:
            merge.undo_merge(db, u, a, b.id, NOW)  # 기록 없음
        assert e.value.status == 409
        merge.merge(db, u, b, a, NOW)
        with pytest.raises(ActionError) as e:
            merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=11))
        assert e.value.status == 409 and str(e.value) == "되돌릴 수 있는 합치기가 없습니다."
        assert b.merged_into_id == a.id


def test_dismiss():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        assert [t.id for t in merge_candidates(db, b, NOW)] == [a.id]
        merge.dismiss_merge(b)
        db.commit()
        assert b.merge_dismissed is True
        assert merge_candidates(db, b, NOW) == []


# ---------------------------------------------------------------- 리뷰 수정

def test_undo_picks_todos_by_marker_not_id():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        assert [p.get("merged_from") for p in a.plan] == [None, None, b.id, b.id]
        # 옮겨 온 5번(납기 확인)을 지우고 새 할 일을 만든다 → 새 할 일이 5번을 받는다
        task_actions.edit_todos(a, {"op": "delete", "id": 5})
        mine = task_actions.edit_todos(a, {"op": "add", "title": "내 할 일"})
        assert mine["id"] == 5
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=1))
        assert [p["title"] for p in a.plan] == ["견적서 보내기", "납기 회신", "내 할 일"]
        assert [p["title"] for p in b.plan] == ["견적서 보내기 ", "샘플 발송"]
        assert all("merged_from" not in p for p in a.plan + b.plan)


def test_deleted_and_restored_moved_todo_still_returns():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        removed = task_actions.edit_todos(a, {"op": "delete", "id": 4})
        task_actions.edit_todos(a, {"op": "restore", **removed})
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=1))
        assert [p["id"] for p in b.plan] == [1, 2, 5]


def test_two_merges_into_same_target():
    with SessionLocal() as db:
        u = _user(db)
        a, b, b_mails = _pair(db, u.id)
        c = _task(db, u.id, plan=[_todo(1, "계약서 검토")], priority="LOW",
                  brief={"analysis": "done", "requests": ["계약 검토"]})
        c_mail = _mail(u.id, thread=c.gmail_thread_id, task_id=c.id)
        db.add(c_mail)
        db.commit()
        merge.merge(db, u, b, a, NOW)
        merge.merge(db, u, c, a, NOW + timedelta(minutes=2))
        assert set(a.merge_undo) == {str(b.id), str(c.id)}
        assert merge.undoable_sources(a, NOW + timedelta(minutes=3)) == [c.id, b.id]
        assert merge.undoable_sources(a, NOW + timedelta(minutes=11)) == [c.id]

        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=3))
        db.commit()
        assert b.merged_into_id is None and set(a.merge_undo) == {str(c.id)}
        assert c.merged_into_id == a.id and c_mail.task_id == a.id
        assert [p["title"] for p in a.plan] == ["견적서 보내기", "납기 회신", "계약서 검토"]
        assert a.brief["requests"] == ["견적서 송부", "계약 검토"]
        assert _thread_mails(db, b.id) == b_mails
        assert merge.undoable_sources(a, NOW + timedelta(minutes=3)) == [c.id]

        merge.undo_merge(db, u, a, c.id, NOW + timedelta(minutes=4))
        db.commit()
        assert c.merged_into_id is None and c_mail.task_id == c.id and a.merge_undo == {}
        assert [p["title"] for p in a.plan] == ["견적서 보내기", "납기 회신"]
        assert a.brief["requests"] == ["견적서 송부"]


def test_old_records_pruned_on_merge():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        c = _task(db, u.id)
        merge.merge(db, u, b, a, NOW)
        merge.merge(db, u, c, a, NOW + timedelta(minutes=30))
        assert set(a.merge_undo) == {str(c.id)}


def test_merge_rejects_pending_source():
    with SessionLocal() as db:
        u = _user(db)
        a = _task(db, u.id)
        b = _task(db, u.id, status=TaskStatus.PENDING, brief={"analysis": "pending", "attempts": 0})
        with pytest.raises(ActionError) as e:
            merge.merge(db, u, b, a, NOW)
        assert e.value.status == 409 and str(e.value) == "합칠 수 없는 업무입니다."


def test_undo_after_snooze_wake_keeps_target_visible():
    from app.mail import inbox
    with SessionLocal() as db:
        u = _user(db)
        a, b, _ = _pair(db, u.id)
        a.snoozed_until = NOW + timedelta(days=10)
        b.deadline_at, b.deadline_all_day = _kst(2026, 10, 27), True  # 합치면 A 기한이 10/27 → 10/26 09:00에 풀림
        db.commit()
        merge.merge(db, u, b, a, NOW)
        db.commit()
        assert not inbox.is_snoozed(a, NOW, TZ)
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=5))
        db.commit()
        assert a.deadline_at.astimezone(UTC) == _kst(2026, 11, 5)  # 이전 기한으로 돌아감
        assert a.snoozed_until is None and not inbox.is_snoozed(a, NOW + timedelta(days=5), TZ)
        assert a.snooze_returned_at.astimezone(UTC) == _kst(2026, 10, 26, 9)


@pytest.mark.parametrize("change", ["closed", "merged"])
def test_analysis_skips_task_closed_or_merged_meanwhile(change):
    from sqlalchemy import update
    from tests.test_task_actions import _analysis
    with SessionLocal() as db:
        u = _user(db)
        a = _task(db, u.id)
        t = _task(db, u.id, status=TaskStatus.PENDING, plan=[], brief={"analysis": "pending", "attempts": 0})
        m = _mail(u.id, thread=t.gmail_thread_id, task_id=t.id)
        db.add(m)
        db.commit()

        def meanwhile(prompt, schema):  # LLM이 분석하는 사이 사용자가 닫거나 합쳤다
            values = {"status": TaskStatus.COMPLETED}
            if change == "merged":
                values["merged_into_id"] = a.id
            db.execute(update(Task).where(Task.id == t.id).values(**values))
            return _analysis("HIGH")

        assert intake.analyze_into_task(db, t, m, ScriptedLLM(structured_steps=[meanwhile]), TZ)
        db.commit()
        db.expire_all()
        t2 = db.get(Task, t.id)
        assert t2.status == TaskStatus.COMPLETED and t2.plan == []
        assert t2.brief["analysis"] == "pending"


# ---------------------------------------------------------------- 6단계: 합친 뒤 바뀐 내용 분석 (설계 7.3)

B_BODY = "안녕하세요. 앞서 요청드린 견적 수량을 150개로 변경 부탁드립니다. 감사합니다."


def _on(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)


def _qty_pair(db, uid):
    """A: 수량 100개 견적(기한 11/5). B: 같은 상대의 새 스레드 '150개로 변경'(기한 10/30, 요청 150개)."""
    from app.models import EmailDirection
    a = _task(db, uid, created_at=NOW - timedelta(days=3), deadline_at=_kst(2026, 11, 5), deadline_all_day=True,
              deadline_kind="explicit", deadline_source="11월 5일까지",
              brief={"analysis": "done", "summary": "수량 100개 견적 요청", "needs_reply": True,
                     "requests": ["수량 100개 견적"]})
    b = _task(db, uid, created_at=NOW - timedelta(hours=2), deadline_at=_kst(2026, 10, 30), deadline_all_day=True,
              deadline_kind="explicit", deadline_source="30일까지", plan=[_todo(1, "수량 150개로 견적 다시 내기")],
              brief={"analysis": "done", "summary": "수량 150개로 변경", "needs_reply": True,
                     "requests": ["수량 150개 견적"]})
    am = _mail(uid, thread=a.gmail_thread_id, task_id=a.id)
    am.content = "견적 수량은 100개입니다. 11월 5일까지 부탁드립니다."
    bm = _mail(uid, thread=b.gmail_thread_id, task_id=b.id)
    bm.content = B_BODY
    sent = _mail(uid, thread=b.gmail_thread_id, task_id=b.id)  # B 스레드에 내가 보낸 메일
    sent.direction, sent.content = EmailDirection.OUTBOUND, "확인했습니다."
    db.add_all([am, bm, sent])
    db.commit()
    return a, b, bm.id, sent.id


def _qty_change(evidence="견적 수량을 150개로 변경 부탁드립니다"):
    return {"changes": [{"field": "requests", "before": "수량 100개 견적", "after": "수량 150개 견적",
                         "evidence": evidence, "kind": "confirmed",
                         "deadline": {"text": "", "date": "", "time": "", "kind": "none"}}],
            "needs_reply": True, "suspicious": []}


def _run_ma(db, uid, llm):
    from app.mail.merge_analysis import run_merge_analyses
    n = run_merge_analyses(db, uid, llm, TZ)
    db.commit()
    db.expire_all()
    return n


def test_merge_runs_change_analysis(monkeypatch):
    _on(monkeypatch)
    with SessionLocal() as db:
        u = _user(db)
        a, b, bm, sent = _qty_pair(db, u.id)
        b.brief = {**b.brief, intake.CHANGES_PENDING_KEY: [bm]}  # B에서 아직 변경 분석을 기다리던 메일
        db.commit()
        merge.merge(db, u, b, a, NOW)
        db.commit()
        # 합치는 순간에는 메일 · 할 일 · 중요도만 옮긴다. 요청 · 기한 · 요약은 분석 결과로 고친다
        assert a.brief["requests"] == ["수량 100개 견적"] and a.brief["summary"] == "수량 100개 견적 요청"
        assert a.deadline_at.astimezone(UTC) == _kst(2026, 11, 5)
        assert [p["title"] for p in a.plan][-1] == "수량 150개로 견적 다시 내기"
        assert a.merge_analysis == {"state": "pending", "source_id": b.id, "email_ids": sorted([bm, sent]),
                                    "at": NOW.isoformat()}
        assert a.merge_undo[str(b.id)]["added_requests"] == []
        assert b.brief[intake.CHANGES_PENDING_KEY] == [bm]  # 되돌리면 B의 대기열이 그대로 살아 있다

        llm = ScriptedLLM(structured_steps=[_qty_change()])
        assert _run_ma(db, u.id, llm) == 1
        assert len(llm.structured_prompts) == 1 and B_BODY in llm.structured_prompts[0]
        assert "확인했습니다." not in llm.structured_prompts[0].split("새로 온 메일")[1]  # 보낸 메일은 새 메일이 아니다
        a = db.get(Task, a.id)
        assert a.brief["requests"] == ["수량 150개 견적"]  # "100개"와 "150개"가 함께 남지 않는다
        assert a.merge_analysis["state"] == "done"
        assert [(e["field"], e["email_id"]) for e in a.change_log] == [("requests", bm)]
        assert _run_ma(db, u.id, ScriptedLLM(structured_steps=[AssertionError("no call")])) == 0

        # 되돌리기: 분석으로 바뀐 내용도 합치기 전으로, 그 변경 기록은 지운다
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=5))
        db.commit()
        assert a.brief["requests"] == ["수량 100개 견적"] and a.change_log == [] and a.merge_analysis == {}
        assert db.get(Task, b.id).brief[intake.CHANGES_PENDING_KEY] == [bm]


def test_merge_analysis_fallback_on_failure(monkeypatch):
    _on(monkeypatch)
    with SessionLocal() as db:
        u = _user(db)
        a, b, bm, _ = _qty_pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        db.commit()
        assert _run_ma(db, u.id, ScriptedLLM(structured_steps=[{"bad": 1}, {"bad": 2}])) == 1
        a = db.get(Task, a.id)
        assert a.brief["requests"] == ["수량 100개 견적", "수량 150개 견적"]  # 5단계: 합집합
        assert merge._utc(a.deadline_at) == _kst(2026, 10, 30)  # 5단계: 이른 기한
        assert a.merge_analysis["state"] == "fallback"
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=5))
        db.commit()
        assert a.brief["requests"] == ["수량 100개 견적"] and merge._utc(a.deadline_at) == _kst(2026, 11, 5)


def test_merge_analysis_outage_stays_pending(monkeypatch):
    from app.agent.llm import LLMError
    _on(monkeypatch)
    with SessionLocal() as db:
        u = _user(db)
        a, b, _, _ = _qty_pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        db.commit()
        assert _run_ma(db, u.id, ScriptedLLM(structured_steps=[LLMError("down")])) == 0
        a = db.get(Task, a.id)
        assert a.merge_analysis["state"] == "pending" and a.brief["requests"] == ["수량 100개 견적"]
        assert not a.brief.get(intake.CHANGES_PENDING_KEY)  # 합친 메일을 재답장 대기열로 보내지 않는다(두 번 분석 방지)
        assert _run_ma(db, u.id, ScriptedLLM(structured_steps=[_qty_change()])) == 1
        assert db.get(Task, a.id).brief["requests"] == ["수량 150개 견적"]


def test_merge_analysis_after_undo_dropped(monkeypatch):
    _on(monkeypatch)
    with SessionLocal() as db:
        u = _user(db)
        a, b, _, _ = _qty_pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        db.commit()
        merge.undo_merge(db, u, a, b.id, NOW + timedelta(minutes=1))
        db.commit()
        assert a.merge_analysis == {}
        assert _run_ma(db, u.id, ScriptedLLM(structured_steps=[AssertionError("no call")])) == 0

        # 분석하는 사이에 되돌렸다: 결과를 버린다
        merge.merge(db, u, db.get(Task, b.id), db.get(Task, a.id), NOW)
        db.commit()

        def meanwhile(prompt, schema):
            merge.undo_merge(db, u, db.get(Task, a.id), b.id, NOW + timedelta(minutes=1))
            db.commit()
            return _qty_change()

        _run_ma(db, u.id, ScriptedLLM(structured_steps=[meanwhile]))
        a = db.get(Task, a.id)
        assert a.brief["requests"] == ["수량 100개 견적"] and a.change_log == [] and a.merge_analysis == {}
        assert db.get(Task, b.id).merged_into_id is None


def test_merge_switch_off_uses_stage5_rules():
    with SessionLocal() as db:
        u = _user(db)
        a, b, _, _ = _qty_pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        db.commit()
        assert a.brief["requests"] == ["수량 100개 견적", "수량 150개 견적"]
        assert a.deadline_at.astimezone(UTC) == _kst(2026, 10, 30)
        assert a.merge_analysis == {}
        assert _run_ma(db, u.id, ScriptedLLM(structured_steps=[AssertionError("no call")])) == 0


def test_pending_merge_finished_by_stage5_rules_when_switch_turned_off(monkeypatch):
    from app.config import get_settings
    from app.mail import sync
    _on(monkeypatch)
    with SessionLocal() as db:
        u = _user(db)
        a, b, _, _ = _qty_pair(db, u.id)
        merge.merge(db, u, b, a, NOW)
        db.commit()
        monkeypatch.setattr(get_settings(), "mail_triage_enabled", False)
        sync.process_inbox(db, u.id)
        db.expire_all()
        a = db.get(Task, a.id)
        assert a.merge_analysis["state"] == "fallback"
        assert a.brief["requests"] == ["수량 100개 견적", "수량 150개 견적"]


def test_merge_api_wakes_worker_and_shows_analyzing(client, monkeypatch):
    from app.mail import sync
    _on(monkeypatch)
    woke = []
    monkeypatch.setattr(sync, "wake", lambda: woke.append(1))
    me = register(client)
    with SessionLocal() as db:
        a, b, _, _ = _qty_pair(db, me["id"])
        a_id, b_id = a.id, b.id
    r = client.post(f"/api/tasks/{b_id}/merge", json={"into_task_id": a_id})
    assert r.status_code == 200, r.text
    assert r.json()["mail"]["merge_analyzing"] is True and woke == [1]
