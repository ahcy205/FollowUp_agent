# RAG 1단계 내 자료로 답장 초안 채우기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 올린 자료(XLSX · CSV · TXT · MD · PDF · DOCX)를 키워드로 검색해 답장 초안의 사실 출처로 더하고, 그 줄을 근거로 코드가 다시 확인하며, 내부용 자료 값은 경고 + 보내기 전 확인을 받는다.

**Architecture:** 새 패키지 `backend/app/knowledge/`에 글 뽑기(`extract.py`) · 덩어리 나누기(`chunking.py`) · 저장(`store.py`) · 검색(`search.py`) · 초안 근거 확인(`draft_sources.py`)을 둔다. 답장 초안(`app/mail/reply_draft.py`)과 보내기 확인(`app/mail/reply_send.py`)이 이것을 부른다. 외부 호출은 없다(검색은 파이썬 안에서). 화면은 새 페이지 `Knowledge.tsx`와 기존 `ReplyDraftCard.tsx` 확장.

**Tech Stack:** FastAPI(UploadFile), SQLAlchemy 2, Alembic, Pydantic v2(extra=forbid), React 19 + TypeScript, 새 의존성 `pypdf` · `python-docx` · `openpyxl` · `python-multipart`, 가짜 `ScriptedLLM`

**Spec:** `docs/superpowers/specs/2026-10-29-rag-documents-design.md` (설계가 최우선. 절 번호는 이 문서 기준)

## Global Constraints

- 모든 조회는 `user_id`로 거른다. 남의 자료 · 초안은 404.
- 상태를 바꾸는 API는 POST + `get_current_user`. 올리기에는 `RateLimiter`(새 `upload_limiter`, 5회/60초).
- `KNOWLEDGE_ENABLED` 기본 `false`. 꺼지면 올리기 · 등급 바꾸기 409 "자료 기능이 꺼져 있습니다.", **목록 · 지우기는 된다**, 초안은 자료 없이 6단계처럼.
- 기본 공유 등급 `internal`. 등급은 `shareable` | `internal` 두 가지.
- `internal` 자료는 LLM 프롬프트 · 외부로 절대 가지 않는다. 내부용 값 비교는 로컬에서만.
- 자료 본문은 `wrap_untrusted()`로 감싸 LLM에 보낸다. 로그 · 타임라인에는 자료 번호 · 덩어리 수 · 상태 · 점수 · 예외 타입 이름만. 자료 이름 · 본문 · 질의는 남기지 않는다.
- 상한: 파일 10MB(`KNOWLEDGE_MAX_FILE_MB=10`), 사용자당 자료 200개(`KNOWLEDGE_MAX_SOURCES=200`), 초안에 넣는 덩어리 전체 5개 · 자료당 3개, 덩어리 최대 1,500자, 일반 글 1,200자에서 다시 자름 · 겹침 100자, 표 20행 묶음. (→ 변경 #4)
- 근거 확인(`draft_warnings`)에는 인용한 **그 줄만** 더한다. 덩어리 전체를 더하지 않는다.
- 내부용 값 비교 대상: 초안의 금액 · 세 자리 이상 숫자. 메모 · 입력에 있어도 경고.
- 재연락(`followup`) 초안은 자료를 검색하지 않는다.
- 새 설정은 `tests/conftest.py` 맨 위에서 고정한다(`KNOWLEDGE_ENABLED=false`, 상한 기본값).
- 의존성은 `requirements.txt`에 `==` 고정. 버전은 설치 시점 최신 안정판(`pip index versions <pkg>`), 이유를 커밋 메시지에.
- 마이그레이션은 SQLite(`batch_alter_table`) · PostgreSQL 양쪽. 로컬 `dev.db`는 SQLite backup API로 `dev.db.bak-<날짜>` 후 `upgrade head`.
- 프론트는 `window.confirm/alert/prompt` 금지. 사용자에게 보이는 문구는 설계 문서 문구를 그대로.

## Review Focus

1. 같은 단가표에 비슷한 품번(`TS-200`, `TS-2000`)이 함께 있을 때, 식별자 일치가 부분 문자열로 `TS-200`을 `TS-2000` 줄에 맞추면 안 된다 → Task 4 `test_identifier_match_is_whole_token`, Task 5 `test_other_item_warning_on_similar_code`.
2. 사용자가 초안을 고쳐 자료 값을 다른 숫자로 바꾸면 그 항목이 "자료에서 가져온 내용"에서 빠지고, 새 숫자는 "근거 없음"이 된다 → Task 5 `test_edit_removes_stale_doc_fact`.
3. 엑셀 숫자 칸이 `12000`(정수) · `12000.0`(실수) · 천 단위 서식으로 저장된 경우, 뽑은 글이 초안의 `12,000`과 토큰 비교로 맞아야 한다 → Task 1 `test_xlsx_number_cells_render_without_float_suffix`.
4. 등급을 내부용으로 바꾼 자료를 인용한 초안은, 다른 업무 · 이미 보낸 초안까지 모두 사본이 지워지고 아직 보내지 않은 초안은 보내기 409 → Task 6 `test_downgrade_purges_all_drafts_and_blocks_send`.
5. 자료 검색이 예외를 던져도 초안은 만들어지고 `sources.search_failed`가 참 → Task 5 `test_search_error_still_creates_draft`.

---

### Task 1: 글 뽑기 · 덩어리 나누기

**Files:**
- Modify: `backend/requirements.txt` (`pypdf`, `python-docx`, `openpyxl`, `python-multipart`)
- Create: `backend/app/knowledge/__init__.py`, `backend/app/knowledge/extract.py`, `backend/app/knowledge/chunking.py`
- Test: `backend/tests/test_knowledge_extract.py` (테스트 파일은 테스트 안에서 `openpyxl` · `python-docx`로 만들고, PDF는 손으로 쓴 최소 텍스트 PDF 바이트 도우미로 만든다)

**Interfaces:**
- Produces `extract.SUPPORTED = {"xlsx", "csv", "txt", "md", "pdf", "docx"}`
- Produces `@dataclass Block(kind: Literal["text", "table"], heading: str, lines: list[str])` — 표는 `lines`가 행 한 줄씩(`열이름: 값 · 열이름: 값`), `heading`은 시트 이름 · 표 머리글 줄
- Produces `@dataclass Extracted(blocks: list[Block], notes: dict)` — `notes["formula_without_value"]: int`
- Produces `extract_text(file_type: str, data: bytes) -> Extracted` — 글이 하나도 없으면 `ExtractError("글자를 읽을 수 없는 파일입니다. 텍스트가 들어 있는 PDF나 문서로 올려 주세요.")`. 형식 오류 · 암호 PDF도 `ExtractError`(한국어 이유)
- Produces `@dataclass ChunkData(text: str, lines: list[str])` (`text`는 `lines`를 줄바꿈으로 이은 것, 표는 첫 줄이 머리글)
- Produces `chunk_blocks(blocks: list[Block]) -> list[ChunkData]`

- [ ] **Step 1: 실패하는 테스트**
  - `test_xlsx_rows_become_labeled_lines`: 머리글 `품번 | 단가 | 최소수량`, 행 `TS-200 | 12000 | 100` → 줄 `"품번: TS-200 · 단가: 12000 · 최소수량: 100"`, `heading`에 시트 이름 포함
  - `test_xlsx_number_cells_render_without_float_suffix`: `12000.0` 칸 → `"12000"`, `12.5` → `"12.5"`; `tokens()`(draft_checks)로 `"12,000원"`과 같은 키
  - `test_xlsx_formula_without_cached_value_counted`: 수식만 있고 값 없는 칸 → 빈칸, `notes["formula_without_value"] == 1`
  - `test_csv_utf8_bom_and_cp949`: BOM 붙은 UTF-8, CP949 둘 다 읽음
  - `test_docx_paragraphs_and_table`: 제목 단락 → 새 `Block`, 표 → `kind="table"`
  - `test_pdf_text_layer`: 텍스트 PDF → 글; 글 없는 PDF → `ExtractError` 메시지 그대로
  - `test_table_chunks_keep_rows_whole_with_header`: 45행 표 → 덩어리 3개(20·20·5), 각 덩어리 `lines[0]`이 머리글, 행이 잘리지 않음, 모든 덩어리 ≤ 1,500자(긴 행이면 행 수를 줄임)
  - `test_text_chunks_split_at_paragraph_with_overlap`: 3,000자 글 → 각 ≤ 1,500자, 1,200자 넘으면 문단 경계에서 자르고 다음 덩어리 앞이 직전 끝 100자를 겹침
- [ ] **Step 2: 실패 확인** — `cd backend && ../.venv/Scripts/python -m pytest -q tests/test_knowledge_extract.py`
- [ ] **Step 3: 구현** — 의존성 설치 · 고정(`../.venv/Scripts/pip install`), `extract_text` · `chunk_blocks`. 일반 글의 `lines`는 줄바꿈 · 문장 끝(`. ? ! 다.`) 단위.
- [ ] **Step 4: 통과** — 같은 명령, 그리고 전체 `../.venv/Scripts/python -m pytest -q`
- [ ] **Step 5: 커밋** — `자료: 글 뽑기 · 덩어리 나누기 (pypdf · python-docx · openpyxl · python-multipart 추가: PDF · 워드 · 엑셀 읽기, 파일 올리기)`

### Task 2: 데이터 모델 · 마이그레이션 · 설정

**Files:**
- Modify: `backend/app/models.py`, `backend/app/config.py`, `backend/tests/conftest.py`
- Create: `backend/alembic/versions/c1d5e8f2a7b3_knowledge_documents.py` (down `b9c4d7e2f5a8`)
- Test: `backend/tests/test_knowledge_models.py`

**Interfaces:**
- Produces `KnowledgeSource` (`knowledge_sources`): `id, user_id(FK users CASCADE, index), kind str(20)='document', title str(300), file_type str(10), size int, content_hash str(64), visibility str(10)='internal', status str(10)('indexed'|'flagged'|'failed'), error Text='', notes JSON={}, created_at`. 유니크 `(user_id, content_hash)`.
- Produces `KnowledgeChunk` (`knowledge_chunks`): `id, source_id(FK knowledge_sources CASCADE, index), user_id(FK users CASCADE, index), ordinal int, text Text, lines JSON list[str], content_hash str(64), flagged bool=False`
- Produces `ReplyDraft.sources: dict` JSON 기본 `{}` (형식은 Task 5)
- Produces settings `knowledge_enabled: bool = False`, `knowledge_max_file_mb: int = 10`, `knowledge_max_sources: int = 200`
- Produces `UserOut.knowledge_enabled: bool = False` (`/auth/me`가 채움)

- [ ] **Step 1: 실패하는 테스트**
  - `test_source_hash_unique_per_user`: 같은 사용자 같은 해시 두 번 → `IntegrityError`; 다른 사용자는 허용
  - `test_delete_source_cascades_chunks` (SQLite `PRAGMA foreign_keys=ON`)
  - `test_reply_draft_sources_default_empty`
  - `test_me_reports_knowledge_enabled` (client, 기본 false)
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_knowledge_models.py`
- [ ] **Step 3: 구현** — 모델 · 마이그레이션 · 설정 · conftest(`KNOWLEDGE_ENABLED=false`) · `UserOut`. `dev.db` 백업 후 `alembic upgrade head`.
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `자료: 데이터 모델 · 마이그레이션 · 기능 스위치`

### Task 3: 자료 저장 (올리기 · 목록 · 등급 · 지우기 서비스)

**Files:**
- Create: `backend/app/knowledge/store.py`, `backend/app/knowledge/draft_sources.py`
- Test: `backend/tests/test_knowledge_store.py`

**Interfaces:**
- Consumes: Task 1 `extract_text`, `chunk_blocks`, `ExtractError`; Task 2 모델
- Produces `add_document(db, user: User, filename: str, data: bytes, visibility: str) -> KnowledgeSource`
  - 확장자로 형식 판단(대소문자 무시). `SUPPORTED` 밖이면 `ActionError("지원하지 않는 형식입니다. 엑셀 · CSV · 텍스트 · PDF · 워드 파일을 올려 주세요.", 400)`
  - 10MB 초과 → `ActionError("파일이 너무 큽니다. 10MB 이하로 올려 주세요.", 400)`
  - 200개 초과 → `ActionError("자료는 200개까지 올릴 수 있습니다. 쓰지 않는 자료를 지운 뒤 올려 주세요.", 409)`
  - 같은 해시 → `ActionError(f"이미 올린 자료입니다({기존 이름}).", 409)`
  - `ExtractError` → `status="failed"`, `error`=메시지로 **저장**(목록에 실패로 보임)
  - 덩어리마다 `detect_injection` → `flagged`. 하나라도 있으면 `status="flagged"`, 아니면 `"indexed"`
  - 타임라인 `KNOWLEDGE_ADDED` `output_data={"source_id", "chunks", "flagged", "status"}`
- Produces `list_sources(db, user_id) -> list[KnowledgeSource]` (최근 것부터), `get_source(db, user_id, source_id) -> KnowledgeSource` (없거나 남의 것 → `ActionError("자료를 찾을 수 없습니다.", 404)`)
- Produces `set_visibility(db, source, visibility: str) -> None` — `shareable`→`internal`이면 Task 6 `purge_source_from_drafts(db, source.user_id, source.id)` 호출
- Produces `delete_source(db, source) -> None` — `purge_source_from_drafts` 호출 후 삭제
- 이 작업에서 `app/knowledge/draft_sources.py`를 만들고 `purge_source_from_drafts(db, user_id, source_id) -> int`를 0을 돌려주는 함수로 둔다. Task 5가 같은 파일에 초안 함수를 더하고, Task 6이 이 함수를 채운다

- [ ] **Step 1: 실패하는 테스트**
  - `test_add_xlsx_indexed_with_chunks`, `test_add_rejects_unsupported_and_large_and_limit` (메시지 그대로), `test_duplicate_hash_rejected_with_name`
  - `test_unreadable_pdf_saved_as_failed`, `test_injection_chunk_flagged_rest_kept`
  - `test_get_other_users_source_404`, `test_list_newest_first`
  - `test_timeline_has_no_title_or_text`: 로그 `output_data` · `reason`에 파일 이름 · 본문 글자가 없음
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_knowledge_store.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `자료: 올리기 · 목록 · 공유 등급 · 지우기`

### Task 4: 키워드 검색 · 내부용 값 토큰

**Files:**
- Create: `backend/app/knowledge/search.py`
- Test: `backend/tests/test_knowledge_search.py`

**Interfaces:**
- Produces `@dataclass Hit(chunk_id: int, source_id: int, title: str, uploaded_at: datetime, ordinal: int, text: str, lines: list[str], score: float)`
- Produces `identifiers(text: str) -> set[str]` — 영문과 숫자가 함께 든 낱말(하이픈 허용, 예 `TS-200`, `AB12`), 대문자로 맞춤, **낱말 단위**(앞뒤가 영숫자 · 하이픈이 아님)
- Produces `search_shareable(db, user_id: int, query: str, limit: int = 5, per_source: int = 3) -> list[Hit]`
  - 대상: 그 사용자의 `visibility="shareable"`, `flagged=False`, 상태 `indexed`|`flagged` 자료의 덩어리
  - 점수 = 식별자 일치 수 × `ID_WEIGHT` + 2글자 조각 겹침 비율(Jaccard) × `GRAM_WEIGHT`. 비슷하면(차이 < `TIE`) 최근 자료 먼저. `MIN_SCORE` 미만 버림. 상수 시작값 `ID_WEIGHT=3.0`, `GRAM_WEIGHT=1.0`, `TIE=0.05`, `MIN_SCORE=0.15` — Task 9 측정으로 조정 (→ 변경 #5 · #9)
  - 2글자 조각: 소문자, 공백 · 문장부호 제거 후 연속 2글자
  - 로그: `log.info`에 덩어리 수 · 최고 점수만
- Produces `internal_value_index(db, user_id: int) -> dict[str, list[tuple[int, str]]]` — 그 사용자의 `internal` · `flagged=False` 덩어리 전체에서 `draft_checks.tokens` 중 `n`(숫자) 키를 모아 `{토큰 키: [(source_id, title), …]}`
- Produces `is_internal_candidate(key: str, shown: str) -> bool` — 숫자 키가 세 자리 이상이거나, `shown` 뒤에 `원`이 붙은 금액이면 참

- [ ] **Step 1: 실패하는 테스트**
  - `test_identifier_match_ranks_row_first`: 질의 "TS-200 단가 문의" → `TS-200` 줄이 든 덩어리 1등
  - `test_identifier_match_is_whole_token`: `TS-2000`만 든 덩어리는 `TS-200` 식별자 점수 0
  - `test_internal_and_flagged_never_returned`
  - `test_other_users_chunks_never_returned`
  - `test_limit_and_per_source_caps`: 자료 하나에 맞는 덩어리 5개 → 3개만, 전체 5개
  - `test_newer_source_first_on_tie`
  - `test_below_min_score_dropped_returns_empty`
  - `test_internal_value_index_skips_small_numbers`: 원가표 `8,000` · `12` → `8000`만 후보(`is_internal_candidate`)
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_knowledge_search.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `자료: 키워드 검색 · 내부용 값 색인`

### Task 5: 답장 초안에 자료 넣기 · 줄 단위 근거 확인

**Files:**
- Modify: `backend/app/knowledge/draft_sources.py` (Task 3이 만든 파일)
- Modify: `backend/app/mail/reply_draft.py` (`DRAFT_SCHEMA`, `DRAFT_SYSTEM`, `_build_prompt`, `_validate`, `generate_draft`, `update_draft`, `_context_texts`)
- Modify: `backend/app/schemas.py` (`ReplyDraftOut.sources: dict`), `backend/app/api/tasks.py` (`_draft_out`)
- Test: `backend/tests/test_reply_draft_sources.py`

**Interfaces:**
- Consumes: Task 4 `search_shareable`, `identifiers`, `internal_value_index`, `is_internal_candidate`; `draft_checks.tokens`, `draft_warnings`
- Produces `ReplyDraft.sources` 형식:
  ```
  {"chunks": [{"ref": "D{source_id}-{ordinal}", "source_id", "chunk_id", "title", "uploaded_at", "visibility": "shareable", "lines": [...]}],
   "facts": [{"text", "ref", "line": int, "line_text", "checked": "numbers"|"manual", "other_item": bool}],
   "internal_values": [{"value", "source_ids": [...], "titles": [...]}],
   "search_failed": bool, "sources_changed": bool}
  ```
- Produces `build_query(task: Task, emails: list[Email], user_input: str) -> str` (요약 + 요청 + 최근 받은 메일 앞 500자 + 입력)
- Produces `prompt_block(chunks: list[dict]) -> str` — "내 자료 (답장에 써도 되는 자료)" 블록, 덩어리마다 `[자료 {ref}] {title} ({M/D} 올림)` + 줄 번호 붙은 `lines`(1부터)를 `wrap_untrusted`
- Produces `check_facts(body: str, used_facts: list[dict], chunks: list[dict], task_ids: set[str]) -> list[dict]` — 설계 5.2 1~4. `task_ids` = 상대 요청 · 최근 받은 메일의 `identifiers`
- Produces `evidence_lines(facts: list[dict]) -> str` — 통과 항목의 `line_text`만 이은 글(근거 확인에 더함)
- Produces `internal_values(db, user_id, body: str, allowed_text: str) -> list[dict]` — 설계 5.3: 본문 토큰 중 `is_internal_candidate`이고 내부용 색인에 있고 `allowed_text`(인용 줄 + 스레드 메일 본문)에 없는 것. 메모 · 입력은 `allowed_text`에 넣지 않는다
- `DRAFT_SCHEMA.used_facts.items`: `source` enum에 `"doc"` 추가, `chunk: string`, `line: integer` 필드(둘 다 required, doc이 아니면 `""` · `0`)
- `DRAFT_SYSTEM`에 설계 5.1 규칙 추가(메모 · 입력 우선, 최근 자료 우선, 같은 품목 줄, `source: "doc"` + 덩어리 · 줄 번호, 상대 메일 숫자 되풀이 허용) (→ 변경 #11)
- `generate_draft`: `purpose == "reply"`이고 `knowledge_enabled`일 때만 검색. 검색 예외 → `search_failed=True`, 자료 없이 진행. `draft.sources`를 새 dict로 **덮어쓴다**. `warnings["internal_values"]`, `warnings["other_item"]` 개수 (→ 변경 #8)
- `update_draft`: 저장된 `sources.chunks` 사본으로 `check_facts` · 근거 · 내부용 값을 다시 계산(외부 호출 없음)

- [ ] **Step 1: 실패하는 테스트** (`ScriptedLLM`, 자료는 Task 3 `add_document`로 만들고 `knowledge_enabled` monkeypatch)
  - `test_shareable_chunk_in_prompt_internal_never`: 프롬프트에 공유 단가표 줄이 있고 내부 원가표 글자는 없음
  - `test_doc_fact_kept_with_line`: LLM이 `{"text":"개당 12,000원","source":"doc","chunk":"D1-0","line":2}` → `sources.facts[0].line_text`가 그 줄, `checked="numbers"`
  - `test_doc_fact_wrong_chunk_or_line_dropped`, `test_doc_fact_number_not_in_line_dropped`, `test_doc_fact_number_not_in_body_dropped`
  - `test_other_item_warning_on_similar_code`: 메일 `TS-200`, 인용 줄 `TS-2000` → `other_item=True`
  - `test_non_numeric_fact_manual`: "무료 배송" → `checked="manual"`
  - `test_only_cited_line_counts_as_evidence`: 같은 덩어리 다른 줄의 `5,000`을 본문에 쓰면 `warnings.unsupported`에 `5000`
  - `test_internal_value_warned_even_from_memo`: 메모 "원가 8,000원" + 본문 8,000 → `internal_values` 1건
  - `test_followup_does_not_search`: `purpose="followup"` → 검색 함수 미호출
  - `test_switch_off_no_search`
  - `test_search_error_still_creates_draft`: `search_shareable` 예외 → 초안 생성, `sources.search_failed is True`
  - `test_regenerate_overwrites_sources`
  - `test_edit_removes_stale_doc_fact`: 본문 12,000 → 13,000으로 고치면 facts 0개, `unsupported`에 `13000`
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_reply_draft_sources.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트(6단계 초안 테스트 포함 그대로 통과해야 함. 스키마 enum이 바뀌어 기대값을 고쳐야 하면 이유를 커밋 메시지에)
- [ ] **Step 5: 커밋** — `답장 초안: 답장에 써도 되는 자료를 넣고 인용한 줄로 다시 확인`

### Task 6: 자료 변경 연쇄 · 보내기 직전 확인

**Files:**
- Modify: `backend/app/knowledge/draft_sources.py` (`purge_source_from_drafts`)
- Modify: `backend/app/mail/reply_send.py` (`ensure_can_send`, `send_draft`의 로그)
- Modify: `backend/app/schemas.py` (`ReplySendIn(_Strict): internal_ack: StrictBool = False`), `backend/app/api/tasks.py` (`send_reply_draft`가 `ReplySendIn` 받음, `task_fingerprint`에 초안 `updated_at`) (→ 변경 #7)
- Test: `backend/tests/test_knowledge_cascade.py`

**Interfaces:**
- Produces `purge_source_from_drafts(db, user_id: int, source_id: int) -> int` — 그 사용자의 **모든** `ReplyDraft`(업무 · 초안 상태 무관)에서 그 `source_id`의 `chunks` · `facts`를 빼고 새 dict 대입. `status == "draft"`이면 경고 다시 계산, `sources_changed=True`, `updated_at` 갱신. 바뀐 초안 수 반환 (→ 변경 #6)
- Produces `ensure_can_send(db, user, task, draft, *, internal_ack: bool = False)` 추가 확인(기존 확인 다음, 외부 호출 없음):
  - `sources.chunks`의 각 `source_id`가 아직 있고 `shareable` → 아니면 409 "참고한 자료가 지워졌거나 내부용으로 바뀌었습니다. 초안을 다시 만들어 주세요."
  - `warnings.internal_values`가 있고 `internal_ack`가 아니면 409 "내부용 자료의 값이 들어 있습니다. 내용을 확인하고 '알고 보냅니다'를 체크해 주세요." (→ 변경 #7)
- `send_draft`의 `REPLY_SENT` 로그 `output_data`에 `internal_ack: bool`

- [ ] **Step 1: 실패하는 테스트**
  - `test_delete_purges_copy_and_marks_changed`
  - `test_downgrade_purges_all_drafts_and_blocks_send`: 업무 둘(하나는 `sent` 초안)이 같은 자료 인용 → 내부용으로 바꾸면 두 초안 모두 사본 없음, `draft` 상태 초안만 `sources_changed`. (사본을 지우기 전 상태를 만들어) 보내기 → 409 메시지 그대로
  - `test_send_requires_internal_ack`: 409 → `internal_ack=True`면 통과(FakeGoogle)
  - `test_upgrade_to_shareable_does_not_purge`
  - `test_fingerprint_changes_when_draft_updated`
  - `test_send_body_rejects_extra_fields` (extra=forbid)
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_knowledge_cascade.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트(기존 보내기 테스트는 본문 없이 POST해도 기본 `internal_ack=False`로 통과해야 함)
- [ ] **Step 5: 커밋** — `자료: 지우기 · 등급 변경을 초안에 반영하고 보내기 직전 다시 확인`

### Task 7: 자료 API

**Files:**
- Create: `backend/app/api/knowledge.py` (router, `app/main.py`에 등록)
- Modify: `backend/app/security.py` (`upload_limiter = RateLimiter(max_calls=5, window_seconds=60)`), `backend/tests/conftest.py`(다른 limiter처럼 초기화), `backend/app/schemas.py` (`KnowledgeSourceOut`, `VisibilityIn`)
- Test: `backend/tests/test_knowledge_api.py`

**Interfaces:**
- Consumes: Task 3 서비스
- Produces (모두 `/api` 아래, 로그인 필요):
  - `GET /knowledge/sources` → `{"enabled": bool, "sources": [KnowledgeSourceOut]}`
  - `POST /knowledge/sources` (multipart `files: list[UploadFile]`, `visibility: "shareable"|"internal"="internal"`) → `{"results": [{"filename", "ok": bool, "error": str, "source": KnowledgeSourceOut|None}]}` — 파일마다 따로 성공 · 실패
  - `POST /knowledge/sources/{id}/visibility` (`VisibilityIn{visibility}`) → `KnowledgeSourceOut`
  - `POST /knowledge/sources/{id}/delete` → `{"ok": true}`
- `KnowledgeSourceOut`: `id, title, file_type, size, visibility, status, error, notes, chunk_count, flagged_count, created_at`
- 스위치 꺼짐: 올리기 · 등급 → 409 "자료 기능이 꺼져 있습니다."; 목록(`enabled: false`) · 지우기는 동작
- 올리기는 스위치 · 로그인 확인 뒤 `upload_limiter.check(f"upload:{user.id}")`
- CSRF: multipart 요청도 `X-Requested-With` 검사를 그대로 받는다

- [ ] **Step 1: 실패하는 테스트**
  - `test_upload_multiple_files_partial_failure`, `test_upload_default_visibility_internal`
  - `test_switch_off_upload_409_list_and_delete_ok`
  - `test_other_users_source_404_on_visibility_and_delete`
  - `test_upload_rate_limited`, `test_upload_requires_csrf_header`
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_knowledge_api.py`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과** — 전체 테스트
- [ ] **Step 5: 커밋** — `자료: 자료실 API`

### Task 8: 화면 (자료실 · 초안 카드)

**Files:**
- Create: `frontend/src/pages/Knowledge.tsx`
- Modify: `frontend/src/App.tsx` (메뉴 "자료실" `/knowledge`), `frontend/src/api.ts` (`api.upload<T>(path, form: FormData)` — JSON 헤더 없이, `X-Requested-With` 유지), `frontend/src/types.ts` (`KnowledgeSource`, `ReplyDraft.sources`, `ReplyDraftWarnings.internal_values` · `other_item`), `frontend/src/components/mail/ReplyDraftCard.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 5 `ReplyDraftOut.sources`, Task 6 `POST .../reply-draft/send {internal_ack}`, Task 7 API

- [ ] **Step 1: 자료실** (설계 2.1)
  - 올리기(파일 선택 · 끌어다 놓기 · 여러 개), 등급 선택(기본 내부용) + 안내 문구, 파일별 결과
  - 목록 카드: 이름 · 형식 · 크기 · 올린 날짜 · 등급 · 상태 · 덩어리 수 · "계산 값이 없는 칸 N개 — Excel에서 열어 저장한 뒤 다시 올려 주세요" · 내부용 안내 "답장에 쓰려면 '답장에 써도 됨'으로 바꾸세요"
  - 등급 바꾸기, 카드 안 "정말 지울까요? [지우기] [취소]"
  - 스위치 꺼짐: "자료 기능이 꺼져 있습니다" + 목록 · 지우기만. 무료 키 안내(설계 6.3)
  - 480px 이하 세로 배치
- [ ] **Step 2: 초안 카드** (설계 5.5)
  - "자료에서 가져온 내용": `값 — 이름 (M/D 올림) · "그 줄"`, `other_item` → "다른 품목의 값일 수 있습니다", `manual` → "직접 확인 필요"
  - 접는 "참고한 자료"(안 쓰인 것은 흐리게)
  - 경고: "내부용 자료에 있는 값"(빨강, 값 · 자료 이름), `sources_changed` → "참고한 자료가 바뀌었습니다 — 초안을 다시 만들어 주세요", `search_failed` → 작은 글씨 "자료를 찾지 못해 메모와 입력만으로 만들었습니다"
  - "정말 보내기" 단계: 내부용 값이 있으면 체크 "내부용 자료의 값이 들어 있습니다 — 알고 보냅니다", 체크 전에는 버튼 비활성, `{internal_ack: true}` 전송 (→ 변경 #10)
- [ ] **Step 3: `cd frontend && npm run build && npm run lint`** — 경고는 App.tsx 기존 1개만
- [ ] **Step 4: 커밋** — `자료 화면: 자료실, 초안 카드 출처 · 내부용 값 확인`

### Task 9: 정확도 측정 · 문서 · 점검 · 병합

**Files:**
- Create: `backend/smoke/knowledge_cases.py`(가상 자료는 테스트 중 `openpyxl` · `python-docx`로 생성, 주소는 `.example`), `backend/smoke/test_knowledge_smoke.py`, `docs/evals/2026-10-29-rag-documents.md`
- Modify: `docs/privacy.md`, `docs/decisions.md`, `docs/troubleshooting.md`, `CHANGELOG.md`, `README.md`, 상위 설계(설계 9절), 이 계획의 변경 이력

- [ ] **Step 1: 시험 세트** — 설계 7절 자료 7종, 검색 질문 ~20개(`{query, expected_ref}`), 초안 사례 8건(`{name, task, mails, memo, user_input, expected}`)
- [ ] **Step 2: 오프라인 검색 측정** — 검색은 외부 호출이 없으므로 recall@5는 **일반 테스트**(`tests/test_knowledge_recall.py`)로 돌려 표를 출력하고, 단언은 "식별자로 묻는 질문은 모두 상위 5개 안". 결과로 Task 4 상수를 조정한다.
- [ ] **Step 3: 실제 LLM 스모크** — `cd backend && PYTHONUTF8=1 RUN_LLM_SMOKE=1 ../.venv/Scripts/python -m pytest smoke/test_knowledge_smoke.py -v -s`. 단언: 경고 없는 내부용 값 0, 자료에 없는데 자료 표시 0, 경고 없는 다른 품번 값 0. 실패하면 프롬프트를 고치고 다시(최대 3회, 결과마다 표 기록)
- [ ] **Step 4: 로컬 점검** (사용자와 함께, `KNOWLEDGE_ENABLED=true`, 가상 단가표 · 원가표 사용)
  - 자료실: 엑셀 · PDF · 워드 올리기, 실패 파일 메시지, 등급 바꾸기, 지우기
  - 업무 메일 "TS-200 단가 문의" → 초안에 단가 채움 · 그 줄 표시 → 보내기
  - 메모에 원가 → 경고 · "알고 보냅니다" 체크 전 보내기 막힘
  - 자료를 내부용으로 바꾸면 초안에 "참고한 자료가 바뀌었습니다", 보내기 409
  - 스위치 끄면 자료실 목록 · 지우기만
- [ ] **Step 5: 문서** — privacy(설계 6.3), decisions(설계의 "내 판단"), troubleshooting(올리기 실패 · 계산 값 없는 칸 · 자료가 안 쓰임(내부용) · 보내기 409 두 가지), CHANGELOG, README 진행 표, 상위 설계 반영(설계 9절)
- [ ] **Step 6: push → CI → `main` 병합 · 테스트 · push → 로컬 브랜치 삭제** (원격 브랜치 삭제는 사용자 확인)

---

## 계획 대비 변경 이력

| # | 날짜 | 작업 | 계획 | 실제 구현 | 이유 | 영향 |
|---|---|---|---|---|---|---|
| 1 | 2026-10-29 | Task 2 · 6 (계획 작성 시) | 설계 6.1 "내부용 값 확인을 받았는지 `reply_drafts`에 기록" | 확인은 보내기 요청(`internal_ack`)으로 받고 `REPLY_SENT` 로그에 남긴다. 초안 칸은 만들지 않는다 | 확인은 보낼 때마다 새로 받아야 하므로(설계 5.3 "고치면 확인도 다시") 초안에 저장할 상태가 없다 | 없음 |
| 2 | 2026-10-29 | Task 9 (계획 작성 시) | 설계 7절 "검색 질문 recall@5"를 스모크로 | 검색은 외부 호출이 없어 일반 오프라인 테스트로 잰다 | 키워드 검색만 쓰므로(사용자 결정) 실제 LLM이 필요 없다 | 측정이 CI에서도 돈다 |
| 3 | 2026-10-30 | 최종 리뷰 반영 (AI 지시 탐지) | 한국어 "AI에게 시키는 지시" 규칙을 공용 `_INJECTION_PATTERNS`에 추가 | 그 규칙을 `_DOCUMENT_PATTERNS`로 옮기고 `detect_document_injection`(메일 규칙 + 자료 전용 규칙)을 새로 둔다. 자료 덩어리 표시(`store.py`)만 이 함수를 쓰고 메일 경로는 `detect_injection` 그대로 | 공용 규칙이라 메일에도 걸려 "비서에게 일정 보내 주세요" 같은 평범한 업무 메일에 빨간 보안 경고가 떴다(오탐). 사용자 승인 | 메일 경로 동작은 6단계와 같다. 자료 경로는 그대로 걸린다 |
| 4 | 2026-10-29 | Task 1 | 덩어리 겹침 100자 | 앞 덩어리 끝의 **통째 줄**을 200자 안에서 겹친다(조각 줄 없음). 1,000자 넘는 한 줄은 그 안의 공백에서 자른다 | 글자 수로 자르면 숫자가 잘린 조각 줄이 다음 덩어리 `lines`에 들어가 줄 단위 숫자 확인(설계 5.2)이 틀린다 | 겹침 길이가 100자와 조금 다르다 |
| 5 | 2026-10-29 | Task 4 | 2글자 조각 점수 = Jaccard | 질문 기준 겹침 비율(질문 조각 중 덩어리에 있는 비율) | 덩어리가 질문보다 훨씬 길어 Jaccard는 모든 덩어리가 `MIN_SCORE` 아래로 떨어진다 | 상수는 Task 9 측정으로 확인 |
| 6 | 2026-10-29 | Task 6 | 자료 변경 시 `draft` 상태 초안만 `sources_changed`, 다른 상태는 사본만 지움 | **보내지 않은 모든 초안**(draft · sending · unknown)에 `sources_changed`. `ensure_can_send`는 `sources_changed`면 409(다시 만들기 전까지) | 결과 불분명 초안이 "안 보내졌어요"로 돌아오면 표시 없이 보낼 수 있었다(리뷰). 사본만 지우면 내부용으로 바뀐 값이 "근거 없음" 경고 하나로 나간다 | 값을 직접 지워도 다시 만들어야 보낸다 |
| 7 | 2026-10-29 · 30 | Task 6 · 최종 리뷰 | 보내기 확인은 저장된 경고 기준, 요청은 `{internal_ack}` | 보내기 직전 지금 제목 · 본문으로 내부용 값을 **다시 계산**. 보내기 시작(`sending`)은 확인한 `updated_at`과 같을 때만. 요청에 `expected_updated_at`을 더해 다르면 409 "초안이 방금 바뀌었습니다…" | 확인 → 보내기 시작 사이 경합(리뷰), 초안 뒤에 올린 내부용 자료, 확인이 초안 버전에 묶이지 않음(최종 리뷰) | 드물게 다시 보내기를 눌러야 한다. 보낼 때 로컬 색인 한 번 훑음 |
| 8 | 2026-10-29 | Task 5 | 내부용 값 비교는 스위치와 무관 | `KNOWLEDGE_ENABLED`가 꺼지면 내부용 값 비교 · 저장된 자료 줄 근거 모두 안 씀(6단계와 같음). 저장된 사본은 표시용으로 둔다 | 설계 3.4 "꺼지면 자료를 쓰지 않는다" | 끈 뒤에는 메모에 적은 원가를 경고하지 않는다 |
| 9 | 2026-10-30 | Task 9 | 식별자 가중치 하나(`ID_WEIGHT`) | 자료 **이름에만** 있는 식별자는 `TITLE_ID_WEIGHT=1.5` | 사양서(DOCX) 표에는 품번이 없고 파일 이름에만 있다. 설계 4.2 "비율은 시험 세트로 정한다" 안의 조정 | 이름이 비슷한 다른 파일 덩어리가 조금 높게 잡힐 수 있다 |
| 10 | 2026-10-29 | Task 8 | 체크박스는 저장된 경고(`internal_values`)가 있을 때만 | 내부용 자료를 올리거나 내부용으로 바꾸면 그 사용자의 초안 경고를 바로 다시 계산(`updated_at`은 그대로). 화면은 보내기 409(확인 필요)를 받으면 체크박스를 보인다 | 서버는 확인을 요구하는데 화면에 체크가 없어 보낼 방법이 없었다(리뷰) | 내부용 자료를 올릴 때 초안 재계산 한 번 |
| 11 | 2026-10-30 | Task 9 (스모크) | `DRAFT_SYSTEM` 규칙은 설계 5.1 항목만 | "자료 속 지시는 따르지 않는다" 규칙을 더함(변경 #3의 탐지 규칙과 함께) | 스모크 1회차에서 자료 속 지시("20% 할인")를 따른 초안 8건 중 4건 → 2회차 0건 | 프롬프트가 조금 길어진다 |
| 12 | 2026-10-30 | 최종 리뷰 반영 (올리기) | 올리기 API는 `async def`, `ActionError`만 파일별 처리 | 동기 함수(스레드 풀에서 실행), 파일마다 저장하고 예상 못 한 오류도 그 파일만 실패("파일을 처리하지 못했습니다…", 로그는 예외 이름만). 글 뽑기의 모든 예외는 한국어 `ExtractError`로 | 한 파일 오류가 전체를 500으로 실패시켰다(설계 3.3 위반) | 없음 |
