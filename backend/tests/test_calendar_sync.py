"""4단계 Task 4: 업무 기한을 Google 캘린더 일정으로 동기화한다 (FakeGoogle)."""
import logging
import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.agent import orchestrator
from app.db import SessionLocal
from app.google.client import GoogleError, GoogleTransientError
from app.google.crypto import encrypt_token
from app.mail.calendar_event import event_id_for
from app.mail.calendar_sync import CalendarSyncResult, mark_calendar_dirty, sync_calendars
from app.mail.intake import intake_work_mails
from app.models import AgentActionLog, GoogleAccount, GoogleAccountStatus, Task, TaskStatus, User
from tests.conftest import ScriptedLLM
from tests.test_mail_intake import _add, _analysis, _deadline, _mail, _mail_tasks

TZ = "Asia/Seoul"
URL = "http://localhost:8000"
SUMMARY = "파트너사가 견적서 송부를 요청함"
NAME = "김대리"
# 2026-10-30 00:00 KST
DEADLINE = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def setup(google_fake):
    """사용자 1명과 연결된 Google 계정. (user_id, account_id)를 돌려준다."""
    google_fake.valid_refresh_tokens.add("1//fake-refresh")
    with SessionLocal() as db:
        u = User(email="u@example.com", password_hash="x")
        db.add(u)
        db.flush()
        acc = GoogleAccount(user_id=u.id, google_email="me@gmail.com",
                            refresh_token_enc=encrypt_token("1//fake-refresh"),
                            scopes=["openid"], status=GoogleAccountStatus.CONNECTED)
        db.add(acc)
        db.commit()
        return u.id, acc.id


def _task(db, uid, *, deadline=DEADLINE, all_day=True, state="none", event_id=None, origin="mail",
          status=TaskStatus.ACTION_REQUIRED) -> int:
    t = Task(user_id=uid, title=SUMMARY, goal="견적서 송부", status=status, origin=origin, agent_enabled=False,
             plan=[{"id": 1, "title": "견적서 작성해 보내기", "done": False}],
             brief={"analysis": "done", "summary": SUMMARY,
                    "counterpart": {"name": NAME, "organization": "파트너"}, "requests": ["견적서 송부"]},
             deadline_at=deadline, deadline_all_day=all_day,
             deadline_kind="explicit" if deadline else "none", deadline_source="이번 주 금요일" if deadline else "",
             calendar_state=state, calendar_event_id=event_id)
    db.add(t)
    db.commit()
    return t.id


def _sync(db, account_id, fake, max_tasks=10) -> CalendarSyncResult:
    acc = db.get(GoogleAccount, account_id)
    r = sync_calendars(db, acc, fake, max_tasks=max_tasks, tz=TZ, public_url=URL)
    db.expire_all()
    return r


def _salt(db, account_id, value=None) -> str:
    acc = db.get(GoogleAccount, account_id)
    if value is not None:
        acc.calendar_id_salt = value
        db.commit()
    return acc.calendar_id_salt


MARK = "testsalt"  # 테스트에서 미리 정해 두는 salt (base32hex)


def _logs(db, task_id, action_type):
    return db.scalars(select(AgentActionLog).where(AgentActionLog.task_id == task_id,
                                                   AgentActionLog.action_type == action_type)).all()


def _calls(fake, method):
    return [c for c in fake.calendar_calls if c[0] == method]


def test_creates_event_for_deadline_task(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        r = _sync(db, aid, google_fake)
        assert (r.created, r.updated, r.deleted_by_user, r.errors) == (1, 0, 0, 0)
        task = db.get(Task, tid)
        eid = event_id_for(task, _salt(db, aid))
        assert _calls(google_fake, "insert") == [("insert", eid)]
        assert task.calendar_state == "synced"
        assert task.calendar_event_id == eid
        assert task.calendar_event_url == f"https://calendar.example/event/{eid}"
        assert task.calendar_attempts == 0 and task.calendar_error == ""
        ev = google_fake.events[eid]
        assert ev["start"] == {"date": "2026-10-30"} and ev["summary"] == f"[업무] {SUMMARY}"
        (log,) = _logs(db, tid, "CALENDAR_SYNCED")
        assert log.output_data == {"task_id": tid, "state": "synced", "created": True}
        assert log.run_id is None and log.user_id == uid
        # 다시 돌려도 아무 호출이 없다
        _sync(db, aid, google_fake)
        assert len(google_fake.calendar_calls) == 1


def test_no_deadline_no_event(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid, deadline=None)
        _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == []
        assert db.get(Task, tid).calendar_state == "none"
        # pending으로 남아 있어도 기한이 없으면 none으로 끝낸다
        tid2 = _task(db, uid, deadline=None, state="pending")
        r = _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == [] and r.skipped == 1
        assert db.get(Task, tid2).calendar_state == "none"


def test_other_users_and_non_mail_tasks_ignored(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        other = User(email="o@example.com", password_hash="x")
        db.add(other)
        db.commit()
        _task(db, other.id)
        _task(db, uid, origin="user")
        _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == []


def test_pending_updates_existing(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        task.deadline_at = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
        mark_calendar_dirty(task)
        assert task.calendar_state == "pending"
        db.commit()
        r = _sync(db, aid, google_fake)
        assert (r.created, r.updated) == (0, 1)
        eid = event_id_for(task, _salt(db, aid))
        assert _calls(google_fake, "patch") == [("patch", eid)]
        assert len(_calls(google_fake, "insert")) == 1
        assert google_fake.events[eid]["start"] == {"date": "2026-11-03"}
        assert db.get(Task, tid).calendar_state == "synced"
        logs = _logs(db, tid, "CALENDAR_SYNCED")
        assert [l.output_data["created"] for l in logs] == [True, False]


def test_patch_body_has_no_id(setup, google_fake, monkeypatch):
    uid, aid = setup
    seen = []
    orig = google_fake.calendar_patch
    monkeypatch.setattr(google_fake, "calendar_patch", lambda at, eid, body: (seen.append(body), orig(at, eid, body))[1])
    with SessionLocal() as db:
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        mark_calendar_dirty(task)
        db.commit()
        _sync(db, aid, google_fake)
        assert len(seen) == 1 and "id" not in seen[0] and "summary" in seen[0]
        # 종일 일정이면 시간 필드를 명시적으로 지운다 (Google PATCH는 중첩 객체를 합친다)
        assert seen[0]["start"] == {"date": "2026-10-30", "dateTime": None, "timeZone": None}
        assert seen[0]["end"] == {"date": "2026-10-31", "dateTime": None, "timeZone": None}


def test_user_deleted_event_not_recreated(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        eid = task.calendar_event_id
        google_fake.delete_event(eid)
        task.deadline_at = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
        mark_calendar_dirty(task)
        db.commit()
        r = _sync(db, aid, google_fake)
        assert r.deleted_by_user == 1
        task = db.get(Task, tid)
        assert task.calendar_state == "deleted_by_user"
        assert len(_calls(google_fake, "insert")) == 1
        assert len(_logs(db, tid, "CALENDAR_DELETED_BY_USER")) == 1
        # 이후 기한이 바뀌어도 pending으로 바뀌지 않고, 다시 만들지 않는다
        task.deadline_at = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
        mark_calendar_dirty(task)
        assert task.calendar_state == "deleted_by_user"
        db.commit()
        n = len(google_fake.calendar_calls)
        _sync(db, aid, google_fake)
        assert len(google_fake.calendar_calls) == n
        assert eid not in google_fake.events


def test_patch_returning_cancelled_is_deleted_by_user(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        google_fake.events[task.calendar_event_id]["status"] = "cancelled"
        mark_calendar_dirty(task)
        db.commit()
        _sync(db, aid, google_fake)
        assert db.get(Task, tid).calendar_state == "deleted_by_user"


def test_crash_after_insert_no_duplicate(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        task = db.get(Task, tid)
        eid = event_id_for(task, _salt(db, aid, MARK))
        google_fake.events[eid] = {"id": eid, "summary": "old",  # 지난 주기에 만들고 번호 저장 전에 죽었다
                                   "extendedProperties": {"private": {"followupTask": f"{MARK}:{tid}"}}}
        r = _sync(db, aid, google_fake)
        assert [c[0] for c in google_fake.calendar_calls] == ["insert", "get", "patch"]
        assert list(google_fake.events) == [eid]
        assert google_fake.events[eid]["summary"] == f"[업무] {SUMMARY}"
        task = db.get(Task, tid)
        assert task.calendar_state == "synced" and task.calendar_event_id == eid
        assert task.calendar_event_url.endswith(eid)
        assert r.updated == 1 and r.created == 0


def test_conflict_with_user_deleted_event(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        eid = event_id_for(db.get(Task, tid), _salt(db, aid, MARK))
        google_fake.deleted_events[eid] = {"id": eid,
                                           "extendedProperties": {"private": {"followupTask": f"{MARK}:{tid}"}}}
        _sync(db, aid, google_fake)
        assert [c[0] for c in google_fake.calendar_calls] == ["insert", "get"]
        task = db.get(Task, tid)
        assert task.calendar_state == "deleted_by_user" and task.calendar_event_id == eid


def test_cancel_marks_check(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        orchestrator.cancel_task(db, db.get(User, uid), task, "")
        assert db.get(Task, tid).calendar_state == "pending"
        _sync(db, aid, google_fake)
        ev = google_fake.events[event_id_for(task, _salt(db, aid))]
        assert ev["summary"].startswith("✓ ")
        assert db.get(Task, tid).calendar_state == "synced"


def test_cancel_after_user_deleted_untouched(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid, state="deleted_by_user", event_id="fuag0000000100000001")
        orchestrator.cancel_task(db, db.get(User, uid), db.get(Task, tid), "")
        assert db.get(Task, tid).calendar_state == "deleted_by_user"
        _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == []


def test_cancel_user_task_not_marked(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid, origin="user")
        orchestrator.cancel_task(db, db.get(User, uid), db.get(Task, tid), "")
        assert db.get(Task, tid).calendar_state == "none"


def test_transient_retries_then_error(setup, google_fake):
    uid, aid = setup
    google_fake.calendar_fail = GoogleTransientError("503")
    google_fake.calendar_fail_times = 5
    with SessionLocal() as db:
        tid = _task(db, uid)
        for i in range(1, 5):
            r = _sync(db, aid, google_fake)
            task = db.get(Task, tid)
            assert task.calendar_state == "pending" and task.calendar_attempts == i
            assert r.errors == 1
        assert _logs(db, tid, "CALENDAR_ERROR") == []
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        assert task.calendar_state == "error"
        assert task.calendar_error == "Google 캘린더 연결이 불안정해 일정을 반영하지 못했습니다."
        assert len(_logs(db, tid, "CALENDAR_ERROR")) == 1
        # error 상태는 자동으로 다시 시도하지 않는다
        _sync(db, aid, google_fake)
        assert len(google_fake.calendar_calls) == 5


def test_other_error_immediate(setup, google_fake):
    uid, aid = setup
    google_fake.calendar_fail = GoogleError("400")
    with SessionLocal() as db:
        tid = _task(db, uid)
        tid2 = _task(db, uid)
        r = _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        assert task.calendar_state == "error"
        assert task.calendar_error == "Google 캘린더에 일정을 반영하지 못했습니다."
        (log,) = _logs(db, tid, "CALENDAR_ERROR")
        assert SUMMARY not in str(log.output_data)
        # 한 업무의 오류가 다음 업무를 막지 않는다
        assert db.get(Task, tid2).calendar_state == "synced"
        assert (r.errors, r.created) == (1, 1)


def test_unexpected_error_continues(setup, google_fake, monkeypatch):
    uid, aid = setup
    from app.mail import calendar_sync

    real = calendar_sync.build_event
    def boom(task, **kw):
        if task.id == tid:
            raise KeyError("bad brief")
        return real(task, **kw)
    monkeypatch.setattr(calendar_sync, "build_event", boom)
    with SessionLocal() as db:
        tid = _task(db, uid)
        tid2 = _task(db, uid)
        r = _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        assert task.calendar_state == "error"
        assert task.calendar_error == "Google 캘린더에 일정을 반영하지 못했습니다."
        assert db.get(Task, tid2).calendar_state == "synced"
        assert r.errors == 1


def test_auth_error_stops_quietly(setup, google_fake):
    uid, aid = setup
    google_fake.valid_refresh_tokens.clear()
    with SessionLocal() as db:
        tid = _task(db, uid)
        r = _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == []
        task = db.get(Task, tid)
        assert task.calendar_state == "none" and task.calendar_attempts == 0
        assert (r.created, r.errors) == (0, 0)


def test_transient_token_refresh_stops_quietly(setup, google_fake, monkeypatch):
    uid, aid = setup

    def fail(_):
        raise GoogleTransientError("token endpoint 503")
    monkeypatch.setattr(google_fake, "refresh_access_token", fail)
    with SessionLocal() as db:
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == []
        task = db.get(Task, tid)
        assert task.calendar_state == "none" and task.calendar_attempts == 0


def test_auth_error_on_calendar_call_stops(setup, google_fake):
    from app.google.client import GoogleAuthError

    uid, aid = setup
    google_fake.calendar_fail = GoogleAuthError("401")
    with SessionLocal() as db:
        tid = _task(db, uid)
        tid2 = _task(db, uid)
        _sync(db, aid, google_fake)
        assert len(google_fake.calendar_calls) == 1
        assert db.get(Task, tid).calendar_state == "none"
        assert db.get(Task, tid2).calendar_state == "none"


def test_max_tasks(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        ids = [_task(db, uid) for _ in range(12)]
        r = _sync(db, aid, google_fake, max_tasks=10)
        assert r.created == 10 and len(_calls(google_fake, "insert")) == 10
        states = [db.get(Task, i).calendar_state for i in ids]
        assert states[:10] == ["synced"] * 10 and states[10:] == ["none", "none"]
        r = _sync(db, aid, google_fake, max_tasks=10)
        assert r.created == 2


def test_logs_have_no_content(setup, google_fake, caplog):
    uid, aid = setup
    caplog.set_level(logging.DEBUG)
    google_fake.calendar_fail = GoogleError("400")
    with SessionLocal() as db:
        tid = _task(db, uid)
        _task(db, uid)
        _sync(db, aid, google_fake)
        for t in ("CALENDAR_SYNCED", "CALENDAR_ERROR"):
            for log in db.scalars(select(AgentActionLog).where(AgentActionLog.action_type == t)).all():
                assert SUMMARY not in str(log.output_data) and NAME not in str(log.output_data)
        assert db.get(Task, tid).calendar_state == "error"
    text = caplog.text
    assert SUMMARY not in text and NAME not in text and "견적" not in text


def test_mark_calendar_dirty_rules():
    t = Task(user_id=1, title="x", goal="x", origin="mail", calendar_state="none", deadline_at=None)
    mark_calendar_dirty(t)
    assert t.calendar_state == "none"
    t.deadline_at = DEADLINE
    mark_calendar_dirty(t)
    assert t.calendar_state == "pending"
    t2 = Task(user_id=1, title="x", goal="x", origin="mail", calendar_state="synced", deadline_at=None,
              calendar_event_id="fuagabc")
    mark_calendar_dirty(t2)
    assert t2.calendar_state == "pending"
    t3 = Task(user_id=1, title="x", goal="x", origin="user", calendar_state="none", deadline_at=DEADLINE)
    mark_calendar_dirty(t3)
    assert t3.calendar_state == "none"
    t4 = Task(user_id=1, title="x", goal="x", origin="mail", calendar_state="error", deadline_at=DEADLINE)
    mark_calendar_dirty(t4)
    assert t4.calendar_state == "pending"


def test_deadline_removed_keeps_event(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        task.deadline_at = None
        mark_calendar_dirty(task)
        db.commit()
        n = len(google_fake.calendar_calls)
        _sync(db, aid, google_fake)
        assert len(google_fake.calendar_calls) == n
        assert db.get(Task, tid).calendar_state == "synced"
        assert event_id_for(task, _salt(db, aid)) in google_fake.events


def test_intake_marks_dirty():
    with SessionLocal() as db:
        u = User(email="i@example.com", password_hash="x")
        db.add(u)
        db.commit()
        _add(db, [_mail(u.id), _mail(u.id)])
        llm = ScriptedLLM(structured_steps=[
            _analysis(deadline=_deadline("이번 주 금요일", "2026-10-30", "", "explicit")), _analysis()])
        intake_work_mails(db, u.id, llm, max_mails=5, tz=TZ)
        db.expire_all()
        with_deadline, without = _mail_tasks(db, u.id)
        assert with_deadline.calendar_state == "pending"
        assert without.calendar_state == "none"


def test_closed_task_without_event_not_created(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        done = _task(db, uid, status=TaskStatus.COMPLETED)
        cancelled = _task(db, uid, status=TaskStatus.CANCELLED, state="pending")
        r = _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == []
        assert db.get(Task, done).calendar_state == "none"
        assert db.get(Task, cancelled).calendar_state == "none"
        assert r.skipped == 1 and r.created == 0  # 완료 업무는 대상에서 빠지고, pending 취소 업무만 건너뛴다
        assert _logs(db, cancelled, "CALENDAR_SYNCED") == []
        # 다음 주기에도 다시 고르지 않는다 (한도를 차지하지 않는다)
        r = _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == [] and r.skipped == 0


def test_cancelled_before_first_sync_no_event(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        orchestrator.cancel_task(db, db.get(User, uid), db.get(Task, tid), "")
        assert db.get(Task, tid).calendar_state == "pending"
        _sync(db, aid, google_fake)
        assert google_fake.calendar_calls == [] and google_fake.events == {}
        task = db.get(Task, tid)
        assert task.calendar_state == "none" and task.calendar_event_id is None


def test_remark_after_error_resets_attempts(setup, google_fake):
    uid, aid = setup
    google_fake.calendar_fail = GoogleTransientError("503")
    google_fake.calendar_fail_times = 5
    with SessionLocal() as db:
        tid = _task(db, uid)
        for _ in range(5):
            _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        assert task.calendar_state == "error" and task.calendar_attempts == 5
        mark_calendar_dirty(task)
        assert task.calendar_state == "pending" and task.calendar_attempts == 0
        db.commit()
        google_fake.calendar_fail = GoogleTransientError("503")
        google_fake.calendar_fail_times = 1
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        assert task.calendar_state == "pending" and task.calendar_attempts == 1


def test_salt_generated_once_and_reused(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        assert _salt(db, aid) == ""
        t1 = _task(db, uid)
        _sync(db, aid, google_fake)
        salt = _salt(db, aid)
        assert re.fullmatch(r"[0-9a-v]{8}", salt)
        t2 = _task(db, uid)
        _sync(db, aid, google_fake)
        assert _salt(db, aid) == salt
        for tid in (t1, t2):
            eid = db.get(Task, tid).calendar_event_id
            assert eid == "fu" + salt + f"{tid:08x}"
            assert re.fullmatch(r"^[a-v0-9]{5,1024}$", eid)
            assert google_fake.events[eid]["extendedProperties"] == {
                "private": {"followupTask": f"{salt}:{tid}"}}


def test_preset_salt_kept(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        _salt(db, aid, MARK)
        tid = _task(db, uid)
        _sync(db, aid, google_fake)
        assert _salt(db, aid) == MARK
        assert db.get(Task, tid).calendar_event_id == f"fu{MARK}{tid:08x}"


def test_no_targets_no_salt(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        _sync(db, aid, google_fake)
        assert _salt(db, aid) == ""


@pytest.mark.parametrize("marker", [None, "othersalt:1", f"{MARK}:999"])
def test_conflict_with_foreign_event_is_error(setup, google_fake, marker):
    """다른 DB · 다른 인스턴스가 만든 같은 번호의 일정은 고치지도, 사용자 삭제로 보지도 않는다."""
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        eid = event_id_for(db.get(Task, tid), _salt(db, aid, MARK))
        foreign = {"id": eid, "summary": "남의 일정"}
        if marker is not None:
            foreign["extendedProperties"] = {"private": {"followupTask": marker.replace("999", str(tid + 1))}}
        google_fake.events[eid] = dict(foreign)
        r = _sync(db, aid, google_fake)
        assert [c[0] for c in google_fake.calendar_calls] == ["insert", "get"]
        assert google_fake.events[eid] == foreign
        task = db.get(Task, tid)
        assert task.calendar_state == "error"
        assert task.calendar_error == "같은 번호의 다른 캘린더 일정이 있어 반영하지 못했습니다."
        assert task.calendar_event_id is None and task.calendar_event_url == ""
        assert r.errors == 1 and r.deleted_by_user == 0
        assert len(_logs(db, tid, "CALENDAR_ERROR")) == 1


def test_conflict_with_foreign_cancelled_event_is_error(setup, google_fake):
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid)
        eid = event_id_for(db.get(Task, tid), _salt(db, aid, MARK))
        google_fake.deleted_events[eid] = {"id": eid}  # 표식 없는 지워진 일정
        r = _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        assert task.calendar_state == "error" and task.calendar_event_id is None
        assert r.deleted_by_user == 0 and r.errors == 1


TIMED = datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc)  # 2026-10-30 14:00 KST


@pytest.mark.parametrize("first_all_day", [True, False])
def test_switch_between_all_day_and_timed(setup, google_fake, first_all_day):
    """종일 ↔ 시간 일정 전환. Google PATCH는 start/end를 합치므로 반대쪽 필드를 None으로 지워야 한다."""
    uid, aid = setup
    with SessionLocal() as db:
        tid = _task(db, uid, deadline=TIMED, all_day=first_all_day)
        _sync(db, aid, google_fake)
        task = db.get(Task, tid)
        eid = task.calendar_event_id
        task.deadline_all_day = not first_all_day
        mark_calendar_dirty(task)
        db.commit()
        r = _sync(db, aid, google_fake)
        assert (r.updated, r.errors) == (1, 0)
        assert db.get(Task, tid).calendar_state == "synced"
        ev = google_fake.events[eid]
        if first_all_day:
            assert ev["start"] == {"dateTime": "2026-10-30T14:00:00+09:00", "timeZone": TZ}
            assert ev["end"] == {"dateTime": "2026-10-30T14:30:00+09:00", "timeZone": TZ}
        else:
            assert ev["start"] == {"date": "2026-10-30"} and ev["end"] == {"date": "2026-10-31"}
