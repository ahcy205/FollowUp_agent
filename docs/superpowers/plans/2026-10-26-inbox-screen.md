# 받은 업무 화면 구현 계획 (설계 5단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 앱을 열면 메일 업무가 "지금 처리 → 이번 주 → 나중에" 순서로 보이고, 업무 상세에서 기한 · 중요도 · 할 일을 다루며, 스누즈 · 답장 넛지 · 업무 합치기 · 브라우저 알림으로 놓치지 않게 한다.

**Architecture:**
- 묶음 · 정렬 · 스누즈 해제 · 넛지 · 알림 판단은 서버의 순수 함수 모듈(`app/mail/inbox.py`)에 모은다. 화면과 알림은 같은 판단을 쓴다.
- 업무를 바꾸는 동작은 `app/mail/task_actions.py`, 합치기는 `app/mail/merge.py`에 둔다.
- 화면은 새 "받은 업무" 페이지와 업무 상세의 메일 업무 전용 영역이다.
- 기존 실시간 갱신(SSE), 캘린더 반영 표시(`mark_calendar_dirty`), 워커 깨우기(`wake`)를 그대로 재사용한다.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, `zoneinfo`, React 19 + react-router 7, Notification API.

**Spec:** `docs/superpowers/specs/2026-10-26-inbox-screen-design.md`
- 상위 설계는 `2026-10-10-gmail-calendar-design.md` 5절 · 12.2절
- 시안: `.superpowers/brainstorm/1303-1793020617/content/inbox-layout-v3.html`(첫 화면), `inbox-layout.html`(업무 상세)

## Global Constraints

- **대상:** 메일 업무(`origin="mail"`)만 새 화면에 나온다. 합쳐진 업무(`merged_into_id`가 있음)는 어떤 목록에도 나오지 않는다.
- **시간대:** 날짜 판단("오늘 · 내일 · 3일 · 7일", 오전 9시, 오후 6시)은 `USER_TIMEZONE`, 저장은 UTC다. SQLite가 돌려주는 시간대 없는 값은 UTC로 본다.
- **묶음 기준** (설계 2.2절, 코드 상수)
  - **지금 처리:** 기한이 지났거나 오늘 · 내일이다. 또는 중요도 `HIGH`이고 기한이 3일 안이다(오늘 + 3일 날짜까지).
  - **이번 주:** 기한이 7일 안이다. 또는 기한이 없고 `HIGH`다.
  - **나중에:** 나머지. 분석 대기 업무는 "나중에" 맨 위에 둔다.
  - **묶음 안 정렬:** 중요도 HIGH → NORMAL → LOW → 기한 이른 순(없음은 마지막) → 원본 메일 받은 순(오래된 순)
- **탭** (설계 2.1절)

  | 탭 | 조건 |
  |---|---|
  | 처리할 일 | `ACTION_REQUIRED` · `IN_PROGRESS` · `BLOCKED`, 그리고 `PENDING`이면서 `brief.analysis`가 `pending` 또는 `failed` |
  | 답장 보냄 | `WAITING` |
  | 참고용 | `PENDING` + `analysis == "done"` |
  | 완료 | `COMPLETED` · `CANCELLED` |

- **스누즈:**
  - 선택지: `evening`(오늘 18:00, 지났으면 400) · `tomorrow`(내일 09:00) · `next_monday`(다음 주 월요일 09:00) · 직접 지정(미래, 1년 이내)
  - 실제 해제 시각 = min(정한 시각, 기한 전날 09:00)
  - 상대 새 메일이 오거나 "지금 보기"를 누르면 즉시 해제한다.
  - "다시 표시됨"은 해제 후 24시간 동안 붙인다.
  - 업무가 닫히면 스누즈를 지운다.
- **답장 넛지:**
  - 조건: `brief.needs_reply`가 참이고, 처리할 일 탭(분석 대기는 제외)이며, 스누즈 중이 아니고, 가장 최근 상대 메일(`direction=INBOUND`, `received_at` 또는 없으면 `created_at`)로부터 48시간 이상 지났다.
  - 표시: "N일째 답장 안 함", N = 경과 일수(내림)
- **합치기 후보:**
  - 이 업무보다 먼저 만들어졌고, 원본 메일 보낸 사람 주소가 같고(대소문자 무시), `ACTION_REQUIRED` · `IN_PROGRESS` · `BLOCKED` · `WAITING` 상태이며, 최근 14일 안에 만들어지고, 합쳐지지 않은 메일 업무
  - 최근 것부터 최대 3개
  - 이 업무가 닫혔거나, 합쳐졌거나, `merge_dismissed`면 후보를 내지 않는다.
- **합치기 되돌리기:** 합친 뒤 10분 안에만 가능하다. 옮긴 것만 돌려보내고, 사용자가 합친 뒤 직접 바꾼 기한 · 중요도는 유지한다.
- **알림 키**
  - `deadline-d1:{task}:{YYYY-MM-DD}` — 기한 전날 09:00 이후
  - `deadline-d0:{task}:{YYYY-MM-DD}` — 기한 당일 09:00 이후
  - `snooze:{task}:{해제 시각 ISO}`
  - `nudge:{task}:{최근 상대 메일 id}`
  - 대상은 처리할 일 탭의 업무다.
- **캘린더:** 기한 수정 · 확인, 할 일 변경, 닫기, 합치기 · 되돌리기 뒤에 해당 업무에 `mark_calendar_dirty`를 호출한다. 중요도 변경은 호출하지 않는다.
- **기존 규칙:** `CLAUDE.md`를 따른다. 로그에 메일 내용을 남기지 않고, POST + 로그인 + 본인 업무만 허용하며, 화면에 `confirm` / `alert`를 쓰지 않고, 사용자 문구는 한국어로 쓴다.
- **테스트:** `cd backend && ../.venv/Scripts/python -m pytest -q`(기존 340개 포함 전부 통과), `cd frontend && npm run build && npm run lint`(App.tsx 기존 경고 1개만 허용)

## Review Focus

1. **스누즈 중 마감이 다가오는 경우.** 스누즈를 다음 주 월요일로 걸었는데 기한이 내일이면, 오늘 09:00(기한 전날)에 다시 나타나고 알림이 와야 한다. 마감을 넘겨 숨어 있으면 안 된다 (Task 2).
2. **시간대 경계.** 한국 시간 00:30은 UTC로 전날이다. 이때 "오늘 · 내일"과 D-day 판단은 한국 날짜로 해야 한다 (Task 2).
3. **합친 뒤 되돌리기 전에 생긴 변화.** 합친 뒤 새 메일이 B 스레드로 들어와 A에 붙었거나, 사용자가 A의 기한을 바꿨다. 되돌리면 그 메일은 B로 가고 사용자의 기한은 유지되어야 한다 (Task 4).
4. **합쳐진 스레드로 오는 새 메일.** B가 A에 합쳐진 뒤 B 스레드로 오는 메일은 A에 붙고, A의 스누즈를 풀어야 한다. 닫힌 B를 다시 열면 안 된다 (Task 4).
5. **같은 알림 반복.** 1분마다 알림 목록을 가져와도 같은 키는 한 번만 띄워야 한다. 기한이 바뀌면 키가 달라져 다시 알려야 한다 (Task 2 · 6).

---

## 파일 구조

| 경로 | 책임 |
|---|---|
| `backend/app/models.py` · `alembic/versions/a8b3c6d1e4f7_inbox_fields.py` | 업무 컬럼 |
| `backend/app/mail/inbox.py` | 묶음 · 정렬 · 탭 · 스누즈 해제 · 넛지 · 합치기 후보 · 알림 판단 (DB 조회 함수 + 순수 함수) |
| `backend/app/mail/task_actions.py` | 스누즈, 중요도, 기한, 할 일, 닫기, 다시 분석, 업무로 처리 |
| `backend/app/mail/merge.py` | 합치기, 되돌리기, 따로 두기, 스레드 대상 따라가기 |
| `backend/app/mail/intake.py` (수정) | 합쳐진 대상 따라가기, 새 메일 도착 시 스누즈 해제, 대기 업무 만들기 공개 함수 |
| `backend/app/api/inbox.py` (새 라우터) · `backend/app/api/tasks.py` (수정) | API |
| `backend/app/schemas.py` (수정) | 응답 · 요청 모델 |
| `frontend/src/pages/Inbox.tsx` · `frontend/src/components/InboxCard.tsx` | 받은 업무 |
| `frontend/src/components/mail/*.tsx` · `frontend/src/pages/TaskDetail.tsx` (수정) | 업무 상세 메일 영역 |
| `frontend/src/alerts.ts` · `frontend/src/App.tsx` (수정) | 알림 폴링, 탭 제목, 라우트 |

---

### Task 1: 업무 컬럼과 마이그레이션

**Files:**
- Modify: `backend/app/models.py`, `backend/app/schemas.py`
- Create: `backend/alembic/versions/a8b3c6d1e4f7_inbox_fields.py` (down_revision `f7a2b5c9d3e6`)
- Test: `backend/tests/test_inbox_fields.py`

**Interfaces:**
- Produces: `Task`에 추가하는 컬럼
  - `snoozed_until: datetime | None`
  - `snooze_returned_at: datetime | None`
  - `priority_source: str(10) = "llm"`
  - `merged_into_id: int | None` (FK `tasks.id`, ondelete SET NULL, index)
  - `merge_dismissed: bool = False`
  - `merge_undo: JSON = {}`
- `TaskBrief`에는 `snoozed_until`, `priority_source`, `merged_into_id`를 추가한다. 나머지는 Task 5의 상세 응답에서 계산값으로 내보낸다.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_defaults(): ...            # priority_source "llm", merge_dismissed False, merge_undo {}, snoozed_until None
def test_merged_into_fk(): ...      # B.merged_into_id = A.id 저장 · 조회; A 삭제 시 B.merged_into_id None
def test_api_exposes(client): ...   # GET /api/tasks/{id} task에 snoozed_until, priority_source, merged_into_id
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
  - 로컬 `dev.db`는 SQLite 백업 API로 백업한 뒤 `alembic upgrade head`를 실행한다.
  - 임시 사본에서 왕복(downgrade → upgrade)을 확인한다.
- [ ] **Step 5: 커밋** — `받은 업무: 스누즈·중요도 출처·합치기 컬럼`

---

### Task 2: 받은 업무 판단 모듈

**Files:**
- Create: `backend/app/mail/inbox.py`
- Test: `backend/tests/test_inbox_rules.py`

**Interfaces:**
- Produces (순수 함수, `now`는 UTC aware, `tz`는 시간대 이름)

```python
TAB_TODO, TAB_SENT, TAB_REFERENCE, TAB_DONE = "todo", "sent", "reference", "done"
BUCKET_NOW, BUCKET_WEEK, BUCKET_LATER = "now", "week", "later"

def tab_of(task: Task) -> str | None                               # 합쳐진 업무 · 메일 업무가 아니면 None
def snooze_wake_at(task: Task, tz: str) -> datetime | None         # min(snoozed_until, 기한 전날 09:00), 스누즈 없으면 None
def is_snoozed(task: Task, now: datetime, tz: str) -> bool
def returned_at(task: Task, now: datetime, tz: str) -> datetime | None  # snooze_returned_at, 또는 wake가 지난 경우 wake; 24시간 지나면 None
def bucket_of(task: Task, now: datetime, tz: str) -> str            # 처리할 일 탭 업무에만 의미
def sort_key(task: Task, received_at: datetime | None) -> tuple
def deadline_badge(task: Task, now: datetime, tz: str) -> dict      # {"text": "D+1 지남"|"오늘"|"D-1 내일"|"D-5"|"기한 없음", "late": bool, "estimated": bool}
def nudge_days(task: Task, latest_inbound_at: datetime | None, now: datetime, tz: str) -> int | None
def alerts_for(task: Task, latest_inbound_id: int | None, latest_inbound_at, now, tz) -> list[dict]  # [{"key","kind","task_id","text"}]
```

- DB 조회 함수
  - `latest_inbound(db, task) -> tuple[int | None, datetime | None]`
  - `merge_candidates(db, task, now) -> list[Task]`
- 알림 문구
  - 기한: `"[D-1] {요약}"`, `"[오늘 마감] {요약}"`
  - 스누즈: `"[다시 표시] {요약}"`
  - 넛지: `"[답장 {N}일째 없음] {요약}"`
  - 요약은 `brief.summary`, 없으면 `title`이다.

- [ ] **Step 1: 실패하는 테스트** (`now` 고정: `2026-10-26T01:00Z` = 한국 시간 월요일 10:00)

```python
def test_tabs(): ...                       # 상태 · analysis 조합별 탭; 합쳐진 업무 · 에이전트 업무 → None
def test_bucket_now_rules(): ...           # 지난 기한, 오늘, 내일 → now; HIGH + 10/29 → now; NORMAL + 10/29 → week
def test_bucket_week_and_later(): ...      # 11/2 → week; 11/3 → later; 기한 없음 + HIGH → week; 기한 없음 + LOW → later; 분석 대기 → later
def test_bucket_tz_boundary(): ...         # now 2026-10-26T15:30Z(한국 10/27 00:30), 기한 10/28 → "내일" → now   [RF 2]
def test_sort_order(): ...                 # 중요도 > 기한(없음 마지막) > 받은 순
def test_badges(): ...                     # D+1 지남(late), 오늘, D-1 내일, D-5, 기한 없음, estimated 표시
def test_snooze_wake_safety(): ...         # 스누즈 11/2 09:00, 기한 10/27 → wake = 10/26 09:00(한국) → 지금 해제 상태   [RF 1]
def test_snooze_active_and_returned(): ... # 미래 wake → is_snoozed; wake 지남 → returned_at = wake, 24시간 뒤 None
def test_nudge(): ...                      # needs_reply + 47시간 → None, 49시간 → 2, 스누즈 중 → None, 참고용 → None, 분석 대기 → None
def test_alerts_keys(): ...                # 기한 내일 + 09:00 이후 → deadline-d1 키; 09:00 이전 → 없음; 당일 → d0; 넛지 키에 메일 id; 닫힌 업무 → []   [RF 5]
def test_merge_candidates(db): ...         # 같은 주소(대소문자 다름) · 14일 안 · 열린 업무만, 최대 3개 최근 순; 15일 전, 닫힘, 합쳐짐, 다른 사용자 제외; dismissed → []
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 커밋** — `받은 업무: 묶음·정렬·스누즈·넛지·알림 판단`

---

### Task 3: 업무 동작 (스누즈 · 중요도 · 기한 · 할 일 · 닫기 · 다시 분석 · 업무로 처리)

**Files:**
- Create: `backend/app/mail/task_actions.py`
- Modify: `backend/app/mail/intake.py`, `backend/app/api/tasks.py`, `backend/app/schemas.py`, `backend/app/security.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_task_actions.py`

**Interfaces:**
- Consumes: `snooze_wake_at` (Task 2), `mark_calendar_dirty`, `app.mail.sync.wake`
- Produces (모두 커밋은 API가 한다. 잘못된 요청은 `ActionError(message, status=400|409)`)
  - `snooze(task, *, preset: str | None, until: datetime | None, now, tz)`
  - `unsnooze(task)`
  - `set_priority(task, priority: str)` — `priority_source="user"`
  - `set_deadline(task, *, date: str | None, time: str | None, clear: bool, confirm: bool, tz)`
    - 정한 기한은 `kind="explicit"`, `source="사용자가 직접 정함"`이다.
    - `confirm`은 추정 기한을 확정으로 바꾸고 `source`는 유지한다.
    - 날짜는 오늘 기준 ±2년 안이어야 한다.
  - `edit_todos(task, op: dict) -> dict`
    - `op`의 종류: `{"op":"add","title"}` · `{"op":"toggle","id","done"}` · `{"op":"memo","id","memo"}` · `{"op":"delete","id"}` · `{"op":"restore","item"}`
    - 반환값: 바뀐 항목
    - 새 id는 max + 1, 제목 1~300자, 메모 0~1000자
    - 추가한 항목은 `source="user"`
  - `close_without_reply(db, user, task, reason: str)`
    - `COMPLETED`, `completed_at`, `current_step="답장 필요 없음으로 닫음"`으로 바꾼다.
    - 스누즈를 지우고, 타임라인 `TASK_CLOSED`에 `{"reason"}`을 남기고, 캘린더 반영을 표시한다.
  - `request_reanalyze(task)` — `brief.analysis == "failed"`일 때만 `pending`, attempts 0
  - `promote_email(db, user, email) -> Task`
    - 스위치가 꺼져 있으면 `ActionError(409, "자동 판별이 꺼져 있어 분석할 수 없습니다.")`
    - 같은 스레드에 업무가 있으면 그 업무에 붙인다. 합쳐진 업무면 합쳐진 대상을 따라가고, 닫힌 업무면 `ACTION_REQUIRED`로 다시 연다.
    - 업무가 없으면 `intake.create_pending_task`로 분석 대기 업무를 만든다.
- `intake.py`에 공개 함수 `create_pending_task(db, email) -> Task`를 추가한다. 기존 `_create_task`를 감싸고, IntegrityError가 나면 그 스레드의 업무를 돌려준다.
- `intake._attach`에서 새 메일이 붙으면 그 업무의 `snoozed_until=None`, `snooze_returned_at=now`로 바꾼다.
- `intake._apply`는 `priority_source == "user"`인 업무의 `priority`를 바꾸지 않는다.
- `orchestrator.cancel_task`도 메일 업무의 스누즈를 지운다.
- API (`tasks.py`): 응답은 모두 `build_task_detail`이다.
  - `POST /api/tasks/{id}/snooze` `{preset?, until?}`
  - `/unsnooze`, `/priority` `{priority}`
  - `/deadline` `{date?, time?, clear?, confirm?}`
  - `/todos` `{op...}`, `/close` `{reason?}`, `/reanalyze`
  - `POST /api/emails/{id}/promote` → `{"task_id"}`
- 메일 업무가 아니면 409("메일 업무에서만 쓸 수 있습니다.")를 돌려준다.
- 호출 제한: `promote` · `reanalyze`는 `ai_action_limiter = RateLimiter(10, 60)`, 키 `ai:{uid}`. conftest에서 초기화한다.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_snooze_presets(): ...             # now 월 10:00 → evening 18:00 오늘, tomorrow 화 09:00, next_monday 11/2 09:00; 저녁 지난 뒤 evening → 400
def test_snooze_custom_validation(): ...   # 과거 · 1년 초과 → 400
def test_new_mail_unsnoozes(): ...         # 스누즈 중 업무 스레드에 새 메일 attach → snoozed_until None, returned_at now
def test_priority_user(): ...              # set_priority LOW → priority LOW, source user; 이후 analyze_into_task가 priority를 덮지 않음
def test_deadline_set_clear_confirm(): ... # 날짜만 → all_day; 시각 → timed; clear → none; confirm → estimated→explicit, source 유지; 모두 calendar pending
def test_deadline_invalid(): ...           # "2026-13-01", 3년 뒤 → 400
def test_todos_ops(): ...                  # add(id=max+1, source user), toggle, memo, delete 반환값으로 restore → 원래 위치 · 상태; 캘린더 pending
def test_close_without_reply(): ...        # COMPLETED, 스누즈 해제, TASK_CLOSED 로그, calendar pending; 이미 닫힌 업무 → 409
def test_reanalyze(): ...                  # failed → pending attempts 0, wake 호출; done 업무 → 409
def test_promote_new_and_existing(): ...   # 업무 없는 not_work 메일 → 분석 대기 업무 생성 + triage work;
                                           #   스레드에 취소된 업무 → 다시 ACTION_REQUIRED, 메일 연결; 합쳐진 업무 → 대상에 연결; 스위치 off → 409
def test_api_ownership_and_limits(client): ...  # 다른 사용자 404, 에이전트 업무 409, promote 11번째 429
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 커밋** — `받은 업무: 스누즈·중요도·기한·할 일·닫기·다시 분석·업무로 처리`

---

### Task 4: 업무 합치기

**Files:**
- Create: `backend/app/mail/merge.py`
- Modify: `backend/app/mail/intake.py` (`_thread_task`), `backend/app/api/tasks.py`
- Test: `backend/tests/test_merge.py`

**Interfaces:**
- Consumes: `merge_candidates` (Task 2), `mark_calendar_dirty`, `log_action`
- Produces
  - `resolve_merged(db, task) -> Task`: `merged_into_id`를 따라간다. 최대 5단계, 순환이면 멈춘다.
  - `merge(db, user, source: Task, target: Task, now) -> None` (설계 4.3절)
    - 거절 조건(409): 서로 같은 업무, 합쳐진 업무, 닫힌 원본, 다른 사용자, 메일 업무가 아닌 경우
    - `target.merge_undo`에 기록하는 것
      - `{"source_id", "at", "moved_email_ids", "added_todo_ids", "added_requests"}`
      - `"before"`: target의 deadline_at · all_day · kind · source · priority · priority_source
      - `"after"`: 합친 직후 같은 값
      - `"source_before"`: source의 status · current_step · completed_at · snoozed_until
  - `undo_merge(db, user, target: Task, now) -> Task` (설계 4.3절)
    - 10분이 지났거나 기록이 없으면 409를 돌려준다.
    - 옮긴 메일, 그리고 합친 뒤 source 스레드로 들어온 메일(`gmail_thread_id == source.gmail_thread_id`)을 source로 돌려보낸다.
    - 추가한 할 일은 현재 체크 상태 그대로 돌려보내고, 추가한 요청은 target에서 뺀다.
    - target의 기한 · 중요도는 현재 값이 `after`와 같을 때만 `before`로 돌린다.
    - source는 `source_before`로 복구하고 `merged_into_id`를 지운다.
    - 양쪽 캘린더 반영을 표시하고 타임라인 `TASK_UNMERGED`를 남긴다. `merge_undo`는 `{}`로 비운다.
  - `dismiss_merge(task)` → `merge_dismissed=True`
- `intake._thread_task`: 찾은 업무에 `resolve_merged`를 적용한다. 합쳐진 원본 B는 다시 열지 않는다.
- API: `POST /api/tasks/{id}/merge` `{into_task_id}`, `/merge/undo`(target에서 호출), `/merge/dismiss`. 응답은 `build_task_detail`(merge는 target 기준)이다.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_merge_moves_everything(): ...       # B 메일 2통 → A; B 할 일 중 A와 제목이 같은 것은 건너뜀; 요청 중복 제외;
                                             #   B 기한이 더 이르면 A 기한 교체; 중요도 높은 쪽; B COMPLETED + merged_into_id; 양쪽 calendar pending; 로그 2건
def test_merge_rejects(): ...                # 같은 업무, 이미 합쳐짐, 다른 사용자(404), 에이전트 업무 → 409
def test_followup_mail_goes_to_target(): ... # 합친 뒤 B 스레드 새 메일 intake → A에 연결, A 스누즈 해제, B는 COMPLETED 유지   [RF 4]
def test_undo_restores(): ...                # 합치기 → 되돌리기 → 메일 · 할 일 · 요청 · 기한 · 중요도 · B 상태 원래대로
def test_undo_keeps_user_changes(): ...      # 합친 뒤 A 기한을 set_deadline으로 변경, 옮겨 온 할 일 체크, B 스레드 새 메일 도착 →
                                             #   되돌리기 후 A 기한은 사용자 값 유지, 할 일은 체크된 채 B로, 새 메일은 B로   [RF 3]
def test_undo_window(): ...                  # 11분 뒤 → 409
def test_dismiss(): ...                      # dismiss 후 merge_candidates == []
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 커밋** — `받은 업무: 같은 상대 업무 합치기와 되돌리기`

---

### Task 5: 받은 업무 · 알림 API와 상세 응답 확장

**Files:**
- Create: `backend/app/api/inbox.py`
- Modify: `backend/app/main.py`, `backend/app/schemas.py`, `backend/app/api/tasks.py` (`build_task_detail`, `task_fingerprint`)
- Test: `backend/tests/test_inbox_api.py`

**Interfaces:**
- Consumes: Task 2의 모든 판단 함수, Task 4의 `merge_candidates`
- Produces
  - `GET /api/inbox` → `InboxOut`

    ```
    InboxOut {
      counts: {now, week, later, todo, sent, reference, done},
      tabs: {todo: {now: [Card], week: [Card], later: [Card]}, sent: [Card], reference: [Card], done: [Card]},
      snoozed: [Card + wake_at],
      not_work: [NotWorkMail {email_id, sender, subject, triage, triage_reason, received_at}],
      triage_enabled: bool
    }
    ```

    - `Card`: `{task_id, title, summary, sender_name, sender_org, priority, deadline_badge, todo_done, todo_total, nudge_days, returned, has_merge_candidates, analysis, received_at}`
    - `done` 탭은 최근 30일, 최대 50건이다.
    - `not_work`는 `triage in (not_work, filtered)`, 최근 30일, 최대 50건이다.
  - `GET /api/inbox/alerts` → `{now_count: int, alerts: [{key, kind, task_id, text}]}`
  - `TaskDetail`에 추가하는 필드: `mail: MailExtras | None` (메일 업무일 때만)
    - `MailExtras {is_snoozed, wake_at, returned, nudge_days, merge_candidates: [{task_id, title}], can_undo_merge, merged_into_id, deadline_badge, priority_source}`
  - `task_fingerprint`에 `snoozed_until`, `merge_undo` 유무, 메일 수를 더한다. 실시간 갱신이 따라오게 하기 위해서다.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_inbox_groups_and_order(client): ...  # 기한 · 중요도가 섞인 업무 6개 → 묶음과 순서가 Task 2 기준과 일치; counts 일치
def test_inbox_excludes(client): ...          # 에이전트 업무, 합쳐진 업무, 다른 사용자 업무 없음; 스누즈 중 업무는 snoozed에만
def test_not_work_list(client): ...           # not_work · filtered 메일만, 30일 넘은 것 제외
def test_alerts(client): ...                  # 내일 기한 업무 + now 09:30 → deadline-d1 하나; 같은 요청 두 번 → 같은 키 (중복 방지는 브라우저 몫)
def test_detail_mail_extras(client): ...      # 메일 업무 상세에 mail 필드, 에이전트 업무는 None; 합친 직후 can_undo_merge True
def test_fingerprint_changes_on_snooze(): ... # snooze 후 fingerprint 달라짐
```

- `now`는 테스트에서 고정할 수 있게 `app.mail.inbox.now_utc()` 함수로 얻고, 테스트는 monkeypatch한다.
- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 커밋** — `받은 업무: 목록·알림 API와 업무 상세 확장`

---

### Task 6: 받은 업무 화면, 라우트, 알림

**Files:**
- Create: `frontend/src/pages/Inbox.tsx`, `frontend/src/components/InboxCard.tsx`, `frontend/src/alerts.ts`
- Modify: `frontend/src/App.tsx`, `frontend/src/pages/Settings.tsx`, `frontend/src/types.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/inbox`, `GET /api/inbox/alerts`, `POST /api/tasks/{id}/unsnooze`, `POST /api/emails/{id}/promote`
- 라우트
  - `/` → 받은 업무
  - `/tasks` → 기존 Dashboard("전체 업무")
  - 상단 메뉴: 받은 업무 · 전체 업무 · 워크스페이스 데이터 · 설정
- **받은 업무 화면 (시안 v3 그대로)**
  - 상태 줄: 기존 Google 상태 재사용, "지금 확인"
  - "지금 처리 N건"(큰 글자)과 "이번 주 N건 · 나중에 N건", 오른쪽 위 범례
  - 탭 4개(건수 포함)
  - 처리할 일 탭: 묶음 제목 + 카드. 비어 있으면 "지금 처리할 업무가 없습니다"
  - 접힌 "나중에 보기 N건"(다시 뜨는 시각, "지금 보기" 버튼)
  - 접힌 "업무 아님으로 분류된 메일 N건"(보낸 사람 · 제목 · 이유, "업무로 처리" 버튼 → 성공 시 그 업무로 이동; 스위치가 꺼져 있으면 버튼 대신 안내)
- **카드:** `InboxCard`
  - 중요도 점: 높음 꽉 찬 빨강 / 보통 반쯤 찬 노랑 / 낮음 빈 초록. `title`과 `aria-label`에 "중요도 높음" 등을 넣는다.
  - 기한 배지: 테두리만 있고, 지난 것만 빨강, "· 확인 필요"
  - 보낸 사람, 요약, 할 일 진행도
  - 상황 표시: "N일째 답장 안 함", "다시 표시됨", "합치기 제안", "분석 중"
  - 카드 전체가 업무 상세로 가는 링크다.
- **새로고침:** 30초마다 다시 불러온다. 다른 화면에서 돌아올 때도 다시 불러온다.
- **알림 (`alerts.ts`, Shell에서 한 번 시작)**
  - 60초마다 `/api/inbox/alerts`를 부른다.
  - `document.title`을 `now_count > 0 ? "(N) FollowUp Agent" : "FollowUp Agent"`로 둔다.
  - `Notification.permission === "granted"`일 때만, `localStorage["followup.shownAlerts"]`(최대 500개 키, try/catch)에 없는 키를 `new Notification(text)`로 띄우고 기록한다.
  - 알림을 누르면 `window.focus()` 후 `/tasks/{id}`로 이동한다.
- **설정 화면:** "브라우저 알림" 줄을 추가한다.
  - 현재 권한 표시와 "알림 허용" 버튼(`Notification.requestPermission()`)
  - 거부됐으면 "브라우저 설정에서 이 사이트의 알림을 허용해 주세요."
  - 브라우저가 알림을 지원하지 않으면 그 사실을 안내한다.
- 휴대폰 너비(400px)에서 카드 내용이 한 줄로 쌓인다.

- [ ] **Step 1: 구현**
- [ ] **Step 2: 확인** — `npm run build && npm run lint`(기존 경고 1개 외 없음)
- [ ] **Step 3: 커밋** — `받은 업무: 첫 화면, 메뉴 정리, 브라우저 알림`

---

### Task 7: 업무 상세 메일 영역

**Files:**
- Create: `frontend/src/components/mail/SummaryCard.tsx`, `TodoChecklist.tsx`, `MailActions.tsx`, `MailBanners.tsx`
- Modify: `frontend/src/pages/TaskDetail.tsx`, `frontend/src/types.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 3 · 4의 API, `TaskDetail.mail`(Task 5)
- 메일 업무(`task.origin === "mail"`)일 때만 기존 목표 · 계획 영역 대신 아래를 보여준다. 다른 업무는 지금 그대로 둔다.
- **`MailBanners`** (설계 3.1절 순서)
  - 분석 실패 + "다시 분석"
  - 합치기 제안: 후보마다 "이 업무에 합치기", 그리고 "따로 두기"
  - 합친 직후: "합쳐졌습니다 — 되돌리기"(`can_undo_merge`)
  - "N일째 답장하지 않았습니다"
  - 스누즈 중: "{시각}에 다시 표시됩니다 — 지금 보기"
  - "상대 답장 없음" 부품은 만들되 이번에는 렌더하지 않는다.
- **`SummaryCard`**
  - 요약, 보낸 사람 · 받은 날짜, 상대가 원하는 것
  - 중요도 신호등: 세 점 버튼과 글자
  - 기한 영역: D-day 배지, 근거 문장, 기존 캘린더 줄
  - 기한 편집: "기한 수정"(날짜 입력, 선택적 시각, 저장 · 기한 없애기 · 취소, 카드 안 편집)
  - 추정 기한이면 "이 기한이 맞아요", 기한이 없으면 "기한 추가"
- **`TodoChecklist`**
  - 체크박스, 제목, 항목 기한(`due_at` 또는 `due_note`), 메모 펼치기 · 저장, 삭제
  - 삭제하면 카드 안에 "삭제했습니다 — 되돌리기"를 5초 동안 보여준다(restore op).
  - 맨 아래에 추가 입력을 둔다.
  - 모두 끝나면 "답장 초안 만들기" 강조 문구를 보여준다.
- **`MailActions`**
  - 나중에 보기: 선택지 4개, 직접 지정은 `datetime-local`
  - 답장 필요 없음으로 닫기: 카드 안에서 이유 입력 후 확인
  - 업무 취소: 기존 `CancelTaskPanel`
  - 답장 초안 만들기: 비활성, "6단계에서 제공"
- 모든 동작은 응답(`TaskDetail`)으로 화면 상태를 바꾼다. 실패하면 `ApiError` 문구를 보여준다.
- 기존 실시간 갱신(SSE)이 같은 데이터를 덮어써도 결과가 같아야 한다.

- [ ] **Step 1: 구현**
- [ ] **Step 2: 확인** — 빌드 · lint
- [ ] **Step 3: 커밋** — `받은 업무: 업무 상세 요약·할 일·스누즈·닫기·합치기 화면`

---

### Task 8: 상위 설계 반영, 브라우저 점검, 문서, 병합

- [ ] **Step 1: 상위 설계 수정** (`2026-10-10-gmail-calendar-design.md`)
  - 4.9절: "답장 보냄 3일 → 자동 완료"를 "3일 후 다시 연락할지 물어보기(6단계)"로 바꾼다.
  - 5절: 정렬을 묶음 기준으로 바꾸고, 스누즈 · 넛지 · 합치기를 추가한다. 5단계 설계 문서 링크를 단다.
  - 12.2절: "확정됨 — 5단계 설계 4.3절"로 표시한다.
- [ ] **Step 2: 로컬 점검** (테스트 계정이 아니라 실제 연결된 사용자 계정 화면이 필요하므로 사용자와 함께 진행한다)
  - [ ] 첫 화면에 기존 메일 업무가 묶음으로 보이고, 탭 건수가 맞다.
  - [ ] 기한 수정 → 카드 배지 · 묶음이 바뀌고, 캘린더 일정 날짜가 바뀐다(종일 ↔ 시각 전환 포함).
  - [ ] 할 일 추가 · 체크 · 메모 · 삭제 · 되돌리기
  - [ ] 스누즈 "내일 오전 9시" → 목록에서 빠지고 "나중에 보기"에 보인다. "지금 보기"로 돌아온다.
  - [ ] 다른 계정으로 같은 상대 새 메일 → 합치기 제안 → 합치기 → 되돌리기 → 다시 합치기
  - [ ] "업무로 처리"로 업무 아님 메일을 업무로 만든다.
  - [ ] 브라우저 알림 허용 → 기한 알림 하나가 한 번만 뜬다. 탭 제목에 건수가 보인다.
  - [ ] 휴대폰 너비에서 첫 화면이 깨지지 않는다.
- [ ] **Step 3: 문서**
  - `CHANGELOG.md`, `docs/decisions.md`(묶음 기준, 점 표시, 합치기 방식, 넛지 한계)
  - `docs/troubleshooting.md`(알림이 안 뜰 때)
  - 이 계획의 "계획 대비 변경 이력", README 진행 상황 표
- [ ] **Step 4: 브랜치 push → CI 통과 → `main` 병합 · push**

---

## 계획 대비 변경 이력

| # | 날짜 | 작업 | 계획 | 실제 구현 | 이유 | 영향 |
|---|---|---|---|---|---|---|

## 이 계획 다음

- **6단계 (답장 · 수명주기):** 답장 초안 · 승인 · 발송, Gmail에서 보낸 답장 감지(→ 답장 넛지 해제, "답장 보냄" 탭), "상대 답장 없음" 넛지 연결, 재답장으로 업무 내용 갱신(상위 설계 12.1).
