# 6단계 답장과 수명주기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 준 내용으로 답장 초안을 만들어 승인 후 Gmail 스레드에 보내고, 보낸 답장 감지 · 상대 답장 없음 확인 · 재답장 변경 반영 · 다른 스레드 수정 요청 판단 · 기한 삭제 시 일정 삭제를 붙이고 정확도를 잰다.

**Architecture:** 새 로직은 `backend/app/mail/` 아래 기능별 모듈(`lifecycle.py`, `reply_draft.py`, `reply_send.py`, `changes.py`, `related.py`)에 두고, 동기화 주기(`sync.py` → `process_inbox`)와 업무 API(`api/tasks.py`)가 부른다. LLM 호출은 기존 `LLMClient.structured()` 1회 방식만 쓴다. 화면은 기존 업무 상세 부품(`components/mail/*`)을 넓힌다.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic v2(extra=forbid), React 19 + TypeScript, 가짜 구현 `ScriptedLLM` · `FakeGoogle`

**Spec:** `docs/superpowers/specs/2026-10-27-reply-and-lifecycle-design.md` (설계가 최우선. 절 번호는 이 문서 기준)

## Global Constraints

- 모든 조회는 `user_id`로 거른다. 남의 데이터는 404.
- 상태를 바꾸는 API는 POST, `get_current_user`, LLM · Google을 부르는 API는 `RateLimiter`.
- 실제 메일을 LLM에 보내는 기능(초안 · 변경 분석 · 관련 업무 판단)은 `MAIL_TRIAGE_ENABLED`가 꺼져 있으면 동작하지 않는다.
- 메일 본문은 `wrap_untrusted()`로 감싸 LLM에 보낸다. 로그(`logging`, `AgentActionLog`)에는 본문 · 제목 · 주소 · 초안 본문을 남기지 않는다. 타임라인은 한 줄 요약(예: "기한 10/30 → 10/31")까지.
- 받는 사람은 서버가 정한다(설계 3.1). LLM 출력의 주소는 받는 사람 · 참조에 절대 쓰지 않는다.
- 빈칸 표기는 `[[ … ]]` (정규식 `\[\[[^\[\]]{1,80}\]\]`). 빈칸이 있으면 발송 409.
- 영업일 = 사용자 시간대(`USER_TIMEZONE`) 월~금. 공휴일 없음. 상대 답장 없음 기준 3영업일.
- 발송 결과 불분명 → `unknown`, 5분 뒤 확인 버튼(설계 3.2).
- 무한 재시도 없음. 형식 오류는 1회 재시도.
- JSON 컬럼은 새 객체를 대입한다. 시간은 UTC 저장.
- 프론트는 `window.confirm/alert/prompt` 금지. 확인 단계는 카드 안에.
- 사용자에게 보이는 문구는 설계 문서의 문구를 그대로 쓴다.

## Review Focus

1. 앱에서 보낸 답장이 다음 동기화에서 다시 들어올 때 중복 저장 · 상태 재변경 없이 `sent` 확인으로만 쓰인다 → Task 4 `test_app_sent_mail_not_duplicated`.
2. 스레드 메일 여러 통이 한 주기에 오면 변경 분석은 업무당 1회 → Task 7 `test_batch_one_call_per_task`.
3. 상대 답장 없음 확인이 금요일 오후 발송 → 다음 주 수요일 같은 시각에만 뜬다(시간대 경계 포함) → Task 3 `test_business_days_friday_evening_kst`.
4. 초안 카드에서 사용자가 빈칸을 지운 뒤에도 `[[`만 남은 깨진 표기(`[[납기`)는 빈칸이 아니다 → 발송 허용. 정확한 `[[x]]`만 막는다 → Task 5 `test_placeholder_regex_edges`.
5. 되돌리기(10분) 뒤 늦게 도착한 합치기 분석 결과는 버려진다 → Task 8 `test_merge_analysis_after_undo_dropped`.

---

### Task 1: 데이터 모델 · 마이그레이션 · Google 클라이언트 확장

**Files:**
- Modify: `backend/app/models.py` (Task 컬럼, 새 모델 `ReplyDraft`)
- Create: `backend/alembic/versions/b9c4d7e2f5a8_reply_and_lifecycle.py` (down `a8b3c6d1e4f7`, SQLite `batch_alter_table`)
- Modify: `backend/app/google/client.py` (`gmail_send`, `calendar_delete` 프로토콜 + httpx 구현)
- Modify: `backend/tests/fake_google.py` (두 메서드 가짜 구현, 보낸 메일은 `mailbox`에 `SENT` 라벨로 추가하고 history에 기록)
- Test: `backend/tests/test_reply_models.py`, `backend/tests/test_google_client.py`(기존 파일에 추가)

**Interfaces:**
- Produces (Task 컬럼, 모두 nullable/기본값):
  - `reply_sent_at: datetime | None`, `wait_from: datetime | None`(상대 답장 없음 계산 기준. 보낼 때 · "더 기다리기" 때 now), `no_reply_asked_at: datetime | None`
  - `change_log: list` JSON 기본 `[]` (Task 7 형식), `changed_unseen: bool` 기본 False
  - `related: dict` JSON 기본 `{}` (Task 8 형식), `merge_analysis: dict` JSON 기본 `{}` (Task 8 형식)
- Produces `ReplyDraft` 테이블 `reply_drafts`: `id, user_id(FK CASCADE), task_id(FK CASCADE, unique), purpose('reply'|'followup'), to_addr str(255), cc_candidates JSON list[str], cc_selected JSON list[str], subject str(300), body Text, status('draft'|'sending'|'sent'|'unknown'), warnings JSON dict, user_input Text, gmail_message_id str(64)|None, sending_at datetime|None, created_at, updated_at`
- Produces `GoogleClient.gmail_send(access_token: str, raw_rfc822: bytes, thread_id: str) -> SentMessage` (`@dataclass(frozen=True) SentMessage(id: str, thread_id: str)`), `GoogleClient.calendar_delete(access_token: str, event_id: str) -> None` (404/410 → `GoogleNotFound`)

- [ ] **Step 1: 실패하는 테스트**
  - `test_reply_draft_unique_per_task`: 같은 task_id로 두 번째 `ReplyDraft` 커밋 → `IntegrityError`
  - `test_task_new_columns_defaults`: 새 Task → `change_log == []`, `related == {}`, `changed_unseen is False`, `reply_sent_at is None`
  - `test_fake_gmail_send_adds_sent_message`: `FakeGoogle.gmail_send(tok, raw, "thr-1")` → 반환 id가 `mailbox`에 있고 `label_ids == ["SENT"]`, history에 기록
  - `test_httpx_gmail_send_posts_raw_and_thread` (httpx MockTransport): POST `.../messages/send` 본문 `{"raw": base64url, "threadId": "thr-1"}`
  - `test_httpx_calendar_delete_404_raises_not_found`
- [ ] **Step 2: 실행해서 실패 확인** — `cd backend && ../.venv/Scripts/python -m pytest -q tests/test_reply_models.py tests/test_google_client.py`
- [ ] **Step 3: 구현** (모델, 마이그레이션, 클라이언트, 가짜). 로컬 DB는 백업 후 `alembic upgrade head`(SQLite backup API로 `dev.db.bak-<날짜>`).
- [ ] **Step 4: 전체 테스트 통과** — `cd backend && ../.venv/Scripts/python -m pytest -q`
- [ ] **Step 5: 커밋** — `답장: 데이터 모델 · 마이그레이션, Gmail 발송 · 캘린더 삭제 클라이언트`

### Task 2: 기한을 없애면 앱이 만든 캘린더 일정 지우기 (설계 8절)

**Files:**
- Modify: `backend/app/mail/calendar_sync.py` (`_process`의 `deadline_at is None` 분기)
- Modify: `frontend/src/components/mail/SummaryCard.tsx` (`calendarKept` 안내 제거)
- Test: `backend/tests/test_calendar_sync.py`(기존 파일에 추가)

**Interfaces:**
- Consumes: `GoogleClient.calendar_delete` (Task 1)
- Produces: 기한 없는 업무의 동기화 결과 문자열 `"deleted"`; `CalendarSyncResult`에 `removed: int` 추가 (→ 변경 #2)

- [ ] **Step 1: 실패하는 테스트**
  - `test_clear_deadline_deletes_own_event`: synced 일정 있는 업무의 기한 제거 → 동기화 → FakeGoogle 일정 사라짐, `calendar_state == "none"`, `calendar_event_id is None`, 타임라인 `CALENDAR_REMOVED`
  - `test_clear_deadline_foreign_event_not_deleted`: 일정의 `private` 표식이 다른 업무/없음 → 삭제 호출 없음, `calendar_state == "none"`, 일정 번호 비움
  - `test_clear_deadline_already_gone`: get이 404 → 조용히 `none`
  - `test_closed_task_still_gets_check_mark`: 닫힌 업무는 지우지 않고 ✓ (기존 동작 유지)
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_calendar_sync.py`
- [ ] **Step 3: 구현** — 삭제 전 `calendar_get`으로 표식(`followupTask` == 업무 번호) 확인 후 `calendar_delete`. 프론트 안내 문구 · `calendarKept` 상태 제거. (→ 변경 #2)
- [ ] **Step 4: 통과** — 전체 테스트, `cd frontend && npm run build && npm run lint`
- [ ] **Step 5: 커밋** — `캘린더: 기한을 없애면 앱이 만든 일정을 지운다`

### Task 3: 수명주기 공통 · 상대 답장 없음 (설계 5절)

**Files:**
- Create: `backend/app/mail/lifecycle.py`
- Modify: `backend/app/mail/sync.py` (`run_sync_cycle`에서 사용자마다 `process_inbox` 다음에 `check_no_reply` 호출. `process_inbox`는 스위치가 꺼지면 바로 돌아가므로 그 밖에서 부른다 — LLM 없음)
- Modify: `backend/app/mail/inbox.py` (`alerts_for`에 `no-reply` 알림, 카드 상황 표시용 `no_reply_days`) (→ 변경 #4)
- Modify: `backend/app/api/tasks.py` (`POST /api/tasks/{id}/no-reply` body `{"choice": "followup"|"wait"|"close"}`), `MailExtras`에 `no_reply: bool`, `reply_sent_at`
- Test: `backend/tests/test_lifecycle.py`

**Interfaces:**
- Produces in `lifecycle.py`:
  - `NO_REPLY_BUSINESS_DAYS = 3`
  - `add_business_days(start: datetime, days: int, tz: str) -> datetime` — 같은 지역 시각을 유지하며 토 · 일을 건너뛴다. 시작이 주말이면 다음 월요일 00:00(지역)부터 센다.
  - `mark_reply_sent(db, task: Task, email: Email | None, now: datetime, *, source: str) -> None` — `status=WAITING`, `waiting_for="상대 회신"`, `current_step="답장 보냄"`, `reply_sent_at=now`, `wait_from=now`, `no_reply_asked_at=None`, 스누즈 해제, 타임라인 `REPLY_SENT`(source: "app"|"gmail", 수신자 수만). 닫힌 · 합쳐진 업무는 아무것도 안 함. 커밋 안 함.
  - `check_no_reply(db, user_id: int, now: datetime, tz: str) -> int` — `WAITING`이고 `wait_from` 뒤 상대 메일이 없고 `add_business_days(wait_from, 3) <= now`인 메일 업무를 `ACTION_REQUIRED`, `current_step="상대 답장 없음 — 다시 연락할까요?"`, `no_reply_asked_at=now`로. 바꾼 수 반환. 커밋함. (→ 변경 #4)
  - `answer_no_reply(db, user, task, choice: str, now) -> None` — `wait`: `WAITING`, `wait_from=now`, `no_reply_asked_at=None`; `close`: `COMPLETED`(이유 "상대 답장 없음으로 닫음"); `followup`: 상태 그대로(초안은 Task 5 API가 만든다). `no_reply_asked_at`이 없으면 409 `ActionError`.
  - `clear_wait_on_inbound(task: Task) -> None` — 상대 메일이 오면 `no_reply_asked_at=None`(Task 7이 부름)
- Produces alert kind `"no-reply"`, key suffix = 보낸 답장 기준 시각 iso, 문구 `"[답 없음] {요약}"`. 카드 상황 표시 문구 `"보낸 지 영업일 3일째 답이 없습니다"`.

- [ ] **Step 1: 실패하는 테스트**
  - `test_business_days_friday_evening_kst`: KST 금 17:00 + 3 → KST 수 17:00
  - `test_business_days_from_saturday`: KST 토 10:00 + 3 → KST 수 00:00 (→ 변경 #3)
  - `test_check_no_reply_moves_after_3_days`: 보낸 지 2영업일 → 그대로, 3영업일 → `ACTION_REQUIRED` · `no_reply_asked_at` 설정, 알림 목록에 `no-reply`
  - `test_check_no_reply_ignores_when_inbound_after`: `wait_from` 뒤 상대 메일 있음 → 안 바뀜
  - `test_answer_wait_restarts_clock` / `test_answer_close_completes` / `test_answer_without_question_409`
  - `test_mark_reply_sent_ends_nudge`: 답장 넛지(`nudge_days`) 있던 업무 → `mark_reply_sent` 뒤 `tab_of == "sent"`, 넛지 None
  - API: `test_no_reply_api_validates_choice` (알 수 없는 값 422, 남의 업무 404)
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_lifecycle.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `수명주기: 보낸 답장 처리 공통, 상대 답장 없음 확인(영업일 3일)`

### Task 4: Gmail에서 보낸 답장 감지 (설계 4절)

**Files:**
- Modify: `backend/app/mail/sync.py` (`SENT` 메일: 업무가 있는 스레드면 `OUTBOUND`로 저장, 아니면 지금처럼 건너뜀)
- Modify: `backend/app/mail/lifecycle.py` (`record_sent_mail`)
- Test: `backend/tests/test_sent_mail_sync.py`

**Interfaces:**
- Consumes: `mark_reply_sent` (Task 3); `ReplyDraft` (Task 1)
- Produces `record_sent_mail(db, user_id: int, email: Email, now: datetime) -> None` — 스레드 업무(`resolve_merged` 따라감)에 붙이고(`processed=True`, `triage="sent"`), 열린 업무면 `mark_reply_sent(source="gmail")`. 그 업무의 `ReplyDraft`가 `sending`/`unknown`이고 `gmail_message_id`가 같거나(앱 발송) 제목 · 받는 사람이 같고 `sending_at` 뒤 10분 안이면 `status="sent"`, `gmail_message_id` 기록. (→ 변경 #7, #8)
- 동기화: `SENT` 라벨 메일은 스레드 업무가 없으면 지금처럼 `skipped_sent`. 저장할 때 `direction=OUTBOUND`, `sender`=내 주소. (→ 변경 #5, #6)

- [ ] **Step 1: 실패하는 테스트**
  - `test_sent_in_task_thread_marks_waiting`: 처리할 일 업무 스레드에 SENT 메일 → `OUTBOUND` 저장, `WAITING`, `reply_sent_at` 설정
  - `test_sent_outside_task_thread_not_stored`: 업무 없는 스레드의 SENT → 저장 안 됨
  - `test_app_sent_mail_not_duplicated`: 앱 발송 메일(이미 `OUTBOUND`, 같은 `gmail_message_id`)이 동기화로 다시 옴 → 행 수 그대로, 타임라인 `REPLY_SENT` 1개
  - `test_unknown_draft_resolved_by_sync`: 초안 `unknown` + 같은 스레드 SENT(제목 · 받는 사람 일치) → 초안 `sent`
  - `test_sent_on_closed_task_keeps_status`
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `동기화: 업무 스레드의 보낸 메일을 저장하고 답장 보냄으로`

### Task 5: 답장 초안 (설계 2절, 3.1절)

**Files:**
- Create: `backend/app/mail/reply_draft.py`
- Modify: `backend/app/api/tasks.py` (초안 API), `backend/app/schemas.py` (`ReplyDraftOut`, 요청 스키마), `TaskDetail.mail`에 `draft: ReplyDraftOut | None`
- Test: `backend/tests/test_reply_draft.py`

**Interfaces:**
- Produces in `reply_draft.py`:
  - `PLACEHOLDER_RE = re.compile(r"\[\[[^\[\]]{1,80}\]\]")`, `placeholders(text: str) -> list[str]`
  - `reply_recipients(db, task: Task, my_address: str) -> tuple[str, list[str]]` — (받는 사람, 참조 후보). 받는 사람 = 이 업무 `INBOUND` 중 가장 최근(`received_at`, id)의 `sender`. 참조 후보 = 그 메일의 `receiver`(쉼표 분리) · `cc` + 처음 요청자(가장 오래된 INBOUND `sender`), 내 주소 · 받는 사람 제외, 소문자 중복 제거, 최대 10개. (→ 변경 #9)
  - `DRAFT_SCHEMA` (`subject`, `body`, `used_facts: [{text, source: "memo"|"input"|"mail"}]`, `suspicious: [str]`) (→ 변경 #10)
  - `generate_draft(db, task: Task, user: User, llm: LLMClient, *, purpose: str, user_input: str, tz: str) -> ReplyDraft` — 프롬프트는 이 업무 메일(최근 것부터, 합계 12,000자 상한, `wrap_untrusted`) · 요약 · 요청 · 할 일(제목 · 완료 · 메모) · 사용자 입력(2,000자 상한) · 목적. 형식 오류 1회 재시도 뒤 `ActionError("초안을 만들지 못했습니다. 다시 시도해 주세요.", 502)`. 기존 초안이 `sending`/`unknown`이면 409. 그 밖엔 대체(upsert). 커밋 안 함. (→ 변경 #12)
  - `draft_warnings(draft_body: str, sources: str, thread_text: str) -> dict` — `{"placeholders": n, "unsupported": [숫자 · 날짜 토큰], "outside_contacts": [이메일 · 전화], "suspicious": [...]}`. 숫자 토큰 = `\d[\d,.:/-]*\d|\d`, 쉼표 제거 후 `sources`(메모 · 입력 · 스레드 본문 합친 글)에 없으면 unsupported. 이메일/전화가 스레드 글에 없으면 outside. (→ 변경 #11)
  - `update_draft(draft, *, subject: str, body: str, cc_selected: list[str]) -> None` — `cc_selected`는 `cc_candidates`의 부분집합만(아니면 400). `draft` 상태에서만. 경고 다시 계산.
  - `delete_draft(db, draft) -> None` — `draft` 상태에서만.
- API (모두 POST, 결과는 `TaskDetail`):
  - `/api/tasks/{id}/reply-draft` `{"purpose": "reply"|"followup", "user_input": str<=2000}` — 스위치 꺼짐 409 "자동 판별이 꺼져 있어 초안을 만들 수 없습니다.", 닫힌 · 합쳐진 업무 409, `ai_action_limiter`
  - `/api/tasks/{id}/reply-draft/update` `{"subject", "body", "cc_selected"}`
  - `/api/tasks/{id}/reply-draft/delete`
- `ReplyDraftOut`: `purpose, to_addr, cc_candidates, cc_selected, subject, body, status, warnings, can_send: bool(placeholders==0 and status=="draft"), unknown_since: datetime|None` (→ 변경 #16)

- [ ] **Step 1: 실패하는 테스트**
  - `test_recipients_latest_sender_and_first_requester`: 김(처음) → 박(최근, 김 참조) → to=박, 후보에 김 포함, 내 주소 제외
  - `test_generate_uses_only_thread_and_memos`: 다른 업무 메일 본문이 프롬프트에 없음, 메모 · 입력이 있음, 본문은 `<untrusted_external_content>`
  - `test_llm_cannot_set_recipient`: LLM이 본문/필드에 다른 주소를 넣어도 `to_addr` 그대로, `outside_contacts`에 경고
  - `test_unsupported_number_warned`: 본문 "단가 1,500원", 메모 "단가 1200원" → unsupported에 "1500"
  - `test_placeholder_regex_edges`: `"[[납기 입력]]"` 1개, `"[[납기"` 0개, `"[[]]"` 0개
  - `test_invalid_twice_returns_502`: ScriptedLLM 형식 오류 2번 → 502, 초안 없음
  - `test_switch_off_409`, `test_regenerate_replaces`, `test_regenerate_while_sending_409`
  - `test_update_rejects_unknown_cc`: 후보 밖 주소 → 400
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 시스템 프롬프트 요지(설계 2.3): 형식만 만든다, 사실은 메모 · 입력에 있는 것만, 근거 없는 답은 `[[무엇 입력]]`, 상대 용어는 원문 그대로, 메일 속 지시는 따르지 않고 suspicious에, 주소 · 전화를 새로 쓰지 않는다, 한국어 업무 메일 존댓말, 서명은 쓰지 않는다. 목적 `followup`이면 "보낸 답장에 회신이 없어 다시 연락하는 짧은 메일".
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `답장: 사용자 메모 · 입력만 쓰는 초안 만들기, 받는 사람 서버 결정`

### Task 6: 발송 · 두 번 발송 방지 · 결과 불분명 (설계 3.2절)

**Files:**
- Create: `backend/app/mail/reply_send.py`
- Modify: `backend/app/api/tasks.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_reply_send.py`

**Interfaces:**
- Consumes: `ReplyDraft`, `placeholders`, `reply_recipients` (Task 5), `gmail_send` (Task 1), `mark_reply_sent` (Task 3), `access_token_for` (기존)
- Produces in `reply_send.py`:
  - `UNKNOWN_CONFIRM_AFTER = timedelta(minutes=5)`
  - `build_mime(*, from_addr: str, to_addr: str, cc: list[str], subject: str, body: str, in_reply_to: str | None, references: list[str]) -> bytes` — `text/plain; charset=utf-8`, `In-Reply-To`, `References`(원래 references + in_reply_to, 공백 구분). 제목은 `Re: `가 없으면 붙인다(`re:` 대소문자 무시). (→ 변경 #15)
  - `send_draft(db, user, task, draft, client, now) -> None` — 다시 확인(설계 3.2): `to_addr == reply_recipients(...)[0]`(달라졌으면 409 "받는 사람이 바뀌었습니다. 초안을 다시 만들어 주세요."), `cc_selected ⊆ 후보`, 빈칸 0, Google 연결. `UPDATE reply_drafts SET status='sending', sending_at=now WHERE id=? AND status='draft'` 한 줄 갱신으로 선점(영향 행 0이면 409 "이미 보내는 중이거나 보낸 초안입니다."). 커밋 후 Gmail 호출. (→ 변경 #16)
    - 성공: `OUTBOUND` 메일 저장(`gmail_message_id`, `gmail_thread_id`, `rfc_message_id`는 비움), 초안 `sent`, `mark_reply_sent(source="app")`, 커밋. (→ 변경 #15)
    - `GoogleTransientError`/타임아웃: `unknown`. `GoogleAuthError`/그 밖 `GoogleError`(응답이 확실한 거절): `draft`로 되돌리고 409 이유("Google 연결을 다시 해 주세요." 등). (→ 변경 #14)
  - `confirm_unknown(db, user, task, draft, *, sent: bool, now) -> None` — `unknown`이고 `sending_at + 5분 <= now`일 때만(아니면 409). `sent=True` → `sent` + `mark_reply_sent(source="app")`, `False` → `draft`. (→ 변경 #13, #17)
  - `complete_remaining_todos(task) -> int` — 남은 할 일 모두 `done=True`, 바꾼 수.
- API (POST, 결과 `TaskDetail`): `/reply-draft/send`, `/reply-draft/confirm` `{"sent": bool}`, `/todos/complete-all`. 발송은 `ai_action_limiter`와 별도의 `send_limiter`(분당 5회).
- `MailExtras`: `remaining_todos_prompt: bool` (WAITING이고 `reply_sent_at` 뒤 남은 할 일 있음), `draft.unknown_since`

- [ ] **Step 1: 실패하는 테스트**
  - `test_mime_headers`: 파싱해서 `In-Reply-To`, `References`, `Re:` 제목, 본문 utf-8 확인
  - `test_send_success_waiting`: FakeGoogle 발송 기록에 threadId · 받는 사람, 업무 `WAITING`, 초안 `sent`, OUTBOUND 메일 1개
  - `test_double_send_second_409`: 같은 초안 두 번 → 두 번째 409, FakeGoogle 발송 1번
  - `test_send_blocked_by_placeholder`, `test_send_recipient_changed_409`(초안 뒤 새 상대 메일)
  - `test_transient_error_unknown`, `test_confirm_before_5min_409`, `test_confirm_not_sent_back_to_draft`, `test_confirm_sent_marks_waiting`
  - `test_auth_error_back_to_draft_with_message`
  - `test_complete_all_todos`
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `답장: 승인 후 Gmail 스레드 발송, 두 번 발송 방지, 결과 불분명 확인`

### Task 7: 재답장으로 업무 고치기 (설계 6절)

**Files:**
- Create: `backend/app/mail/changes.py` (→ 변경 #22)
- Modify: `backend/app/mail/intake.py` (스레드 메일: 분석 완료 업무는 업무별로 묶어 `analyze_changes`; 분석 대기 · 실패 업무는 처음 분석을 스레드 전체로 다시(6.6)) (→ 변경 #19)
- Modify: `backend/app/api/tasks.py` (`/changes/{change_id}/undo`, `/changes/{change_id}/apply`), 상세를 열면 `changed_unseen=False`(GET 상세에서 끄지 않고 `POST /seen`으로, 화면이 상세를 열 때 호출)
- Test: `backend/tests/test_changes.py`

**Interfaces:**
- Consumes: `clear_wait_on_inbound` (Task 3), `normalize_deadline`, `mark_calendar_dirty`, `USER_DEADLINE_SOURCE`
- Produces in `changes.py`:
  - `CHANGES_SCHEMA`: `changes: [{field: "title"|"summary"|"requests"|"deadline"|"todo_add", before: str, after: str, evidence: str, kind: "confirmed"|"question", deadline: {text, date, time, kind}?}]`, `needs_reply: bool`, `suspicious: [str]`
  - `evidence_in(evidence: str, body: str) -> bool` — 공백 · 줄바꿈을 하나로 줄여 부분 문자열 비교. 8자 미만 근거는 거짓. (→ 변경 #20)
  - `analyze_changes(db, task: Task, emails: list[Email], llm: LLMClient, tz: str) -> bool` — 1회 호출(묶음). 성공 시 반영 후 True. `LLMError` → False(나중에), 형식 오류 → 1회 재시도 후 실패 기록(`brief.change_failed_at`) · 상태는 "처리할 일"(6.5 마지막 줄).
  - 반영 규칙(6.3): title/summary/requests 즉시; deadline은 사용자 기한이면 `status="suggested"`; todo_add는 `source="agent"` 새 번호로 추가만; question은 `status="question"`; 참고용(`PENDING`)에 할 일이 생기면 `ACTION_REQUIRED`. (→ 변경 #29, #30)
  - 상태 규칙(6.5): `needs_reply` 또는 적용된 변경/새 할 일 → `ACTION_REQUIRED`; 아니고 `WAITING`이면 그대로 + `brief.thanks_email_id = 최신 메일 id`(화면: "상대가 답장했습니다(답장 필요 없음) [완료로 닫기]"); 닫힌 업무는 상태 규칙대로 다시 연다(`REOPENED_STEP`). (→ 변경 #21)
  - `change_log` 항목: `{"id": int, "email_id": int, "sender": str, "received_at": iso, "field", "before", "after", "evidence", "status": "applied"|"suggested"|"question"|"undone", "prev": any}`(prev = 되돌릴 값). 최대 50개, 오래된 것부터 버림.
  - `undo_change(task, change_id) -> None` — applied만. 현재 값이 그 변경의 `after`와 다르면(사용자가 고침) 409 "그 뒤에 직접 고친 내용이 있어 되돌릴 수 없습니다.". todo_add 되돌리기는 그 할 일 삭제(체크됐으면 409).
  - `apply_suggestion(task, change_id, tz) -> None` — suggested deadline만 → 적용(`deadline_source`는 변경 근거), `status="applied"`.
  - 타임라인 `TASK_CHANGED`: `"기한 10/30 → 10/31"`, `"요청: 수량 100개 → 150개"` (전후 값 각 40자 자름).

- [ ] **Step 1: 실패하는 테스트**
  - `test_confirmed_change_applied_with_log`: 요청 "수량 100개" → "150개", 근거가 본문에 있음 → `brief.requests` 갱신, change_log 1개, `changed_unseen=True`, 타임라인 한 줄
  - `test_evidence_missing_dropped`: 근거가 본문에 없음 → 반영 안 됨, 로그 없음
  - `test_question_not_applied`: kind=question → 값 그대로, `status="question"`
  - `test_user_deadline_suggested_only`: `deadline_source == USER_DEADLINE_SOURCE` → 기한 그대로, suggested; `apply_suggestion` 후 바뀜 · 캘린더 dirty
  - `test_todos_only_added`: 체크된 기존 할 일 유지, 새 할 일 추가
  - `test_thanks_keeps_waiting`: WAITING + 변경 없음 · `needs_reply=false` → WAITING, `thanks_email_id` 설정, `no_reply_asked_at` None
  - `test_reference_task_promoted_on_new_todo`
  - `test_batch_one_call_per_task`: 같은 업무로 2통 + 다른 업무 1통 → `structured` 호출 2번, 첫 업무 프롬프트에 2통 모두
  - `test_pending_task_gets_full_reanalysis`: 분석 실패 업무 + 새 메일 → 접수 분석(INTAKE) 1회, 변경 분석 아님
  - `test_undo_change_and_conflict`: undo → 이전 값; 사용자가 그 뒤 고쳤으면 409
  - `test_switch_off_attaches_only`: 스위치 꺼짐 → 메일만 붙고 상태 "처리할 일" (→ 변경 #18)
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `재답장: 근거 있는 변경만 반영하고 무엇이 바뀌었는지 기록`

### Task 8: 다른 스레드 수정 요청 판단 · 합치기 연동 (설계 7절)

**Files:**
- Create: `backend/app/mail/related.py`
- Modify: `backend/app/mail/intake.py` (`INTAKE_SCHEMA`에 `related_task: int|null`, `relation`, `relation_evidence` 추가, 후보가 있을 때만 프롬프트에 후보 블록) (→ 변경 #23)
- Modify: `backend/app/mail/inbox.py` (`merge_candidates`가 `task.related` 사용)
- Modify: `backend/app/mail/merge.py` (합칠 때 `merge_analysis` 설정, 분석 결과 반영 · 되돌리기) (→ 변경 #22)
- Modify: `backend/app/mail/sync.py` (`process_inbox`에서 `run_merge_analyses` 호출)
- Test: `backend/tests/test_related.py`, `backend/tests/test_merge.py`(추가)

**Interfaces:**
- Produces in `related.py`:
  - `FREE_MAIL_DOMAINS: frozenset[str]` = {gmail.com, googlemail.com, naver.com, daum.net, hanmail.net, kakao.com, nate.com, outlook.com, hotmail.com, live.com, yahoo.com, yahoo.co.kr, icloud.com, me.com, proton.me, protonmail.com}
  - `company_domain(addr: str) -> str | None` — 소문자 도메인, 무료 메일이면 None
  - `related_candidates(db, task: Task, sender: str, now: datetime) -> list[Task]` — 설계 7.1(같은 주소 또는 같은 회사 도메인, 처리할 일 · 답장 보냄, 14일, 최대 3, 최근 것부터, 합쳐짐 · 닫힘 · 자기 자신 제외)
  - `Judgement` 함수 경계: `judge_related(data: dict, candidates: list[Task], body: str) -> dict` — 분석 응답에서 `{"task_id", "relation", "evidence"}` 또는 `{"state": "none"}`을 만든다. `relation == "none"`, 후보 밖 번호, `evidence_in` 실패 → `{"state": "none"}`. (나중에 다른 모델로 바꿀 때 이 함수만 바꾼다.)
  - `task.related` 형식: `{"state": "suggested", "task_id": int, "relation": "change"|"followup", "evidence": str}` / `{"state": "none"}` / `{"state": "unjudged"}`(분석 실패 · 스위치 꺼짐)
- `merge_candidates(db, task, now)` 규칙: `related.state == "suggested"` → 그 업무 하나(여전히 후보 조건 만족할 때). `"none"` → []. `"unjudged"`/없음 → 5단계 규칙(같은 보낸 사람).
- `MailExtras.merge_candidates` 항목에 `evidence: str | None` 추가.
- 합치기 연동(설계 7.3):
  - `merge()`는 메일 · 할 일 · 중요도만 5단계 규칙으로 옮기고, 판별이 켜져 있으면 요청 · 기한 · 요약은 옮기지 않고 `target.merge_analysis = {"state": "pending", "source_id": B, "email_ids": [...], "at": iso}`를 남긴다. 꺼져 있으면 지금 규칙(합집합 · 이른 기한) 그대로. (→ 변경 #24, #27)
  - `run_merge_analyses(db, user_id, llm, tz) -> int` — pending인 업무마다 `analyze_changes(db, target, 옮긴 메일들, ...)`. 실패(형식 오류 두 번) → 5단계 규칙으로 요청 · 기한을 합치고 `state="fallback"`. 성공 → `state="done"`. 분석 직전 업무를 다시 읽어 `merge_analysis.state != "pending"`이거나 `source_id`의 합치기 기록이 없으면(되돌려짐) 결과를 버린다. (→ 변경 #25, #26)
  - `undo_merge`: `merge_analysis`를 지우고, 그 합치기 뒤 `change_log`에서 `email_id`가 옮긴 메일인 항목을 `prev`로 되돌린 뒤 지운다.
  - 화면용 `MailExtras.merge_analyzing: bool`.

- [ ] **Step 1: 실패하는 테스트**
  - `test_company_domain`: `kim@bsangsa.co.kr` → `bsangsa.co.kr`, `x@gmail.com` → None
  - `test_candidates_same_company_not_free_mail`
  - `test_judge_suggested_with_evidence` / `test_judge_none_when_evidence_missing` / `test_judge_rejects_non_candidate_id`
  - `test_same_sender_but_llm_none_no_suggestion`: 같은 보낸 사람인데 `relation="none"` → `merge_candidates == []`
  - `test_unjudged_falls_back_to_same_sender`
  - `test_prompt_has_candidates_only_when_any`: 후보 없으면 프롬프트에 후보 블록 없음
  - `test_merge_runs_change_analysis`: B(150개) → A(100개) 합치기 → `run_merge_analyses` → A 요청 "150개"만, "100개"와 함께 남지 않음
  - `test_merge_analysis_fallback_on_failure`: 형식 오류 2번 → 합집합 · 이른 기한
  - `test_merge_analysis_after_undo_dropped`
  - `test_merge_switch_off_uses_stage5_rules`
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `합치기: 같은 회사 후보 + 내용 판단, 합친 뒤 바뀐 내용 반영`

### Task 9: 화면

**Files:**
- Create: `frontend/src/components/mail/ReplyDraftCard.tsx`, `frontend/src/components/mail/ChangesSection.tsx`
- Modify: `frontend/src/types.ts`, `components/mail/MailActions.tsx`(초안 버튼 · 입력칸), `MailBanners.tsx`(상대 답장 없음 띠 연결, 감사 답장 띠, 남은 할 일 정리 띠, 합치기 띠에 근거 · "분석 중"), `SummaryCard.tsx`(바뀐 내용 칸 자리), `TodoChecklist.tsx`(6단계 안내 문구 교체), `pages/TaskDetail.tsx`(상세 열 때 `/seen`), `components/InboxCard.tsx`(상황 표시 "내용 바뀜" · "보낸 지 영업일 3일째 답이 없습니다"), `alerts.ts`(새 kind 그대로 표시), `styles.css` (→ 변경 #28)

**Interfaces:**
- Consumes: Task 3~8의 API · `MailExtras` 필드(`draft`, `no_reply`, `remaining_todos_prompt`, `merge_analyzing`, `merge_candidates[].evidence`, `brief.thanks_email_id`, `task.change_log`, `task.changed_unseen`)
- 문구(설계 그대로):
  - 입력칸 안내: "할 일 메모는 자동으로 들어갑니다. 더 넣을 내용이 있으면 적어 주세요."
  - 남은 할 일: "아직 끝나지 않은 할 일이 N개 있습니다"
  - 빈칸: "빈칸 N개를 채워야 보낼 수 있습니다"
  - 보내기 확인: "{받는 사람}에게 보냅니다 · 참조 N명 [정말 보내기] [취소]"
  - unknown 5분 전: "발송 여부를 확인하는 중입니다" / 뒤: "Gmail 보낸편지함을 확인해 주세요" [보내진 걸 확인했어요] [안 보내졌어요]
  - 남은 할 일 정리: "남은 할 일을 완료로 정리할까요? [모두 완료] [그대로 두기]"
  - 상대 답장 없음 띠: "보낸 지 영업일 3일째 답이 없습니다" [다시 연락하기] [더 기다리기] [완료로 닫기]
  - 감사 답장: "상대가 답장했습니다(답장 필요 없음) [완료로 닫기]"
  - 관련 업무: "업무 #{n}({제목})의 수정 요청으로 보입니다 — '{근거}'"
  - 바뀐 내용: 항목 `"{before} → {after}"`, 근거, 보낸 사람 · 받은 시각, [되돌리기] / 제안: [반영하기] / 질문: "확인 필요 — {after}" (→ 변경 #28)
- [ ] **Step 1: 구현** — 바뀐 내용 칸은 가장 최근 메일의 변경만 펼치고 이전 것은 "이전 변경 N건"으로 접는다(설계 6.4). 초안 본문 편집은 textarea, `[[ ]]`는 본문 아래 "남은 빈칸" 목록으로 강조(textarea 안 하이라이트는 하지 않음). 휴대폰 너비(480px 이하)에서 버튼은 세로 배치.
- [ ] **Step 2: `cd frontend && npm run build && npm run lint`** — 경고는 App.tsx 기존 1개만
- [ ] **Step 3: 커밋** — `답장 화면: 초안 카드 · 발송 확인 · 바뀐 내용 · 상대 답장 없음 안내`

### Task 10: 정확도 측정 · 문서 · 점검 · 병합 (설계 9절)

**Files:**
- Create: `backend/smoke/test_reply_lifecycle_smoke.py`, `backend/smoke/reply_cases.py`(가상 메일 · 정답)
- Create: `docs/evals/2026-10-27-reply-lifecycle.md`(측정 결과 표)
- Modify: `docs/privacy.md`, `docs/decisions.md`, `docs/troubleshooting.md`, `CHANGELOG.md`, `README.md`, 상위 설계(설계 12절 항목), 이 계획의 변경 이력

- [ ] **Step 1: 시험 세트** — 설계 9절의 세 묶음(변경 감지 ~10, 관련 판단 ~10, 초안 ~5). 각 사례 `{name, task_state, mails, expected}`. 주소는 `.example` 도메인.
- [ ] **Step 2: 스모크 테스트** — `RUN_LLM_SMOKE=1`일 때만. 사례마다 실제 함수(`analyze_changes`, 분석+`judge_related`, `generate_draft` + `draft_warnings`)를 메모리 DB로 돌려 맞음/놓침/잘못을 세고 `-s`로 표를 출력. 단언: "잘못 반영한 변경 == 0", "근거 없는 숫자가 든 초안 == 0". 놓침은 단언하지 않는다.
- [ ] **Step 3: 실행** — `cd backend && PYTHONUTF8=1 RUN_LLM_SMOKE=1 ../.venv/Scripts/python -m pytest smoke/test_reply_lifecycle_smoke.py -v -s`. 실패하면 프롬프트를 고치고 다시(최대 3회, 결과마다 표 기록).
- [ ] **Step 4: 로컬 점검** (사용자와 함께, 실제 계정)
  - 다른 계정으로 업무 메일 → 초안(메모 넣기) → 빈칸 막힘 확인 → 채우고 보내기 → Gmail 스레드에 답장 확인 → "답장 보냄"
  - Gmail 앱에서 다른 업무에 직접 답장 → 90초 안에 "답장 보냄"
  - 상대가 같은 스레드에 "수량 150개로" → 바뀐 내용 · 되돌리기
  - 상대가 "감사합니다" → 답장 보냄 유지 · 완료로 닫기 띠
  - 기한 없애기 → 캘린더 일정 사라짐
  - 같은 회사 다른 주소로 새 스레드 수정 요청 → 근거 있는 합치기 제안 → 합치기 → 바뀐 내용
  - 상대 답장 없음은 테스트 계정 DB에서 `wait_from`을 과거로 바꿔 확인
- [ ] **Step 5: 문서** — privacy(설계 10절의 새 전송 · 저장), decisions(설계의 "내 판단"), troubleshooting(발송 실패 · 결과 불분명 · 보낸 답장 감지 안 됨), CHANGELOG, README 진행 표, 상위 설계 반영(설계 12절)
- [ ] **Step 6: push → CI → `main` 병합 · 테스트 · push → 로컬 브랜치 삭제** (원격 브랜치 삭제는 사용자 확인)

---

## 계획 대비 변경 이력

| # | 날짜 | 작업 | 계획 | 실제 구현 | 이유 | 영향 |
|---|---|---|---|---|---|---|
| 1 | 2026-10-27 | Task 10 Step 3 | 통과 기준이 실패하면 프롬프트를 고치고 다시 잰다 | 통과 기준은 실행 1에서 통과했지만, 판단 잘못 1건(별건이라고 밝힌 메일에 followup 제안)을 보고 판단 규칙 한 줄(INTAKE_SYSTEM · candidates_block)을 더해 실행 2로 다시 쟀다 | 잘못된 합치기 제안은 사용자에게 보이는 오류다(설계 11절: 측정 결과에 따라 프롬프트를 고친다) | 판단 10/10. 오프라인 테스트 영향 없음. 결과는 docs/evals/2026-10-27-reply-lifecycle.md |
| 2 | 2026-10-27 | Task 2 | 표식(`followupTask`)을 확인한 뒤 `calendar_delete`. 결과 문자열 `"deleted"` | 지우기 전에 일정에 `followupRemoved="1"` 표식을 덧붙이고 지운다. 다시 기한을 넣어 같은 번호 일정이 `cancelled`로 남아 있으면, 우리 업무 표식과 `followupRemoved`가 함께 있을 때만 되살린다(그 밖에는 4단계처럼 `deleted_by_user`). 결과 문자열은 `"removed"` | 같은 번호로 다시 만들면 409가 나서 `deleted_by_user`로 끝났다. 표식만으로는 "앱이 지움"과 "사용자가 지움"을 구별할 수 없다. `"deleted"`는 이미 사용자 삭제 집계에 쓰인다 | 기한을 없앴다 다시 넣으면 일정이 돌아온다. 사용자가 캘린더에서 지운 일정은 여전히 다시 만들지 않는다. 일정 하나 지울 때 Google 호출 1번 더. [결정](../../decisions.md#앱이-지운-일정만-되살린다) |
| 3 | 2026-10-27 | Task 3 | 예시: KST 토 10:00 + 3영업일 → 수 00:00 | 목 00:00. 주말에 보내면 월 00:00부터 3영업일을 온전히 센다 | 계획 예시가 계산 규칙("다음 월요일 00:00부터 센다")과 어긋났다. 금 17:00 → 수 17:00과 같은 셈법이다 | 주말에 보낸 답장은 "답이 없습니다"가 하루 늦게(목요일 0시) 뜬다. [결정](../../decisions.md#상대-답장-없음은-영업일-3일-뒤에-묻는다) |
| 4 | 2026-10-27 | Task 3 | 카드 상황 표시용 `no_reply_days`. 상대 메일은 업무에 붙은 메일로 판단 | 카드에는 `InboxCard.no_reply: bool`(문구는 화면이 붙임). 상대 메일 판단에 아직 업무에 붙지 않은 같은 스레드(합쳐진 업무 스레드 포함) 메일도 센다 | 카드 스키마에 상황 문자열 필드가 없다. 판별이 꺼져 있으면 스레드 답장이 업무에 붙지 않아 "답이 없다"고 잘못 물었다 | 없음(내부 필드). 판별이 꺼져 있어도 상대가 답했으면 묻지 않는다 |
| 5 | 2026-10-27 | Task 4 | 스레드 업무가 없는 SENT 메일은 지금처럼 건너뜀 | 업무가 있거나 **이 사용자의 받은 메일이 저장된** 스레드의 보낸 메일은 저장한다(업무가 없으면 `task_id` 없이). 한 묶음 안에서 보낸 메일은 받은 메일을 다 저장한 뒤 처리한다. 접수가 업무를 만들 때 같은 스레드의 붙지 않은 보낸 메일을 붙이고, 그 메일이 분석한 받은 메일보다 늦으면 답장 보냄으로 옮긴다 | 최종 리뷰: 받은 메일이 아직 분석 대기인 사이(업무가 생기기 전) Gmail에서 답장하면 그 보낸 메일을 영영 건너뛰었다 | 업무가 없는 스레드의 보낸 메일도 일부 저장된다(받은 메일이 저장된 스레드만). privacy.md 반영됨. 보낸 메일과 받은 메일이 다른 동기화 묶음으로 나뉘면 여전히 건너뛸 수 있다(남은 위험). [결정](../../decisions.md#보낸-메일은-아는-스레드의-것만-저장한다) |
| 6 | 2026-10-27 | Task 4 · 접수 | (계획에 없음) | 처음 분석이 늦게 끝났을 때 `reply_sent_at`이 분석한 최신 받은 메일보다 늦으면 답장 보냄 상태를 유지하고 요약 · 요청 · 할 일 · 기한만 고친다 | 최종 리뷰: 분석 대기 중 답장을 보낸 업무가 분석 완료 순간 "처리할 일"로 되돌아갔다 | 답장한 업무가 다시 처리할 일로 올라오지 않는다. [결정](../../decisions.md#늦게-끝난-분석은-답장-보냄을-덮지-않는다) |
| 7 | 2026-10-27 | Task 4 | `mark_reply_sent(..., now)` — 답장 보낸 시각 = 동기화 시각 | Gmail에서 보낸 답장은 메일 자체의 시각(`received_at` = Gmail internalDate)을 `reply_sent_at` · `wait_from`으로 쓴다. 이미 더 늦은 값이 있으면 되돌리지 않는다. 앱 발송은 발송 시각 그대로 | 서버가 꺼져 있다가 나중에 동기화하면 "보낸 뒤 상대 회신"을 "회신 뒤 보냄"으로 잘못 보고, 답 없음 확인도 늦어졌다 | 답 없음 확인 · 재답장 판단이 실제 보낸 시각 기준. [결정](../../decisions.md#gmail에서-보낸-답장은-메일-시각을-쓴다) |
| 8 | 2026-10-27 | Task 4 | `sending`/`unknown` 초안을 제목 · 받는 사람 · `sending_at` 뒤 10분 안으로 맞춤 | 창을 `sending_at − 2분 ≤ 보낸 시각 ≤ sending_at + 10분`으로 넓혔다(`SENT_MATCH_LEAD`). `sending` 초안도 같은 규칙으로 맞춘다(테스트 추가) | Gmail internalDate와 서버 시계가 달라 보낸 시각이 `sending_at`보다 조금 이를 수 있다. 그러면 맞춰 보기가 실패해 "안 보내졌어요"로 두 번 보낼 길이 열린다 | 없음. [결정](../../decisions.md#보낸-메일을-맞춰-볼-때-시계-오차-2분을-허용한다) |
| 9 | 2026-10-27 | Task 5 | 받는 사람 = 가장 최근 INBOUND의 `sender` | 가장 최근 받은 메일의 보낸 사람이 내 주소면 그 전의 나 아닌 보낸 사람. 그런 사람이 없거나 받은 메일이 없으면 409 "받은 메일이 없어 답장할 상대를 정할 수 없습니다." / "답장할 상대를 찾지 못했습니다." | 내가 나에게 보낸 메일이나 받은편지함에 들어온 내 메일에 답하면 받는 사람이 나 자신이 된다 | 그런 업무에서는 초안을 만들 수 없다. [결정](../../decisions.md#받는-사람은-가장-최근-받은-메일의-보낸-사람) |
| 10 | 2026-10-27 | Task 5 | `DRAFT_SCHEMA.subject`(LLM이 제목을 만듦) | 제목은 서버가 `Re: 원래 제목`(가장 최근 받은 메일 제목, 이미 `Re:`면 그대로)으로 정한다. LLM의 `subject`는 형식 검증만 하고 쓰지 않는다 | 설계 3.1이 제목 규칙을 정했다. 제목이 바뀌면 Gmail이 다른 대화로 볼 수 있다 | 사용자는 카드에서 제목을 고칠 수 있다(바꾸면 화면이 경고, #28) |
| 11 | 2026-10-27 | Task 5 | 숫자 토큰 `\d[\d,.:/-]*\d` 부분 문자열 비교. 스레드 글에 없는 이메일 · 전화는 outside | 숫자 · 날짜를 토큰으로 비교(15일, 11월 15일, 11/15 등 날짜 형식 포함, 양쪽 쉼표 제거). 연락처 근거는 메일 **머리글**(보낸 사람 · 받는 사람 · 참조)만 — 본문에만 있는 주소 · 전화는 스레드 밖으로 경고. 띄어 쓰거나 하이픈 없는 휴대폰 번호도 잡는다 | 리뷰: 부분 문자열 비교는 날짜를 놓쳤다. 본문 속 "이 주소로 보내 주세요"가 근거가 되면 주입 문장을 통과시킨다 | 경고가 조금 늘 수 있다(막지는 않음). 문장 끝 "11/15."은 근거가 있어도 경고될 수 있다(알려진 오탐). [결정](../../decisions.md#근거를-찾지-못한-숫자와-날짜는-경고만-한다) |
| 12 | 2026-10-27 | Task 5 | `generate_draft` 하나로 검사 · 생성 | 검사 함수 `ensure_can_generate`(API가 호출 횟수 제한보다 먼저 부름) · `find_draft`(없으면 404)를 따로 둠. 초안 대체 · 고치기 · 지우기는 상태 조건을 건 UPDATE/DELETE(영향 행 확인). LLM 장애는 재시도 없이 503 "AI 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요." | 거절된 요청이 한도를 쓰지 않게 한다. LLM을 기다리는 사이 보내기가 시작돼도 초안을 덮지 않는다. 장애와 잘못된 응답을 구분(CLAUDE.md 재시도 규칙) | 없음(내부 구조) |
| 13 | 2026-10-27 | Task 6 | `confirm_unknown`은 `unknown`만 | 5분 넘게 `sending`에 머문 초안도 결과 불분명으로 보이고(`status="unknown"`, `unknown_since`) 같은 확인 버튼을 받는다 | 선점 커밋 뒤 Gmail 호출 중 서버가 죽으면 초안이 영원히 "보내는 중"에 남는다 | 화면 상태값이 DB 값과 다를 수 있다(의도). [결정](../../decisions.md#5분이-지나면-사용자가-보낸편지함을-보고-답한다) |
| 14 | 2026-10-27 | Task 6 | 일시 오류 → `unknown`, 인증 · 그 밖 `GoogleError` → `draft` + 409 | 순서: 확인(DB만) → `send_limiter` → 토큰 갱신 → 선점 → 발송. 토큰 갱신 실패는 보내기 전이므로 `draft` 그대로(인증 409 / 그 밖 503 "Google에 연결하지 못해 보내지 않았습니다. 잠시 후 다시 보내 주세요."). 응답을 읽지 못한 예상 밖 예외도 `unknown`. 닫혔거나 합쳐진 업무는 409. 일시 오류 응답은 200 + 초안 `unknown` | 나갔을 수도 있는 경우는 모두 다시 보내지 못하게 하고, 확실히 안 나간 경우만 바로 다시 보낼 수 있게 한다 | 없음 |
| 15 | 2026-10-27 | Task 6 | References = 원래 references + in_reply_to. 보낸 사본의 `rfc_message_id`는 비움 | Email은 원래 References 머리글을 저장하지 않으므로 References = 이 업무 **같은 스레드** 메일들의 `rfc_message_id` 시간순 마지막 10개. 보내는 메일에 Message-ID를 직접 만들어(보내는 사람 도메인) OUTBOUND 행에 저장한다 | 사본에 Message-ID가 없으면 다음 답장의 References에서 우리 답장이 빠져 스레드가 끊길 수 있다 | `test_send_success_waiting` 기대값이 `rfc_message_id is None` → 보낸 머리글의 Message-ID로 바뀜(판정으로 기대 자체가 바뀜, 커밋 메시지에 기록). [결정](../../decisions.md#앱이-보낸-답장의-message-id를-저장한다) |
| 16 | 2026-10-27 | Task 6 · 9 | (계획에 없음) | `ensure_can_send`가 초안을 만들거나 고치거나 되돌린 시각(`updated_at`) 뒤에 보낸 답장이 확인됐으면(`reply_sent_at` 또는 보낸 메일 저장 시각이 더 늦음) 409 "이미 답장을 보낸 것으로 확인됐습니다. 초안을 지우거나 다시 만들어 주세요." `ReplyDraftOut`에 `updated_at` · `replied_after` 추가, 화면도 같은 판단으로 보내기를 막는다 | "안 보내졌어요" 뒤 동기화가 실제로 나간 답장을 찾으면 같은 초안을 또 보낼 수 있었다. #7로 `reply_sent_at`이 메일 시각이 되면서 저장 시각도 함께 본다 | 그 초안은 다시 만들거나 고친 뒤에만 보낼 수 있다. [결정](../../decisions.md#이미-보낸-답장이-확인되면-그-초안은-보내지-않는다) |
| 17 | 2026-10-27 | Task 6 | `sent=False` → `draft` | 동기화가 이미 이 답장을 찾았으면(`reply_sent_at ≥ sending_at − 2분`) 409 "Gmail에서 이 답장이 이미 보내진 것으로 확인됩니다. 보낸편지함을 확인해 주세요." | 최종 리뷰: 사용자가 잘못 누르면 두 번 발송된다 | "안 보내졌어요"를 눌러도 거절될 수 있다. [결정](../../decisions.md#이미-보낸-답장이-확인되면-그-초안은-보내지-않는다) |
| 18 | 2026-10-27 | Task 7 | `test_switch_off_attaches_only`: 메일만 붙고 상태 "처리할 일" | 스위치가 꺼지면 접수 자체가 돌지 않으므로 아무것도 붙지 않고 LLM 호출도 없고 업무 상태도 그대로임을 확인한다. 답 없음 확인은 스레드로 상대 메일을 본다(#4). 3단계 테스트 3건(`test_same_thread_attaches_without_llm` 등)의 "스레드 메일은 LLM 없음" 기대를 "변경 분석 1회"로 바꿨다 | 계획 문구가 `process_inbox` 동작(꺼지면 바로 돌아감)과 맞지 않았다. 3단계 기대는 설계 6.1로 대체됐다(커밋 메시지에 이유) | 없음 |
| 19 | 2026-10-27 | Task 7 · 8 | 스레드 메일은 업무별로 묶어 변경 분석 | 이번 주기에 만든 업무의 두 번째 메일은 다음 주기에 분석한다. 이번 주기 1단계에서 분석한 업무는 변경 분석을 건너뛴다. 합친 내용 분석을 일반 변경 분석보다 먼저 돌린다. 사용자가 "업무로 처리"한 메일이 분석된 업무에 붙으면 변경 분석 대기열에 넣는다 | "업무당 주기마다 분석 1회"를 지키고, 같은 메일을 두 번 분석하지 않게 한다 | 새 스레드의 두 번째 메일 반영이 한 주기(90초) 늦을 수 있다 |
| 20 | 2026-10-27 | Task 7 | `evidence_in`: 공백을 줄여 부분 문자열 비교 | 근거는 메일마다 따로 확인한다(어느 메일에서 왔는지 기록). 인용을 지운 본문에 남은 앞 메일 문장은 근거로 보지 않는다. 근거 문장이 `?` · "까요" · "나요"로 끝나면 `confirmed`라도 질문으로 본다 | 리뷰: 인용이 덜 지워진 옛 문장이 근거 확인을 통과했다. 질문형 문장을 확정으로 반영하면 설계 1절 "실패로 보는 경우"다 | 정중한 요청("보내 주실 수 있나요?")은 할 일로 자동 추가되지 않고 "확인 필요"로 보인다. [결정](../../decisions.md#재답장은-분석-1회로-고친다) |
| 21 | 2026-10-27 | Task 7 | `needs_reply` 또는 적용된 변경/새 할 일 → 처리할 일 | `question`(확인 필요)도 처리할 일로 올린다. 분석 실패(형식 오류 두 번 · 반영 중 오류)는 닫힌 업무도 다시 열고 "새 메일 자동 분석에 실패했습니다 — 메일을 직접 확인해 주세요"로 둔다. 처음 분석이 대기 · 실패인 닫힌 업무는 메일만 붙인다 | 확인 필요 항목이 "답장 필요 없음" 뒤에 숨으면 놓친다. 실패는 안전한 쪽(설계 6.5) | 답장 보냄 업무가 질문 하나로 처리할 일로 올라온다. [결정](../../decisions.md#질문과-제안도-처리할-일로-올린다) |
| 22 | 2026-10-27 | Task 7 · 8 | `changes.py` · `merge.py`에 모두 둠 | 프롬프트 · 스키마 · 검증은 `app/mail/change_prompt.py`, 워커 쪽 합친 내용 분석 루프는 `app/mail/merge_analysis.py`로 분리. `changes.run_analysis`가 결과 종류(applied/dropped/outage/invalid)를 돌려준다(`analyze_changes`는 그대로) | 파일 크기. 장애와 형식 오류를 구분해야 합치기 분석이 같은 메일을 두 번 분석하지 않는다 | 없음(내부 구조) |
| 23 | 2026-10-27 | Task 8 | `INTAKE_SCHEMA`에 관계 필드 추가 | 선택 필드로 추가하고, 후보가 있을 때만 세 필드를 required로 한 `RELATED_SCHEMA`를 넘긴다. `test_inbox_api::test_detail_mail_extras`의 후보 dict에 `"evidence": None`을 더했다 | 기존 required 집합을 유지하면서 Gemini가 후보가 있을 때 판단을 빠뜨리지 않게 한다. 기대값은 새 필드라서 바뀐 것(커밋 메시지에 이유) | 없음 |
| 24 | 2026-10-27 | Task 8 | 합칠 때 `merge_analysis` 하나를 남긴다 | 원본이나 대상에 진행 중인 합친 내용 분석이 있으면 두 번째 합치기는 409 "합친 내용을 분석하는 중입니다. 잠시 후 다시 합쳐 주세요." 되돌리기는 `source_id`가 맞을 때만 분석 상태를 지운다 | 리뷰: 칸 하나에 두 번째 원본이 들어오면 앞 원본의 내용이 분석에서 빠졌다. 업무별 대기열보다 단순하고, 기다림은 몇 초~몇 분이다 | 연달아 합치면 잠시 기다려야 한다. [결정](../../decisions.md#합친-내용-분석은-업무마다-하나씩) |
| 25 | 2026-10-27 | Task 8 | 합친 메일도 `analyze_changes` 규칙대로 | 합친 메일 분석은 "답장 필요 없음" 표시(`thanks_email_id`)를 남기지 않는다. 답 없음 물음은 옮겨 온 메일이 내 답장 뒤에 온 것일 때만 거둔다. 옮겨 온 할 일이 답장 보냄 업무에 생기거나 `needs_reply`면(내 답장보다 오래된 메일이어도) 처리할 일로 올린다 | 다른 스레드의 옛 메일이 "감사합니다" 판정이나 답 없음 해제를 일으키면 안 된다. 다른 스레드 메일은 이 업무 답장으로 답한 게 아니다(Ruling 13: 구현자가 넓힌 예외를 되돌림) | 이미 답한 메일을 합쳐도 처리할 일이 하나 더 생길 수 있다 |
| 26 | 2026-10-27 | Task 8 | 형식 오류 두 번 → 5단계 규칙 `fallback` | 판별이 꺼진 뒤 남은 pending도 LLM 없이 5단계 규칙으로 마무리한다. 10분 넘게 pending이면 5단계 규칙으로 마무리한다. 다른 곳에 합쳐진 업무의 오래된 pending은 지운다. 장애(outage)면 pending 유지 · 다음 주기 | "합친 내용을 분석하는 중"이 영원히 남지 않게 한다(설계 7.3 "꺼져 있으면 5단계 규칙") | 없음 |
| 27 | 2026-10-27 | Task 8 · 최종 | (계획에 없음) | 원본 · 대상 중 하나라도 보내는 중 · 결과 불분명 초안이 있으면 합치기 409 "보내는 중인 답장이 있어 합칠 수 없습니다. 발송 결과를 확인한 뒤 다시 시도해 주세요." | 최종 리뷰: 발송 결과(보낸 메일 · 답장 보냄)가 어느 업무로 갈지 흐려진다 | 발송 확인이 끝날 때까지 합칠 수 없다. [결정](../../decisions.md#보내는-중인-답장이-있으면-합치지-않는다) |
| 28 | 2026-10-27 | Task 9 | 카드 "내용 바뀜", 바뀐 내용 항목 `"{before} → {after}"` | `InboxCard.changed_unseen` 추가(파란 태그 "내용 바뀜"). 바뀐 내용 항목은 "제목: A → B" · "요청 추가: …" · "할 일 추가: …"처럼 앞에 항목 이름을 붙인다. 보내기 확인 단계에서는 참조 · 제목 · 본문 입력을 잠근다. 제목이 원래 제목으로 시작하지 않으면 "제목을 바꾸면 Gmail에서 다른 대화로 보일 수 있습니다." "그대로 두기"(남은 할 일 정리)는 브라우저 저장소에 기억한다. "다시 연락하기"는 `/no-reply followup`을 부르지 않고 입력칸만 연다 | 백엔드에 카드 필드가 없었다. 확인 중 고친 내용이 아니라 저장된 초안이 나가는 문제(리뷰). 그대로 두기는 서버 상태가 필요 없는 화면 선택 | "그대로 두기"는 다른 기기에서 다시 보인다. 확인 문구의 받는 사람은 주소만 보인다(이름 없음) |
| 29 | 2026-10-27 | Task 10 Step 4 (점검) | Task 7: 할 일은 추가만(설계 6.3) | 변경 항목 `todo_update` 추가: 기존 할 일 제목을 고치고 메모는 남기며 끝낸 할 일은 체크를 푼다. 못 찾으면 추가. 되돌리기 지원. 한 내용이 바뀌면 옛 값이 든 제목 · 요약 · 요청 · 할 일을 모두 고치라는 규칙 추가(측정 실행 3). 프롬프트 · 스키마 · 화면 타입 · 측정 사례 1건 추가. `test_schema_shape` 기대값에 `todo_update` 추가(항목이 새로 생겨서) | 실제 계정 점검: "100개" 할 일이 남은 채 "150개" 할 일이 새로 생김. 사용자 결정 | 설계 6.3 변경. [결정](../../decisions.md#내용이-바뀐-할-일은-새로-만들지-않고-고친다-실제-계정-점검-2026-10-27) |
| 30 | 2026-10-27 | Task 10 Step 4 (점검) | Task 7: 기한 · 제목이 바뀌면 캘린더 다시 반영 | 요약 · 요청 · 할 일 변경과 그 되돌리기도 캘린더를 다시 반영한다 | 점검에서 발견한 버그: 일정 제목은 한 줄 요약, 설명은 요청 · 할 일로 만드는데 반영하지 않았다 | Google 일정 수정 호출이 조금 는다. [결정](../../decisions.md#일정에-쓰이는-값이-바뀌면-캘린더도-다시-반영한다-실제-계정-점검-2026-10-27) |
