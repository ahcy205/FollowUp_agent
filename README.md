# FollowUp Agent — 업무 메일 후속조치 Agent

Gmail 받은편지함에서 **업무 메일만 골라 업무로 바꾸고, 끝날 때까지 챙기는** 개인용 웹 앱입니다.
요약 · 상대가 원하는 것 · 할 일 · 기한(근거 문장 포함)을 정리해 기한은 Google 캘린더에 올리고, 근거 있는 값만 채운 답장 초안을 내가 확인해야만 보냅니다.
보낸 뒤에는 상대가 답하지 않거나 내용을 바꾸면 알려 주고 업무를 고칩니다.

**기술 스택** · Python · FastAPI · SQLAlchemy · Alembic · PostgreSQL(로컬 SQLite) · React 19 · Vite · TypeScript · Gemini / Claude(구조화 출력) · SSE · Docker

### 핵심 포인트

- **메일 한 통 → 끝난 업무** — 받은 뒤(판별 · 요약 · 할 일 · 기한 · 캘린더), 답장(근거 있는 초안 · 확인 후 발송), 보낸 뒤(무응답 · 재답장 반영)까지 한 흐름입니다.
- **근거를 보입니다** — 기한 근거 문장, 바뀐 내용의 근거, 초안 값의 자료 줄. 근거가 없는 값은 빈칸 `[[ ]]`으로 남깁니다.
- **되돌릴 수 없는 일은 사람이** — 답장은 화면 안 확인 단계를 거쳐야만 나가고, 받는 사람은 서버가 정합니다.
- **외부 콘텐츠는 데이터로만** — 메일 · 자료 속 지시문은 격리 태그와 탐지 규칙으로 막고 보안 경고로 남깁니다.
- **실제 데이터를 LLM에 보내는 기능은 기본 꺼짐** — `MAIL_TRIAGE_ENABLED`, `KNOWLEDGE_ENABLED`.

**목차** · [진행 상황](#진행-상황) · [구조](#구조) · [보안](#보안) · [로컬 실행](#로컬-실행) · [Railway 배포](#railway-배포) · [범위 밖](#범위-밖-의도적으로-제외)

## 진행 상황

| 단계 | 상태 |
|---|---|
| Gmail 연결 · 메일 동기화 (설계 1 · 2단계) | 완료 (로컬 기준) |
| 업무 메일 판별과 접수 (설계 3단계) | 완료 (실제 계정 확인) — [구현 계획](docs/superpowers/plans/2026-10-26-mail-triage-and-intake.md) |
| Google 캘린더 연동 (설계 4단계) | 완료 (실제 캘린더 확인) — [구현 계획](docs/superpowers/plans/2026-10-26-calendar-sync.md) |
| 받은 업무 화면 (설계 5단계) | 완료 (실제 계정 확인) — [설계](docs/superpowers/specs/2026-10-26-inbox-screen-design.md) · [구현 계획](docs/superpowers/plans/2026-10-26-inbox-screen.md) |
| 답장과 수명주기 (설계 6단계) | 완료 (실제 계정 확인) — [설계](docs/superpowers/specs/2026-10-27-reply-and-lifecycle-design.md) · [구현 계획](docs/superpowers/plans/2026-10-27-reply-and-lifecycle.md) · [정확도 측정](docs/evals/2026-10-27-reply-lifecycle.md) |
| RAG 1단계: 내 자료로 답장 초안 채우기 | 완료 (로컬 점검, 가상 자료) — [설계](docs/superpowers/specs/2026-10-29-rag-documents-design.md) · [구현 계획](docs/superpowers/plans/2026-10-29-rag-documents.md) · [정확도 측정](docs/evals/2026-10-29-rag-documents.md) |
| 화면 전면 개편 (견적서 · 거래명세서 양식) | 완료 (가상 데이터 점검) — [설계](docs/superpowers/specs/2026-10-31-ui-redesign-design.md) · [디자인 체계](DESIGN.md) |
| 데모 모드 · 목표 입력 Agent 제거 | 완료 — [설계](docs/superpowers/specs/2026-10-31-remove-demo-and-goal-agent-design.md) · [구현 계획](docs/superpowers/plans/2026-10-31-remove-demo-and-goal-agent.md) |
| 정리 (설계 7단계) | 예정 — [설계 문서](docs/superpowers/specs/2026-10-10-gmail-calendar-design.md) |
| RAG 다음 단계 (업무 요약 · 메일 색인 · 문체, 의미 검색) | 예정 — [상위 설계](docs/superpowers/specs/2026-10-26-rag-design.md) |

해커톤 PoC(목표 입력 → Agent 도구 실행 → 승인 → 재개, 가상 메일 데모)는 2026-10-31에 지웠습니다. 기록은 git 이력과 [변경 이력](CHANGELOG.md)에 있습니다.

**문서** · [변경 이력](CHANGELOG.md) · [결정 기록](docs/decisions.md) · [데이터 · 개인정보 흐름](docs/privacy.md) · [문제 해결](docs/troubleshooting.md) · [설계 문서 · 구현 계획](docs/superpowers/) · [제품 기준](PRODUCT.md) · [디자인 체계](DESIGN.md)

## 구조

```
React (Vite, TS)  ──same-origin cookie──▶  FastAPI   (업무 상세는 SSE `/api/tasks/{id}/events`로 실시간 수신)
                                             ├─ api/        auth · inbox · tasks(메일 업무 동작) · google · knowledge
                                             ├─ google/     OAuth · Gmail · 캘린더 클라이언트
                                             ├─ mail/       동기화 · 판별 · 접수 · 재답장 · 합치기 · 답장 초안 · 발송 · 캘린더 반영
                                             ├─ knowledge/  자료 글 뽑기 · 덩어리 · 키워드 검색 · 초안 출처 확인
                                             ├─ agent/      llm.py · gemini_llm.py (구조화 출력 호출) · guard.py (외부 콘텐츠 격리 · 주입 탐지) · audit.py (처리 기록)
                                             └─ models.py  SQLAlchemy → PostgreSQL (로컬: SQLite) · Alembic
```

### 실시간 갱신 (SSE)
업무 상세 화면은 폴링 대신 `GET /api/tasks/{id}/events`에 EventSource로 붙습니다. 서버는 연결 직후 전체 업무 상세를 1회 보내고,
이후 0.4초 간격으로 변경 지문(업무 updated_at · 마지막 기록 id 등)을 확인해 바뀐 경우에만 `event: task`로 다시 보냅니다.
변화가 없으면 15초마다 주석 줄로 연결만 유지합니다. 인증은 기존 쿠키, 소유권 검사는 스트림 시작 전에 수행(남의 업무 404)합니다.

### LLM
`LLM_PROVIDER`로 Gemini/Claude를 선택합니다. 판별 · 분석 · 재답장 · 답장 초안은 모두 구조화 출력(JSON 스키마) 1회 호출이고, 결과는 코드가 다시 확인합니다(날짜 · 근거 문장 · 숫자). 무료 등급 분당 한도는 `LLM_RPM_LIMIT`로 간격을 둡니다.

## 보안
- **인증**: bcrypt 해시, HttpOnly + SameSite=Lax(+ 운영 Secure) JWT 쿠키, 로그인/가입 rate limit.
- **데이터 격리**: 모든 조회는 `user_id` 조건 포함. 남의 ID는 404(존재 여부 비노출). 테스트로 검증.
- **CSRF**: 상태 변경 API는 `X-Requested-With` 헤더 + Origin 허용 목록 필수.
- **Prompt Injection**: 메일 · 자료는 데이터로만 취급(태그 격리 + 정규식 탐지 + 보안 경고 기록). 받는 사람은 LLM이 아니라 서버가 정합니다.
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

### Google 연결 준비 (로컬)

Gmail을 연결하려면 본인 Google Cloud 프로젝트에 OAuth 클라이언트를 만들어야 합니다. 설정하지 않으면 Google 기능만 꺼지고 나머지는 그대로 동작합니다.

1. [Google Cloud Console](https://console.cloud.google.com/)에서 새 프로젝트를 만들고 **Gmail API**와 **Google Calendar API**를 사용 설정합니다.
2. Google Auth Platform → **대상(Audience)**: 사용자 유형 **외부**, 게시 상태 **테스트**로 두고, 테스트 사용자에 본인 Gmail 주소를 추가합니다.
3. **데이터 액세스**에 다음 권한을 추가합니다.
   - `.../auth/gmail.readonly`
   - `.../auth/gmail.send`
   - `.../auth/calendar.events`
   - `openid`
   - `.../auth/userinfo.email`
4. **클라이언트** → 클라이언트 만들기 → 유형 **웹 애플리케이션**. 승인된 리디렉션 URI에 다음 두 개를 넣습니다.
   - `http://localhost:5173/api/google/callback`
   - `http://localhost:8000/api/google/callback`
5. `backend/.env`에 값을 넣습니다. 비밀값이므로 커밋하지 마세요.
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   GOOGLE_TOKEN_KEY=...   # python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
   PUBLIC_URL=http://localhost:5173   # 빌드본을 8000에서 볼 때는 http://localhost:8000
   ```
6. `alembic upgrade head` 후 서버를 다시 띄우고, 앱의 **설정** 화면에서 "Google 계정 연결"을 누릅니다. 연결되면 최근 3일치 받은편지함 메일을 가져오고, 이후 90초마다 새 메일을 확인합니다.

- 브라우저 주소는 `PUBLIC_URL`과 같은 호스트(`localhost`)로 여세요. `127.0.0.1`로 열면 로그인 쿠키가 달라 연결이 실패합니다.
- 테스트 상태 앱은 Google 정책상 갱신 토큰이 **7일 뒤 만료**됩니다. 만료되면 앱 상단 빨간 배너에서 다시 연결하세요.
- 연결만 하면 메일을 가져와 저장하는 데까지 동작합니다. 업무 판별 · 요약 · 답장 초안은 아래 "업무 메일 자동 판별 켜기"로 스위치를 켜야 동작합니다. 기한이 있는 업무는 캘린더 일정으로 들어갑니다.
- 답장은 업무 상세의 초안 카드에서 사용자가 확인하고 보내기를 눌렀을 때만 원래 Gmail 스레드로 나갑니다. Gmail에서 직접 보낸 답장도 감지해 "답 기다림"으로 옮깁니다.

### 업무 메일 자동 판별 켜기

`backend/.env`에 `MAIL_TRIAGE_ENABLED=true`를 넣고 서버를 재시작하면, 가져온 메일에서 업무 메일을 골라 요약 · 할 일 · 기한이 담긴 업무를 자동으로 만듭니다. 기본값은 꺼짐입니다.
같은 스위치로 재답장 분석(상대가 바꾼 내용 반영), 다른 스레드 수정 요청 판단, 답장 초안 만들기도 켜집니다.
켜면 메일의 보낸 사람 · 제목 · 본문 일부가 LLM 공급자로 전송됩니다. 전송 범위와 무료 등급 주의 사항은 [데이터 · 개인정보 흐름](docs/privacy.md)을 보세요.

### 내 자료로 답장 초안 채우기 (RAG 1단계)

`backend/.env`에 `KNOWLEDGE_ENABLED=true`를 넣고 서버를 재시작하면 왼쪽 메뉴 **자료실**에서 단가표 · 정책 문서 등을 올릴 수 있습니다(xlsx · csv · txt · md · pdf · docx). 기본값은 꺼짐이고, 답장 초안을 만들려면 위 판별 스위치도 켜져 있어야 합니다.
- 올린 자료는 기본이 **내부용**입니다. 답장에 쓰려면 **답장에 써도 됨**으로 바꿉니다. 그 자료 중 검색된 부분(최대 5덩어리)만 초안을 만들 때 LLM으로 갑니다. 내부용 자료는 밖으로 나가지 않습니다.
- 초안 카드에 "자료에서 가져온 내용"과 그 자료 줄이 보입니다. 내부용 자료의 값(예: 원가)이 초안에 들어 있으면 빨간 경고가 뜨고, "알고 보냅니다"를 체크해야 보낼 수 있습니다.
- 검색은 내 PC 안에서 키워드로 합니다. 품번 같은 낱말이 메일 · 메모에 있어야 잘 찾습니다. 문제가 생기면 [문제 해결](docs/troubleshooting.md#자료실--자료로-채운-답장-rag-1단계)을 보세요.

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

4. Settings → Networking → **Generate Domain**. Railway가 주입하는 `RAILWAY_PUBLIC_DOMAIN`으로 허용 호스트/Origin이 자동 설정됩니다(커스텀 도메인은 `PUBLIC_URL`, `ALLOWED_HOSTS` 추가).
5. 컨테이너 시작 시 `alembic upgrade head` 후 uvicorn 기동. 헬스체크 `/api/health`.

## 범위 밖 (의도적으로 제외)
기업 메일(Gmail 외) 연동, 자동 발송(사용자 승인 없이 나가는 메일), 모델 학습/파인튜닝, Multi-Agent, 결제, 모바일 앱.

설계 문서: [Gmail · Google 캘린더 연동](docs/superpowers/specs/2026-10-10-gmail-calendar-design.md) · [Agent 전체 구조와 RAG](docs/superpowers/specs/2026-10-26-rag-design.md)
