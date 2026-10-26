# Gmail 연결 · 메일 동기화 구현 계획 (설계 1 · 2단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬에서 실행한 앱에 본인 Google 계정을 연결하고, 받은편지함의 새 메일이 90초마다 DB에 쌓이는 것까지 만든다.

**Architecture:** Google 호출은 전부 `GoogleClient` 프로토콜 뒤에 둔다. 운영 구현은 httpx로 REST를 직접 부르고, 테스트는 메모리 안의 `FakeGoogle`로 바꿔 끼운다(`ScriptedLLM`과 같은 방식). 동기화는 FastAPI 프로세스 안의 데몬 스레드 하나가 돌리고, DB 임대(`sync_lease_until`)로 중복 실행을 막는다. 판별 · 접수 · 캘린더 · 답장은 이 계획에 없다 (3 ~ 7단계, 별도 계획).

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, httpx, cryptography(AES-GCM), Python 표준 `email` 패키지, React 19 + Vite.

**Spec:** `docs/superpowers/specs/2026-10-10-gmail-calendar-design.md` (2 · 3 · 4.1 · 4.2 · 6 · 7 · 8절)

**실행 환경:** 로컬 전용. 배포는 이 계획 범위 밖이다. 백엔드 `:8000`, Vite `:5173`(`/api`를 8000으로 프록시), `PUBLIC_URL=http://localhost:5173`.

## Global Constraints

- 권한(scope)은 정확히 `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/gmail.send`, `https://www.googleapis.com/auth/calendar.events`, `openid`, `email`. 삭제 · 수정 권한은 요청하지 않는다.
- 갱신 토큰은 `GOOGLE_TOKEN_KEY`(32바이트, base64)로 AES-GCM 암호화해서만 저장한다. 접근 토큰은 메모리에만 둔다.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_TOKEN_KEY` 중 하나라도 없으면 Google 기능 전체를 끈다(`google_enabled=False`). API는 503 대신 `{"enabled": false}` 상태를 돌려준다.
- OAuth state는 로그인한 사용자 id에 묶인 1회용 값, 10분 만료. 콜백 주소는 `{PUBLIC_URL}/api/google/callback`으로 고정한다.
- 비밀값은 환경변수로만 받는다. `.env.example`에는 이름만 둔다. `backend/.env`는 커밋하지 않고, 실제 값은 사용자가 직접 넣는다.
- 메일 본문(`content`)은 인용 · 전달 부분을 제거한 텍스트, 최대 20,000자. 제목은 300자, 주소는 255자로 자른다.
- 저장하는 메일: `INBOX` 라벨이 있고 `SENT` 라벨이 없는 메일만. 보낸 메일 처리는 6단계에서 추가한다.
- 기본값: `MAIL_SYNC_INTERVAL_SECONDS=90`, `MAIL_BACKFILL_DAYS=3`, 한 주기당 메일 조회 최대 200통(`MAIL_SYNC_MAX_PER_CYCLE`).
- 새 메일은 `triage="pending"`으로 저장한다. 판별은 3단계에서 한다.
- 로그에 메일 본문 · 토큰을 남기지 않는다. 메일 번호와 건수만 남긴다.
- 테스트 실행: `cd backend && ../.venv/Scripts/python -m pytest` (기존 44개 포함 전부 통과 유지).
- 커밋 메시지는 기존처럼 한국어 `영역: 내용` 형식이고, 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`를 붙인다.

## Review Focus

1. **동의 화면에서 권한 체크를 일부 해제한 경우.** Google은 권한을 하나씩 끌 수 있다. 받은 scope에 필수 권한이 빠졌으면 연결된 것처럼 보이면 안 된다. 계정을 저장하지 않고 "필요한 권한이 빠졌습니다"를 보여준다 (Task 4 테스트).
2. **토큰 응답에 `refresh_token`이 없는 경우.** 이전 동의가 남아 있으면 생길 수 있다. 저장하지 않고 다시 연결하라고 안내한다 (Task 4).
3. **동기화가 겹치는 경우.** "지금 확인" 버튼과 주기 스레드가 동시에 돌거나 서버가 재시작된 경우에도 같은 메일이 두 번 저장되면 안 된다 (Task 6 임대 · 중복 테스트).
4. **한국 메일의 인코딩과 형식.** `ks_c_5601-1987` / EUC-KR 본문, HTML만 있는 메일, 300자를 넘는 제목, 첨부만 있고 본문이 없는 메일도 예외 없이 저장된다 (Task 5).
5. **연결 해제 중 Google 취소 요청이 실패하는 경우.** 네트워크 오류가 나도 로컬 토큰은 반드시 지운다 (Task 4).

---

## 파일 구조

| 경로 | 책임 |
|---|---|
| `backend/app/config.py` (수정) | Google · 동기화 설정, `google_enabled` |
| `backend/app/google/__init__.py` | 빈 파일 |
| `backend/app/google/crypto.py` | 토큰 암호화 · 복호화 |
| `backend/app/google/client.py` | 데이터 클래스, 오류 타입, `GoogleClient` 프로토콜, `HttpGoogleClient`, 교체 훅 |
| `backend/app/google/oauth.py` | state 쿠키, PKCE, 접근 토큰 캐시와 `reauth_required` 전환 |
| `backend/app/api/google.py` | `/api/google/*` 라우터 |
| `backend/app/mail/__init__.py` | 빈 파일 |
| `backend/app/mail/parse.py` | RFC 822 원문 → `ParsedMail` |
| `backend/app/mail/sync.py` | 임대, 계정 1개 동기화, 주기 스레드 |
| `backend/app/models.py` (수정) | `GoogleAccount`, `Email` 확장 |
| `backend/alembic/versions/c4d8e2f1a9b3_google_accounts.py` | 마이그레이션 |
| `backend/tests/fake_google.py` | `FakeGoogle`, `make_raw()` |
| `frontend/src/pages/Settings.tsx` | 설정 화면 |
| `frontend/src/components/GoogleBanner.tsx` | 재연결 배너 |

---

### Task 1: 설정과 토큰 암호화

**Files:**
- Modify: `backend/app/config.py`, `backend/requirements.txt`, `.env.example`
- Create: `backend/app/google/__init__.py`, `backend/app/google/crypto.py`
- Test: `backend/tests/test_google_crypto.py`

**Interfaces:**
- Produces:
  - `Settings` 필드 `google_client_id: str = ""`, `google_client_secret: str = ""`, `google_token_key: str = ""`, `mail_sync_interval_seconds: int = 90` (ge=30), `mail_backfill_days: int = 3` (ge=1, le=30), `mail_sync_max_per_cycle: int = 200`, `mail_sync_enabled: bool = True`, `user_timezone: str = "Asia/Seoul"`
  - `Settings.google_enabled -> bool` (세 값이 모두 있으면 True)
  - `Settings.google_redirect_uri -> str` = `public_url.rstrip("/") + "/api/google/callback"`
  - `encrypt_token(plain: str) -> str`, `decrypt_token(blob: str) -> str` (`app/google/crypto.py`)
  - `TokenDecryptError(Exception)`

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_google_crypto.py`)

```python
def test_roundtrip(monkeypatch): ...        # encrypt → decrypt == "1//refresh-abc"
def test_ciphertext_differs_each_time(): ... # 같은 평문 두 번 암호화 결과가 다름 (nonce)
def test_tampered_blob_raises(): ...         # 마지막 글자를 바꾸면 TokenDecryptError
def test_wrong_key_raises(): ...             # 다른 키로 복호화하면 TokenDecryptError
def test_google_disabled_without_key(): ...  # client_id/secret만 있고 key 없음 → google_enabled is False
def test_invalid_key_length_rejected(): ...  # 16바이트 키 → Settings() 생성 시 ValueError
```

테스트에서는 `Settings(google_client_id=..., google_token_key=base64(32바이트))`를 직접 만들고, `crypto` 함수는 `key` 키워드 인자로 받은 키를 쓰게 해서 전역 설정을 건드리지 않는다.

- [ ] **Step 2: 실패 확인** — `../.venv/Scripts/python -m pytest tests/test_google_crypto.py -v` → ImportError

- [ ] **Step 3: 구현**
  - `encrypt_token(plain, *, key: bytes | None = None)`. 반환 형식은 `"v1:" + base64url(nonce12 + ciphertext)`. `key`가 없으면 설정의 키를 쓴다. AES-GCM에는 `cryptography.hazmat.primitives.ciphers.aead.AESGCM`을 쓴다.
  - `google_token_key` 검증기: 비어 있으면 통과. 값이 있으면 base64로 해독해서 정확히 32바이트가 아니면 `ValueError("GOOGLE_TOKEN_KEY must be 32 bytes, base64-encoded")`.
  - `requirements.txt`: `cryptography`와 `httpx`를 추가한다 (httpx는 지금 dev에만 있다). 버전은 `pip index versions`로 확인한 최신 안정판으로 `==` 고정한다.
  - `.env.example`: 위 설정 이름들과 키 생성 명령 주석을 추가한다.
    `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`
  - `tests/conftest.py`: `os.environ["MAIL_SYNC_ENABLED"] = "false"`를 추가한다. 테스트에서 스레드가 뜨지 않게 하기 위해서다.

- [ ] **Step 4: 통과 확인** — 같은 명령 → 6 passed. 전체 스위트도 통과.

- [ ] **Step 5: 커밋** — `Google 연결: 설정 항목과 갱신 토큰 AES-GCM 암호화`

---

### Task 2: 데이터 모델과 마이그레이션

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/c4d8e2f1a9b3_google_accounts.py` (down_revision `b7c2d1e4f5a6`)
- Test: `backend/tests/test_google_models.py`

**Interfaces:**
- Produces:
  - `GoogleAccountStatus(StrEnum)`: `CONNECTED="connected"`, `REAUTH_REQUIRED="reauth_required"`
    (해제하면 행을 지우므로 `disconnected`는 저장하지 않는다. 상태 API가 행 없음을 `disconnected`로 표시한다.)
  - `GoogleAccount` 테이블 `google_accounts`: `id`, `user_id`(FK, **unique**), `google_email: str(255)`, `refresh_token_enc: Text`, `scopes: JSON list`, `history_id: str(40) | None`, `last_synced_at`, `last_sync_error: Text = ""`, `status: str(20)`, `sync_lease_until: datetime | None`, `created_at`, `updated_at`
  - `Email`에 추가하는 컬럼(모두 nullable 또는 기본값 있음):
    `gmail_message_id: str(64) | None`, `gmail_thread_id: str(64) | None` (index), `rfc_message_id: str(500) | None`, `cc: JSON = []`, `received_at: datetime | None`, `triage: str(20) | None` (index), `triage_reason: Text = ""`, `attachments: JSON = []`, `gmail_meta: JSON = {}`
  - `Email`에 `UniqueConstraint("user_id", "gmail_message_id", name="uq_emails_user_gmail_id")`

  `gmail_meta`는 설계에 없는 컬럼이다. 3단계 규칙 필터가 쓸 값(`labels`, `list_unsubscribe: bool`, `precedence: str`, `sender_name: str`)을 여기 둔다. 헤더 원문 전체는 저장하지 않는다.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_gmail_message_id_unique_per_user(): ...   # 같은 user + 같은 gmail id 두 번 → IntegrityError
def test_same_gmail_id_other_user_ok(): ...         # 다른 user면 허용
def test_seed_emails_have_null_gmail_fields(): ...  # register 후 기존 시드 메일은 gmail_message_id None, triage None
def test_one_google_account_per_user(): ...         # 같은 user_id 두 번 → IntegrityError
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 모델과 마이그레이션 구현.** SQLite에서 컬럼과 제약을 추가할 때는 `op.batch_alter_table("emails")`를 쓴다. JSON 컬럼은 기존 `usage` 마이그레이션처럼 `server_default`를 준다.
- [ ] **Step 4: 통과 확인.** 그다음 로컬 DB에 적용한다.
  - `cp dev.db dev.db.bak-20261026`
  - `../.venv/Scripts/python -m alembic upgrade head` → 오류 없음
  - `sqlite3` 또는 파이썬으로 `PRAGMA table_info(emails)`를 확인해 새 컬럼이 있는지 본다.
  - 개발 서버는 `create_all`만 하고 기존 테이블에 컬럼을 추가하지 않는다. 그래서 이 업그레이드를 빼먹으면 서버가 깨진다.
- [ ] **Step 5: 커밋** — `Google 연결: google_accounts 테이블과 메일 Gmail 컬럼 추가`

---

### Task 3: GoogleClient 프로토콜, httpx 구현, FakeGoogle

**Files:**
- Create: `backend/app/google/client.py`, `backend/tests/fake_google.py`
- Test: `backend/tests/test_google_client.py`

**Interfaces:**
- Produces (`app/google/client.py`):

```python
@dataclass(frozen=True)
class TokenGrant:  access_token: str; refresh_token: str | None; expires_in: int; scopes: list[str]
@dataclass(frozen=True)
class AccessToken: token: str; expires_at: datetime
@dataclass(frozen=True)
class GmailProfile: email: str; history_id: str
@dataclass(frozen=True)
class IdPage: message_ids: list[str]; next_page_token: str | None; history_id: str | None = None
@dataclass(frozen=True)
class RawMessage: id: str; thread_id: str; label_ids: list[str]; internal_date_ms: int; raw: bytes

class GoogleError(Exception)            # 기본
class GoogleAuthError(GoogleError)      # invalid_grant / 401 → 재연결 필요
class GoogleHistoryGone(GoogleError)    # history.list 404
class GoogleTransientError(GoogleError) # 429, 5xx, 네트워크 오류

class GoogleClient(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str
    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenGrant
    def refresh_access_token(self, refresh_token: str) -> AccessToken
    def revoke(self, token: str) -> None
    def gmail_profile(self, access_token: str) -> GmailProfile
    def gmail_history(self, access_token: str, start_history_id: str, page_token: str | None) -> IdPage
    def gmail_list(self, access_token: str, query: str, page_token: str | None) -> IdPage
    def gmail_get_raw(self, access_token: str, message_id: str) -> RawMessage

def get_google_client() -> GoogleClient
def set_google_override(client: GoogleClient | None) -> None
REQUIRED_SCOPES: tuple[str, ...]   # Global Constraints의 5개
```

- Produces (`tests/fake_google.py`):
  - `make_raw(*, sender, to, subject, body="", html=None, cc=None, message_id=None, headers=None, attachments=(), charset="utf-8") -> bytes` (`email.message.EmailMessage`로 생성)
  - `FakeGoogle` (GoogleClient 구현). 속성:
    - `mailbox: dict[str, RawMessage]`
    - `history_id: int` (시작값 1000)
    - `valid_refresh_tokens: set[str]`
    - `grant: TokenGrant` (exchange_code가 돌려줄 값)
    - `history_gone: bool`
    - `revoked: list[str]`, `revoke_fails: bool`
    - `get_calls: list[str]`
  - `FakeGoogle.deliver(raw: bytes, *, labels=("INBOX",), thread_id=None) -> str`: 메일을 추가하고 `history_id`를 1 올린다. 히스토리 기록에 `(history_id, message_id)`를 남긴다.
  - `gmail_history`는 `start_history_id` 이후 기록만 돌려주고, `history_gone`이면 `GoogleHistoryGone`을 낸다. `gmail_list`는 `after:<epoch>` 조건만 해석해 `internal_date_ms`로 거른다.

- [ ] **Step 1: httpx 구현 테스트 작성** (`httpx.MockTransport`로 요청 모양 검증)

```python
def test_authorization_url_params():
    # access_type=offline, prompt=consent, include_granted_scopes=true, code_challenge_method=S256,
    # response_type=code, scope에 REQUIRED_SCOPES가 공백으로 이어져 있음, redirect_uri 일치
def test_exchange_code_parses_scopes(): ...        # "scope": "a b c" → scopes == ["a","b","c"]
def test_refresh_invalid_grant_raises_auth_error(): ...  # 400 {"error":"invalid_grant"} → GoogleAuthError
def test_history_404_raises_gone(): ...
def test_429_and_503_raise_transient(): ...
def test_network_error_raises_transient(): ...     # httpx.ConnectError → GoogleTransientError
def test_get_raw_decodes_base64url(): ...          # format=raw, 패딩 없는 base64url 해독
def test_history_collects_messages_added(): ...    # history[].messagesAdded[].message.id 평탄화, 중복 제거, historyId 반환
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: `HttpGoogleClient(transport: httpx.BaseTransport | None = None)` 구현**
  - 엔드포인트:
    - 인증 `https://accounts.google.com/o/oauth2/v2/auth`
    - 토큰 `https://oauth2.googleapis.com/token`
    - 취소 `https://oauth2.googleapis.com/revoke`
    - Gmail `https://gmail.googleapis.com/gmail/v1/users/me/{profile|history|messages|messages/{id}?format=raw}`
  - `gmail_history`에는 `historyTypes=messageAdded`를 붙인다.
  - 타임아웃은 20초. `get_google_client()`는 오버라이드가 없으면 모듈 단일 인스턴스를 돌려준다.
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `Google 연결: GoogleClient 프로토콜과 httpx 구현, 테스트용 FakeGoogle`

---

### Task 4: OAuth 연결 · 상태 · 해제 API

**Files:**
- Create: `backend/app/google/oauth.py`, `backend/app/api/google.py`, `backend/app/mail/__init__.py`, `backend/app/mail/sync.py` (이 단계에서는 `def wake() -> None: pass`만)
- Modify: `backend/app/main.py` (라우터 등록), `backend/app/schemas.py`
- Test: `backend/tests/test_google_oauth.py`

**Interfaces:**
- Consumes: Task 1 crypto와 설정, Task 2 `GoogleAccount`, Task 3 `GoogleClient`와 `FakeGoogle`
- Produces:
  - `app/google/oauth.py`:
    - `new_pkce() -> tuple[str, str]` (verifier, S256 challenge)
    - `make_state_cookie(user_id: int, state: str, verifier: str) -> str` (설정의 secret_key로 서명한 JWT, 10분 만료)
    - `read_state_cookie(value: str) -> dict | None`
    - `access_token_for(db, account, client) -> str`: 메모리 캐시를 쓰고, 만료 60초 전에 갱신한다. `GoogleAuthError`가 나면 `account.status = REAUTH_REQUIRED`, `last_sync_error = "Google 재연결이 필요합니다."`로 바꾸고 커밋한 뒤 다시 raise한다.
    - `forget_access_token(user_id) -> None`
  - 쿠키 이름 `google_oauth`, `path=/api/google`, HttpOnly, SameSite=Lax, 운영에서 Secure
  - `GoogleStatusOut`:
    - `enabled: bool`
    - `status: Literal["disconnected","connected","reauth_required"]`
    - `google_email: str | None`, `scopes: list[str]`
    - `last_synced_at: datetime | None`, `last_sync_error: str`
    - `mail_count: int`, `recent: list[RecentMailOut]` (`subject`, `sender`, `received_at`, 최근 5건)
  - 라우트 (`/api/google`):
    - `GET /status` → `GoogleStatusOut`
    - `GET /connect` → 302 Google. 로그인 필요, `google_enabled` 아니면 404.
    - `GET /callback?code&state` 또는 `?error=` → 302 `/settings?google=connected` 또는 `/settings?google=error&reason=<code>`
      - `reason` 코드: `denied`, `state`, `scopes`, `no_refresh_token`, `exchange`
    - `POST /disconnect` 본문 `{"delete_mail": bool}` → 204
  - 콜백이 성공하면 `app.mail.sync.wake()`를 호출한다 (실제 동작은 Task 7에서 구현한다).

- [ ] **Step 1: 실패하는 테스트** (FakeGoogle을 `set_google_override`로 끼우고, 설정은 monkeypatch로 Google 활성화)

```python
def test_status_disabled_when_not_configured(client): ...   # enabled False, status "disconnected"
def test_connect_redirects_with_state_cookie(client): ...    # 302, Location이 accounts.google.com, google_oauth 쿠키 설정
def test_callback_stores_encrypted_token(client): ...        # connect → callback(state 일치) → 302 connected,
                                                             #   DB의 refresh_token_enc != 평문, decrypt == 평문, status connected, google_email == profile
def test_callback_state_mismatch_rejected(client): ...       # reason=state, 계정 없음
def test_callback_state_reuse_rejected(client): ...          # 같은 쿠키로 두 번째 콜백 → reason=state (쿠키 삭제됨)
def test_callback_other_user_session_rejected(client): ...   # A가 connect, B로 로그인해 콜백 → reason=state
def test_callback_missing_scope(client): ...                 # grant.scopes에 gmail.send 없음 → reason=scopes, 계정 없음   [Review Focus 1]
def test_callback_without_refresh_token(client): ...         # grant.refresh_token None → reason=no_refresh_token          [Review Focus 2]
def test_callback_user_denied(client): ...                   # ?error=access_denied → reason=denied
def test_reconnect_updates_existing_row(client): ...         # 두 번 연결 → 행 1개, 토큰 교체, status connected
def test_disconnect_revokes_and_deletes(client): ...         # FakeGoogle.revoked에 토큰, 계정 행 없음, status disconnected
def test_disconnect_when_revoke_fails(client): ...           # revoke_fails=True여도 204, 계정 행 없음                      [Review Focus 5]
def test_disconnect_delete_mail(client): ...                 # delete_mail=True → gmail_message_id 있는 메일만 삭제, 시드 메일은 남음
def test_status_requires_login(): ...                        # 401
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현.** 콜백의 순서는 이렇다.
  1. `error` 파라미터를 확인한다.
  2. 쿠키를 읽고 `uid`가 현재 세션 사용자와 같은지, `state`가 같은지 비교한다 (`hmac.compare_digest`).
  3. 쿠키를 지운다.
  4. `exchange_code`를 호출한다.
  5. scope를 검사한다.
  6. refresh_token이 있는지 확인한다.
  7. `gmail_profile`로 이메일을 가져온다.
  8. upsert한다 (재연결이면 `history_id`는 유지).
  9. `wake()`를 호출한다.

  콜백은 GET이라 CSRF 미들웨어 대상이 아니다. 세션 쿠키가 없으면 `/login`으로 302한다.
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `Google 연결: OAuth 연결·콜백·상태·해제 API`

---

### Task 5: 메일 파싱

**Files:**
- Create: `backend/app/mail/parse.py`
- Test: `backend/tests/test_mail_parse.py`

**Interfaces:**
- Produces:

```python
@dataclass
class ParsedMail:
    sender_email: str; sender_name: str
    to: list[str]; cc: list[str]
    subject: str            # 최대 300자
    text: str               # 인용 제거 후 최대 20,000자
    rfc_message_id: str | None
    in_reply_to: str | None
    references: list[str]
    list_unsubscribe: bool
    precedence: str          # 소문자, 없으면 ""
    attachments: list[str]   # 파일 이름만
    sent_at: datetime | None # Date 헤더, UTC

def parse_raw(raw: bytes) -> ParsedMail
def strip_quoted(text: str) -> str
def html_to_text(html: str) -> str
```

- [ ] **Step 1: 실패하는 테스트** (`make_raw`로 입력 생성)

```python
def test_plain_utf8(): ...                 # 보낸 사람 "홍길동 <hong@acme.co.kr>" → name/email 분리, 제목·본문 일치
def test_prefers_plain_over_html(): ...
def test_html_only_converted(): ...        # "<p>안녕하세요<br>견적 부탁드립니다</p><script>x</script>" → 두 줄 텍스트, script 내용 없음   [RF 4]
def test_ks_c_5601_body(): ...             # charset="ks_c_5601-1987"로 인코딩한 한국어 본문이 정상 해독                                [RF 4]
def test_encoded_word_subject(): ...       # =?UTF-8?B?...?= 제목 해독
def test_long_subject_truncated(): ...     # 400자 제목 → 300자                                                                    [RF 4]
def test_attachment_only(): ...            # 본문 없음 + 첨부 "견적서.pdf" → text "", attachments ["견적서.pdf"]                     [RF 4]
def test_strip_gmail_korean_quote(): ...   # "확인했습니다.\n\n2026년 10월 10일 (금) 오후 3:00, 김 <k@x.com>님이 작성:\n> 이전" → "확인했습니다."
def test_strip_english_quote(): ...        # "On Fri, Oct 10, 2026 at 3:00 PM Kim <k@x.com> wrote:" 이후 제거
def test_strip_outlook_original(): ...     # "-----Original Message-----" / "-----원본 메시지-----" 이후 제거
def test_strip_gt_lines(): ...             # ">"로 시작하는 줄 제거
def test_headers(): ...                    # Message-ID, In-Reply-To, References 목록, List-Unsubscribe 있음 → True, Precedence: Bulk → "bulk"
def test_cc_and_multiple_to(): ...
def test_body_capped(): ...                # 30,000자 본문 → 20,000자
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
  - `email.message_from_bytes(raw, policy=email.policy.default)`로 읽고, `get_body(preferencelist=("plain", "html"))`로 본문을 고른다.
  - 해독이 실패하면 `get_payload(decode=True).decode(charset or "utf-8", errors="replace")`로 대체한다.
  - `html_to_text`는 `html.parser.HTMLParser` 하위 클래스로 만든다. `script`와 `style`은 버리고, `br`과 `p`, `div`, `li`, `tr`은 줄바꿈으로 바꾼다. `html.unescape`도 적용한다.
  - 인용 시작 패턴은 아래 네 가지다. 첫 번째로 맞는 줄부터 끝까지 자르고, 앞뒤 공백을 정리한다.
    - `^On .+wrote:$`
    - `^\d{4}년 \d{1,2}월 \d{1,2}일 .+(님이 작성|작성):$`
    - `^-{2,}\s*(Original Message|원본 메시지)\s*-{2,}`
    - `^From: .+` 바로 뒤에 `^(Sent|Date|보낸 날짜):` 줄이 오는 경우
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `메일 동기화: Gmail 원문 파싱과 인용 제거`

---

### Task 6: 계정 1개 동기화 (백필 · 변경 내역 · 404 · 임대 · 중복)

**Files:**
- Modify: `backend/app/mail/sync.py` (Task 4의 `wake` 자리 유지)
- Test: `backend/tests/test_mail_sync.py`

**Interfaces:**
- Consumes:
  - `access_token_for` (Task 4)
  - `GoogleClient`와 오류 타입 (Task 3)
  - `parse_raw` (Task 5)
- Produces:
  - `acquire_lease(db, account_id: int, now: datetime, seconds: int = 300) -> bool`
    - `sync_lease_until`가 비었거나 지났을 때만 조건부 UPDATE로 잡는다. 바뀐 행 수가 1이면 True.
  - `release_lease(db, account_id: int) -> None`
  - `@dataclass SyncResult: listed: int; stored: int; skipped_sent: int; complete: bool`
  - `sync_account(db, account: GoogleAccount, client: GoogleClient, now: datetime | None = None) -> SyncResult`

- [ ] **Step 1: 실패하는 테스트** (등록 → 계정 행 직접 생성 → FakeGoogle에 메일 배달 → `sync_account` 호출)

```python
def test_first_sync_backfills_recent_inbox(): ...      # 2일 전 메일 1, 5일 전 메일 1 → 2일 전만 저장 (MAIL_BACKFILL_DAYS=3),
                                                       #   triage "pending", direction INBOUND, history_id == profile 값
def test_saved_fields(): ...                           # gmail_message_id, thread_id, rfc_message_id, cc, received_at(internalDate), attachments,
                                                       #   gmail_meta.labels / list_unsubscribe / sender_name
def test_incremental_uses_history(): ...               # 첫 동기화 후 새 메일 배달 → 두 번째 동기화가 gmail_history만 호출, 1건 저장
def test_sent_and_non_inbox_skipped(): ...             # labels ("SENT",), ("INBOX","SENT"), ("DRAFT",) → 저장 안 됨, skipped_sent 집계
def test_dedupe_no_refetch(): ...                      # 같은 동기화를 두 번 → get_calls에 같은 id 없음, 행 수 그대로        [RF 3]
def test_history_gone_falls_back_to_query(): ...       # history_gone=True → gmail_list(after: last_synced_at-1일) 사용,
                                                       #   이미 있는 메일 중복 없음, history_id 갱신
def test_cap_defers_history_advance(): ...             # max_per_cycle=2, 새 메일 3통 → 1차 stored 2, complete False, history_id 그대로;
                                                       #   2차 stored 1, complete True, history_id 전진
def test_auth_error_marks_reauth(): ...                # valid_refresh_tokens 비움 → status reauth_required, 저장 0, 예외 밖으로 안 나감
def test_transient_error_records_message(): ...        # gmail_get_raw가 GoogleTransientError → last_sync_error 설정, history_id 그대로
def test_success_clears_error_and_sets_synced_at(): ...
def test_lease_blocks_second_holder(): ...             # acquire 두 번 → True, False; 만료 시각 지나면 다시 True             [RF 3]
def test_parse_failure_skips_one_mail(): ...           # raw가 깨진 메일 1통 + 정상 1통 → 정상 1통 저장, 깨진 메일은 id만 로그
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**

  **ID 목록 만들기**

  - **첫 동기화** (`history_id`가 없을 때)
    1. 먼저 `gmail_profile`로 history_id를 받아 둔다.
    2. `gmail_list(f"after:{epoch(now - backfill_days)} -in:spam -in:trash")`를 페이지 끝까지 돈다.
  - **이후 동기화**
    - `gmail_history`를 페이지 끝까지 돌고, 마지막 페이지의 `history_id`를 새 지점으로 쓴다.
    - `GoogleHistoryGone`이 나면 첫 동기화처럼 처리한다. 이때 기준은 `last_synced_at - 1일`이다.

  **처리**

  1. 이미 저장한 `gmail_message_id`를 한 번에 조회해서 뺀다.
  2. 남은 것 중 앞에서부터 `mail_sync_max_per_cycle`개만 `gmail_get_raw`로 가져온다.
  3. 라벨을 거른다. `INBOX`가 있고 `SENT`가 없는 메일만 남긴다.
  4. `parse_raw` 후 `Email` 행을 만든다. `receiver`에는 `to`를 쉼표로 이어 255자로 자른 값을 넣는다.
  5. 메일 1통마다 `IntegrityError`가 나면 롤백 후 건너뛴다 (savepoint 사용).

  **끝**

  - 남은 ID가 없을 때만 `history_id`를 전진시키고 `complete=True`로 둔다.
  - 성공하면 `last_synced_at=now`, `last_sync_error=""`로 바꾼다.
  - `GoogleTransientError`가 나면 지금까지 저장한 것은 커밋하고, `last_sync_error`에 짧은 한국어 문구를 남긴다.
  - `GoogleAuthError`가 나면 `access_token_for`가 상태를 바꿔 두었으므로 그대로 반환한다.
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `메일 동기화: 백필·변경 내역·404 재동기화·임대·중복 방지`

---

### Task 7: 주기 스레드와 "지금 확인" API

**Files:**
- Modify: `backend/app/mail/sync.py`, `backend/app/main.py` (lifespan), `backend/app/api/google.py`
- Test: `backend/tests/test_mail_sync_worker.py`

**Interfaces:**
- Consumes: Task 6 `sync_account`, `acquire_lease`, `release_lease`
- Produces:
  - `run_sync_cycle(now: datetime | None = None, user_id: int | None = None) -> int`
    - `status == connected`인 계정마다 새 세션을 열어 임대를 잡고, 동기화한 뒤 임대를 푼다.
    - 처리한 계정 수를 돌려준다. 계정 하나에서 예외가 나도 다음 계정은 계속 처리하고, 로그를 남긴다.
  - `MailSyncWorker`: `start()`, `stop()`, `wake()`
    - 데몬 스레드다. `threading.Event.wait(interval)` 또는 `wake` 신호마다 `run_sync_cycle()`을 실행한다.
  - 모듈 함수 `wake() -> None`: 워커가 있으면 깨우고, 없으면 아무것도 하지 않는다. Task 4의 임시 함수를 이것으로 바꾼다.
  - lifespan: `google_enabled and mail_sync_enabled`일 때만 워커를 시작하고, 종료할 때 `stop()`한다.
  - `POST /api/google/sync` → `run_in_threadpool(run_sync_cycle, user_id=user.id)` 후 `GoogleStatusOut`
    - 분당 3회로 제한한다 (`RateLimiter(3, 60)`, 키 `gsync:{uid}`).
    - 연결이 안 되어 있으면 409.

- [ ] **Step 1: 실패하는 테스트**

```python
def test_cycle_skips_reauth_and_other_users(): ...  # connected 1개, reauth 1개 → 1 반환, user_id 지정 시 그 사용자만
def test_cycle_continues_after_account_error(): ... # 첫 계정 sync에서 예외 → 두 번째 계정은 처리됨
def test_sync_now_endpoint(client): ...             # 메일 배달 → POST /api/google/sync → mail_count 1, recent[0].subject 일치
def test_sync_now_rate_limited(client): ...         # 4번째 호출 429
def test_sync_now_not_connected(client): ...        # 409
def test_worker_wake_runs_cycle(): ...              # interval 3600으로 start → wake() → 2초 안에 cycle 실행 (monkeypatch로 카운트), stop() 후 스레드 종료
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** (전체 스위트 포함)
- [ ] **Step 5: 커밋** — `메일 동기화: 90초 주기 스레드와 지금 확인 API`

---

### Task 8: 설정 화면과 재연결 배너

**Files:**
- Create: `frontend/src/pages/Settings.tsx`, `frontend/src/components/GoogleBanner.tsx`
- Modify: `frontend/src/App.tsx` (라우트 `/settings`, 상단 메뉴 "설정", 배너), `frontend/src/types.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/google/status`, `GET /api/google/connect`, `POST /api/google/sync`, `POST /api/google/disconnect`
- Produces: `GoogleStatus` 타입 (`GoogleStatusOut`와 같은 필드)

- [ ] **Step 1: 화면 구현**

  **설정 화면 (`/settings`)**

  | 상태 | 화면 |
  |---|---|
  | `enabled=false` | "서버에 Google 설정이 없습니다. README의 Google 연결 준비를 따라 주세요." |
  | `disconnected` | "Google 계정 연결" 버튼 (`window.location.href = "/api/google/connect"`) |
  | `connected` | 연결된 주소, 받은 권한, 마지막 확인 시각(`format.ts`의 기존 시각 함수), 오류 문구, 가져온 메일 수, 최근 5건(보낸 사람 · 제목 · 받은 시각), "지금 확인"(누르는 동안 비활성), "연결 해제" |
  | `reauth_required` | 빨간 안내와 "다시 연결" 버튼 |

  - "연결 해제"를 누르면 같은 카드 안에서 확인 단계를 보여준다. 브라우저 `confirm()`은 쓰지 않는다. "저장된 Gmail 메일도 삭제" 체크박스를 둔다.
  - 쿼리 `?google=connected`가 있으면 초록 안내 "연결되었습니다. 첫 메일을 가져오는 중입니다."를 보여준다. `?google=error&reason=`이 있으면 아래 문구를 빨간 안내로 보여준다.

  | `reason` | 문구 |
  |---|---|
  | `denied` | "Google 동의가 취소되었습니다." |
  | `state` | "연결 요청이 만료되었거나 올바르지 않습니다. 다시 시도해 주세요." |
  | `scopes` | "필요한 권한(메일 읽기 · 발송 · 캘린더)이 빠졌습니다. 모든 항목에 체크하고 다시 연결해 주세요." |
  | `no_refresh_token` | "Google에서 장기 토큰을 받지 못했습니다. Google 계정의 '타사 연결'에서 이 앱을 삭제한 뒤 다시 연결해 주세요." |
  | `exchange` | "Google 응답 처리 중 오류가 났습니다. 잠시 후 다시 시도해 주세요." |

  - 연결된 상태에서는 30초마다 상태를 다시 불러온다.

  **배너 (`GoogleBanner`)**

  - Shell 상단에서 라우트가 바뀔 때마다 상태를 확인한다.
  - `reauth_required`면 빨간 띠 "Google 연결이 만료되었습니다. [다시 연결]"을 보여준다.
  - 설정 화면에서는 같은 내용이 중복되므로 숨긴다.

- [ ] **Step 2: 빌드 확인** — `cd frontend && npm run build && npm run lint` → 오류 없음
- [ ] **Step 3: 커밋** — `Google 연결: 설정 화면과 재연결 배너`

---

### Task 9: Google Cloud 준비, 실제 계정 브라우저 점검, README

**Files:**
- Modify: `README.md` ("Google 연결 준비 (로컬)" 절)

- [ ] **Step 1: README에 사용자 준비 절차를 적는다.** 이 단계는 사용자가 직접 한다. 비밀값은 Claude가 보거나 출력하지 않는다.
  1. Google Cloud Console에서 새 프로젝트를 만들고 **Gmail API**와 **Google Calendar API**를 사용 설정한다.
  2. Google Auth Platform → 대상(Audience)에서 **외부 · 테스트** 상태로 둔다. 테스트 사용자에 본인 Gmail을 추가한다.
  3. 데이터 액세스에 Global Constraints의 5개 권한을 추가한다.
  4. 클라이언트 → **웹 애플리케이션**을 만든다. 승인된 리디렉션 URI에 `http://localhost:5173/api/google/callback`을 넣는다. 빌드본을 `:8000`에서 직접 볼 때를 위해 `http://localhost:8000/api/google/callback`도 넣는다.
  5. `backend/.env`에 아래 세 값을 넣는다. 8000으로 볼 때는 `PUBLIC_URL`도 그 주소로 바꾼다.
     - `GOOGLE_CLIENT_ID`
     - `GOOGLE_CLIENT_SECRET`
     - `GOOGLE_TOKEN_KEY` (생성 명령은 `.env.example`에 있다)
  6. 테스트 상태 앱은 갱신 토큰이 7일 뒤 만료된다. 만료되면 앱 상단 배너에서 다시 연결한다.

- [ ] **Step 2: 로컬 실행.** 백엔드와 Vite를 띄운 뒤 Claude in Chrome으로 점검한다. 사용자 세션 쿠키를 건드리지 않도록 `127.0.0.1` 쪽 테스트 계정을 쓴다. 단, Google 리디렉션 URI가 `localhost`라면 실제 연결 단계는 사용자가 직접 누른다. Google 로그인과 동의 화면은 Claude가 입력하지 않는다.

  점검 목록:
  - [ ] 설정 화면이 미연결 상태로 보인다.
  - [ ] 사용자가 연결 → 동의 → `connected` 안내 → 90초 안에 최근 3일 받은편지함 메일이 목록에 나타난다.
  - [ ] 본인에게 테스트 메일을 하나 보낸 뒤 "지금 확인" → 1건 늘어난다. 같은 버튼을 다시 눌러도 늘지 않는다.
  - [ ] 프로모션 메일도 지금은 저장되고 `triage=pending`이다. 판별은 3단계에서 한다. 내가 보낸 메일은 저장되지 않는다.
  - [ ] 동의 화면에서 권한 하나를 끄고 연결하면 `scopes` 안내가 나온다.
  - [ ] 연결 해제(메일 삭제 체크) → 미연결 상태, 가져온 메일 0건. myaccount.google.com의 타사 연결 목록에서도 사라진다.
  - [ ] 서버를 재시작해도 중복 메일이 생기지 않는다.

- [ ] **Step 3: 발견한 문제를 고치고**, 해당 테스트를 추가한 뒤 다시 점검한다.
- [ ] **Step 4: 커밋 후 push** — `README: 로컬 Google 연결 준비 절차`

---

## 이 계획 다음

3 ~ 7단계는 이 계획이 끝나고 실제 메일이 쌓인 상태를 본 뒤 별도 계획으로 쓴다.

- 3단계(판별 · 접수): `triage="pending"` 메일을 처리한다.
- 6단계: 보낸 메일 처리를 `sync_account`의 라벨 분기에 추가한다.
- 7단계: `contacts` 기록을 동기화에 추가한다.
