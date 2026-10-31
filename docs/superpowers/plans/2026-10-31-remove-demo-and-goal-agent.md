# 데모 모드 · 목표 입력 Agent 제거 구현 계획

설계: [2026-10-31-remove-demo-and-goal-agent-design.md](../specs/2026-10-31-remove-demo-and-goal-agent-design.md) (2026-10-31 사용자 승인)
브랜치: `feat/remove-demo-agent`

## Task 1: 백엔드 (작업 에이전트)
- [ ] 메일이 빌려 쓰던 것 옮기기: `CLOSED_TASK_STATUSES` → `mail/` 공용 상수, 업무 취소 → `mail/task_actions.cancel_mail_task`(동작 동일, 메일 업무만). 먼저 테스트(취소 · 캘린더)를 새 함수로 옮겨 실패 → 구현 → 통과.
- [ ] 삭제: `agent/orchestrator.py` · `tools.py` · `prompts.py`, `seed.py`, `api/demo.py`, `main.py`의 데모 계정 · `recover_stale_runs`, `api/tasks.py`의 Agent · 데모 라우트(`/dashboard`, `POST /tasks`, `/agent/retry`, `/goal`, `/complete`, `/approvals/*`, `/customers` · `/documents` · `/emails`), `security.agent_limiter`, 스키마 · 설정의 데모 · Agent 항목, `llm.py` · `gemini_llm.py`의 `agent_turn` 계열.
- [ ] `GET /tasks/{id}` · SSE: 메일 업무만(아니면 404), 응답에서 Agent 필드 제거, 지문에서 `AgentRun` 제거.
- [ ] `LLMError` 문구의 'Agent 재시도' 안내 바꾸기.
- [ ] 모델 · 마이그레이션(설계 3절). `dev.db` 백업 → upgrade, 메일 업무 건수 확인. SQLite 사본에서 downgrade → upgrade.
- [ ] 테스트: 데모 · Agent 전용 파일 삭제, 섞인 테스트 메일 기준으로 재작성, conftest 정리. 전체 통과.

## Task 2: 프론트엔드 (직접)
- [ ] 삭제: Dashboard · Workspace · ResetDialog · ReplySimulator · ApprovalCard · GoalEditor · CompleteFollowupCard.
- [ ] App: 메뉴 3개, `/tasks` · `/workspace` → `/`, 데모 초기화 · `refreshKey` 제거.
- [ ] TaskDetail: 메일 업무 화면만. Timeline: Agent 기록 이름 제거. types · format 정리. CancelTaskPanel 문구.
- [ ] styles.css의 Agent 전용 클래스 정리. 빌드 · lint.

## Task 3: 문서 · 점검 · 병합
- [ ] README · PRODUCT · CLAUDE.md 첫 문단 · privacy · decisions · DESIGN · CHANGELOG · `.env.example`.
- [ ] 점검 서버에서 메뉴 · 상세 · 취소 · 이동 확인. 리뷰. CI → 병합.

## 계획 대비 변경 이력

| # | 날짜 | 작업 | 계획 | 실제 구현 | 이유 | 영향 |
|---|---|---|---|---|---|---|
| 1 | 2026-10-31 | Task 1 | 설계 2절: `POST /tasks/{id}/cancel`은 메일이 아닌 업무에 404("다른 메일 동작과 같음") | 다른 메일 동작과 같은 `_mail_task`를 써서 다른 사용자 업무는 404, 메일이 아닌 내 업무는 409 | 다른 메일 동작은 실제로 409를 돌려준다. 설계의 두 조건(404 · 다른 메일 동작과 같음)이 충돌해 기존 동작과 맞췄다. 업무 상세 · SSE는 설계대로 404 | 마이그레이션 뒤 메일이 아닌 업무가 없어 사용자 체감 없음 |
| 2 | 2026-10-31 | Task 1 | `TASK_CANCELLED` 기록 동작 동일 | 기록의 `output_data`에서 `stopped_run` · `voided_approvals`를 뺐다 | 실행 · 승인이 없어져 늘 비어 있는 값 | 처리 기록 화면 표시 변화 없음 |
| 3 | 2026-10-31 | Task 1 | 업무 상세 응답에서 Agent 필드 제거 | 추가로 `TaskBrief`의 `agent_enabled` · `parent_task_id` · `customer_id` · `result_summary`, `EmailOut.customer_id`, `ActionLogOut.run_id`를 뺐고 `TaskDetail.mail`은 늘 채운다(선택 아님). `log_action`의 `run_id` 인자도 없앴다 | 칸이 지워져 내보낼 값이 없다. 메일 업무만 열리므로 mail이 늘 있다 | 프론트 types가 이 필드를 읽지 않아야 한다 |
| 4 | 2026-10-31 | Task 1 | 설정 삭제 목록 | `LLM_EFFORT`도 지웠다(Agent 호출만 썼다). `LLM_ANALYSIS_MODEL`은 남김 | 사용처 없음 | `.env`에 남아도 무시(`extra="ignore"`) |
| 5 | 2026-10-31 | Task 1 | 마이그레이션 확인: SQLite 사본 downgrade → upgrade | PostgreSQL 16 임시 컨테이너에서도 예시 행으로 upgrade → downgrade → upgrade, 양쪽 `alembic check` 차이 없음 | 규칙상 양쪽에서 동작해야 하고 CI에는 PostgreSQL이 없다 | 없음 |
| 6 | 2026-10-31 | Task 1 | 테스트 파일 정리 | `test_gemini_conversion.py`를 남은 내용에 맞게 `test_gemini_helpers.py`로 이름을 바꿨다. `test_inbox_fields` · `test_task_calendar_fields` · `test_task_mail_fields`도 데모 가상 업무 대신 메일 업무를 만들도록 고쳤다 | 데모 가상 데이터가 없어졌다 | 없음 |
| 7 | 2026-10-31 | Task 1 (리뷰) | 업무 취소 동작 동일 | 새 `cancel_mail_task`는 Agent 실행과 겨루던 잠금(`_task_lock` · `refresh`)을 쓰지 않는다. 취소 API는 다른 메일 동작처럼 동기화를 바로 깨운다 | 다른 메일 동작에도 잠금이 없고 이제 경쟁할 Agent 실행이 없다. 깨우기는 예전 동작(캘린더 즉시 반영)을 되살린 것 | 취소 직후 캘린더 일정 삭제가 바로 반영 |
| 8 | 2026-10-31 | Task 1 (리뷰) | 모델 정리 | 업무 `origin` 기본값을 `user`에서 `mail`로 | 남은 업무는 메일 업무뿐 | 모든 생성 경로가 `origin="mail"`을 직접 넣어 영향 없음 |
| 9 | 2026-10-31 | Task 1 (리뷰) | 메일이 아닌 업무 삭제 | 그 업무에 연결돼 있던 Gmail 메일은 지우지 않고 연결만 끊는다(`task_id` NULL). 판별이 `work`이면 다음 접수 주기에 메일 업무로 다시 만들어질 수 있다 | 사용자의 실제 Gmail 메일은 지우지 않는다(작업 규칙). 로컬 해당 행 0건 | 없음 |
| 10 | 2026-10-31 | Task 2 (리뷰) | 처리 기록에서 Agent 기록 이름 제거 | 정리하면서 빠졌던 보안 경고의 탐지 문구 · 위험도 표시를 되살렸다. 개발용 "입력 / 결과 보기"는 메일 기록에서 이미 숨기기로 한 것(사용성 검토)이라 그대로 뺐다 | 보안 경고 정보는 남기는 기능 | 없음(예전 표시와 같음) |
