# 데모 모드 분리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `DEMO_MODE`(기본 꺼짐) 스위치로 데모 데이터 · Demo Reset · 워크스페이스 메뉴 · 회신 시뮬레이터를 켜고 끄고, 켜져 있어도 Reset이 메일 업무를 지우지 않게 한다.

**Architecture:** 서버가 스위치로 막고(404 · 시드 생략), 로그인 사용자 응답의 `demo_mode`로 화면이 메뉴를 숨긴다. 코드는 지우지 않는다.

**Tech Stack:** FastAPI, pydantic-settings, SQLAlchemy, React 19 + react-router 7

**Spec:** `docs/superpowers/specs/2026-10-27-demo-mode-split-design.md`

## Global Constraints

- `demo_mode: bool = False` (`DEMO_MODE`). 테스트는 `tests/conftest.py`에서 `DEMO_MODE=true`로 고정한다(기존 데모 테스트 유지). 꺼짐 테스트는 `monkeypatch.setattr(get_settings(), "demo_mode", False)`.
- 꺼짐일 때 데모 API는 404 "찾을 수 없습니다."(존재를 드러내지 않음).
- Reset은 `origin == "mail"` 업무 · 그 기록, Gmail 메일(`gmail_message_id` 있음), Google 연결을 지우지 않는다.
- 사용자에게 보이는 문구는 한국어.

## Review Focus

1. 꺼짐 상태에서 가입 → 빈 계정. 받은 업무 · 전체 업무 화면이 빈 상태로 깨지지 않아야 한다.
2. 켜짐 Reset: 메일 업무가 지워질 고객 · 문서를 가리켜도 FK 오류 없이 연결만 끊긴다.
3. 켜짐 Reset: 메일 업무를 가리키는 데모 업무의 `parent_task_id` · `merged_into_id`도 정리돼야 한다.
4. 이미 로그인한 화면이 `demo_mode`를 모르는 오래된 응답을 받아도(필드 없음) 메뉴가 숨겨진 쪽으로 동작한다.
5. 서버 시작 시 `DEMO_USER_PASSWORD`가 있어도 꺼짐이면 데모 계정을 만들지 않는다.

---

### Task 1: 서버 스위치와 안전한 Reset

**Files:**
- Modify: `backend/app/config.py` (`demo_mode: bool = False`), `.env.example` (`DEMO_MODE=false` 설명)
- Modify: `backend/app/api/auth.py` (가입 시 `if demo_mode: seed_user_data`), `backend/app/main.py` (`_ensure_demo_user`는 꺼짐이면 바로 반환)
- Modify: `backend/app/api/demo.py` (`simulate-reply` · `reset` 앞에서 꺼짐이면 404)
- Modify: `backend/app/api/tasks.py` (`can_simulate_reply = demo_mode and ...`)
- Modify: `backend/app/schemas.py` (`UserOut.demo_mode: bool`) — `register` · `login` · `me`가 채운다
- Modify: `backend/app/seed.py` `reset_user_data` — 메일 업무 · Gmail 메일 보존
- Modify: `backend/tests/conftest.py` (`DEMO_MODE=true`)
- Test: `backend/tests/test_demo_mode.py`

**Interfaces:**
- Produces: `UserOut { id, email, demo_mode }`

- [ ] **Step 1: 실패하는 테스트** (`test_demo_mode.py`)
  - `test_off_register_creates_empty_account`: 꺼짐 가입 → `tasks` · `customers` · `documents` 0개, 응답 `demo_mode is False`, `/api/auth/me`도 False
  - `test_off_demo_endpoints_404`: 꺼짐에서 `POST /api/demo/reset`, `POST /api/tasks/{id}/simulate-reply` → 404
  - `test_off_no_simulate_flag`: 꺼짐에서 보낸 메일이 있는 업무 상세 `can_simulate_reply is False`
  - `test_off_no_demo_user_on_startup`: 꺼짐 + `demo_user_password` 설정 → `_ensure_demo_user()` 뒤 사용자 0명 늘어남
  - `test_on_reset_keeps_mail_tasks`: 켜짐 가입(시드됨) → 메일 업무(할 일 · 기한 · 기록 1개) + Gmail 메일 + 데모 메일 하나 추가, 메일 업무와 Gmail 메일이 데모 고객을 가리키게 함, 데모 업무 하나의 `parent_task_id`를 메일 업무로 → Reset → 메일 업무 · 그 기록 · Gmail 메일 남음(고객 연결은 None), Gmail 아닌 메일은 시드분만, 데모 업무 다시 생김
- [ ] **Step 2: 실행해서 실패 확인** — `cd backend && ../.venv/Scripts/python -m pytest -q tests/test_demo_mode.py`
- [ ] **Step 3: 구현** — 위 Files 대로. `reset_user_data`는 지울 업무 = `origin != "mail"`. 남는 업무 · 메일의 `customer_id` · `document_id`를 먼저 None으로, 모든 업무의 `parent_task_id` · `merged_into_id` 중 지울 업무를 가리키는 것을 None으로 만든 뒤 지운다. 기록 · 실행 · 승인은 지울 업무의 것과 `task_id`가 없는 것만 지운다.
- [ ] **Step 4: 전체 테스트 통과** — `cd backend && ../.venv/Scripts/python -m pytest -q`
- [ ] **Step 5: 커밋** — `데모 모드: DEMO_MODE 스위치(기본 꺼짐)와 메일 업무를 지키는 Reset`

### Task 2: 화면 숨김과 로그인 문구

**Files:**
- Modify: `frontend/src/types.ts` (`User.demo_mode?: boolean`)
- Modify: `frontend/src/App.tsx` — `user?.demo_mode === true`일 때만 "워크스페이스 데이터" · "Demo Reset" · `ResetDialog`, `/workspace` 라우트. 아니면 `/workspace` → `/`
- Modify: `frontend/src/pages/TaskDetail.tsx` — `ReplySimulator`는 `can_simulate_reply`일 때만 렌더
- Modify: `frontend/src/pages/Login.tsx` — 왼쪽 소개를 메일 Agent 문구로: 제목 "업무 메일, 놓치지 않게." / 설명 "Gmail의 업무 메일을 정리하고, 할 일과 기한을 챙기고, 캘린더에 올립니다." / 특징 3줄: 업무 메일만 골라 요약 · 할 일과 기한 정리, 캘린더 일정 · 알림, 답장은 승인 후 발송(6단계 예정 표시 없이 "답장은 내가 확인한 뒤에만 보냅니다")

- [ ] **Step 1: 구현**
- [ ] **Step 2: `cd frontend && npm run build && npm run lint`** — 경고는 App.tsx 기존 1개만
- [ ] **Step 3: 커밋** — `데모 모드: 꺼져 있으면 데모 메뉴 · 시뮬레이터 숨김, 로그인 문구 변경`

### Task 3: 브라우저 점검 · 문서 · 병합

- [ ] **Step 1: 로컬 점검** (서버 재시작, `127.0.0.1:8000`의 별도 테스트 계정, 끝나면 테스트 계정 삭제)
  - 꺼짐: 가입 → 빈 받은 업무 · 전체 업무, 메뉴에 워크스페이스 · Demo Reset 없음, `/workspace` → 첫 화면, 로그인 화면 새 문구
  - 5단계 남은 점검: 휴대폰 너비(390px)에서 받은 업무 · 업무 상세가 깨지지 않음. "업무로 처리"(가짜 업무 아님 메일로)
- [ ] **Step 2: 문서** — `CHANGELOG.md`, `docs/decisions.md`(기본 꺼짐, Reset 범위), `README.md`(DEMO_MODE 안내), `docs/privacy.md`(데모 데이터 생성 조건), 이 계획의 변경 이력
- [ ] **Step 3: push → CI → `main` 병합 · 테스트 · push → 로컬 브랜치 삭제**

---

## 계획 대비 변경 이력

| # | 날짜 | 작업 | 계획 | 실제 구현 | 이유 | 영향 |
|---|---|---|---|---|---|---|
