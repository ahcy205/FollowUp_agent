---
version: 1
slug: "frontend-src-pages-inbox-tsx"
primary_target: "frontend/src/pages/Inbox.tsx"
related_targets: ["frontend/src/pages/TaskDetail.tsx","frontend/src/App.tsx"]
---

## Scope
FollowUp Agent 앱 전체(받은 업무 · 업무 상세 · 자료실 · 설정 · 로그인 · 전체 업무). Mode: Operate.
Audience: 견적 · 납기 메일이 많은 영업 · 구매 담당, PC와 휴대폰 반반. Job: 오늘 누구에게 무엇을 언제까지 해야 하는지 보고 처리한다.
Constraints: API · 데이터 · 동작 그대로. 확인 단계는 화면 안. 생성 이미지 없음. AI 티 나는 장식 금지(사용자 요구).

## Direction contract
THESIS: 업무 메일을 담당자가 매일 쓰는 견적서 · 거래명세서의 질서로 정리한다. 카드 더미 대시보드(둥근 카드 + 그림자 + 보라 강조 + 이모지)를 거부하고, 흰 종이 한 장 위의 구획선 · 칸 · 기입란 · 도장으로 업무를 보여 준다.
OWN-WORLD: 차가운 회색 책상 위 흰 서식 용지. 잉크 흑청 #1C2127 본문, 인쇄 라벨은 회색 칸(#F4F5F6) 안 작은 글씨, 굵은 바깥 테두리와 가는 안쪽 구획선, 모서리 2px. 강조색은 인주 주홍 #C2361B 하나(보내기 · 확정 도장, 기한 지남, 지금 처리 표시), 링크 · 선택 · 포커스는 청색 볼펜 #1F4E9C. 중요도는 상/중/하 칸 글자. Pretendard, 숫자는 고정폭 숫자.
STORY: 처음 연 사람은 "오늘 날짜 기준 업무 대장"을 보고, 기한 순으로 무엇이 급한지 안다. 업무를 열면 상대 · 요청 · 기한 · 근거가 칸에 정리된 양식이 있고, 할 일을 체크한 뒤 근거가 붙은 답장 초안에 확인 도장을 찍어 보낸다.
FIRST VIEWPORT: 받은 업무. 맨 위 기준일 · Gmail 상태 한 줄, 그 아래 합계 칸 띠(지금 처리 · 이번 주 · 나중에 · 답 기다림, 지금 처리 숫자가 가장 크고 주홍), 그 아래 탭과 업무 대장 표(중요 · 기한(D-day 오른쪽 정렬 숫자) · 상대 · 업무 요약 · 할 일 · 상황). 주 동작은 행 전체(업무 열기). 휴대폰은 행이 두 줄 기입란으로 접히고 메뉴는 아래 탭 막대.
FORM: 견적서 · 거래명세서 양식 (내 순위 1위, IMPECCABLE'S PICK으로 사용자가 고름), seed key 5bb5c401.
SIGNATURE: 답장을 보내거나 업무를 완료하면 주홍 사각 도장("보냄" / "완료")이 눌리듯 찍힌다(200ms, 축소 + 불투명도). 그 밖의 움직임은 상태 전환 150~200ms뿐.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
