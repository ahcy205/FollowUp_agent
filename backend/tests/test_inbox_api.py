"""5단계 Task 5: 받은 업무 · 알림 API와 상세 응답 확장."""
from datetime import timedelta

from app.api.tasks import task_fingerprint
from app.db import SessionLocal
from app.mail import inbox, merge, task_actions
from app.models import Email, EmailDirection, Task, TaskStatus
from tests.conftest import make_client, register
from tests.test_task_actions import NOW, TZ, _kst, _mail, _task, _user
from tests.test_merge import _pair


def _fix_now(monkeypatch, at=NOW):
    monkeypatch.setattr(inbox, "now_utc", lambda: at)


def _ids(cards):
    return [c["task_id"] for c in cards]


def test_inbox_groups_and_order(client, monkeypatch):
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        late = _task(db, me["id"], deadline_at=_kst(2026, 10, 25, 12), priority="LOW").id          # 지남 → now
        tomorrow_high = _task(db, me["id"], deadline_at=_kst(2026, 10, 27), deadline_all_day=True,
                              priority="HIGH").id                                                   # now, HIGH 먼저
        high_3d = _task(db, me["id"], deadline_at=_kst(2026, 10, 29), deadline_all_day=True, priority="HIGH").id  # now
        week = _task(db, me["id"], deadline_at=_kst(2026, 11, 1), deadline_all_day=True).id         # week
        later = _task(db, me["id"], deadline_at=_kst(2026, 12, 1), deadline_all_day=True).id        # later
        waiting = _task(db, me["id"], deadline_at=_kst(2026, 11, 1), deadline_all_day=True,
                        brief={"analysis": "pending"}).id                                           # later 맨 위
        sent = _task(db, me["id"], status=TaskStatus.WAITING).id
        ref = _task(db, me["id"], status=TaskStatus.PENDING, brief={"analysis": "done", "summary": "참고"}).id
        db.add(Email(user_id=me["id"], sender="kim@partner.co.kr", receiver="me@x.com", subject="s", content="c",
                     direction=EmailDirection.INBOUND, task_id=late, received_at=NOW - timedelta(hours=1)))
        db.commit()
    body = client.get("/api/inbox").json()
    todo = body["tabs"]["todo"]
    assert _ids(todo["now"]) == [tomorrow_high, high_3d, late]
    assert _ids(todo["week"]) == [week]
    assert _ids(todo["later"]) == [waiting, later]
    assert _ids(body["tabs"]["sent"]) == [sent] and _ids(body["tabs"]["reference"]) == [ref]
    assert body["counts"] == {"now": 3, "week": 1, "later": 2, "todo": 6, "sent": 1, "reference": 1, "done": 0}
    card = todo["now"][0]
    assert set(card) == {"task_id", "title", "summary", "sender_name", "sender_org", "priority", "deadline_badge",
                         "todo_done", "todo_total", "nudge_days", "returned", "has_merge_candidates", "analysis",
                         "received_at", "no_reply", "changed_unseen"}
    assert card["deadline_badge"]["text"] == "D-1 내일" and card["todo_total"] == 2 and card["todo_done"] == 0
    assert card["changed_unseen"] is False
    assert body["triage_enabled"] is False


def test_inbox_excludes(client, monkeypatch):
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        other = _user(db, "other@example.com")
        mine = _task(db, me["id"]).id
        agent = _task(db, me["id"], origin="user").id
        target = _task(db, me["id"]).id
        merged = _task(db, me["id"], merged_into_id=target).id
        theirs = _task(db, other.id).id
        snoozed = _task(db, me["id"], snoozed_until=NOW + timedelta(days=2)).id
    body = client.get("/api/inbox").json()
    shown = _ids(body["tabs"]["todo"]["now"] + body["tabs"]["todo"]["week"] + body["tabs"]["todo"]["later"])
    assert sorted(shown) == sorted([mine, target])
    assert not {agent, merged, theirs, snoozed} & set(shown)
    assert _ids(body["snoozed"]) == [snoozed] and body["snoozed"][0]["wake_at"]
    assert body["counts"]["todo"] == 2


def test_done_tab_window(client, monkeypatch):
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        recent = _task(db, me["id"], status=TaskStatus.COMPLETED, completed_at=NOW - timedelta(days=2)).id
        old = _task(db, me["id"], status=TaskStatus.COMPLETED, completed_at=NOW - timedelta(days=40)).id
        cancelled = _task(db, me["id"], status=TaskStatus.CANCELLED, completed_at=NOW - timedelta(days=1)).id
    body = client.get("/api/inbox").json()
    assert _ids(body["tabs"]["done"]) == [cancelled, recent]
    assert old not in _ids(body["tabs"]["done"]) and body["counts"]["done"] == 2


def test_not_work_list(client, monkeypatch):
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        task = _task(db, me["id"])
        fresh = _mail(me["id"], thread="t1", triage="not_work")
        filtered = _mail(me["id"], thread="t2", triage="filtered")
        filtered.received_at = NOW - timedelta(hours=5)
        old = _mail(me["id"], thread="t3", triage="not_work")
        old.received_at = NOW - timedelta(days=31)
        work = _mail(me["id"], thread="t4", triage="work")
        linked = _mail(me["id"], thread="t5", triage="not_work", task_id=task.id)
        long = _mail(me["id"], thread="t6", triage="not_work")
        long.subject = "가" * 300
        long.received_at = NOW - timedelta(hours=9)
        db.add_all([fresh, filtered, old, work, linked, long])
        db.commit()
        ids = (fresh.id, filtered.id, long.id)
    rows = client.get("/api/inbox").json()["not_work"]
    assert [r["email_id"] for r in rows] == list(ids)
    assert rows[0]["sender"] == "kim@partner.co.kr" and rows[1]["triage"] == "filtered"
    assert len(rows[2]["subject"]) == 200


def test_alerts(client, monkeypatch):
    me = register(client)
    with SessionLocal() as db:
        t = _task(db, me["id"], deadline_at=_kst(2026, 10, 27), deadline_all_day=True,
                  brief={"analysis": "done", "summary": "견적 요청", "needs_reply": False}).id
        _task(db, me["id"], deadline_at=_kst(2026, 12, 27), deadline_all_day=True)
    _fix_now(monkeypatch, _kst(2026, 10, 26, 9, 30))
    first = client.get("/api/inbox/alerts").json()
    assert [a["key"] for a in first["alerts"]] == [f"deadline-d1:{t}:2026-10-27"]
    assert first["alerts"][0]["kind"] == "deadline-d1" and first["now_count"] == 1
    assert client.get("/api/inbox/alerts").json() == first
    _fix_now(monkeypatch, _kst(2026, 10, 26, 8, 30))
    assert client.get("/api/inbox/alerts").json()["alerts"] == []
    with make_client() as anon:
        assert anon.get("/api/inbox/alerts").status_code == 401 and anon.get("/api/inbox").status_code == 401


def test_detail_mail_extras(client, monkeypatch):
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        agent = _task(db, me["id"], origin="user").id
        older = _task(db, me["id"], created_at=NOW - timedelta(days=3), snoozed_until=NOW + timedelta(days=1))
        newer = _task(db, me["id"], created_at=NOW - timedelta(days=1))
        db.add_all([_mail(me["id"], thread="x", task_id=older.id), _mail(me["id"], thread="y", task_id=newer.id)])
        db.commit()
        older_id, newer_id = older.id, newer.id
    assert client.get(f"/api/tasks/{agent}").status_code == 404  # 메일이 아닌 업무는 상세가 없다
    m = client.get(f"/api/tasks/{older_id}").json()["mail"]
    assert m["is_snoozed"] is True and m["wake_at"] and m["merge_candidates"] == []
    m = client.get(f"/api/tasks/{newer_id}").json()["mail"]
    assert m["merge_candidates"] == [{"task_id": older_id, "title": "견적 요청", "evidence": None}]
    assert m["undoable_merges"] == [] and m["deadline_badge"]["text"] == "기한 없음"
    assert m["priority_source"] is not None and m["merged_into_id"] is None


def test_detail_undoable_after_merge(client, monkeypatch):
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        from app.models import User
        a, b, _ = _pair(db, me["id"])
        merge.merge(db, db.get(User, me["id"]), b, a, NOW)
        db.commit()
        a_id, b_id = a.id, b.id
    m = client.get(f"/api/tasks/{a_id}").json()["mail"]
    assert m["undoable_merges"] == [{"source_id": b_id, "title": "견적 요청"}]
    assert client.get(f"/api/tasks/{b_id}").json()["mail"]["merged_into_id"] == a_id


def test_fingerprint_changes_on_snooze_merge_and_mail():
    with SessionLocal() as db:
        u = _user(db)
        t = _task(db, u.id)
        base = task_fingerprint(db, t)
        task_actions.snooze(t, preset="tomorrow", until=None, now=NOW, tz=TZ)
        db.commit()
        snoozed = task_fingerprint(db, t)
        assert snoozed != base
        t.merge_undo = {"9": {"source_id": 9, "at": NOW.isoformat()}}
        db.commit()
        undo = task_fingerprint(db, t)
        assert undo != snoozed
        db.add(_mail(u.id, thread="z", task_id=t.id))
        db.commit()
        assert task_fingerprint(db, t) != undo


def test_action_wakes_calendar_sync_only_when_pending(client, monkeypatch):
    """기한을 바꿔 캘린더 반영이 필요해지면 90초를 기다리지 않고 동기화를 바로 깨운다."""
    _fix_now(monkeypatch)
    woke = []
    monkeypatch.setattr("app.mail.sync.wake", lambda: woke.append(1))
    me = register(client)
    with SessionLocal() as db:
        tid = _task(db, me["id"]).id

    r = client.post(f"/api/tasks/{tid}/snooze", json={"preset": "tomorrow"})
    assert r.status_code == 200, r.text
    assert woke == []  # 캘린더와 상관없는 동작은 깨우지 않는다

    r = client.post(f"/api/tasks/{tid}/deadline", json={"date": "2026-10-30"})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["calendar_state"] == "pending"
    assert woke == [1]


def test_detail_merged_sources(client, monkeypatch):
    """합친 대상(A) 상세에 합쳐진 업무(B)의 요약을 따로 보여준다. 10분이 지나도 남고, 되돌리면 사라진다."""
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        from app.models import User
        a, b, _ = _pair(db, me["id"])
        assert client.get(f"/api/tasks/{a.id}").json()["mail"]["merged_sources"] == []
        merge.merge(db, db.get(User, me["id"]), b, a, NOW)
        db.commit()
        a_id, b_id = a.id, b.id
    m = client.get(f"/api/tasks/{a_id}").json()["mail"]
    assert [(s["task_id"], s["title"], s["summary"]) for s in m["merged_sources"]] == [(b_id, "견적 요청", "추가 요청")]
    assert m["merged_sources"][0]["merged_at"]
    assert client.get(f"/api/tasks/{b_id}").json()["mail"]["merged_sources"] == []

    _fix_now(monkeypatch, NOW + timedelta(hours=1))  # 되돌리기 시간이 지나도 칸은 남는다
    assert len(client.get(f"/api/tasks/{a_id}").json()["mail"]["merged_sources"]) == 1

    with SessionLocal() as db:
        from app.models import User
        merge.undo_merge(db, db.get(User, me["id"]), db.get(Task, a_id), b_id, NOW + timedelta(minutes=5))
        db.commit()
    assert client.get(f"/api/tasks/{a_id}").json()["mail"]["merged_sources"] == []


def test_card_changed_unseen(client, monkeypatch):
    """상대 재답장으로 바뀐 내용을 아직 보지 않은 업무 카드에 표시한다(설계 6.4)."""
    _fix_now(monkeypatch)
    me = register(client)
    with SessionLocal() as db:
        tid = _task(db, me["id"]).id
        t = db.get(Task, tid)
        t.changed_unseen = True
        db.commit()
    cards = [c for b in client.get("/api/inbox").json()["tabs"]["todo"].values() for c in b]
    assert [c["changed_unseen"] for c in cards if c["task_id"] == tid] == [True]
