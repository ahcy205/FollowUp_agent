# Google 캘린더 동기화 구현 계획 (설계 4단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 메일 업무에 기한이 생기면 사용자의 Google 캘린더에 팝업 알림이 있는 일정을 만들고, 업무가 끝나거나 취소되면 일정 제목에 ✓를 붙인다.

**Architecture:**
- 캘린더 동기화는 "할 일 목록" 방식으로 한다.
  - 업무의 기한이나 상태가 바뀌면 `calendar_state="pending"`으로 표시만 한다.
  - 메일 동기화 워커가 같은 계정 임대 안에서 대기 중인 업무의 일정을 만들거나 고친다.
- 일정 번호는 업무 번호로 미리 정한다(결정적 ID). 같은 일정이 두 번 생기지 않고, 사용자가 지운 일정을 다시 만들지도 않는다.
- Google 호출은 기존 `GoogleClient` 프로토콜에 캘린더 메서드를 더하고, 테스트는 `FakeGoogle`로 한다.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, httpx(Google Calendar API v3), React 19.

**Spec:** `docs/superpowers/specs/2026-10-10-gmail-calendar-design.md`
- 4.7절(캘린더 동기화), 3절(`calendar_event_id`, `calendar_state`), 6 · 7절의 캘린더 행
- 12절의 미확정 항목은 이 계획에 포함하지 않는다.

**선행:** 3단계 계획 `2026-10-26-mail-triage-and-intake.md` (완료, `main`)

## Global Constraints

- **범위:** 대상은 `origin="mail"` 업무뿐이다. 캘린더는 사용자의 기본 캘린더(`primary`)를 쓴다. 권한은 이미 받은 `calendar.events`만 쓴다.
- **기준은 앱:** 캘린더에서 사용자가 옮긴 날짜를 앱으로 되돌려 반영하지 않는다(설계 범위 밖).
- **일정 제목:** `[업무] {한 줄 요약}`
  - 추정 기한이면 `(확인 필요) [업무] …`
  - 완료 · 취소된 업무면 맨 앞에 `✓ `
  - 최대 200자
- **일정 설명:** 다음 내용을 줄 단위로 넣는다. 메일 본문은 넣지 않는다.
  - 상대(이름 · 소속)
  - 상대가 원하는 것
  - 할 일 목록(체크 표시 포함)
  - 기한 근거 문장
  - 앱 업무 링크 `{PUBLIC_URL}/tasks/{id}`
- **일정 시각**
  - **하루 종일:** `start.date` = 기한 날짜(사용자 시간대), `end.date` = 다음 날. 캘린더 API는 종료일을 포함하지 않는다.
  - **시각 있음:** `start.dateTime` = 기한 시각, `end` = +30분, `timeZone` = `USER_TIMEZONE`.
- **알림:** `reminders.useDefault=false`, 팝업만 쓴다.
  - 시각 있는 일정: 하루 전(1,440분)과 1시간 전(60분)
  - 하루 종일 일정: 전날 오전 9시. 시작(그날 00:00)보다 900분 전이다.
- **일정 번호(결정적 ID):** `"fuag" + f"{user_id:08x}" + f"{task_id:08x}"` (→ 변경 #2)
  - 캘린더 ID 규칙(소문자 a–v, 숫자 0–9, 5자 이상)을 만족한다.
  - 같은 번호로 다시 만들면 Google이 409를 돌려준다. 이를 "이미 있음"으로 처리해서 중복을 막는다.
- **상태값 `calendar_state`**

  | 값 | 뜻 |
  |---|---|
  | `none` | 일정 없음 |
  | `pending` | 반영 대기 |
  | `synced` | 반영됨 |
  | `deleted_by_user` | 사용자가 캘린더에서 지움. 다시 만들지 않음 |
  | `error` | 오류. "다시 시도" 버튼으로 재시도 |

- **오류 처리:** 캘린더 오류는 업무 처리를 막지 않는다.
  - 일시 오류(429 · 5xx · 네트워크): `pending`을 유지하고 다음 주기에 다시 한다. 5번 연속 실패하면 `error`로 바꾼다.
  - 그 밖의 오류: 바로 `error`로 바꾸고 한국어 문구를 남긴다.
  - 재연결 필요(`GoogleAuthError`): 상태를 바꾸지 않고 건너뛴다.
- **주기당 한도:** 캘린더 호출 대상 업무 최대 10개(`CALENDAR_SYNC_PER_CYCLE`).
- **기능 스위치:** 캘린더 동기화에는 별도 스위치를 두지 않는다. 메일 업무는 판별 스위치가 켜져야만 생기고, 일정은 사용자 본인의 캘린더에만 쓰기 때문이다.
- **로그:** 로그에는 업무 번호와 상태만 남긴다. 요약, 상대 이름, 일정 내용은 남기지 않는다.
- **테스트 · 커밋:** `cd backend && ../.venv/Scripts/python -m pytest -q`로 기존 268개를 포함해 전부 통과해야 한다. 커밋 메시지는 한국어 `영역: 내용` 형식으로 쓴다.

## Review Focus

1. **사용자가 캘린더에서 일정을 지운 경우.** 다음에 일정을 고칠 때 404 · 410 또는 `status=cancelled`를 확인하면 `deleted_by_user`로 바꾸고, 다시는 만들지 않는다. 이후 기한이 바뀌어도 마찬가지다. 결정적 ID 덕분에 다시 만들려 해도 409가 난다 (Task 4).
2. **일정을 만든 직후 서버가 죽은 경우.** 일정 번호를 저장하기 전에 죽어도, 다음 주기에 같은 ID로 만들려다 409를 받는다. 이때 기존 일정을 조회해서 이어 쓰므로 중복 일정이 생기지 않는다 (Task 4).
3. **하루 종일 일정의 날짜와 시간대.**
   - 한국 시간 10/30 하루 종일 기한은 `start.date=2026-10-30`, `end.date=2026-10-31`이다.
   - DB의 UTC 값(10/29 15:00Z)을 그대로 날짜로 쓰면 하루가 밀린다 (Task 3).
4. **업무 취소 · 완료.** 일정은 지우지 않고 제목 앞에 `✓ `만 붙인다. 이미 사용자가 지운 일정은 건드리지 않는다 (Task 4).
5. **캘린더 오류가 업무를 막는 경우.** 캘린더 API가 실패해도 메일 동기화 · 판별 · 접수는 정상으로 진행된다. 오류는 업무 상세에만 보인다 (Task 4 · 5).

---

## 파일 구조

| 경로 | 책임 |
|---|---|
| `backend/app/config.py` (수정) | `calendar_sync_per_cycle` |
| `backend/app/models.py` (수정) | `Task` 캘린더 컬럼 |
| `backend/alembic/versions/e6f1a4b8c2d5_task_calendar_fields.py` | 마이그레이션 |
| `backend/app/google/client.py` (수정) | 캘린더 메서드, `GoogleNotFound`, `GoogleConflict` |
| `backend/tests/fake_google.py` (수정) | 메모리 캘린더 |
| `backend/app/mail/calendar_event.py` | 업무 → 일정 본문 (순수 함수) |
| `backend/app/mail/calendar_sync.py` | 상태 전환, 계정 단위 동기화, `mark_calendar_dirty` |
| `backend/app/mail/intake.py`, `app/agent/orchestrator.py` (수정) | 기한 설정 · 업무 취소 시 `mark_calendar_dirty` |
| `backend/app/mail/sync.py` (수정) | 워커에서 캘린더 동기화 호출 |
| `backend/app/api/tasks.py`, `backend/app/schemas.py` (수정) | 캘린더 다시 시도 API, 응답 필드 |
| `frontend/src/pages/TaskDetail.tsx`, `frontend/src/types.ts` (수정) | 캘린더 상태 한 줄 |

---

### Task 1: 캘린더 컬럼과 설정

**Files:**
- Modify: `backend/app/models.py`, `backend/app/config.py`, `.env.example`, `backend/app/schemas.py`
- Create: `backend/alembic/versions/e6f1a4b8c2d5_task_calendar_fields.py` (down_revision `d5e9f3a2b7c4`)
- Test: `backend/tests/test_task_calendar_fields.py`

**Interfaces:**
- Produces:
  - `Task`에 추가하는 컬럼
    - `calendar_event_id: str(64) | None`
    - `calendar_event_url: Text = ""`
    - `calendar_state: str(20) = "none"` (index)
    - `calendar_error: Text = ""`
    - `calendar_attempts: int = 0`
  - `Settings.calendar_sync_per_cycle: int = 10` (ge=1, le=50)
  - `TaskBrief`에 추가하는 필드: `calendar_state`, `calendar_event_url`, `calendar_error`

- [ ] **Step 1: 실패하는 테스트**

```python
def test_defaults(): ...                      # 새 업무 → calendar_state "none", attempts 0, url "", error ""
def test_task_api_exposes_calendar(client): ...  # GET /api/tasks/{id} task에 calendar_state, calendar_event_url, calendar_error
def test_setting_default(): ...               # calendar_sync_per_cycle == 10
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과.** 로컬 `dev.db`는 백업한 뒤 `alembic upgrade head`를 실행한다. 임시 사본에서 `downgrade -1` → `upgrade head` 왕복도 확인한다.
- [ ] **Step 5: 커밋** — `캘린더: 업무 캘린더 컬럼과 설정`

---

### Task 2: GoogleClient 캘린더 메서드와 FakeGoogle

**Files:**
- Modify: `backend/app/google/client.py`, `backend/tests/fake_google.py`
- Test: `backend/tests/test_google_client.py`(추가), `backend/tests/test_fake_calendar.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class CalendarEvent: id: str; html_link: str; status: str   # status: "confirmed" | "tentative" | "cancelled"

class GoogleNotFound(GoogleError)   # 404, 410
class GoogleConflict(GoogleError)   # 409

# GoogleClient 프로토콜에 추가
def calendar_insert(self, access_token: str, body: dict) -> CalendarEvent
def calendar_get(self, access_token: str, event_id: str) -> CalendarEvent
def calendar_patch(self, access_token: str, event_id: str, body: dict) -> CalendarEvent
```

- 엔드포인트: `https://www.googleapis.com/calendar/v3/calendars/primary/events`
  - 추가: `POST`
  - 조회: `GET /{id}`
  - 수정: `PATCH /{id}`
- 오류 분류
  - 404 · 410 → `GoogleNotFound`
  - 409 → `GoogleConflict`
  - 기존 규칙은 그대로다(401 → 인증, 429 · 5xx · 403 사용량 한도 → 일시 오류).
  - Gmail 변경 기록용 `history=True` 경로는 바꾸지 않는다.
- `FakeGoogle` 추가 사항
  - `events: dict[str, dict]`, `deleted_events: set[str]`
  - `calendar_fail: GoogleError | None` (설정하면 다음 호출에서 한 번 발생)
  - `calendar_calls: list[tuple[str, str]]`
  - 동작
    - 지운 ID로 `insert` → `GoogleConflict`
    - 지운 ID로 `get` → `status="cancelled"`
    - 지운 ID로 `patch` → `GoogleNotFound`
    - `delete_event(id)` 헬퍼: 사용자가 캘린더에서 지운 상황을 흉내 낸다.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_calendar_insert_request_shape(): ...   # POST URL, Authorization 헤더, JSON 본문 그대로, 응답 id/htmlLink/status 파싱
def test_calendar_patch_and_get(): ...
def test_calendar_404_410_not_found(): ...
def test_calendar_409_conflict(): ...
def test_calendar_403_rate_limit_transient(): ...
def test_fake_deleted_event_semantics(): ...     # delete_event 후 insert→Conflict, get→cancelled, patch→NotFound
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 커밋** — `캘린더: GoogleClient 일정 추가·조회·수정과 테스트용 가짜 캘린더`

---

### Task 3: 업무 → 일정 본문

**Files:**
- Create: `backend/app/mail/calendar_event.py`
- Test: `backend/tests/test_calendar_event.py`

**Interfaces:**
- Produces:
  - `event_id_for(task: Task) -> str` (→ 변경 #2)
  - `build_event(task: Task, *, tz: str, public_url: str) -> dict` (→ 변경 #2)
    - 반환값은 Calendar API 본문이다: `id`, `summary`, `description`, `start`, `end`, `reminders`
    - `id`는 insert에만 쓴다. patch에서는 호출하는 쪽이 뺀다.
- 규칙: Global Constraints의 제목 · 설명 · 시각 · 알림 규칙을 그대로 따른다.
  - 요약은 `task.brief["summary"]`가 비었으면 `task.title`을 쓴다.
  - 기한이 없는 업무에 호출하면 `ValueError`를 낸다.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_event_id_format(): ...           # user 2, task 31 → "fuag" + "00000002" + "0000001f"; 정규식 ^[a-v0-9]{5,1024}$
def test_all_day_dates_in_user_tz(): ...  # deadline_at 2026-10-29T15:00Z all_day → start.date 2026-10-30, end.date 2026-10-31   [RF 3]
def test_timed_event(): ...               # 2026-10-30T05:00Z → start.dateTime 2026-10-30T14:00:00+09:00, end 14:30, timeZone Asia/Seoul
def test_reminders(): ...                 # timed → [1440, 60] popup; all-day → [900] popup; useDefault False
def test_title_variants(): ...            # explicit → "[업무] 요약"; estimated → "(확인 필요) [업무] 요약";
                                          #   COMPLETED/CANCELLED → "✓ " 접두; 200자 제한
def test_description_lines(): ...         # 상대, 요청, 할 일(완료 표시), 근거 문장, http://localhost:8000/tasks/31 포함, 메일 본문 미포함
def test_no_deadline_raises(): ...
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 커밋** — `캘린더: 업무를 캘린더 일정 본문으로 변환`

---

### Task 4: 캘린더 동기화와 상태 전환

**Files:**
- Create: `backend/app/mail/calendar_sync.py`
- Modify: `backend/app/mail/intake.py`, `backend/app/agent/orchestrator.py` (`cancel_task`)
- Test: `backend/tests/test_calendar_sync.py`

**Interfaces:**
- Consumes: `build_event`, `event_id_for` (Task 3), 캘린더 메서드와 오류 (Task 2), `access_token_for` (`app.google.oauth`)
- Produces:
  - `mark_calendar_dirty(task: Task) -> None`
    - 메일 업무이고, `calendar_state`가 `deleted_by_user`가 아니며, 기한이 있거나 이미 일정이 있으면 `pending`으로 바꾼다.
    - 커밋은 호출하는 쪽이 한다.
  - `sync_calendars(db, account: GoogleAccount, client: GoogleClient, *, max_tasks: int, tz: str, public_url: str) -> CalendarSyncResult`
    - `@dataclass CalendarSyncResult: created: int; updated: int; deleted_by_user: int; errors: int; skipped: int`

**대상 업무:** 이 사용자의 `origin="mail"` 업무 중 다음 하나에 해당하는 것을 오래된 순으로 최대 `max_tasks`개 처리한다.
- `calendar_state == "pending"`
- `calendar_state == "none"`이고 기한이 있는 것 (3단계에서 이미 만들어진 업무를 별도 이전 작업 없이 처리하기 위해서다)

**업무 1개 처리**

1. 접근 토큰을 받는다. `GoogleAuthError`면 이번 호출 전체를 멈추고 상태는 그대로 둔다.
2. **일정 번호가 없는 경우** (`calendar_event_id`가 없음) (→ 변경 #1)
   - 기한이 없으면 `none`으로 두고 끝낸다.
   - `calendar_insert(build_event(...))`를 호출한다.
   - **성공:** `calendar_event_id`, `calendar_event_url`을 저장하고 `synced`, `attempts=0`, `error=""`로 둔다.
   - **`GoogleConflict`** (같은 ID 일정이 이미 있음)
     - `calendar_get`을 호출한다. `status=="cancelled"`면 `deleted_by_user`로 둔다 `[RF 1]`. (→ 변경 #2)
     - 아니면 ID와 링크를 저장하고 수정 경로(3번)로 이어간다 `[RF 2]`.
3. **일정 번호가 있는 경우**
   - 기한이 없어졌으면 이 단계에서는 일정을 건드리지 않고 `synced`로 둔다. 기한 삭제는 6단계 범위다.
   - 그 밖에는 `calendar_patch(id 없는 본문)`을 호출한다. (→ 변경 #3)
   - **`GoogleNotFound`** 또는 응답의 `status=="cancelled"` → `deleted_by_user`로 둔다 `[RF 1]`.
   - **성공** → `synced`
4. **오류**
   - `GoogleTransientError`: `attempts`를 1 올린다. 5 미만이면 `pending`을 유지하고, 5 이상이면 `error`로 바꾸고 "Google 캘린더 연결이 불안정해 일정을 반영하지 못했습니다."를 남긴다.
   - 그 밖의 `GoogleError`: `error`로 바꾸고 "Google 캘린더에 일정을 반영하지 못했습니다."를 남긴다.
   - 업무 1개마다 커밋한다. 예외는 밖으로 내보내지 않는다 `[RF 5]`.
5. **타임라인:** 상태가 바뀔 때만 남긴다.
   - `CALENDAR_SYNCED`: `{"task_id", "state", "created": bool}`
   - `CALENDAR_DELETED_BY_USER`
   - `CALENDAR_ERROR`

**연결 지점**
- `intake._apply`: 기한을 정하거나 바꾼 뒤 `mark_calendar_dirty(task)`를 호출한다.
- `orchestrator.cancel_task`: 메일 업무를 취소하면 `mark_calendar_dirty(task)`를 호출한다. 일정이 있으면 `✓`가 붙는다 `[RF 4]`.

- [ ] **Step 1: 실패하는 테스트** (FakeGoogle + `google_fake` 픽스처, 업무와 계정은 직접 생성)

```python
def test_creates_event_for_deadline_task(): ...      # none + 기한 → insert 1회, synced, id/url 저장, 타임라인 CALENDAR_SYNCED
def test_no_deadline_no_event(): ...                 # 기한 없는 업무 → 호출 0, none 유지
def test_pending_updates_existing(): ...             # synced 업무 기한 변경 → mark dirty → patch 1회, 새 날짜 반영
def test_user_deleted_event_not_recreated(): ...     # 일정 생성 → fake.delete_event → 기한 변경 → deleted_by_user, insert 추가 호출 없음;
                                                     #   다시 dirty 표시해도 pending으로 안 바뀜                                     [RF 1]
def test_crash_after_insert_no_duplicate(): ...      # fake에 같은 ID 일정이 이미 있음, 업무에는 id 없음 → Conflict → get → patch, 일정 1개   [RF 2]
def test_cancel_marks_check(): ...                   # synced 업무 cancel_task → 다음 sync에서 제목이 "✓ "로 시작                        [RF 4]
def test_cancel_after_user_deleted_untouched(): ...  # deleted_by_user 업무 취소 → 호출 0
def test_transient_retries_then_error(): ...         # calendar_fail=Transient 5번 → 4번째까지 pending, 5번째 error + 문구
def test_other_error_immediate(): ...                # GoogleError → error 즉시
def test_auth_error_stops_quietly(): ...             # refresh 실패 → 상태 그대로, 예외 없음
def test_max_tasks(): ...                            # 12개, max 10 → 10개만 처리
def test_logs_have_no_content(): ...                 # caplog에 요약 · 상대 이름 없음
def test_intake_marks_dirty(): ...                   # 접수로 기한이 생긴 업무 → calendar_state pending
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과**
- [ ] **Step 5: 커밋** — `캘린더: 업무 기한을 Google 캘린더 일정으로 동기화`

---

### Task 5: 워커 연결, 다시 시도 API, 업무 상세 표시

**Files:**
- Modify: `backend/app/mail/sync.py`, `backend/app/api/tasks.py`, `backend/app/security.py`, `frontend/src/pages/TaskDetail.tsx`, `frontend/src/types.ts`, `frontend/src/styles.css`
- Test: `backend/tests/test_calendar_pipeline.py`

**Interfaces:**
- Produces:
  - `run_sync_cycle`: 계정마다 `process_inbox` 다음에 `sync_calendars`를 같은 임대 안에서 호출한다.
    - 판별 스위치와 무관하게 실행한다. 예외는 계정 단위로 격리하고 타입만 로그에 남긴다.
    - `process=False`("지금 확인")일 때는 호출하지 않는다.
  - `POST /api/tasks/{id}/calendar/retry`
    - 로그인 필수. 본인 업무가 아니면 404. 메일 업무가 아니면 409.
    - `deleted_by_user`면 409와 "캘린더에서 삭제된 일정은 다시 만들지 않습니다."를 돌려준다.
    - 그 밖에는 `attempts=0`, `pending`으로 바꾸고 `wake()`를 호출한 뒤 업무 상세를 돌려준다.
    - 호출 제한: `calendar_retry_limiter = RateLimiter(5, 60)`, 키 `cal:{uid}`. conftest에서 초기화한다.
- **업무 상세 화면:** 메일 업무이고 `calendar_state`가 `none`이 아닐 때 요약 영역 아래에 한 줄을 보여준다.

  | 상태 | 표시 |
  |---|---|
  | `synced` | "캘린더에 등록됨" + "캘린더에서 열기" 링크(`calendar_event_url`, 새 탭) |
  | `pending` | "캘린더 반영 대기 중" |
  | `deleted_by_user` | "캘린더에서 삭제된 일정입니다 (다시 만들지 않음)" |
  | `error` | 빨간 문구(`calendar_error`) + "다시 시도" 버튼 |

- [ ] **Step 1: 실패하는 테스트**

```python
def test_cycle_syncs_calendar_after_intake(): ...   # 스위치 on, 기한 있는 업무 메일 → 한 주기 후 일정 1개, synced
def test_calendar_runs_when_triage_off(): ...       # 스위치 off, 기한 있는 업무 직접 생성 → 일정 생성됨, LLM 호출 0
def test_sync_now_skips_calendar(client): ...       # POST /api/google/sync → calendar 호출 0
def test_calendar_error_does_not_block_mail(): ...  # calendar_fail=GoogleError → 메일 저장 · 접수는 정상, 업무 error         [RF 5]
def test_retry_endpoint(client): ...                # error 업무 → retry → pending, wake 호출; 다른 사용자 404; deleted_by_user 409; 6번째 429
```

- [ ] **Step 2 ~ 4: 실패 확인 → 구현 → 통과.** 프론트는 `npm run build && npm run lint`(기존 경고 1개 허용)로 확인한다.
- [ ] **Step 5: 커밋** — `캘린더: 워커 연결, 다시 시도 API, 업무 상세 캘린더 표시`

---

### Task 6: 실제 계정 확인과 문서

사용자와 함께 진행한다. 실제 캘린더에 일정이 생기므로 시작 전에 사용자에게 알린다.

- [ ] **Step 1: 준비.** 서버를 재시작하고 `alembic upgrade head`를 확인한다. 3단계에서 만들어진 기한 있는 업무(#31, #32)가 첫 주기에 캘린더에 생기는지 본다.
- [ ] **Step 2: 점검 목록**
  - [ ] Google 캘린더 10/30에 `[업무] …` 하루 종일 일정 2개가 생기고, 알림이 전날 오전 9시로 되어 있다.
  - [ ] 일정 설명에 할 일과 앱 링크가 있고, 링크를 누르면 해당 업무가 열린다.
  - [ ] 업무 상세에 "캘린더에 등록됨"과 "캘린더에서 열기"가 보인다.
  - [ ] 사용자가 캘린더에서 일정 하나를 지운 뒤 그 업무를 취소하면, 일정이 다시 생기지 않고 업무 상세에 "삭제된 일정"이 보인다.
  - [ ] 다른 업무를 취소하면 일정 제목이 `✓ [업무] …`로 바뀐다.
  - [ ] 새 업무 메일("다음 주 화요일 오후 3시까지 …")은 시각이 있는 일정과 하루 전 · 1시간 전 알림으로 생긴다.
- [ ] **Step 3: 문제 수정 → 테스트 추가 → 재확인**
- [ ] **Step 4: 문서**
  - `CHANGELOG.md`
  - `docs/privacy.md`: 캘린더로 나가는 정보(요약, 상대 이름, 할 일, 앱 링크)
  - `docs/troubleshooting.md`: 캘린더 관련 항목
  - 계획 대비 변경 이력
- [ ] **Step 5: 브랜치 push → CI 통과 → `main` 병합 · push**

---

## 계획 대비 변경 이력

| # | 날짜 | 작업 | 계획 | 실제 구현 | 이유 | 영향 |
|---|---|---|---|---|---|---|
| 1 | 2026-10-26 | Task 4 | 일정 번호가 없으면 기한이 있는 업무는 상태와 관계없이 일정을 만든다 | 완료 · 취소된 업무는 일정이 없으면 새로 만들지 않고 `none`으로 둔다. `none` 상태의 닫힌 업무는 동기화 대상에서도 뺀다 | 끝난 업무의 일정을 새로 만들면 캘린더만 어지럽힌다. 대상에 남기면 매 주기 한도(`max_tasks`)를 차지한다 (Task 4 중 controller 판정) | 이미 일정이 있는 업무는 그대로 ✓가 붙는다. 일정 없이 닫힌 업무는 캘린더에 나타나지 않는다 |
| 2 | 2026-10-26 | 전체 리뷰 | 일정 번호 = `"fuag"` + 사용자 번호 + 업무 번호. 409면 조회해서 `cancelled`는 사용자 삭제, 아니면 이어 쓴다 | `GoogleAccount.calendar_id_salt`(base32hex 8자, 첫 동기화 때 `secrets`로 만들어 저장, 마이그레이션 `f7a2b5c9d3e6`)를 두고 일정 번호 = `"fu"` + salt + 업무 번호(16진 8자리). 본문에 `extendedProperties.private.followupTask = "salt:업무번호"` 표식을 붙인다. 409 때 표식이 이 업무 것일 때만 사용자 삭제 / 이어 쓰기로 처리하고, 표식이 없거나 다르면 고치지 않고 `error`("같은 번호의 다른 캘린더 일정이 있어 반영하지 못했습니다.") | dev.db를 새로 만들거나 다른 인스턴스가 같은 Google 계정을 연결하면 업무 #N이 예전 DB의 일정과 번호가 겹쳐, 남의 일정을 덮어쓰거나 사용자 삭제로 오판해 영영 반영되지 않았다 | `event_id_for(task, salt)`, `build_event(..., salt)`로 시그니처가 바뀌었다. 재연결은 salt를 유지하고, 연결 해제는 행과 함께 지운다(이미 저장된 `calendar_event_id`로 계속 고치므로 문제없다) |
| 3 | 2026-10-26 | 전체 리뷰 | `calendar_patch`에 id만 뺀 본문을 그대로 보낸다 | 시간 일정이면 start/end에 `"date": null`, 종일 일정이면 `"dateTime": null, "timeZone": null`을 함께 보낸다. `FakeGoogle.calendar_patch`도 Google처럼 중첩 객체를 합치고 `None`은 필드 삭제로 처리하며, date와 dateTime이 함께 남으면 400을 낸다 | Google PATCH는 중첩 객체를 합치므로 종일 ↔ 시간 전환 때 두 필드가 함께 남아 400이 났다. 기존 가짜는 얕은 update라 이를 잡지 못했다 | 기한의 종일 / 시간 여부가 바뀌어도 일정이 고쳐진다 |
| 4 | 2026-10-26 | 실계정 확인 (사용자 결정) | 일정 공개 범위는 캘린더 기본값 | 일정 본문에 `"visibility": "private"` | 캘린더를 공유하면 상대 이름 · 요청 · 기한 근거 문장이 공유받은 사람에게 보일 수 있음. 사용자가 비공개로 결정 | 공유받은 사람에게는 "바쁨"으로만 보임. 기존 일정은 다음 수정 때 적용 |

## 이 계획 다음

- **5단계 (받은 업무 화면):** 요약 카드에 기한, D-day, "캘린더에서 열기", 기한 수정을 넣는다. 기한을 수정하면 `mark_calendar_dirty`를 호출한다.
- **6단계 (답장 · 수명주기):** 답장 후 자동 완료 시 ✓ 처리를 한다(`mark_calendar_dirty` 재사용). 재답장으로 기한이 바뀌면 일정을 갱신하고, 기한이 없어지면 일정을 처리한다.
