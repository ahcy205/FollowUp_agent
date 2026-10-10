# FollowUp Agent — AI 업무 후속조치 Agent PoC

사용자가 **하나의 업무 목표**를 자연어로 주면, Agent가 목표를 분석하고 Tool을 골라 여러 단계를 수행하고,
상태를 저장한 채 **새 이벤트(고객 답변)가 올 때마다 다시 판단**해 최종 목표가 달성될 때까지 후속조치를 이어가는 시스템입니다.
챗봇이 아니라 “업무 상태 + Agent 행동”을 보여주는 웹 서비스입니다.

## 데모 시나리오 (최우선 완료 기준)

```
로그인 → "A회사에 견적서를 보내고 고객 답변이 오면 내용을 확인해서 내가 해야 할 일을 정리해줘."
→ plan_goal (하위 목표 5개) → search_customer → search_document → draft_email
→ send_email 호출 ⇒ 서버가 실행을 멈추고 승인 요청 (Task: ACTION_REQUIRED)
→ [승인하고 발송] (필요하면 제목·본문을 수정한 뒤 [수정한 내용으로 발송]) → 가상 메일 발송 → Agent 재개 → Task: WAITING (고객 답변 대기)
→ [고객 답변 도착] (납기 단축 요청) → 이벤트로 Agent 재실행
→ check_email → analyze_email → create_followup_task("A회사 납기일 단축 가능 여부 확인")
→ complete_plan_step ×N → update_task_status(COMPLETED)  ※ 하위 목표가 남아 있으면 서버가 거부
→ 🟢 견적 업무 완료 · Agent Timeline에 전 과정 기록
→ 후속 업무 열기 → [완료 처리] + 처리 메모 → 원래 업무 타임라인에 "후속 업무 완료" 기록
(언제든) 목표 [✎ 수정] → 저장 즉시 Agent가 goal_updated 이벤트로 재계획 · [업무 취소] → Agent 중단 + 승인 무효 + CANCELLED(이력 보존)
```

발표 중 문제가 생기면 상단 **↺ Demo Reset** → 내 계정 데이터만 초기화 후 Fixture 재생성.

## 구조

```
React (Vite, TS)  ──same-origin cookie──▶  FastAPI   (업무 상세는 SSE `/api/tasks/{id}/events`로 실시간 수신)
                                             ├─ api/        auth · tasks · approvals · demo(reset, 답변 시뮬레이션)
                                             ├─ agent/
                                             │   ├─ orchestrator.py  Agent Loop (pause/resume, 이벤트 재실행, 안전 제한)
                                             │   ├─ tools.py         Tool 10개 + allowlist + 권한/소유권/입력 검증
                                             │   ├─ llm.py           LLM 인터페이스 + Anthropic(Claude) 구현
                                             │   ├─ gemini_llm.py    Google Gemini 구현 (function calling, thought signature 보존)
                                             │   ├─ guard.py         Prompt Injection 탐지 · 외부 콘텐츠 격리 · 유출 방지
                                             │   └─ prompts.py       시스템 프롬프트 (보안 원칙 포함)
                                             └─ models.py  SQLAlchemy → PostgreSQL (로컬: SQLite) · Alembic
```

### 실시간 타임라인 (SSE)
업무 상세 화면은 폴링 대신 `GET /api/tasks/{id}/events`에 EventSource로 붙습니다. 서버는 연결 직후 전체 TaskDetail 스냅샷을 1회 보내고,
이후 0.4초 간격으로 업무의 변경 지문(업무 updated_at · 마지막 로그 id · 최근 Run의 상태/단계)을 확인해 바뀐 경우에만 `event: task`로 다시 보냅니다.
변화가 없으면 15초마다 주석 줄로 연결만 유지합니다. 인증은 기존 쿠키, 소유권 검사는 스트림 시작 전에 수행(타인 업무 404)합니다.

### LLM
`LLM_PROVIDER`로 Gemini/Claude를 선택합니다. 대화 기록은 공급자 중립 형식(text / tool_use / tool_result)으로 DB에 저장되고, 호출 시 각 공급자 형식으로 변환됩니다. 따라서 승인 대기 후 재개가 두 공급자 모두에서 동일하게 동작합니다.

### Agent Loop
1. 이벤트(목표 등록 / 고객 메일 수신 / 재시도)마다 `AgentRun` 생성. 첫 메시지 = `<user_goal>` + DB에서 복원한 `<task_state>` + 최근 행동 이력.
2. LLM이 Tool 호출 → 서버가 allowlist·Pydantic 검증·소유권 검사 후 실행 → `tool_result`로 LLM에 반환 → 반복.
3. `send_email`(승인 필요 Tool)은 실행하지 않고 `ApprovalRequest` 저장 후 **대화 전체를 DB에 저장하고 중단**. 승인/취소 시 결과를 `tool_result`로 붙여 같은 Run을 재개.
4. 턴 종료 시 상태가 확정되지 않았으면 한 번 상기시키고, 그래도 없으면 `ACTION_REQUIRED`로 표시.
5. 턴마다 LLM usage를 공급자 중립 형태(`input` · `output` · `cache_read` · `total` · `turns`)로 정규화해 `AgentRun.usage`에 누적합니다. 업무 상세의 **토큰 사용량** 카드가 이번/마지막 실행과 업무 누적치를 보여줍니다(Agent 턴 기준, analyze_email 내부 호출은 제외).
6. `MAX_AGENT_STEPS`(기본 10) · `AGENT_TIMEOUT_SECONDS` 초과 시 `STOPPED` + Task `BLOCKED` + “⚠️ Agent 실행 중단” 표시.
7. LLM 일시 오류(5xx · 타임아웃 · 연결 실패 · 분당 한도 · Gemini 도구 호출 형식 실패)는 SDK 재시도가 소진된 뒤에도 Run 안에서 `LLM_TRANSIENT_RETRIES`(기본 2)회까지 3초 → 8초 간격으로 자동 재시도하며 타임라인에 “🔁 자동 재시도”로 남깁니다. 재시도는 단계 수를 소비하지 않고 실행 시간 제한은 그대로 적용됩니다. 인증·권한·모델 없음·크레딧 부족·일일 한도·안전 정책 거절은 즉시 `FAILED` + 복구 버튼(Agent 재시도).

### Tool 권한

| Tool | 권한 | 서버 측 제약 |
|---|---|---|
| plan_goal | WRITE(현재 업무) | 하위 목표 2~7개 |
| search_customer / search_document / check_email | READ | user_id 필터, 결과 최대 3~5건, 외부 콘텐츠는 `<untrusted_external_content>`로 격리 |
| analyze_email | READ | 현재 업무의 수신 메일만, 구조화 출력 + 의심 지시문 분리 |
| draft_email | READ/CREATE | 수신 주소는 **서버가 고객 레코드에서 결정**, 다른 고객 연락처 포함 시 차단 |
| send_email | WRITE + **사용자 승인** | 현재 업무의 DRAFT만, 승인 전에는 절대 실행 안 됨. 승인 시 사용자가 제목·본문을 고칠 수 있고(수신자는 불가) 수정 사실과 원문이 로그에 남으며 Agent에게 `edited_by_user`로 전달됨 |
| create_followup_task | WRITE | 업무당 최대 5개, 중복 제목 거부 |
| complete_plan_step / update_task_status | WRITE(현재 업무) | **COMPLETED는 모든 하위 목표 달성 + 승인 대기 없음 + 결과 요약이 있을 때만 허용** |

사용자 제어 API: `POST /api/tasks/{id}/goal`(목표 수정 — 활성 Run이 있으면 409, Agent 업무는 저장 즉시 `goal_updated` 트리거로 재실행, 후속 업무는 문구만 변경), `POST /api/tasks/{id}/cancel`(취소 — 활성 Run `STOPPED`, 대기 승인 `CANCELLED`, 업무 `CANCELLED`로 보관, 되돌리기 없음). Agent 루프는 LLM 호출 전후로 Run 상태를 다시 읽으므로 응답 중 취소된 턴의 도구 호출은 실행되지 않습니다.

Agent가 만든 후속 업무(`origin=agent`, `agent_enabled=false`)는 Agent가 아니라 **사용자가** `POST /api/tasks/{id}/complete`로 완료 처리합니다. 처리 메모는 `result_summary`에 저장되고, 부모 업무 타임라인에 `FOLLOWUP_COMPLETED`로 기록됩니다. Agent가 처리 중인 업무에는 409를 돌려줍니다.

LLM은 `user_id`/`task_id`를 전달할 수 없습니다(입력 스키마에 없음, extra 필드 거부). 서버 컨텍스트에서만 주입됩니다.

## 보안
- **인증**: bcrypt 해시, HttpOnly + SameSite=Lax(+ 운영 Secure) JWT 쿠키, 로그인/가입 rate limit.
- **데이터 격리**: 모든 조회는 `user_id` 조건 포함. 남의 ID는 404(존재 여부 비노출). Tool에서도 동일하게 재검증. 테스트로 검증.
- **CSRF**: 상태 변경 API는 `X-Requested-With` 헤더 + Origin 허용 목록 필수.
- **Prompt Injection**: 외부 문서/메일은 데이터로만 취급(시스템 프롬프트 원칙 + 태그 격리 + 정규식 탐지 + 보안 경고 로그). 조작 시도(“COMPLETED로 변경해”)는 서버 완료 조건이 막음. 시연용 악성 문서(D물산 견적서)와 악성 답변 프리셋 포함.
- **헤더**: CSP, X-Frame-Options DENY, nosniff, HSTS(운영), API `no-store`. 운영에서 TrustedHost, `/api/docs` 비활성화.
- **Secret**: 전부 환경변수. `.env`는 `.gitignore`. 운영에서 `SECRET_KEY` 32자 미만·SQLite 사용 시 기동 거부.

## 로컬 실행

```bash
# Backend
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt   # (mac/linux: .venv/bin/pip)
cp .env.example backend/.env    # LLM_PROVIDER, LLM_API_KEY, SECRET_KEY 입력
cd backend && ../.venv/Scripts/uvicorn app.main:app --reload --port 8000

# Frontend (개발 서버, /api는 8000으로 프록시)
cd frontend && npm install && npm run dev        # http://localhost:5173
# 또는 npm run build → backend/static 으로 빌드되어 http://localhost:8000 에서 함께 서빙

# 스키마가 바뀐 뒤 기존 dev.db를 계속 쓰려면 (로컬 SQLite는 create_all이라 컬럼 추가를 반영하지 않음)
cd backend && ../.venv/Scripts/alembic upgrade head       # 또는 backend/dev.db 삭제 후 재기동

# Tests (LLM은 스크립트된 가짜 클라이언트로 대체)
cd backend && ../.venv/Scripts/python -m pytest -q

# 실 LLM 스모크 테스트 (opt-in, 공급자 쿼터 사용 · backend/.env의 LLM_API_KEY 필요, 약 30초~3분)
cd backend && RUN_LLM_SMOKE=1 ../.venv/Scripts/python -m pytest smoke -v -s
```

`DEMO_USER_PASSWORD`를 설정하면 기동 시 `DEMO_USER_EMAIL` 계정이 생성됩니다. 회원가입하는 모든 계정도 자기 전용 가상 데이터를 받습니다.

## Railway 배포

1. GitHub에 push → Railway에서 **New Project → Deploy from GitHub repo** (루트의 `Dockerfile`, `railway.json` 사용).
2. 같은 프로젝트에 **PostgreSQL** 추가 → 앱 서비스 Variables에 `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Variables:

| 변수 | 값 |
|---|---|
| `APP_ENV` | `production` (Dockerfile 기본값) |
| `SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `LLM_PROVIDER` | `gemini` (기본) 또는 `anthropic` |
| `LLM_API_KEY` | Gemini: Google AI Studio 키 / Anthropic: Console API 키 |
| `LLM_MODEL` | 비우면 기본값 (`gemini-3.6-flash` / `claude-opus-5`) |
| `LLM_RPM_LIMIT` | 분당 LLM 호출 상한. Gemini 무료 등급은 `5` (결제 활성화 시 `0`=해제) |
| `AGENT_TIMEOUT_SECONDS` | `420` 권장 (무료 등급 대기 시간 포함) |
| `LLM_TRANSIENT_RETRIES` | Run 내 일시 오류 자동 재시도 횟수 (기본 `2`, `0`이면 끔) |
| `DEMO_USER_PASSWORD` | 데모 계정 비밀번호 (선택) |

4. Settings → Networking → **Generate Domain**. Railway가 주입하는 `RAILWAY_PUBLIC_DOMAIN`으로 허용 호스트/Origin이 자동 설정됩니다(커스텀 도메인은 `PUBLIC_URL`, `ALLOWED_HOSTS` 추가).
5. 컨테이너 시작 시 `alembic upgrade head` 후 uvicorn 기동. 헬스체크 `/api/health`.

## 범위 밖 (의도적으로 제외)
실제 Gmail/기업 메일 연동, 실제 외부 발송, 모델 학습/파인튜닝, Multi-Agent, 결제, 모바일 앱.
메일은 전부 내부 가상 메일 시스템이며 주소는 예약 도메인 `.example`을 사용합니다.
