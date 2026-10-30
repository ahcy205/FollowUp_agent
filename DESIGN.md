---
name: FollowUp Agent
description: 업무 메일을 견적서 · 거래명세서의 질서로 정리하는 후속조치 도구
colors:
  seal: "#c23a22"
  seal-600: "#a52f1a"
  seal-50: "#fcf0ed"
  seal-100: "#f5d6ce"
  ink: "#1e2328"
  ink-2: "#4f5761"
  ink-3: "#69717b"
  desk: "#eff1f3"
  paper: "#ffffff"
  label: "#f4f5f6"
  hover: "#f8f9fa"
  rule: "#e2e5e9"
  rule-2: "#cfd4da"
  blue: "#2453a6"
  blue-50: "#edf2fb"
  blue-100: "#cfdcf2"
  green: "#2e6e48"
  green-50: "#ebf4ee"
  green-100: "#cfe5d6"
  amber: "#8a5800"
  amber-50: "#fdf4df"
  amber-100: "#f1dca8"
typography:
  display:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "2.15rem"
    fontWeight: 700
    lineHeight: 1.32
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "1.6rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  title:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "1.05rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  body:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "'ss06'"
  label:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "0.82rem"
    fontWeight: 650
    lineHeight: 1.4
  figure:
    fontFamily: "'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "1.6rem"
    fontWeight: 750
    lineHeight: 1.1
    letterSpacing: "-0.01em"
    fontFeature: "'tnum'"
rounded:
  xs: "4px"
  sm: "6px"
  md: "10px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "14px"
  lg: "20px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sm}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "#343b43"
  button-seal:
    backgroundColor: "{colors.seal}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sm}"
    padding: "9px 16px"
  button-seal-hover:
    backgroundColor: "{colors.seal-600}"
  button-ghost:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "9px 16px"
  button-ghost-hover:
    backgroundColor: "{colors.label}"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "10px 12px"
  sheet:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.md}"
    padding: "22px 24px"
  form-label-cell:
    backgroundColor: "{colors.label}"
    textColor: "{colors.ink-3}"
    typography: "{typography.label}"
    padding: "12px 14px"
    width: "104px"
  priority-high:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sm}"
    size: "24px"
  priority-mid:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    size: "24px"
  priority-low:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.sm}"
    size: "24px"
  stamp:
    backgroundColor: "{colors.seal-50}"
    textColor: "{colors.seal}"
    rounded: "{rounded.pill}"
    size: "54px"
  flag:
    backgroundColor: "{colors.label}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "1px 8px"
---

# Design System: FollowUp Agent

## Overview

**Creative North Star: "부드러운 서식 용지"**

차가운 회색 책상 위에 흰 서식 용지가 한 장씩 놓여 있다. 업무는 카드 더미가 아니라 견적서 · 거래명세서처럼 **구획선 · 회색 라벨 칸 · 기입란**으로 정리되고, 끝난 일에는 주홍 인주 도장이 찍힌다. 받은 업무는 "업무 대장" 표 한 장, 업무 상세는 용지 한 장이며, 그 안의 구획은 상자를 겹치지 않고 가는 선으로 나눈다.

처음 방향은 각진 양식(모서리 2px, 사각 도장)이었지만, 제작 중 사용자가 "양식으로 하되 너무 딱딱해 보이지 않게"를 요청해 부드럽게 다듬었다. 모서리는 6~10px, 구획선은 옅은 회색(#e2e5e9), 도장은 둥근 인감, 문구는 "~했어요"체의 친근한 말투다. 완성 검토 뒤에는 바탕을 차가운 회색으로 정리하고, 주홍을 보내기 · 확정, 기한 지남, 지금 처리, 도장, 로고에만 남겼다. 선택 · 메뉴 표시는 잉크색이다.

실무자가 하루에 여러 번 여는 도구라서 밀도와 일관성이 장식보다 우선한다. 사용자가 확인한 거부 대상: 보라 · 파랑 그라데이션, 이모지 · 유니코드 기호 아이콘, 그림자 카드 반복, 보라색 강조.

**Key Characteristics:**
- 흰 용지(#ffffff) + 차가운 회색 책상(#eff1f3) 두 층. 구획은 선으로 나눈다.
- 강조색은 인주 주홍 하나. 화면에서 드물수록 급한 일이 보인다.
- 선택 · 현재 위치 · 중요도 "상"은 잉크색으로 채운다.
- 라벨은 회색 칸 안의 작은 굵은 글씨, 값은 흰 기입란.
- 숫자(합계 · D-day · 할 일 n/m)는 고정폭 숫자.
- 아이콘은 직접 그린 24 격자 선 아이콘(선 1.6). 로고는 둥근 사각 도장 안의 FU.

## Colors

차가운 회색 무채색 위에 인주 주홍 하나, 보조 상태색 셋(청 · 녹 · 황)을 옅은 바탕 + 진한 글자로만 쓰는 팔레트다.

### Primary
- **인주 주홍** (`seal`): 보내기 · 확정 버튼(`btn-seal`), 기한 지남(D+n 지남), 합계 띠의 "지금 처리" 칸과 대장의 "지금 처리" 묶음 제목, 완료 · 보냄 도장, 로고. 오류 띠와 위험 버튼(업무 취소 등)의 글자색도 같은 주홍을 옅은 바탕(`seal-50`) · 테두리(`seal-100`)와 함께 쓴다. 눌림 상태는 `seal-600`.

### Secondary
- **청색 볼펜** (`blue`): 링크, 키보드 포커스 고리, 입력칸 포커스 테두리, "내용 바뀜 · 합치기 제안" 표지, 정보 띠, 진행 중 표시. 선택 · 메뉴 활성 상태에는 쓰지 않는다.

### Tertiary
- **장부 녹색** (`green`): 체크 완료, 진행 막대, 연결됨 점, 완료 표지, 근거가 확인된 초안 값 상자.
- **황토** (`amber`): "확인 필요"(추정 기한), 답장 안 함 · 답 없음 표지, 초안의 빈칸 안내.

### Neutral
- **잉크** (`ink`): 본문 글자, 주 버튼 바탕, 메뉴 · 탭 선택 밑줄, 중요도 "상" 칸 채움, 체크박스 accent.
- **흐린 잉크** (`ink-2`, `ink-3`): 보조 글, 라벨 칸 글자, 칸 머리, 비활성 탭.
- **책상** (`desk`): 페이지 바탕. 용지는 이 위에 놓인다.
- **용지** (`paper`): 모든 시트 · 입력칸 · 기입란 바탕.
- **라벨 칸** (`label`): 서식 라벨 칸, 표 머리, 동작 막대 · 안내 띠 바탕, 고스트 버튼 hover.
- **행 hover** (`hover`): 대장 행 · 합계 칸 · 접기 구획의 hover.
- **구획선** (`rule`, `rule-2`): 안쪽 구획선은 `rule`, 입력칸 · 고스트 버튼 · 중요도 "하" 테두리는 한 단계 진한 `rule-2`.

### Named Rules
**The 인주 하나 Rule.** 주홍은 보내기 · 확정, 기한 지남, 지금 처리, 도장, 로고, 그리고 오류 · 위험에만 쓴다. 선택 상태, 일반 주 버튼, 장식에는 쓰지 않는다.

**The 잉크 선택 Rule.** 선택 · 현재 위치 · 가장 중요한 칸은 잉크로 채우거나 잉크 밑줄(2px)을 긋는다. 청색은 링크와 포커스용이다.

**The 보라 없음 Rule.** 보라 · 바이올렛과 그라데이션 강조는 쓰지 않는다(사용자 확정).

## Typography

**Display Font:** Pretendard Variable (with -apple-system, Apple SD Gothic Neo, Malgun Gothic)
**Body Font:** Pretendard Variable (같은 계열)
**Label/Mono Font:** 따로 없음. 코드만 ui-monospace / Cascadia Mono / Consolas.

**Character:** 한 가족의 굵기 차이(400 · 550 · 650 · 700 · 750)로만 위계를 만든다. 인쇄 서식처럼 라벨은 작고 굵고 흐리며, 기입된 값은 본문 크기 · 잉크색이다. 제목은 자간 -0.02em.

### Hierarchy
- **Display** (700, 2.15rem, 1.32): 로그인 첫 화면 문장 하나에만. 휴대폰 1.6rem.
- **Headline** (700, 1.6rem, 1.3): 페이지 제목(받은 업무 등). 업무 상세 제목은 1.45rem, 휴대폰 1.35rem.
- **Title** (700, 1.05rem): 시트 안 구획 제목. 세부 제목은 0.95rem / 650.
- **Body** (400, 15px, 1.55): 본문. 대장 행의 업무 요약은 600 / 0.97rem, 메일 본문은 0.93rem / 1.65, 초안 본문은 1.7.
- **Label** (650, 0.82rem, `ink-3`): 서식 라벨 칸, 합계 칸 이름, 대장 묶음 제목. 표 머리는 0.76rem. 대문자 변환 · 넓은 자간 없음.
- **Figure** (750, 1.6rem, 고정폭 숫자): 합계 칸 숫자. "지금 처리"는 2.1rem 주홍. D-day는 700 / 0.93rem 오른쪽 정렬.

### Named Rules
**The 고정폭 숫자 Rule.** 건수 · D-day · 할 일 n/m · 시간은 모두 `tabular-nums`로 세로 줄을 맞춘다.

## Layout

본문 폭은 최대 1280px, 좌우 여백 24px(휴대폰 14px). 페이지 안 요소 간격은 18px, 시트 안쪽 여백은 22~24px(휴대폰 16px). 구획 간격은 6 · 8 · 14 · 20 · 24px의 리듬을 쓴다.

- **받은 업무:** 제목과 기준일 한 줄 → 시트 한 장 안에 합계 칸 띠(4칸, 1.3fr : 1fr : 1fr : 1fr, 첫 칸이 "지금 처리") → 밑줄 탭 → 대장 표(중요 52px · 상대/업무 · 할 일 92px · 기한 112px 오른쪽 정렬 · 화살표 20px) → 접기 구획(나중에 보기, 업무 아님).
- **업무 상세:** 본문 + 오른쪽 340px 처리 기록(sticky). 본문은 `sheet-stack` 용지 한 장이며 안내 띠 · 요약 양식 · 답장 초안 · 할 일 · 동작 막대 · 원본 메일이 위쪽 구획선으로만 나뉜다. 1080px 이하에서 한 단으로.
- **로그인:** 1.15fr : 400px 두 단(문장 + 예시 대장 / 로그인 용지). 1080px 이하에서 한 단.
- **휴대폰(≤720px):** 메뉴는 아래 탭 막대(아이콘 22px + 작은 글자). 합계 칸은 2×2, 대장 행은 "중요 | 상대 · 업무 | 기한 / 할 일" 두 줄 기입란으로 접힌다. 서식 라벨 칸은 값 위로 올라가고, 동작 버튼은 전체 폭으로 쌓인다.

## Elevation & Depth

거의 평평하다. 깊이는 책상(회색)과 용지(흰색)의 명도 차, 그리고 구획선으로 만든다. 용지에는 거의 보이지 않는 그림자 하나만 깔려 종이가 책상에 놓인 느낌만 준다. 시트 안에서 다시 그림자를 쌓지 않는다.

### Shadow Vocabulary
- **용지** (`box-shadow: 0 1px 2px rgba(30,35,40,0.04), 0 4px 16px -6px rgba(30,35,40,0.06)`): 책상 위 시트 · 안내 용지에만. `sheet-stack` 안의 구획은 그림자를 지운다.
- **떠 있는 판** (`box-shadow: 0 8px 24px -8px rgba(30,35,40,0.18)`): 제목 옆에서 열리는 업무 취소 확인 판처럼 내용 위에 겹치는 것.
- **창** (`box-shadow: 0 24px 60px -12px rgba(30,35,40,0.3)`): 데모 초기화 창.

### Named Rules
**The 용지 한 장 Rule.** 한 주제는 용지 한 장이다. 그 안의 구획은 `rule` 1px 선으로 나누고, 카드 안에 그림자 카드를 넣지 않는다.

## Shapes

부드러운 서식이다. 용지 · 안내 · 드롭 영역은 10px, 버튼 · 입력칸 · 서식 표 · 알림 띠는 6px, 코드 · 작은 표지 · 메모는 4px. 표지(flag · tag · badge)와 탭 개수, 중요도 선택 버튼은 알약형. 중요도 칸은 24px 정사각에 6px 모서리, 도장은 원(54px), 로고는 7/32 모서리의 둥근 사각.

선은 두 굵기: 구획 1px, 상태 표시(중요도 칸 · 체크박스) 1.5px, 도장 2.5px. 점선은 "아직 확정되지 않은 것"에 쓴다: 완료 배너, 초안 메일, 바뀐 내용 사이, 메일 본문 구분, 자료 드롭 영역. 접기 구획 앞의 화살표와 목록 표시(짧은 줄표)는 CSS 선으로 그린다.

## Components

### Buttons
차분하고 단단하다. 누르면 1px 내려간다.
- **Shape:** 6px 모서리, 9px 16px, 650 굵기. 작게 6px 12px, 크게 12px 18px.
- **Primary:** 잉크 바탕 + 흰 글자. 일반 주 동작(로그인, Gmail 연결, 저장, 합치기, 기한 확정).
- **Seal:** 주홍 바탕 + 흰 글자. 답장 보내기 · 보내기 확정처럼 되돌릴 수 없는 마지막 단계에만.
- **Ghost:** 흰 바탕 + `rule-2` 테두리. hover 시 라벨 칸 바탕.
- **Danger:** 흰 바탕 + 주홍 글자 · 옅은 주홍 테두리.
- **Link:** 바탕 없음, `ink-2`, hover 시 라벨 칸 바탕.
- **Hover / Focus:** 색 전환 0.15s. 포커스는 청색 2px 외곽선(offset 2px). 비활성은 불투명도 0.45.

### Chips
- **Style:** 알약형 표지(0.76rem, 650, 1px 테두리). 회색 / 주홍 / 청 / 녹 / 황 다섯 톤, 모두 옅은 바탕 + 진한 글자 + 중간 테두리.
- **State:** 표지는 상태 표시용이며 누르지 않는다. 중요도 선택은 알약 버튼 안에 둥근 중요도 칸을 넣고, 선택되면 잉크 테두리 · 700 굵기.

### Cards / Containers
- **Corner Style:** 10px.
- **Background:** 용지 흰색, 책상 회색 위.
- **Shadow Strategy:** 용지 그림자 하나(Elevation 참조).
- **Border:** `rule` 1px.
- **Internal Padding:** 22px 24px(휴대폰 18px 16px). 대장 · 접기 구획 행은 13~15px 20px.

### Inputs / Fields
- **Style:** 흰 바탕, `rule-2` 1px, 6px 모서리, 10px 12px. 라벨은 위에 0.86rem / 650 / `ink-2`.
- **Focus:** 청색 테두리 + 3px `blue-50` 고리.
- **Error / Disabled:** 비활성은 라벨 칸 바탕 + `ink-2` 글자. 오류는 입력칸 아래 주홍 알림 띠.

### Navigation
- **Style:** 위쪽 막대 60px, 반투명 용지 + 흐림, 아래 구획선. 아이콘 + 글자(0.93rem / 550, `ink-2`).
- **Active:** 잉크 글자 700 + 잉크 밑줄 2px. 밑줄 탭(`tabs-line`)도 같은 규칙이며 개수는 알약형으로 붙인다.
- **Mobile:** 아래 고정 탭 막대, 활성 칸은 라벨 칸 바탕.

### 서식 표 (요약 · 답장 초안 · 설정)
104px 회색 라벨 칸(`label` 바탕, 0.82rem / 650 / `ink-3`, 오른쪽 구획선) + 흰 기입란. 행 사이 1px 구획선, 바깥은 6px 모서리 테두리. 기한이 지나면 그 행의 라벨 칸만 옅은 주홍 바탕 + 주홍 글자. 휴대폰에서는 라벨이 값 위로 올라간다.

### 업무 대장
행 전체가 상세로 가는 링크. 중요도 칸 · 상대(굵은 이름 + 흐린 회사) · 업무 요약 · 상태 표지 · 할 일 n/m · 기한(오른쪽 정렬, 지나면 주홍, "확인 필요"는 황토 작은 글자) · 흐린 화살표. hover 시 `hover` 바탕과 화살표가 2px 오른쪽으로. 묶음 제목(지금 처리 · 이번 주 · 나중에)은 label 크기, "지금 처리"만 주홍.

### 합계 칸 띠
시트 맨 위 4칸, 칸 사이 세로 구획선. 칸 이름(label) 아래 큰 숫자(figure) + 작은 "건". 첫 칸 "지금 처리"만 주홍 · 2.1rem. 칸을 누르면 해당 탭으로 간다.

### 중요도 칸
상 · 중 · 하 글자를 24px 칸에 넣어 색만으로 구분하지 않는다. **상** = 잉크로 채운 칸 + 흰 글자, **중** = 흰 칸 + `ink-2` 테두리, **하** = 흰 칸 + `rule-2` 테두리 + 흐린 글자.

### 도장
54px 원, 주홍 2.5px 테두리 + 안쪽 이중 고리, 옅은 주홍 바탕, 800 굵기, -8° 기울기. "보냄" · "완료" 두 가지. 찍힐 때 한 번만 200ms 동안 1.35배에서 줄어들며 나타난다. 이것이 이 시스템의 유일한 장식적 움직임이며, 나머지 움직임은 150~200ms 상태 전환뿐이다. `prefers-reduced-motion`이면 모두 즉시 끝난다.

## Do's and Don'ts

### Do:
- **Do** 한 주제를 용지 한 장(10px 모서리, `rule` 테두리, 용지 그림자)에 담고 안쪽 구획은 1px 선으로 나눈다.
- **Do** 라벨-값 정보는 104px 회색 라벨 칸 + 흰 기입란 서식 표로 보인다.
- **Do** 주홍은 보내기 · 확정, 기한 지남, 지금 처리, 도장, 로고, 오류 · 위험에만 쓴다.
- **Do** 선택 · 활성 표시는 잉크(채움 또는 2px 밑줄)로, 링크 · 포커스는 청색으로 한다.
- **Do** 중요도는 상/중/하 글자 칸으로, 상태는 색과 글자를 함께 쓴다.
- **Do** 숫자는 고정폭 숫자로, 기한은 오른쪽 정렬로 맞춘다.
- **Do** 아이콘은 `Icon.tsx`의 24 격자 · 선 1.6 직접 그린 선 아이콘에 추가한다.
- **Do** 문구는 짧고 친근하게("급한 순서로 정리했어요"), AI가 했다는 말 대신 결과를 보인다.

### Don't:
- **Don't** 보라 · 바이올렛 강조나 보라 · 파랑 그라데이션을 쓴다.
- **Don't** 이모지나 유니코드 기호(✓ ★ ⚠ 등)를 아이콘으로 쓴다.
- **Don't** 용지 안에 그림자 카드를 다시 쌓거나, 카드 더미 대시보드로 업무를 늘어놓는다.
- **Don't** 주홍을 선택 상태 · 일반 주 버튼 · 장식에 쓴다.
- **Don't** 모서리를 2px 이하로 각지게 하거나 굵은 바깥 테두리로 딱딱하게 만든다(사용자 요청으로 부드럽게 다듬음).
- **Don't** 도장 외의 장식 움직임을 추가한다. 상태 전환은 150~200ms.
- **Don't** 생성 이미지 · 일러스트를 넣는다.
