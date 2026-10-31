---
name: FollowUp Agent
description: 메일 확인부터 답장 · 후속 대응까지 놓치지 않게 하는 차분한 B2B 업무 도구
colors:
  accent: "#3554d1"
  accent-600: "#2a45b4"
  accent-50: "#eef2fd"
  accent-100: "#d7e0fa"
  navy: "#0e1a33"
  navy-2: "#172544"
  navy-3: "#22335a"
  navy-text: "#c8d1e3"
  red: "#c4321c"
  red-50: "#fdf1ef"
  red-100: "#f6d3cc"
  amber: "#965c00"
  amber-50: "#fdf6e7"
  amber-100: "#f2dfb0"
  green: "#18794e"
  green-50: "#edf8f2"
  green-100: "#c9ead8"
  text: "#0f172a"
  text-2: "#3b4658"
  text-3: "#5f6b7e"
  bg: "#f6f7f9"
  surface: "#ffffff"
  surface-2: "#f9fafb"
  surface-3: "#f2f4f7"
  border: "#e6e8ec"
  border-2: "#d5d9e0"
typography:
  display:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif"
    fontSize: "1.85rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
    fontFeature: "'tnum'"
  headline:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif"
    fontSize: "1.43rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  title:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif"
    fontSize: "1rem"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  body:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif"
    fontSize: "0.82rem"
    fontWeight: 650
    lineHeight: 1.5
rounded:
  xs: "4px"
  chip: "6px"
  sm: "7px"
  md: "10px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  pane: "18px"
  xl: "20px"
  page: "28px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
  button-primary-hover:
    backgroundColor: "{colors.accent-600}"
  button-send:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
  button-send-hover:
    backgroundColor: "{colors.navy-3}"
  button-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
  button-ghost-hover:
    backgroundColor: "{colors.surface-2}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.red}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "8px 11px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "18px 20px"
  list-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    padding: "11px 18px"
  list-row-active:
    backgroundColor: "{colors.accent-50}"
  side-nav:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.navy-text}"
    rounded: "{rounded.sm}"
    padding: "8px 10px"
    width: "232px"
  side-nav-active:
    backgroundColor: "{colors.navy-3}"
    textColor: "{colors.surface}"
  pill:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.pill}"
    padding: "1px 8px"
  ai-tag:
    backgroundColor: "{colors.accent-50}"
    textColor: "{colors.accent-600}"
    rounded: "{rounded.xs}"
    padding: "0 6px"
  by-user:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.xs}"
    padding: "0 6px"
  due-chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.chip}"
    padding: "1px 8px"
  due-chip-late:
    backgroundColor: "{colors.red-50}"
    textColor: "{colors.red}"
  tile:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "14px 16px"
  tile-navy:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.surface}"
---

# Design System: FollowUp Agent

## Overview

**Creative North Star: "조용한 관제실"**

밝은 회색 바탕 위에 흰 면이 놓이고, 왼쪽에는 딥 네이비 사이드바가 화면을 고정한다. 받은 업무는 사이드바 · 목록 · 상세 패널의 3단으로 열리며, 사용자는 목록을 위아래로 훑고(`j`/`k`) 오른쪽 패널에서 처리한다. 장식은 없고, 색은 상태를 말할 때만 쓴다: 인디고-블루는 선택 · 링크 · 주 동작, 빨강은 기한 지남 · 막힘, 황토는 확인 필요, 초록은 완료 · 준비됨.

이 시스템의 중심 약속은 **AI가 한 일과 사용자가 정한 일을 눈으로 구분하는 것**이다. AI가 만든 값에는 작은 파란 점이 붙은 `AI` 표지가, 사용자가 고친 값에는 회색 "직접 정함" 표지가 붙고, 근거가 필요한 값 옆에는 메일의 근거 문장이 인용된다. 되돌릴 수 없는 보내기는 발송 체크리스트 한 장을 모두 통과해야 열린다.

하루에 여러 번 여는 실무 도구라서 밀도(본문 14px, 행 안쪽 11px 18px)와 일관성이 우선이다. 사용자가 확정한 거부 대상: 그라데이션 장식, 보라색 AI 장식, 이모지 아이콘, 브라우저 확인창.

**Key Characteristics:**
- 바탕(`bg`) · 흰 면(`surface`) · 네이비 사이드바(`navy`) 세 층. 면 안의 구획은 1px `border` 선.
- 강조색은 인디고-블루(`accent`) 하나. 상태색 셋은 옅은 바탕 + 진한 글자 + 중간 테두리로만 쓴다.
- 데스크톱 3단(사이드바 232px · 목록 · 상세 패널), 1200 · 980 · 720px에서 단계적으로 접힌다.
- 중요도는 세 칸 막대, 기한은 오른쪽 정렬 고정폭 숫자.
- AI 값은 `ai-tag`, 사용자 값은 `by-user`. 근거는 인용 상자로 붙인다.
- 아이콘은 `Icon.tsx`의 24 격자 · 선 1.6 직접 그린 선 아이콘.

## Colors

차가운 무채색 위에 인디고-블루 하나, 상태색 셋(빨강 · 황토 · 초록)을 의미가 있을 때만 쓰는 팔레트다. 모든 색은 `styles.css`의 `:root` 변수가 원본이다.

### Primary
- **인디고-블루** (`accent`): 주 버튼(답장 초안 보기, Gmail 연결하기), 링크, 키보드 포커스 외곽선, 입력칸 포커스 테두리, 밑줄 탭 활성 막대, 분할 탭 활성 개수, 체크박스 채움, 안 읽은 변경 점, 선택된 목록 행 · 표 행 바탕(`accent-50`), 활성 사이드바 배지. hover · 눌림은 `accent-600`, 옅은 바탕 `accent-50`, 옅은 테두리 `accent-100`.

### Secondary
- **딥 네이비** (`navy`): 사이드바 바탕, 보내기 버튼(되돌릴 수 없는 마지막 단계), 홈의 첫 숫자 칸("오늘 처리할 업무"), 보내기 확인 카드의 1.5px 테두리. 사이드바 hover는 `navy-2`, 활성 메뉴 · 보내기 hover · 아바타는 `navy-3`, 사이드바 글자는 `navy-text`.

### Tertiary
- **경고 빨강** (`red`): 기한 지남(D+n 지남), "지금 처리" 묶음 제목, 중요도 높음 막대, 발송 체크리스트의 막힘, 오류 띠, 위험 버튼(업무 취소 · 연결 끊기), 처리 기록의 위험 표시.
- **황토** (`amber`): 확인 필요 — 추정 기한, 근거 없는 값, 답장 안 함 표지, 초안 빈칸 안내, 체크리스트의 "확인 필요".
- **초록** (`green`): 완료 · 준비됨 — 할 일 진행 막대, 연결됨 점, 켜짐 상태, 체크리스트 통과, "보냄" 표시, 자료 출처 표지.

### Neutral
- **잉크** (`text`): 본문 글자, 제목, 선택된 탭 글자.
- **보조 글자** (`text-2`, `text-3`): 보조 설명 · 표지 글자는 `text-2`, 라벨 · 시각 · 비활성 탭 · 표 머리는 `text-3`.
- **바탕** (`bg`): 페이지와 상세 패널 바탕. 흰 면이 이 위에 놓인다.
- **흰 면** (`surface`): 카드 · 목록 판 · 입력칸 · 표.
- **옅은 면** (`surface-2`, `surface-3`): 묶음 제목 띠 · 표 머리 · 행 hover · 근거 인용 상자는 `surface-2`, 회색 표지 · 분할 탭 틀 · 진행 막대 바탕은 `surface-3`.
- **구획선** (`border`, `border-2`): 안쪽 구획선과 카드 테두리는 `border`, 입력칸 · 고스트 버튼 · 기한 칩 테두리와 중요도 막대의 빈 칸은 한 단계 진한 `border-2`.

### Named Rules
**The 파랑 하나 Rule.** 강조색은 `accent` 하나다. 선택 · 링크 · 포커스 · 주 동작 · AI 표지에만 쓰고, 장식에는 쓰지 않는다.

**The 상태색 세 톤 Rule.** 빨강 · 황토 · 초록(그리고 파랑)은 항상 `-50` 바탕 + 본색 글자 + `-100` 테두리 세 톤으로 쓴다. 진한 상태색 면을 채우지 않는다.

**The 네이비 마지막 단계 Rule.** 네이비 채움 버튼은 보내기처럼 되돌릴 수 없는 마지막 단계에만 쓴다. 일반 주 동작은 인디고-블루다.

## Typography

**Display Font:** Pretendard Variable (with -apple-system, Apple SD Gothic Neo, Malgun Gothic, Segoe UI)
**Body Font:** Pretendard Variable (같은 계열)
**Label/Mono Font:** 따로 없음. 코드만 ui-monospace / Cascadia Mono / Consolas.

**Character:** 한 가족의 굵기 차이(400 · 500 · 550 · 600 · 650 · 700)로 위계를 만든다. 기준 글자는 14px(휴대폰 15px)이고 단계는 약 12 · 13 · 14 · 16 · 20 · 26px이다. 제목 자간은 -0.015em.

### Hierarchy
- **Display** (700, 1.85rem ≈ 26px, 1.2, 고정폭 숫자): 홈 숫자 칸의 큰 숫자. 로그인 첫 문장만 2.1rem(휴대폰 1.55rem).
- **Headline** (700, 1.43rem ≈ 20px, 1.3): 페이지 제목. 상세 패널 제목은 1.35rem, 목록 판 제목("받은 업무")은 1.2rem.
- **Title** (650, 1rem, 1.3): 카드 제목. 작은 제목(h3)은 0.93rem / 650.
- **Body** (400, 14px, 1.55): 본문. 목록 행 요약은 550 / 0.92rem / 1.45, 두 줄까지. AI 요약 문장은 600 / 1rem. 메일 본문 0.9rem / 1.65, 초안 본문 1.7.
- **Label** (600~650, 0.82rem ≈ 12px, `text-3`): 라벨 칸, 표 머리, 숫자 칸 이름, 표지 글자. 묶음 제목은 0.78rem / 700. 대문자 변환 · 넓은 자간 없음.

### Named Rules
**The 고정폭 숫자 Rule.** 건수 · 기한 · D-day · 할 일 n/m · 시각은 `.num`(`tabular-nums`)으로 세로 줄을 맞춘다. 기한은 행의 오른쪽 끝에 정렬한다.

## Layout

**앱 틀:** `sidebar 232px | 본문` 두 칸 그리드. 사이드바는 화면 높이에 고정(sticky)되고 위에서부터 로고 · 메뉴(홈 · 받은 업무 · 후속 관리 · 자료실 · 설정) · 맨 아래 계정과 로그아웃.

**일반 페이지(홈 · 후속 관리 · 설정):** 본문 최대 1180px, 안쪽 여백 28px 32px. 페이지 요소 간격 20px, 카드 간격 16px. 설정은 760px 좁은 폭.

**3단(받은 업무):** 목록 판 `minmax(340px, 440px)` + 상세 패널(나머지). 둘 다 화면 높이에서 따로 스크롤된다. 목록 판은 흰 면, 상세 패널은 `bg` 바탕이며 안쪽 본문은 최대 920px 가운데. 자료실은 반대로 표(넓게) + 오른쪽 상세 `minmax(380px, 460px)`.

**간격 리듬:** 4 · 8 · 12 · 16 · 18 · 20 · 28px. 목록 판 머리와 행은 좌우 18px, 카드 안쪽 18px 20px, 상세 본문 20px 28px.

**반응형 3단계:**
- **≤1200px:** 목록 판 `minmax(300px, 380px)`, 홈 숫자 칸 3열, 홈 두 단 → 한 단.
- **≤980px:** 사이드바가 64px 아이콘 막대로 줄고(글자는 화면 읽기용으로만 남김, 배지는 아이콘 위 모서리), 3단은 한 번에 한쪽만 — 업무를 열면 목록이 숨고 상세만 보인다.
- **≤720px:** 기준 글자 15px. 사이드바가 아래 고정 탭 막대(아이콘 + 0.78rem 글자, 높이 48px 이상)가 된다. 숫자 칸 2열(첫 칸은 전체 폭), 라벨-값 행은 라벨이 위로 올라가고, 동작 버튼은 전체 폭으로 쌓이며, 작은 버튼 · 아이콘 버튼은 최소 40px 높이. 자료 표는 이름 · 공유 · 상태 열만 남긴다. 단축키 안내는 숨긴다.

## Elevation & Depth

거의 평평하다. 깊이는 바탕(`bg`)과 흰 면의 명도 차, 1px 구획선으로 만든다. 그림자는 두 단계뿐이며, 큰 그림자는 내용 위에 겹치거나 hover에 반응하는 것에만 쓴다.

### Shadow Vocabulary
- **면** (`box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05)`, `--shadow-1`): 카드 · 숫자 칸 · 표 상자 · 주 버튼 · 고스트 버튼 · 분할 탭의 활성 칸.
- **떠 있는 판** (`box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px -10px rgba(15, 23, 42, 0.12)`, `--shadow-2`): 업무 취소 확인 판, 보내기 확인 카드, 숫자 칸 hover, 로그인 카드 · 예시 목록.
- **포커스 고리** (`box-shadow: 0 0 0 3px var(--accent-50)`): 입력칸 · 검색칸 포커스(파란 테두리와 함께).

### Named Rules
**The 그림자 두 단계 Rule.** 쉬는 면은 `--shadow-1`, 겹치거나 hover로 떠오르는 것만 `--shadow-2`. 카드 안에서 그림자를 다시 쌓지 않고 안쪽 구획은 1px 선으로 나눈다.

## Shapes

부드럽고 단정하다. 카드 · 숫자 칸 · 표 상자 · 드롭 영역은 10px(`--radius`), 버튼 · 입력칸 · 알림 띠 · 안쪽 상자는 7px(`--radius-sm`), 분할 탭 틀은 8px · 그 안의 칸은 6px, 기한 칩 · 배지 · 근거 인용은 6px, AI 표지 · 직접 정함 · 코드 · 단축키는 4px. 상태 표지(pill)와 사이드바 개수 배지는 알약형, 상태 점 · 아바타 · 체크 표시는 원. 로그인 카드만 14px.

선은 구획 1px, 체크박스 · 체크 표시 · 보내기 확인 테두리 1.5px. 점선은 "구분은 하되 끊지 않을 것"에 쓴다: 바뀐 내용 항목 사이, 메일 본문 구분, 자료 드롭 영역. 접기 화살표와 체크 표시는 CSS 선으로 그린다.

## Components

### Buttons
차분하고 납작하다. 색 전환 0.12s.
- **Shape:** 7px 모서리, 8px 14px, 600 굵기, 0.9rem. 작게 5px 11px / 0.84rem, 크게 11px 18px / 0.96rem.
- **Primary:** `accent` 바탕 + 흰 글자 + 면 그림자. hover `accent-600`. 일반 주 동작.
- **보내기:** `navy` 바탕 + 흰 글자, hover `navy-3`. "이대로 보내기" 같은 마지막 단계에만.
- **Ghost:** 흰 바탕 + `border-2` 테두리 + 면 그림자. hover `surface-2`. 클래스 없는 `.btn`의 기본값.
- **Danger:** 흰 바탕 + 빨강 글자 + `red-100` 테두리, hover `red-50`.
- **Link / 아이콘 버튼:** 바탕 없음, `text-2` · `text-3`, hover `surface-3`. 아이콘 버튼 32px 정사각.
- **Focus / Disabled:** 포커스는 `accent` 2px 외곽선(offset 2px). 비활성은 불투명도 0.45.

### Chips
- **상태 표지(pill):** 알약형, 0.82rem / 600, 1px 8px. 회색 · 파랑 · 초록 · 황토 · 빨강 다섯 톤, 모두 세 톤 규칙. 누르지 않는다. "할 일 0/2"도 회색 표지이며 다 끝나면 글자만 초록.
- **배지(badge):** 6px 모서리 사각 표지, 0.78rem / 650. 상세 머리의 업무 상태("처리할 일")에 쓴다.
- **기한 칩:** 흰 바탕 + `border-2` 테두리, 6px, 0.78rem / 700. 지나면 빨강 세 톤.
- **상태 점(state):** 7px 원 + 글자(650, 0.84rem). 켜짐 · 연결됨 초록, 꺼짐 `text-3`, 막힘 빨강, 대기 황토.

### AI 표지와 직접 정함
- **`ai-tag`:** `accent-50` 바탕 + `accent-600` 글자(700, 0.82rem), 4px 모서리, 앞에 5px 파란 점. "AI 정리" · "AI 판단" · "AI · 메일의 근거 문장".
- **`by-user`:** `surface-3` 바탕 + `text-2` 글자 + `border` 테두리, 4px. "직접 정함". AI 값과 같은 자리에 바꿔 놓는다.
- **근거 인용(evidence):** `surface-2` 바탕 + `border` 테두리 6px 상자, 0.86rem `text-2`. 위에 `ai-tag`, 아래 따옴표로 감싼 메일 원문 한 줄.

### Cards / Containers
- **Corner Style:** 10px.
- **Background:** 흰 면, `bg` 위.
- **Shadow Strategy:** 면 그림자(Elevation 참조).
- **Border:** `border` 1px.
- **Internal Padding:** 18px 20px, 안쪽 간격 12px. 접기 카드는 머리 12px 18px.

### Inputs / Fields
- **Style:** 흰 바탕, `border-2` 1px, 7px, 8px 11px. 라벨은 위에 0.84rem / 600 / `text-2`.
- **Focus:** `accent` 테두리 + 3px `accent-50` 고리. 검색칸은 돋보기 아이콘 + 오른쪽 `/` 단축키 표시.
- **Disabled:** `surface-2` 바탕 + `text-2` 글자. 체크박스는 18px · 5px 모서리, 체크되면 `accent` 채움.

### Navigation
- **사이드바:** `navy` 바탕, 232px. 메뉴 항목 8px 10px · 7px 모서리 · 550 굵기 · `navy-text`. hover `navy-2` + 흰 글자, 활성 `navy-3` + 흰 글자 + 밝은 파랑 아이콘. 개수 배지는 반투명 흰 알약, 활성이면 `accent` 알약.
- **분할 탭(seg):** `surface-3` 틀 안에서 활성 칸만 흰 면 + 면 그림자, 개수는 `accent`. 받은 업무 탭(처리할 일 · 답 기다림 · 참고용 · 완료)과 중요도 선택에 쓴다.
- **밑줄 탭:** 활성 글자 `text` + `accent` 2px 밑줄.
- **휴대폰:** 아래 고정 탭 막대(Layout 참조).

### 목록 행
행 전체가 상세로 가는 링크. `14px | 내용 | 기한` 세 칸 그리드, 11px 18px, 행 사이 1px 선. 위줄: 보낸 사람(650) · 회사(`text-3`) · 상대 시각(오른쪽). 아래: 요약(550, 두 줄까지) · 표지 줄. 오른쪽: 기한(700, 0.82rem, 고정폭), 지나면 빨강, 추정이면 아래 황토 "확인 필요", 없으면 `text-3` "기한 없음". hover `surface-2`, 선택 `accent-50` + 요약 650, 안 읽은 변경은 이름 뒤 6px 파란 점. 묶음 제목은 `surface-2` 띠에 붙어 스크롤 중 고정되고, "지금 처리"만 빨강.

### 중요도 막대
3px 폭 막대 세 칸(높이 6 · 10 · 14px, 모서리 1px), 빈 칸은 `border-2`. **낮음** = 첫 칸 `text-3`, **보통** = 두 칸 `accent`, **높음** = 세 칸 모두 `red`. 칸 수로도 구분되고 `aria-label`로 이름을 붙인다.

### 상세 패널
위쪽 고정 막대(닫기 · "2 / 5" · 위아래 이동, `bg` 92% + 6px 흐림). 머리: 배지 · 기한 칩 · 등록 시각 → 제목(1.35rem) → 28px 네이비 아바타 + 보낸 사람. 그 아래 동작 카드(주 버튼 하나 + 고스트 버튼들), 요약 카드(라벨 96px | 값 행, 행 사이 1px 선), 할 일(초록 4px 진행 막대 + 체크 목록), 답장, 원본 메일, 처리 기록(세로선 + 9px 원 표시) 순서.

### 발송 체크리스트
`border` 테두리 7px 상자. 머리 띠(`surface-2`)에 결과 문구가 전체 상태 색(통과 초록 · 확인 필요 황토 · 막힘 빨강)으로. 항목은 `표시 18px | 이름 110px | 설명` 그리드: 통과 = 초록 원 + 체크, 확인 = 황토 원 + 점, 막힘 = 빨강 원 + 가로줄. 막힌 항목은 이름도 빨강. 모두 통과해야 보내기 버튼이 열린다. 보내기 확인은 `navy` 1.5px 테두리 + 떠 있는 판 그림자 카드 안의 라벨-값 표로, 화면 안에서 한 번 더 묻는다.

### 숫자 칸 (홈)
5칸 그리드, 각 칸 10px 흰 면 + 면 그림자, 14px 16px. 이름(label) → 큰 숫자(display) → 작은 설명. 첫 칸은 `navy` 채움 + 흰 숫자, 기한 임박은 빨강 숫자, 확인 필요류는 황토 숫자. 칸 전체가 링크이고 hover에 `--shadow-2`.

### 데이터 표 (자료실)
10px 표 상자(면 그림자). 머리 `surface-2` · 0.82rem / 650 / `text-3`, 칸 9px 14px, 행 사이 1px 선, 숫자 열 오른쪽 정렬. hover `surface-2`, 선택 행 `accent-50`. 이름 칸은 아이콘 + 굵은 글자 버튼, hover `accent`.

### 스위치 목록 (설정)
`64px | 설명` 행 목록, 행 사이 1px 선. 왼쪽은 상태 점(켜짐 초록 · 꺼짐 `text-3`), 오른쪽은 이름과 바꾸는 방법 안내. 읽기 전용이며 토글을 그리지 않는다.

### 알림 띠
7px, 10px 13px, 0.88rem, 세 톤 + 한 단계 더 진한 글자(예: 빨강 띠 `#8a2414`). 동작이 있으면 글과 버튼을 한 줄에, 휴대폰에서는 쌓는다. 화면 안 확인 판(`inline-panel`)은 `surface-2` 상자로 0.16s 동안 3px 위에서 펼쳐진다.

## Do's and Don'ts

### Do:
- **Do** 강조는 `accent` 하나로, 선택 · 링크 · 포커스 · 주 동작 · AI 표지에만 쓴다.
- **Do** 상태는 빨강(기한 지남 · 막힘) · 황토(확인 필요) · 초록(완료 · 준비됨)을 `-50` 바탕 + 본색 글자 + `-100` 테두리 세 톤으로, 항상 글자와 함께 보인다.
- **Do** AI가 만든 값에는 `ai-tag`, 사용자가 정한 값에는 `by-user`를 붙이고, 근거가 있으면 근거 인용 상자로 원문을 보인다.
- **Do** 되돌릴 수 없는 보내기는 발송 체크리스트를 통과한 뒤 `navy` 버튼과 화면 안 확인 카드로 받는다.
- **Do** 건수 · 기한 · 할 일 · 시각은 고정폭 숫자로, 기한은 행 오른쪽에 정렬한다.
- **Do** 면은 10px 흰 카드 + `border` + `--shadow-1`, 안쪽 구획은 1px 선으로 나눈다.
- **Do** 아이콘은 `Icon.tsx`의 24 격자 · 선 1.6 선 아이콘에 추가한다.
- **Do** 확인 단계는 카드 안 확인 판이나 확인 버튼처럼 화면 안에 만든다.

### Don't:
- **Don't** 바탕 · 버튼 · 표지 · 제목에 그라데이션을 쓴다. 로딩 자리 표시의 반짝임만 예외다.
- **Don't** 보라 · 바이올렛이나 반짝이 · 별 같은 AI 장식을 쓴다. AI 표시는 `ai-tag` 하나다.
- **Don't** 카드 · 행 · 알림의 한쪽에만 굵은 색 테두리를 긋는다. 상태는 표지 · 점 · 글자색으로 보인다.
- **Don't** 이모지나 유니코드 기호(✓ ★ ⚠ 등)를 아이콘으로 쓴다.
- **Don't** `window.confirm` / `alert` / `prompt`를 쓴다.
- **Don't** 네이비 채움 버튼을 일반 주 동작에 쓰거나, 상태색을 진하게 면으로 채운다.
- **Don't** 카드 안에 그림자 카드를 다시 쌓는다.
