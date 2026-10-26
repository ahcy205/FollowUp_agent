"""5단계 Task 2: 받은 업무 판단 (탭 · 묶음 · 정렬 · 기한 배지 · 스누즈 · 넛지 · 알림 · 합치기 후보)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db import SessionLocal
from app.mail import inbox
from app.mail.inbox import (
    BUCKET_LATER,
    BUCKET_NOW,
    BUCKET_WEEK,
    TAB_DONE,
    TAB_REFERENCE,
    TAB_SENT,
    TAB_TODO,
    alerts_for,
    bucket_of,
    deadline_badge,
    is_snoozed,
    latest_inbound,
    merge_candidates,
    nudge_days,
    returned_at,
    snooze_wake_at,
    sort_key,
    tab_of,
)
from app.models import Email, EmailDirection, Task, TaskStatus, User

TZ = "Asia/Seoul"
KST = ZoneInfo(TZ)
NOW = datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)  # 한국 시간 10/26(월) 10:00
SUMMARY = "견적서 회신 요청"


def kst(month, day, hour=0, minute=0, year=2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST).astimezone(timezone.utc)


def _t(*, status=TaskStatus.ACTION_REQUIRED, analysis="done", priority="NORMAL", deadline=None, all_day=True,
       kind=None, origin="mail", merged_into_id=None, needs_reply=False, snoozed_until=None,
       snooze_returned_at=None, task_id=7, summary=SUMMARY, title="원래 제목") -> Task:
    brief = {"analysis": analysis, "needs_reply": needs_reply}
    if summary is not None:
        brief["summary"] = summary
    return Task(id=task_id, user_id=1, title=title, goal="g", status=status, origin=origin, priority=priority,
                brief=brief, deadline_at=deadline, deadline_all_day=all_day,
                deadline_kind=kind or ("explicit" if deadline else "none"), merged_into_id=merged_into_id,
                snoozed_until=snoozed_until, snooze_returned_at=snooze_returned_at, plan=[])


def test_tabs():
    for s in (TaskStatus.ACTION_REQUIRED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED):
        assert tab_of(_t(status=s)) == TAB_TODO
    assert tab_of(_t(status=TaskStatus.PENDING, analysis="pending")) == TAB_TODO
    assert tab_of(_t(status=TaskStatus.PENDING, analysis="failed")) == TAB_TODO
    assert tab_of(_t(status=TaskStatus.PENDING, analysis="done")) == TAB_REFERENCE
    assert tab_of(_t(status=TaskStatus.WAITING)) == TAB_SENT
    assert tab_of(_t(status=TaskStatus.COMPLETED)) == TAB_DONE
    assert tab_of(_t(status=TaskStatus.CANCELLED)) == TAB_DONE
    assert tab_of(_t(merged_into_id=3)) is None
    assert tab_of(_t(origin="agent")) is None
    assert tab_of(_t(origin="user")) is None


def test_bucket_now_rules():
    assert bucket_of(_t(deadline=kst(10, 25)), NOW, TZ) == BUCKET_NOW  # 지난 기한
    assert bucket_of(_t(deadline=kst(10, 26)), NOW, TZ) == BUCKET_NOW  # 오늘
    assert bucket_of(_t(deadline=kst(10, 27)), NOW, TZ) == BUCKET_NOW  # 내일
    assert bucket_of(_t(deadline=kst(10, 29), priority="HIGH"), NOW, TZ) == BUCKET_NOW
    assert bucket_of(_t(deadline=kst(10, 29), priority="NORMAL"), NOW, TZ) == BUCKET_WEEK
    # 오늘 09:00에 끝난 시간 지정 기한도 지난 기한이다
    assert bucket_of(_t(deadline=kst(10, 26, 9), all_day=False), NOW, TZ) == BUCKET_NOW


def test_bucket_week_and_later():
    assert bucket_of(_t(deadline=kst(11, 2)), NOW, TZ) == BUCKET_WEEK
    assert bucket_of(_t(deadline=kst(11, 3)), NOW, TZ) == BUCKET_LATER
    assert bucket_of(_t(priority="HIGH"), NOW, TZ) == BUCKET_WEEK
    assert bucket_of(_t(priority="LOW"), NOW, TZ) == BUCKET_LATER
    assert bucket_of(_t(priority="NORMAL"), NOW, TZ) == BUCKET_LATER
    pending = _t(status=TaskStatus.PENDING, analysis="pending", deadline=kst(10, 26), priority="HIGH")
    assert bucket_of(pending, NOW, TZ) == BUCKET_LATER
    failed = _t(status=TaskStatus.PENDING, analysis="failed")
    assert bucket_of(failed, NOW, TZ) == BUCKET_LATER


def test_bucket_tz_boundary():
    now = datetime(2026, 10, 26, 15, 30, tzinfo=timezone.utc)  # 한국 10/27 00:30
    t = _t(deadline=kst(10, 28))
    assert bucket_of(t, now, TZ) == BUCKET_NOW
    assert deadline_badge(t, now, TZ)["text"] == "D-1 내일"
    # 같은 순간 UTC 날짜(10/26)로 셌다면 D-2였을 것
    assert deadline_badge(_t(deadline=kst(10, 27)), now, TZ)["text"] == "오늘"
    # SQLite가 돌려주는 naive 값은 UTC로 본다
    naive = _t(deadline=kst(10, 28).replace(tzinfo=None))
    assert deadline_badge(naive, now, TZ)["text"] == "D-1 내일"


def test_sort_order():
    r = kst(10, 20)
    items = [
        ("low", _t(priority="LOW", deadline=kst(10, 27)), r),
        ("normal-none", _t(priority="NORMAL"), r),
        ("normal-late-recv", _t(priority="NORMAL", deadline=kst(10, 30)), kst(10, 22)),
        ("normal-early-recv", _t(priority="NORMAL", deadline=kst(10, 30)), kst(10, 21)),
        ("normal-early", _t(priority="NORMAL", deadline=kst(10, 28)), r),
        ("high", _t(priority="HIGH", deadline=kst(11, 20)), r),
        ("normal-no-recv", _t(priority="NORMAL", deadline=kst(10, 30)), None),
        ("pending", _t(status=TaskStatus.PENDING, analysis="pending", priority="LOW"), r),
    ]
    ordered = [name for name, t, rec in sorted(items, key=lambda x: sort_key(x[1], x[2]))]
    assert ordered == ["pending", "high", "normal-early", "normal-early-recv", "normal-late-recv",
                       "normal-no-recv", "normal-none", "low"]
    # 모르는 중요도는 보통으로 본다
    assert sort_key(_t(priority="WEIRD"), r) == sort_key(_t(priority="NORMAL"), r)


def test_badges():
    assert deadline_badge(_t(deadline=kst(10, 25)), NOW, TZ) == {"text": "D+1 지남", "late": True,
                                                                "estimated": False}
    assert deadline_badge(_t(deadline=kst(10, 22)), NOW, TZ)["text"] == "D+4 지남"
    assert deadline_badge(_t(deadline=kst(10, 26)), NOW, TZ) == {"text": "오늘", "late": False,
                                                                "estimated": False}
    # 오늘 09:00에 끝난 시간 지정 기한은 "오늘 지남"
    timed = deadline_badge(_t(deadline=kst(10, 26, 9), all_day=False), NOW, TZ)
    assert timed == {"text": "오늘 지남", "late": True, "estimated": False}
    # 오늘 18:00 시간 지정 기한은 아직 오늘
    assert deadline_badge(_t(deadline=kst(10, 26, 18), all_day=False), NOW, TZ)["text"] == "오늘"
    assert deadline_badge(_t(deadline=kst(10, 27)), NOW, TZ)["text"] == "D-1 내일"
    assert deadline_badge(_t(deadline=kst(10, 31)), NOW, TZ)["text"] == "D-5"
    assert deadline_badge(_t(), NOW, TZ) == {"text": "기한 없음", "late": False, "estimated": False}
    est = deadline_badge(_t(deadline=kst(10, 31), kind="estimated"), NOW, TZ)
    assert est == {"text": "D-5", "late": False, "estimated": True}


def test_snooze_wake_safety():
    t = _t(snoozed_until=kst(11, 2, 9), deadline=kst(10, 27))
    assert snooze_wake_at(t, TZ) == kst(10, 26, 9)
    assert not is_snoozed(t, NOW, TZ)  # 기한 전날 09:00이 지났으므로 이미 해제
    assert returned_at(t, NOW, TZ) == kst(10, 26, 9)
    # 스누즈가 기한 전날보다 이르면 정한 시각 그대로
    t2 = _t(snoozed_until=kst(10, 27, 9), deadline=kst(11, 10))
    assert snooze_wake_at(t2, TZ) == kst(10, 27, 9)
    # 기한 없음
    assert snooze_wake_at(_t(snoozed_until=kst(11, 2, 9)), TZ) == kst(11, 2, 9)
    assert snooze_wake_at(_t(deadline=kst(10, 27)), TZ) is None
    # naive 값도 UTC로 본다
    t3 = _t(snoozed_until=kst(11, 2, 9).replace(tzinfo=None), deadline=kst(10, 27).replace(tzinfo=None))
    assert snooze_wake_at(t3, TZ) == kst(10, 26, 9)


def test_snooze_active_and_returned():
    t = _t(snoozed_until=kst(10, 27, 9))
    assert is_snoozed(t, NOW, TZ)
    assert returned_at(t, NOW, TZ) is None
    after = kst(10, 27, 10)
    assert not is_snoozed(t, after, TZ)
    assert returned_at(t, after, TZ) == kst(10, 27, 9)
    assert returned_at(t, kst(10, 28, 9) - timedelta(seconds=1), TZ) == kst(10, 27, 9)
    assert returned_at(t, kst(10, 28, 9), TZ) is None
    # "지금 보기" · 새 메일로 해제된 경우
    r = _t(snooze_returned_at=NOW - timedelta(hours=3))
    assert not is_snoozed(r, NOW, TZ)
    assert returned_at(r, NOW, TZ) == NOW - timedelta(hours=3)
    assert returned_at(r, NOW + timedelta(hours=21), TZ) is None
    assert returned_at(_t(), NOW, TZ) is None


def test_nudge():
    t = _t(needs_reply=True)
    assert nudge_days(t, NOW - timedelta(hours=47), NOW, TZ) is None
    assert nudge_days(t, NOW - timedelta(hours=49), NOW, TZ) == 2
    assert nudge_days(t, NOW - timedelta(hours=48), NOW, TZ) == 2
    assert nudge_days(t, NOW - timedelta(days=5, hours=3), NOW, TZ) == 5
    assert nudge_days(t, (NOW - timedelta(hours=49)).replace(tzinfo=None), NOW, TZ) == 2
    assert nudge_days(t, None, NOW, TZ) is None
    assert nudge_days(_t(needs_reply=False), NOW - timedelta(days=3), NOW, TZ) is None
    snoozed = _t(needs_reply=True, snoozed_until=kst(10, 30, 9))
    assert nudge_days(snoozed, NOW - timedelta(days=3), NOW, TZ) is None
    reference = _t(status=TaskStatus.PENDING, analysis="done", needs_reply=True)
    assert nudge_days(reference, NOW - timedelta(days=3), NOW, TZ) is None
    pending = _t(status=TaskStatus.PENDING, analysis="pending", needs_reply=True)
    assert nudge_days(pending, NOW - timedelta(days=3), NOW, TZ) is None
    closed = _t(status=TaskStatus.COMPLETED, needs_reply=True)
    assert nudge_days(closed, NOW - timedelta(days=3), NOW, TZ) is None


def _keys(alerts):
    return [a["key"] for a in alerts]


def test_alerts_keys():
    tomorrow = _t(deadline=kst(10, 27))
    a = alerts_for(tomorrow, None, None, NOW, TZ)
    assert a == [{"key": "deadline-d1:7:2026-10-27", "kind": "deadline-d1", "task_id": 7,
                  "text": f"[D-1] {SUMMARY}"}]
    # 09:00 이전에는 알리지 않는다
    assert alerts_for(tomorrow, None, None, kst(10, 26, 8, 59), TZ) == []
    # 당일 09:00 이후
    today = alerts_for(_t(deadline=kst(10, 26)), None, None, NOW, TZ)
    assert _keys(today) == ["deadline-d0:7:2026-10-26"]
    assert today[0]["text"] == f"[오늘 마감] {SUMMARY}"
    assert alerts_for(_t(deadline=kst(10, 26)), None, None, kst(10, 26, 8), TZ) == []
    # 오늘 09:00에 이미 지난 시간 지정 기한: 키는 같고 문구만 "마감 지남"
    passed = alerts_for(_t(deadline=kst(10, 26, 9), all_day=False), None, None, NOW, TZ)
    assert passed == [{"key": "deadline-d0:7:2026-10-26", "kind": "deadline-d0", "task_id": 7,
                       "text": f"[마감 지남] {SUMMARY}"}]
    # 오늘 18:00 시간 지정 기한은 아직 "오늘 마감"
    assert alerts_for(_t(deadline=kst(10, 26, 18), all_day=False), None, None, NOW, TZ)[0]["text"] == (
        f"[오늘 마감] {SUMMARY}")
    # 기한이 바뀌면 키도 바뀐다
    assert _keys(alerts_for(_t(deadline=kst(10, 27, 15), all_day=False), None, None, NOW, TZ)) == [
        "deadline-d1:7:2026-10-27"]
    assert alerts_for(_t(deadline=kst(10, 28)), None, None, NOW, TZ) == []
    # 넛지 키에는 최근 상대 메일 번호
    nudge = alerts_for(_t(needs_reply=True), 55, NOW - timedelta(days=3), NOW, TZ)
    assert nudge == [{"key": "nudge:7:55", "kind": "nudge", "task_id": 7, "text": f"[답장 3일째 없음] {SUMMARY}"}]
    # 요약이 없으면 제목
    no_summary = alerts_for(_t(deadline=kst(10, 27), summary=None), None, None, NOW, TZ)
    assert no_summary[0]["text"] == "[D-1] 원래 제목"
    # 스누즈 해제 알림 (안전장치로 해제된 경우에도 기한 알림과 함께)
    woke = alerts_for(_t(snoozed_until=kst(11, 2, 9), deadline=kst(10, 27)), None, None, NOW, TZ)
    assert _keys(woke) == ["deadline-d1:7:2026-10-27", "snooze:7:2026-10-26T00:00:00+00:00"]
    assert woke[1]["text"] == f"[다시 표시] {SUMMARY}"
    assert woke[1]["kind"] == "snooze"
    # 스누즈 중이어도 기한 알림은 울린다 (기한 전날 09:00에 이미 해제되므로 실제로는 겹치지 않음)
    assert _keys(alerts_for(_t(snoozed_until=kst(10, 26, 12), deadline=kst(10, 26)), None, None, NOW, TZ)) == [
        "deadline-d0:7:2026-10-26"]
    # 처리할 일 탭이 아니면 없다
    assert alerts_for(_t(status=TaskStatus.COMPLETED, deadline=kst(10, 27), needs_reply=True), 55,
                      NOW - timedelta(days=3), NOW, TZ) == []
    assert alerts_for(_t(status=TaskStatus.WAITING, deadline=kst(10, 27)), None, None, NOW, TZ) == []
    assert alerts_for(_t(merged_into_id=2, deadline=kst(10, 27)), None, None, NOW, TZ) == []


# ---------- DB ----------

def _user(db, email):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.flush()
    return u.id


def _mail_task(db, uid, sender, *, created, status=TaskStatus.ACTION_REQUIRED, merged_into_id=None,
               origin="mail", dismissed=False, link_brief=True) -> Task:
    t = Task(user_id=uid, title="t", goal="g", status=status, origin=origin, brief={"analysis": "done"},
             created_at=created, merged_into_id=merged_into_id, merge_dismissed=dismissed, plan=[])
    db.add(t)
    db.flush()
    e = Email(user_id=uid, task_id=t.id, sender=sender, receiver="me@example.com", subject="s", content="c",
              direction=EmailDirection.INBOUND, received_at=created, created_at=created)
    db.add(e)
    db.flush()
    if link_brief:
        t.brief = {"analysis": "done", "email_id": e.id}
    db.commit()
    return t


def test_merge_candidates():
    with SessionLocal() as db:
        uid = _user(db, "a@example.com")
        other = _user(db, "b@example.com")
        addr = "Kim@Partner.com"
        c1 = _mail_task(db, uid, "kim@partner.com", created=NOW - timedelta(days=1))
        c2 = _mail_task(db, uid, " KIM@partner.com ", created=NOW - timedelta(days=3), status=TaskStatus.WAITING)
        c3 = _mail_task(db, uid, addr, created=NOW - timedelta(days=5), status=TaskStatus.BLOCKED)
        c4 = _mail_task(db, uid, addr, created=NOW - timedelta(days=6), status=TaskStatus.IN_PROGRESS)
        _mail_task(db, uid, addr, created=NOW - timedelta(days=15))  # 14일 넘음
        _mail_task(db, uid, addr, created=NOW - timedelta(days=2), status=TaskStatus.COMPLETED)
        _mail_task(db, uid, addr, created=NOW - timedelta(days=2), status=TaskStatus.PENDING)
        _mail_task(db, uid, addr, created=NOW - timedelta(days=2), merged_into_id=c1.id)
        _mail_task(db, uid, addr, created=NOW - timedelta(days=2), origin="agent")
        _mail_task(db, uid, "lee@partner.com", created=NOW - timedelta(days=2))
        _mail_task(db, other, addr, created=NOW - timedelta(days=2))
        # brief에 email_id가 없어도 가장 이른 상대 메일로 보낸 사람을 찾는다
        c5 = _mail_task(db, uid, addr, created=NOW - timedelta(days=4), link_brief=False)
        new = _mail_task(db, uid, "kim@PARTNER.com", created=NOW - timedelta(minutes=5),
                         status=TaskStatus.PENDING)
        _mail_task(db, uid, addr, created=NOW - timedelta(minutes=1))  # 이 업무보다 나중

        got = merge_candidates(db, new, NOW)
        assert [t.id for t in got] == [c1.id, c2.id, c5.id]
        assert c3.id and c4.id

        # 후보 자신의 후보: 자기는 빠지고 더 이른 것만
        assert [t.id for t in merge_candidates(db, c2, NOW)] == [c5.id, c3.id, c4.id]

        new.merge_dismissed = True
        db.commit()
        assert merge_candidates(db, new, NOW) == []
        new.merge_dismissed = False
        new.status = TaskStatus.CANCELLED
        db.commit()
        assert merge_candidates(db, new, NOW) == []
        new.status = TaskStatus.ACTION_REQUIRED
        new.merged_into_id = c1.id
        db.commit()
        assert merge_candidates(db, new, NOW) == []


def test_latest_inbound():
    with SessionLocal() as db:
        uid = _user(db, "a@example.com")
        t = _mail_task(db, uid, "kim@partner.com", created=NOW - timedelta(days=5))
        assert latest_inbound(db, Task(id=999, user_id=uid)) == (None, None)
        newer = Email(user_id=uid, task_id=t.id, sender="kim@partner.com", receiver="me", subject="s",
                      content="c", direction=EmailDirection.INBOUND, received_at=None,
                      created_at=NOW - timedelta(days=1))
        out = Email(user_id=uid, task_id=t.id, sender="me", receiver="kim", subject="s", content="c",
                    direction=EmailDirection.OUTBOUND, received_at=NOW, created_at=NOW)
        db.add_all([newer, out])
        db.commit()
        eid, at = latest_inbound(db, t)
        assert eid == newer.id
        assert at.replace(tzinfo=timezone.utc) == NOW - timedelta(days=1)


def test_module_constants():
    assert (inbox.TAB_TODO, inbox.TAB_SENT, inbox.TAB_REFERENCE, inbox.TAB_DONE) == (
        "todo", "sent", "reference", "done")
    assert (inbox.BUCKET_NOW, inbox.BUCKET_WEEK, inbox.BUCKET_LATER) == ("now", "week", "later")
