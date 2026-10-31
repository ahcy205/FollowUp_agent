"""새 화면 6절 ①: 최근 업무 활동 API (읽기 전용)."""
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import AgentActionLog, Task
from tests.conftest import make_client, register

BASE = datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc)
SECRET_IN = "입력-비밀-본문"
SECRET_OUT = "출력-비밀-본문"


def _task(uid, title="견적 회신", origin="mail"):
    with SessionLocal() as db:
        t = Task(user_id=uid, title=title, goal="g", origin=origin)
        db.add(t)
        db.commit()
        return t.id


def _log(uid, task_id, reason, minutes=0, action_type="MAIL_TRIAGE"):
    with SessionLocal() as db:
        log = AgentActionLog(user_id=uid, task_id=task_id, action_type=action_type, reason=reason,
                             input_data={"body": SECRET_IN}, output_data={"body": SECRET_OUT},
                             risk_level="LOW", created_at=BASE + timedelta(minutes=minutes))
        db.add(log)
        db.commit()
        return log.id


def test_login_required(client):
    assert client.get("/api/activity").status_code == 401


def test_newest_first_with_fields_and_no_raw_data(client):
    uid = register(client)["id"]
    tid = _task(uid)
    first = _log(uid, tid, "첫 기록", minutes=0)
    second = _log(uid, tid, "둘째 기록", minutes=5, action_type="REPLY_DRAFT")
    r = client.get("/api/activity")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["id"] for i in items] == [second, first]
    assert set(items[0]) == {"id", "task_id", "task_title", "action_type", "reason", "risk_level", "created_at"}
    assert items[0]["task_id"] == tid and items[0]["task_title"] == "견적 회신"
    assert items[0]["action_type"] == "REPLY_DRAFT" and items[0]["reason"] == "둘째 기록"
    assert items[0]["risk_level"] == "LOW"
    assert SECRET_IN not in r.text and SECRET_OUT not in r.text


def test_other_users_and_non_mail_tasks_excluded(client):
    me = register(client)["id"]
    with make_client() as other_client:
        other = register(other_client, email="other@example.com")["id"]
    mine = _task(me)
    _log(me, mine, "내 기록")
    _log(other, _task(other, title="남의 업무"), "남의 기록")
    _log(me, _task(me, title="옛 업무", origin="user"), "메일 아님")
    _log(me, None, "업무 없음")
    items = client.get("/api/activity").json()["items"]
    assert [i["reason"] for i in items] == ["내 기록"]
    assert "남의" not in client.get("/api/activity").text


def test_limit_default_and_bounds(client):
    uid = register(client)["id"]
    tid = _task(uid)
    for n in range(55):
        _log(uid, tid, f"기록 {n}", minutes=n)
    assert len(client.get("/api/activity").json()["items"]) == 50
    items = client.get("/api/activity?limit=3").json()["items"]
    assert [i["reason"] for i in items] == ["기록 54", "기록 53", "기록 52"]
    assert len(client.get("/api/activity?limit=100").json()["items"]) == 55
    assert client.get("/api/activity?limit=0").status_code == 422
    assert client.get("/api/activity?limit=101").status_code == 422
