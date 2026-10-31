# 데모 모드 · 목표 입력 Agent 제거 설계

- 작성일: 2026-10-31
- 이어받는 것: [데모 모드 분리 설계](2026-10-27-demo-mode-split-design.md)("코드는 지우지 않고 숨긴다")를 뒤집는다 → 이번에 지운다.
- 사용자 결정은 **(사용자 결정)**, 제가 정한 것은 **(내 판단)**으로 표시한다.

## 1. 목적
앱을 **Gmail 업무 메일 Agent 하나**로 정리한다. 해커톤 PoC의 흔적(가상 데이터, 목표 입력 Agent, LLM 표시)이 메뉴와 화면에 남아 "흔한 AI 에이전트 데모"처럼 보이고(사용성 검토), 코드와 테스트가 두 세계로 나뉘어 있다.

### 지우는 것 (사용자 결정: 둘 다)
- **데모 모드:** `DEMO_MODE` · 데모 계정 설정, 가상 데이터(`seed.py`), `/api/demo/*`(초기화 · 회신 시뮬레이터), 워크스페이스 조회 API(`/api/customers` · `/documents` · `/emails`), 화면의 워크스페이스 · 데모 초기화 · 회신 시뮬레이터.
- **목표 입력 Agent:** `agent/orchestrator.py` · `tools.py` · `prompts.py`, 목표로 업무 만들기 · 목표 고치기 · Agent 재시도 · 승인 · 후속 업무 완료 API, `/api/dashboard`, 화면의 "전체 업무"(목표 입력창 · LLM 표시 · 업무 목록) · 승인 카드 · 목표 편집 · Agent 실행 상자 · 토큰 사용량 · 후속 업무.
- **"전체 업무" 메뉴:** 받은 업무로 합친다. 메뉴는 받은 업무 · 자료실 · 설정 3개 **(사용자 결정)**.

### 남기는 것
- 메일 · Google · 캘린더 · 자료 기능 전부와 그 API.
- 공용 부품: `agent/llm.py`의 `structured` 호출 · 오류, `agent/gemini_llm.py`의 `structured` · 호출 간격, `agent/guard.py`(`wrap_untrusted` · 주입 탐지), `agent/audit.py`(`log_action`), 업무 상세 실시간 갱신(SSE). 폴더 이름 `agent/`는 그대로 둔다 **(내 판단: 이동은 import만 바꾸는 큰 변경이라 이번 범위 밖)**.
- 제품 이름 FollowUp Agent.

## 2. 메일이 빌려 쓰던 것 옮기기 (내 판단)
- `CLOSED_TASK_STATUSES`(완료 · 취소): 메일 쪽 상수(`mail/` 공용 위치)로 옮기고 intake · reply_draft · reply_send가 그것을 쓴다.
- **업무 취소:** `orchestrator.cancel_task`의 메일 부분(스누즈 해제, 캘린더 반영 표시, `TASK_CANCELLED` 기록)을 `mail/task_actions`로 옮긴다. 동작은 지금과 같다(완료 시각을 새로 쓰지 않음). `POST /tasks/{id}/cancel`은 메일 업무만 받는다(다른 업무는 404, 다른 메일 동작과 같음).
- 취소 화면 문구에서 "Agent는 멈추고 승인은 무효" 설명을 뺀다.
- `LLMError` 문구의 "'Agent 재시도'를 눌러" 안내를 "잠시 후 다시 시도해 주세요"로 바꾼다.

## 3. 데이터 · 스키마 (내 판단)
- 지금 `dev.db`: 메일 업무 17건, 데모 · 목표 입력 업무 0건, 고객 · 문서 0건. **지워질 사용자 데이터가 없다.**
- Alembic 마이그레이션 하나로:
  - 메일이 아닌 업무(`origin` ≠ `mail`)와 그 기록 · 실행 · 승인을 지운다(배포된 DB에 남은 PoC 행 정리. 로컬은 0건). Gmail이 아닌 메일(`gmail_message_id`가 없는 행)도 지운다.
  - 테이블 `agent_runs` · `approval_requests` · `customers` · `documents`를 지운다.
  - 그 테이블을 가리키던 칸을 지운다: `tasks.customer_id` · `tasks.parent_task_id`, `emails.customer_id` · `emails.document_id`, `agent_action_logs.run_id`. Agent만 쓰던 `tasks.agent_enabled` · `tasks.result_summary`도 지운다.
  - 메일이 쓰는 `goal` · `current_step` · `next_action` · `waiting_for` · `plan`은 남긴다(합치기 되돌리기가 쓴다).
  - SQLite(`batch_alter_table`) · PostgreSQL 양쪽. 반영 전 `dev.db` 백업. downgrade는 칸 · 테이블을 빈 채로 되살린다(지운 행은 돌아오지 않음).
- 업무 상세 응답에서 `customer` · `parent` · `followups` · `approvals` · `runs` · `active_run` · `max_steps` · `can_simulate_reply`를 뺀다. 메일이 아닌 업무를 열면 404.

## 4. 화면
- 메뉴: 받은 업무 · 자료실 · 설정. `/tasks`(전체 업무) · `/workspace`로 들어오면 받은 업무로 보낸다.
- 업무 상세: 메일 업무 화면만 남는다. 옆 칸은 처리 기록.
- 처리 기록: Agent 기록 이름(AGENT_* · TOOL_CALL · 승인 등)을 지우고 메일 기록 · `TASK_CANCELLED` · `SECURITY_WARNING`만 남긴다.
- 로그인 · 설정 문구에 데모 흔적이 없는지 확인.

## 5. 설정 · 테스트 · 문서
- 설정 삭제: `DEMO_MODE` · `DEMO_USER_EMAIL` · `DEMO_USER_PASSWORD` · `MAX_AGENT_STEPS` · `AGENT_TIMEOUT_SECONDS` · `LLM_TRANSIENT_RETRIES` · `LLM_EFFORT`(사용처 확인 후). `.env`에 남은 값은 무시된다(설정은 알 수 없는 이름을 무시하는지 확인, 아니면 무시하게 한다).
- 테스트: 데모 · Agent 전용 테스트 파일은 지운다. 섞인 테스트(보안 · SSE · 캘린더 · 취소)는 메일 업무로 다시 쓴다. CSRF 테스트는 다른 POST로. `conftest.py`의 `DEMO_MODE=true`를 없앤다. **테스트를 지우거나 고친 이유는 커밋에 적는다.**
- 실제 LLM 스모크 `smoke/test_llm_smoke.py`(Agent)는 지운다. 메일 스모크는 그대로.
- 문서: README(데모 시나리오 · Agent Loop · Tool 권한 · 구조도), PRODUCT.md, CLAUDE.md 첫 문단, privacy, decisions("데모 코드는 지우지 않고 숨긴다" → 변경), DESIGN.md의 대화상자 언급, CHANGELOG.

## 6. 하지 않는 것
- `agent/` 폴더 이름 바꾸기, `IN_PROGRESS` · `BLOCKED` 상태값 정리, 할 일 `source: "agent"` 이름 바꾸기(LLM이 뽑은 할 일이라는 뜻, 저장된 데이터에 있음).
- 남은 화면 개선(단축키 등)은 이 작업이 끝난 뒤 별도로.

## 7. 확인
- 백엔드 테스트 전체, 프론트 빌드 · lint, 마이그레이션 upgrade · downgrade(SQLite 사본).
- 로컬 `dev.db` 백업 후 upgrade, 메일 업무 17건 그대로인지 건수로 확인.
- 점검 서버에서 메뉴 3개 · 업무 상세 · 취소 동작 · `/tasks` · `/workspace` 이동 확인.
