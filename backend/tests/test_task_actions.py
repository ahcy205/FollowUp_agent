"""5단계 Task 3: 업무 동작 (스누즈 · 중요도 · 기한 · 할 일 · 닫기 · 다시 분석 · 업무로 처리)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.mail import intake, task_actions
from app.mail.task_actions import ActionError
from app.models import AgentActionLog, Email, EmailDirection, Task, TaskStatus, User
from tests.conftest import ScriptedLLM, make_client, register

TZ = "Asia/Seoul"
UTC = timezone.utc
# 2026-10-26 (월) 10:00 KST
NOW = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)
_seq = iter(range(1, 1_000_000))


def _user(db, email="u@example.com") -> User:
    u = User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    return u


def _task(db, uid, **over) -> Task:
    n = next(_seq)
    fields = dict(user_id=uid, title="견적 요청", goal="견적서 송부", status=TaskStatus.ACTION_REQUIRED,
                  origin="mail", agent_enabled=False, gmail_thread_id=f"thr-{n}", priority="NORMAL",
                  plan=[{"id": 1, "title": "견적서 보내기", "done": False, "evidence": "", "due_at": None,
                         "source": "agent"},
                        {"id": 3, "title": "납기 회신", "done": False, "evidence": "", "due_at": None,
                         "source": "agent"}],
                  brief={"analysis": "done", "summary": "견적 요청", "needs_reply": True})
    fields.update(over)
    t = Task(**fields)
    db.add(t)
    db.commit()
    return t


def _mail(uid, *, thread, triage="work", task_id=None) -> Email:
    n = next(_seq)
    return Email(user_id=uid, sender="kim@partner.co.kr", receiver="me@example.com", subject="견적",
                 content="본문", direction=EmailDirection.INBOUND, processed=False,
                 received_at=NOW - timedelta(hours=1), gmail_message_id=f"msg-{n}", gmail_thread_id=thread,
                 triage=triage, task_id=task_id, gmail_meta={})


def _logs(db, task_id, action_type):
    return db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == task_id,
                                                   AgentActionLog.action_type == action_type)).all()


def _kst(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC) - timedelta(hours=9)


# ---------------------------------------------------------------- 스누즈

def test_snooze_presets():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id, snooze_returned_at=NOW - timedelta(hours=1))
        task_actions.snooze(t, preset="evening", until=None, now=NOW, tz=TZ)
        assert t.snoozed_until.astimezone(UTC) == _kst(2026, 10, 26, 18)
        assert t.snooze_returned_at is None
        task_actions.snooze(t, preset="tomorrow", until=None, now=NOW, tz=TZ)
        assert t.snoozed_until.astimezone(UTC) == _kst(2026, 10, 27, 9)
        task_actions.snooze(t, preset="next_monday", until=None, now=NOW, tz=TZ)
        assert t.snoozed_until.astimezone(UTC) == _kst(2026, 11, 2, 9)
        assert _logs(db, t.id, "TASK_SNOOZED")[-1].output_data == {"until": _kst(2026, 11, 2, 9).isoformat()}

        # 저녁 6시가 지난 뒤의 evening
        late = _kst(2026, 10, 26, 19)
        with pytest.raises(ActionError) as e:
            task_actions.snooze(t, preset="evening", until=None, now=late, tz=TZ)
        assert e.value.status == 400
        assert t.snoozed_until.astimezone(UTC) == _kst(2026, 11, 2, 9)  # 그대로

        # 알 수 없는 선택지 · 둘 다 없음
        for kw in ({"preset": "someday", "until": None}, {"preset": None, "until": None}):
            with pytest.raises(ActionError) as e:
                task_actions.snooze(t, now=NOW, tz=TZ, **kw)
            assert e.value.status == 400

        # 지금 보기: 스누즈를 지우고 '다시 표시됨'을 찍지 않는다
        task_actions.unsnooze(t)
        assert t.snoozed_until is None and t.snooze_returned_at is None
        db.flush()
        assert len(_logs(db, t.id, "TASK_UNSNOOZED")) == 1


def test_snooze_deadline_too_close():
    with SessionLocal() as db:
        u = _user(db)
        # 기한 내일(10/27) 하루 종일 → 안전장치 10/26 09:00 KST는 이미 지났다
        t = _task(db, u.id, deadline_at=_kst(2026, 10, 27), deadline_all_day=True, deadline_kind="explicit")
        with pytest.raises(ActionError) as e:
            task_actions.snooze(t, preset="next_monday", until=None, now=NOW, tz=TZ)
        assert e.value.status == 400
        assert str(e.value) == "기한이 가까워 지금은 숨길 수 없습니다."
        assert t.snoozed_until is None


def test_snooze_custom_validation():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id)
        for bad in (NOW - timedelta(minutes=1), NOW, NOW + timedelta(days=366)):
            with pytest.raises(ActionError) as e:
                task_actions.snooze(t, preset=None, until=bad, now=NOW, tz=TZ)
            assert e.value.status == 400
        assert t.snoozed_until is None
        ok = NOW + timedelta(days=3)
        task_actions.snooze(t, preset=None, until=ok.replace(tzinfo=None), now=NOW, tz=TZ)  # naive → UTC
        assert t.snoozed_until.astimezone(UTC) == ok


def test_closed_or_merged_task_rejects_actions():
    with SessionLocal() as db:
        u = _user(db)
        done = _task(db, u.id, status=TaskStatus.COMPLETED)
        target = _task(db, u.id)
        merged = _task(db, u.id, merged_into_id=target.id)
        for t in (done, merged):
            for call in (lambda: task_actions.snooze(t, preset="tomorrow", until=None, now=NOW, tz=TZ),
                         lambda: task_actions.set_priority(t, "HIGH"),
                         lambda: task_actions.edit_todos(t, {"op": "add", "title": "x"}),
                         lambda: task_actions.close_without_reply(db, u, t, "")):
                with pytest.raises(ActionError) as e:
                    call()
                assert e.value.status == 409
                assert str(e.value) == "이미 완료되었거나 취소된 업무입니다."


def test_new_mail_unsnoozes(monkeypatch):
    monkeypatch.setattr(intake, "datetime", _FixedDatetime)
    with SessionLocal() as db:
        u = _user(db)
        snoozed = _task(db, u.id, snoozed_until=NOW + timedelta(days=3))
        plain = _task(db, u.id)
        db.add_all([_mail(u.id, thread=snoozed.gmail_thread_id), _mail(u.id, thread=plain.gmail_thread_id)])
        db.commit()
        r = intake.intake_work_mails(db, u.id, ScriptedLLM(), max_mails=5, tz=TZ)
        assert r.attached == 2
        db.expire_all()
        s, p = db.get(Task, snoozed.id), db.get(Task, plain.id)
        assert s.snoozed_until is None
        assert s.snooze_returned_at.replace(tzinfo=UTC) == NOW
        assert p.snooze_returned_at is None  # 스누즈 중이 아니던 업무에는 찍지 않는다


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


# ---------------------------------------------------------------- 중요도

def _analysis(priority="HIGH"):
    return {"summary": "파트너사 견적 요청", "counterpart": {"name": "김", "organization": "P"},
            "requests": ["견적"], "todos": [{"title": "견적 보내기", "due_text": "", "due_date": "", "due_time": ""}],
            "deadline": {"text": "", "date": "", "time": "", "kind": "none"},
            "priority": priority, "needs_reply": True, "suspicious": []}


def test_priority_user():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id, priority="HIGH")
        task_actions.set_priority(t, "LOW")
        assert t.priority == "LOW" and t.priority_source == "user"
        db.flush()
        assert _logs(db, t.id, "PRIORITY_CHANGED")[0].output_data == {"from": "HIGH", "to": "LOW"}
        assert t.calendar_state == "none"  # 중요도 변경은 캘린더를 건드리지 않는다
        with pytest.raises(ActionError) as e:
            task_actions.set_priority(t, "URGENT")
        assert e.value.status == 400

        mail = _mail(u.id, thread=t.gmail_thread_id, task_id=t.id)
        db.add(mail)
        db.commit()
        assert intake.analyze_into_task(db, t, mail, ScriptedLLM(analysis=_analysis("HIGH")), TZ)
        assert t.priority == "LOW" and t.priority_source == "user"

        # 사용자가 바꾸지 않은 업무는 분석 결과를 따른다
        other = _task(db, u.id, priority="NORMAL")
        mail2 = _mail(u.id, thread=other.gmail_thread_id, task_id=other.id)
        db.add(mail2)
        db.commit()
        assert intake.analyze_into_task(db, other, mail2, ScriptedLLM(analysis=_analysis("HIGH")), TZ)
        assert other.priority == "HIGH"


# ---------------------------------------------------------------- 기한

def test_deadline_set_clear_confirm():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id)
        task_actions.set_deadline(t, date="2026-10-30", time=None, clear=False, confirm=False, tz=TZ, now=NOW)
        assert t.deadline_at.astimezone(UTC) == _kst(2026, 10, 30)
        assert t.deadline_all_day is True and t.deadline_kind == "explicit"
        assert t.deadline_source == "사용자가 직접 정함"
        assert t.calendar_state == "pending"

        t.calendar_state = "synced"
        task_actions.set_deadline(t, date="2026-10-30", time="14:30", clear=False, confirm=False, tz=TZ, now=NOW)
        assert t.deadline_at.astimezone(UTC) == _kst(2026, 10, 30, 14, 30)
        assert t.deadline_all_day is False
        assert t.calendar_state == "pending"
        db.flush()
        log = _logs(db, t.id, "DEADLINE_CHANGED")[-1].output_data
        assert log == {"from": _kst(2026, 10, 30).isoformat(), "to": _kst(2026, 10, 30, 14, 30).isoformat(),
                       "kind": "explicit"}

        t.calendar_state = "synced"
        task_actions.set_deadline(t, date=None, time=None, clear=True, confirm=False, tz=TZ, now=NOW)
        assert t.deadline_at is None and t.deadline_kind == "none" and t.deadline_source == ""
        assert t.deadline_all_day is False

        est = _task(db, u.id, deadline_at=_kst(2026, 11, 6), deadline_all_day=True, deadline_kind="estimated",
                    deadline_source="다음 주 중", calendar_state="synced")
        task_actions.set_deadline(est, date=None, time=None, clear=False, confirm=True, tz=TZ, now=NOW)
        assert est.deadline_kind == "explicit" and est.deadline_source == "다음 주 중"
        assert est.deadline_at.astimezone(UTC) == _kst(2026, 11, 6)
        assert est.calendar_state == "pending"
        with pytest.raises(ActionError) as e:  # 이미 확정
            task_actions.set_deadline(est, date=None, time=None, clear=False, confirm=True, tz=TZ, now=NOW)
        assert e.value.status == 409


def test_deadline_invalid():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id)
        bad = [dict(date="2026-13-01", time=None), dict(date="2029-10-26", time=None),
               dict(date="2024-10-01", time=None), dict(date="2026/10/30", time=None),
               dict(date="2026-10-30", time="25:00"), dict(date="2026-10-30", time="9시"),
               dict(date=None, time=None)]
        for kw in bad:
            with pytest.raises(ActionError) as e:
                task_actions.set_deadline(t, clear=False, confirm=False, tz=TZ, now=NOW, **kw)
            assert e.value.status == 400, kw
        with pytest.raises(ActionError):  # 두 가지를 한꺼번에
            task_actions.set_deadline(t, date="2026-10-30", time=None, clear=True, confirm=False, tz=TZ, now=NOW)
        assert t.deadline_at is None and t.calendar_state == "none"


# ---------------------------------------------------------------- 할 일

def test_todos_ops():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id, deadline_at=_kst(2026, 10, 30), deadline_all_day=True, deadline_kind="explicit",
                  calendar_state="synced")
        added = task_actions.edit_todos(t, {"op": "add", "title": "  일정 확인  "})
        assert added == {"id": 4, "title": "일정 확인", "done": False, "evidence": "", "due_at": None,
                         "source": "user"}
        assert [p["id"] for p in t.plan] == [1, 3, 4]
        assert t.calendar_state == "pending"

        task_actions.edit_todos(t, {"op": "memo", "id": 1, "memo": "견적서 v2 보냄"})
        item = task_actions.edit_todos(t, {"op": "toggle", "id": 1, "done": True})
        assert item["done"] is True and item["evidence"] == "견적서 v2 보냄"
        item = task_actions.edit_todos(t, {"op": "toggle", "id": 1, "done": False})
        assert item["done"] is False and item["evidence"] == "견적서 v2 보냄"  # 체크 해제해도 메모는 남는다
        task_actions.edit_todos(t, {"op": "toggle", "id": 3, "done": True})

        before = [dict(p) for p in t.plan]
        removed = task_actions.edit_todos(t, {"op": "delete", "id": 3})
        assert removed == {"item": before[1], "index": 1}
        assert [p["id"] for p in t.plan] == [1, 4]
        restored = task_actions.edit_todos(t, {"op": "restore", **removed})
        assert restored == before[1]
        assert t.plan == before
        with pytest.raises(ActionError) as e:  # 같은 id가 이미 있다
            task_actions.edit_todos(t, {"op": "restore", **removed})
        assert e.value.status == 409

        db.flush()
        ops = [(lg.output_data["op"], lg.output_data["id"]) for lg in _logs(db, t.id, "TODO_CHANGED")]
        assert ops == [("add", 4), ("memo", 1), ("toggle", 1), ("toggle", 1), ("toggle", 3), ("delete", 3),
                       ("restore", 3)]

        for bad in ({"op": "add", "title": "   "}, {"op": "add", "title": "x" * 301},
                    {"op": "memo", "id": 1, "memo": "x" * 1001}, {"op": "toggle", "id": 1, "done": "yes"},
                    {"op": "rename", "id": 1}, {"op": "toggle", "id": 99, "done": True}):
            with pytest.raises(ActionError):
                task_actions.edit_todos(t, bad)
        assert t.plan == before


# ---------------------------------------------------------------- 닫기 · 다시 분석

def test_close_without_reply():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id, snoozed_until=NOW + timedelta(days=2), deadline_at=_kst(2026, 10, 30),
                  deadline_all_day=True, calendar_state="synced")
        task_actions.close_without_reply(db, u, t, "  전화로 해결  ")
        assert t.status == TaskStatus.COMPLETED and t.completed_at is not None
        assert t.current_step == "답장 필요 없음으로 닫음"
        assert t.snoozed_until is None
        assert t.calendar_state == "pending"
        assert _logs(db, t.id, "TASK_CLOSED")[0].output_data == {"reason": "전화로 해결"}
        with pytest.raises(ActionError) as e:
            task_actions.close_without_reply(db, u, t, "")
        assert e.value.status == 409


def test_cancel_clears_snooze():
    from app.agent import orchestrator
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id, snoozed_until=NOW + timedelta(days=2))
        orchestrator.cancel_task(db, u, t, "")
        db.refresh(t)
        assert t.status == TaskStatus.CANCELLED and t.snoozed_until is None


def test_reanalyze():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id, status=TaskStatus.PENDING, brief={"analysis": "failed", "attempts": 3, "email_id": 1})
        task_actions.request_reanalyze(t)
        assert t.brief["analysis"] == "pending" and t.brief["attempts"] == 0 and t.brief["email_id"] == 1
        assert t.status == TaskStatus.PENDING
        db.commit()
        db.expire_all()
        assert db.get(Task, t.id).brief["analysis"] == "pending"  # 새 dict라 저장된다
        assert len(_logs(db, t.id, "REANALYZE_REQUESTED")) == 1

        done = _task(db, u.id)
        with pytest.raises(ActionError) as e:
            task_actions.request_reanalyze(done)
        assert e.value.status == 409


# ---------------------------------------------------------------- 업무로 처리

def test_promote_new_and_existing(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "mail_triage_enabled", True)
    with SessionLocal() as db:
        u = _user(db)
        # 1) 업무 없는 not_work 메일 → 분석 대기 업무
        m = _mail(u.id, thread="thr-new", triage="not_work")
        db.add(m)
        db.commit()
        task = task_actions.promote_email(db, u, m)
        db.commit()
        assert task.origin == "mail" and task.status == TaskStatus.PENDING
        assert task.brief == {"analysis": "pending", "attempts": 0, "email_id": m.id}
        assert m.task_id == task.id and m.triage == "work" and m.triage_reason == "사용자가 업무로 지정"
        assert _logs(db, task.id, "EMAIL_PROMOTED")[0].output_data == {"email_id": m.id}
        # 다시 누르면 409
        with pytest.raises(ActionError) as e:
            task_actions.promote_email(db, u, m)
        assert e.value.status == 409 and str(e.value) == "이미 업무로 처리된 메일입니다."

        # 2) 스레드에 취소된 업무 → ACTION_REQUIRED로 다시 열고 메일 연결
        closed = _task(db, u.id, status=TaskStatus.CANCELLED, completed_at=NOW, snoozed_until=NOW,
                       deadline_at=_kst(2026, 10, 30), calendar_state="synced")
        m2 = _mail(u.id, thread=closed.gmail_thread_id, triage="filtered")
        db.add(m2)
        db.commit()
        got = task_actions.promote_email(db, u, m2)
        db.commit()
        assert got.id == closed.id
        assert got.status == TaskStatus.ACTION_REQUIRED and got.completed_at is None
        assert got.current_step == "새 메일로 다시 열림"
        assert got.calendar_state == "pending"
        assert m2.task_id == closed.id and m2.processed is True and m2.triage == "work"
        assert len(_logs(db, closed.id, "MAIL_ATTACHED")) == 1

        # 3) 열린 업무 + 스누즈 → 붙이고 스누즈 해제
        open_t = _task(db, u.id, snoozed_until=datetime.now(UTC) + timedelta(days=5))
        m3 = _mail(u.id, thread=open_t.gmail_thread_id, triage="not_work")
        db.add(m3)
        db.commit()
        assert task_actions.promote_email(db, u, m3).id == open_t.id
        assert open_t.snoozed_until is None and open_t.snooze_returned_at is not None
        assert open_t.status == TaskStatus.ACTION_REQUIRED

        # 4) 보낸 메일 · 다른 사용자의 메일
        out = _mail(u.id, thread="thr-out", triage="not_work")
        out.direction = EmailDirection.OUTBOUND
        other = _user(db, "o@example.com")
        foreign = _mail(other.id, thread="thr-f", triage="not_work")
        db.add_all([out, foreign])
        db.commit()
        with pytest.raises(ActionError) as e:
            task_actions.promote_email(db, u, out)
        assert e.value.status == 409
        with pytest.raises(ActionError) as e:
            task_actions.promote_email(db, u, foreign)
        assert e.value.status == 404

        # 5) 스위치 off
        monkeypatch.setattr(s, "mail_triage_enabled", False)
        m5 = _mail(u.id, thread="thr-off", triage="not_work")
        db.add(m5)
        db.commit()
        with pytest.raises(ActionError) as e:
            task_actions.promote_email(db, u, m5)
        assert e.value.status == 409 and str(e.value) == "자동 판별이 꺼져 있어 분석할 수 없습니다."
        assert m5.triage == "not_work"


def test_create_pending_task_race():
    with SessionLocal() as db:
        u = _user(db)
        existing = _task(db, u.id, gmail_thread_id="thr-race")
        m = _mail(u.id, thread="thr-race")
        db.add(m)
        db.commit()
        got = intake.create_pending_task(db, m)
        assert got.id == existing.id


# ---------------------------------------------------------------- API

def _api_task(uid, **over) -> int:
    with SessionLocal() as db:
        return _task(db, uid, **over).id


def test_api_actions(client, monkeypatch):
    me = register(client)
    woke = []
    monkeypatch.setattr("app.mail.sync.wake", lambda: woke.append(1))
    tid = _api_task(me["id"])

    r = client.post(f"/api/tasks/{tid}/snooze", json={"preset": "tomorrow"})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["snoozed_until"] is not None
    assert client.post(f"/api/tasks/{tid}/snooze", json={"preset": "tomorrow", "x": 1}).status_code == 422
    r = client.post(f"/api/tasks/{tid}/unsnooze")
    assert r.status_code == 200 and r.json()["task"]["snoozed_until"] is None

    r = client.post(f"/api/tasks/{tid}/priority", json={"priority": "HIGH"})
    assert r.status_code == 200 and r.json()["task"]["priority_source"] == "user"

    future = (datetime.now(UTC) + timedelta(days=10)).strftime("%Y-%m-%d")
    r = client.post(f"/api/tasks/{tid}/deadline", json={"date": future, "time": "10:00"})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["deadline_kind"] == "explicit"
    r = client.post(f"/api/tasks/{tid}/deadline", json={"date": "2026-13-01"})
    assert r.status_code == 400 and r.json()["detail"]

    r = client.post(f"/api/tasks/{tid}/todos", json={"op": "add", "title": "새 할 일"})
    assert r.status_code == 200 and r.json()["task"]["plan"][-1]["source"] == "user"
    plan = r.json()["task"]["plan"]
    r = client.post(f"/api/tasks/{tid}/todos", json={"op": "delete", "id": plan[0]["id"]})
    assert r.status_code == 200 and len(r.json()["task"]["plan"]) == len(plan) - 1
    r = client.post(f"/api/tasks/{tid}/todos", json={"op": "restore", "item": plan[0], "index": 0})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["plan"] == plan
    assert client.post(f"/api/tasks/{tid}/todos", json={"op": "toggle", "id": 999, "done": True}).status_code == 404

    assert woke  # 기한을 넣어 캘린더 반영이 필요해지면 동기화를 바로 깨운다
    woke.clear()
    failed = _api_task(me["id"], status=TaskStatus.PENDING, brief={"analysis": "failed", "attempts": 3})
    r = client.post(f"/api/tasks/{failed}/reanalyze")
    assert r.status_code == 200 and r.json()["task"]["brief"]["analysis"] == "pending"
    assert woke == [1]
    assert client.post(f"/api/tasks/{failed}/reanalyze").status_code == 409

    r = client.post(f"/api/tasks/{tid}/close", json={"reason": "해결"})
    assert r.status_code == 200 and r.json()["task"]["status"] == "COMPLETED"
    assert client.post(f"/api/tasks/{tid}/close", json={}).status_code == 409


def test_api_ownership_and_limits(client, monkeypatch):
    me = register(client)
    monkeypatch.setattr("app.mail.sync.wake", lambda: None)
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    tid = _api_task(me["id"])
    agent_tid = _api_task(me["id"], origin="user", gmail_thread_id=None)

    paths = [("snooze", {"preset": "tomorrow"}), ("unsnooze", None), ("priority", {"priority": "LOW"}),
             ("deadline", {"clear": True}), ("todos", {"op": "add", "title": "x"}), ("close", {}),
             ("reanalyze", None)]
    with make_client() as other:
        register(other, "other@example.com")
        for p, body in paths:
            assert other.post(f"/api/tasks/{tid}/{p}", json=body).status_code == 404, p
    for p, body in paths:
        r = client.post(f"/api/tasks/{agent_tid}/{p}", json=body)
        assert r.status_code == 409, p
        assert r.json()["detail"] == "메일 업무에서만 쓸 수 있습니다."

    with SessionLocal() as db:
        mails = [_mail(me["id"], thread=f"thr-p{i}", triage="not_work") for i in range(11)]
        db.add_all(mails)
        db.commit()
        ids = [m.id for m in mails]
    # 다른 사용자의 메일 → 404, 쿼터를 쓰지 않는다
    with make_client() as other:
        other.post("/api/auth/login", json={"email": "other@example.com", "password": "password123"})
        assert other.post(f"/api/emails/{ids[0]}/promote").status_code == 404
    for i in range(10):
        r = client.post(f"/api/emails/{ids[i]}/promote")
        assert r.status_code == 200, r.text
        assert isinstance(r.json()["task_id"], int)
    # 이미 처리된 메일은 409 (쿼터 검사 전)
    assert client.post(f"/api/emails/{ids[0]}/promote").status_code == 409
    assert client.post(f"/api/emails/{ids[10]}/promote").status_code == 429


# ---------------------------------------------------------------- 합쳐진 스레드 (리뷰 수정)

def test_promote_follows_merged_thread(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    with SessionLocal() as db:
        u = _user(db)
        a = _task(db, u.id)
        b = _task(db, u.id, status=TaskStatus.COMPLETED, merged_into_id=a.id, completed_at=NOW)
        m = _mail(u.id, thread=b.gmail_thread_id, triage="not_work")
        db.add(m)
        db.commit()
        got = task_actions.promote_email(db, u, m)
        db.commit()
        assert got.id == a.id and m.task_id == a.id
        db.refresh(b)
        assert b.status == TaskStatus.COMPLETED and b.completed_at is not None


def test_intake_new_mail_on_merged_thread(monkeypatch):
    monkeypatch.setattr(intake, "datetime", _FixedDatetime)
    with SessionLocal() as db:
        u = _user(db)
        a = _task(db, u.id, snoozed_until=NOW + timedelta(days=3))
        b = _task(db, u.id, status=TaskStatus.COMPLETED, merged_into_id=a.id)
        m = _mail(u.id, thread=b.gmail_thread_id)
        db.add(m)
        db.commit()
        r = intake.intake_work_mails(db, u.id, ScriptedLLM(), max_mails=5, tz=TZ)
        assert r.attached == 1
        db.expire_all()
        assert db.get(Email, m.id).task_id == a.id
        a2 = db.get(Task, a.id)
        assert a2.snoozed_until is None and a2.snooze_returned_at.replace(tzinfo=UTC) == NOW
        assert db.get(Task, b.id).status == TaskStatus.COMPLETED


def test_resolve_merged_guards():
    from app.mail.merge import resolve_merged
    with SessionLocal() as db:
        u = _user(db)
        other = _user(db, "o@example.com")
        x = _task(db, u.id)
        y = _task(db, u.id, merged_into_id=x.id)
        x.merged_into_id = y.id  # 순환
        db.commit()
        assert resolve_merged(db, y).id in (x.id, y.id)  # 멈춘다
        foreign = _task(db, other.id)
        z = _task(db, u.id, merged_into_id=foreign.id)
        assert resolve_merged(db, z).id == z.id  # 다른 사용자의 업무로는 가지 않는다
        chain = [_task(db, u.id)]
        for _ in range(7):
            chain.append(_task(db, u.id, merged_into_id=chain[-1].id))
        assert resolve_merged(db, chain[-1]).id == chain[-1 - 5].id  # 최대 5번


def test_reopen_clears_next_action_and_lenient_inputs(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    with SessionLocal() as db:
        u = _user(db)
        closed = _task(db, u.id, status=TaskStatus.COMPLETED, next_action="사용자가 취소한 업무입니다.")
        m = _mail(u.id, thread=closed.gmail_thread_id, triage="not_work")
        db.add(m)
        db.commit()
        task_actions.promote_email(db, u, m)
        assert closed.next_action == ""

        t = _task(db, u.id)
        task_actions.set_deadline(t, date="2026-10-30", time="14:30:00", clear=False, confirm=False, tz=TZ, now=NOW)
        assert t.deadline_at.astimezone(UTC) == _kst(2026, 10, 30, 14, 30) and not t.deadline_all_day
        with pytest.raises(ActionError) as e:
            task_actions.snooze(t, preset="someday", until=None, now=NOW, tz=TZ)
        assert str(e.value) == "알 수 없는 선택지입니다."


def test_api_lenient_inputs(client):
    me = register(client)
    tid = _api_task(me["id"])
    r = client.post(f"/api/tasks/{tid}/snooze", json={"preset": "someday"})
    assert r.status_code == 400 and r.json()["detail"] == "알 수 없는 선택지입니다."
    future = (datetime.now(UTC) + timedelta(days=10)).strftime("%Y-%m-%d")
    assert client.post(f"/api/tasks/{tid}/deadline", json={"date": future, "time": "09:00:00"}).status_code == 200
    r = client.post(f"/api/tasks/{tid}/deadline", json={"date": future, "time": "9 o'clock!"})
    assert r.status_code == 400 and "HH:MM" in r.json()["detail"]


def test_api_restore_keeps_merged_from(client):
    """합쳐 온 할 일(merged_from)을 지웠다 되살려도 표시가 남아야 합치기를 되돌릴 때 원래 업무로 간다."""
    me = register(client)
    item = {"id": 3, "title": "납기 확인", "done": False, "evidence": "", "due_at": None, "source": "agent",
            "merged_from": 42}
    tid = _api_task(me["id"], plan=[item])
    r = client.post(f"/api/tasks/{tid}/todos", json={"op": "delete", "id": 3})
    assert r.status_code == 200 and r.json()["task"]["plan"] == []
    r = client.post(f"/api/tasks/{tid}/todos", json={"op": "restore", "item": item, "index": 0})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["plan"] == [item]


# ---------------------------------------------------------------- 리뷰 수정 (스누즈 종료 · 재분석 · 탭)

def test_deadline_later_after_wake_keeps_task_visible():
    from app.mail import inbox
    with SessionLocal() as db:
        u = _user(db)
        # 정한 시각은 11/10이지만 기한(10/27) 전날 09:00에 이미 풀렸다
        t = _task(db, u.id, snoozed_until=_kst(2026, 11, 10, 9), deadline_at=_kst(2026, 10, 27),
                  deadline_all_day=True, deadline_kind="explicit")
        assert not inbox.is_snoozed(t, NOW, TZ)
        task_actions.set_deadline(t, date="2026-11-20", time=None, clear=False, confirm=False, tz=TZ, now=NOW)
        assert t.snoozed_until is None
        assert t.snooze_returned_at.astimezone(UTC) == _kst(2026, 10, 26, 9)
        # 11/9에도 숨지 않고, 11/10에 다시 표시 알림이 또 울리지 않는다
        assert not inbox.is_snoozed(t, _kst(2026, 11, 9, 10), TZ)
        later = inbox.alerts_for(t, None, None, _kst(2026, 11, 10, 10), TZ)
        assert [a for a in later if a["kind"] == "snooze"] == []

        # 아직 스누즈 중이면 그대로 둔다
        s = _task(db, u.id, snoozed_until=NOW + timedelta(days=2))
        task_actions.set_deadline(s, date="2026-11-20", time=None, clear=False, confirm=False, tz=TZ, now=NOW)
        assert s.snoozed_until is not None and s.snooze_returned_at is None


def test_new_mail_after_long_ended_snooze_no_stamp(monkeypatch):
    monkeypatch.setattr(intake, "datetime", _FixedDatetime)
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id, snoozed_until=NOW - timedelta(days=3))
        db.add(_mail(u.id, thread=t.gmail_thread_id))
        db.commit()
        assert intake.intake_work_mails(db, u.id, ScriptedLLM(), max_mails=5, tz=TZ).attached == 1
        db.expire_all()
        assert db.get(Task, t.id).snooze_returned_at is None


def test_snooze_only_todo_tab():
    with SessionLocal() as db:
        u = _user(db)
        for over in ({"status": TaskStatus.WAITING},
                     {"status": TaskStatus.PENDING, "brief": {"analysis": "done", "needs_reply": False}}):
            t = _task(db, u.id, **over)
            with pytest.raises(ActionError) as e:
                task_actions.snooze(t, preset="tomorrow", until=None, now=NOW, tz=TZ)
            assert e.value.status == 409 and str(e.value) == "처리할 일에 있는 업무만 나중에 볼 수 있습니다."
            assert t.snoozed_until is None
        # 분석 대기 업무는 처리할 일 탭이므로 된다
        p = _task(db, u.id, status=TaskStatus.PENDING, brief={"analysis": "pending", "attempts": 0})
        task_actions.snooze(p, preset="tomorrow", until=None, now=NOW, tz=TZ)
        assert p.snoozed_until is not None


def _reanalysis():
    return {"summary": "파트너사 견적 요청", "counterpart": {"name": "김", "organization": "P"},
            "requests": ["견적"],
            "todos": [{"title": "견적 보내기", "due_text": "", "due_date": "", "due_time": ""},
                      {"title": "납기 확인", "due_text": "", "due_date": "", "due_time": ""}],
            "deadline": {"text": "10월 28일", "date": "2026-10-28", "time": "", "kind": "explicit"},
            "priority": "NORMAL", "needs_reply": True, "suspicious": []}


def test_reanalysis_keeps_user_todos_and_deadline(monkeypatch):
    monkeypatch.setattr(intake, "datetime", _FixedDatetime)
    with SessionLocal() as db:
        u = _user(db)
        user_items = [{"id": 2, "title": "내가 넣은 일", "done": True, "evidence": "했음", "due_at": None,
                       "source": "user"},
                      {"id": 7, "title": "합쳐 온 내 일", "done": False, "evidence": "", "due_at": None,
                       "source": "user", "merged_from": 42}]
        t = _task(db, u.id, plan=[{"id": 1, "title": "옛 할 일", "done": False, "evidence": "", "due_at": None,
                                   "source": "agent"}] + user_items,
                  deadline_at=_kst(2026, 11, 20), deadline_all_day=True, deadline_kind="explicit",
                  deadline_source=task_actions.USER_DEADLINE_SOURCE)
        mail = _mail(u.id, thread=t.gmail_thread_id, task_id=t.id)
        db.add(mail)
        db.commit()
        assert intake.analyze_into_task(db, t, mail, ScriptedLLM(analysis=_reanalysis()), TZ)
        assert [p["title"] for p in t.plan] == ["견적 보내기", "납기 확인", "내가 넣은 일", "합쳐 온 내 일"]
        assert len({p["id"] for p in t.plan}) == 4
        kept = t.plan[2:]
        assert kept[0]["done"] is True and kept[0]["evidence"] == "했음" and kept[0]["source"] == "user"
        assert kept[1]["merged_from"] == 42
        # 사용자가 정한 기한은 그대로
        assert t.deadline_at.replace(tzinfo=UTC) == _kst(2026, 11, 20)  # DB에서 다시 읽어 naive UTC
        assert t.deadline_source == task_actions.USER_DEADLINE_SOURCE and t.deadline_kind == "explicit"

        # 사용자가 정하지 않은 기한은 분석 결과를 따른다
        o = _task(db, u.id, deadline_at=_kst(2026, 11, 20), deadline_kind="estimated", deadline_source="다음 달")
        mail2 = _mail(u.id, thread=o.gmail_thread_id, task_id=o.id)
        db.add(mail2)
        db.commit()
        assert intake.analyze_into_task(db, o, mail2, ScriptedLLM(analysis=_reanalysis()), TZ)
        assert o.deadline_at.astimezone(UTC) == _kst(2026, 10, 28)  # 분석이 새로 넣은 aware 값
        assert [p["source"] for p in o.plan] == ["agent", "agent"]


def test_promote_into_reference_task_makes_it_work(monkeypatch):
    monkeypatch.setattr(get_settings(), "mail_triage_enabled", True)
    with SessionLocal() as db:
        u = _user(db)
        ref = _task(db, u.id, status=TaskStatus.PENDING, brief={"analysis": "done", "needs_reply": False})
        m = _mail(u.id, thread=ref.gmail_thread_id, triage="not_work")
        db.add(m)
        db.commit()
        assert task_actions.promote_email(db, u, m).id == ref.id
        assert ref.status == TaskStatus.ACTION_REQUIRED
        # 분석 대기 업무는 그대로 분석 대기로 둔다
        pend = _task(db, u.id, status=TaskStatus.PENDING, brief={"analysis": "pending", "attempts": 0})
        m2 = _mail(u.id, thread=pend.gmail_thread_id, triage="not_work")
        db.add(m2)
        db.commit()
        task_actions.promote_email(db, u, m2)
        assert pend.status == TaskStatus.PENDING
