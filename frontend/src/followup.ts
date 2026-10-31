import type { Inbox as InboxData, InboxCard } from './types'

/** 닫히지 않은 업무 카드 전부(처리할 일 · 답 기다림 · 참고용). */
export function openCards(d: InboxData): InboxCard[] {
  return [...d.tabs.todo.now, ...d.tabs.todo.week, ...d.tabs.todo.later, ...d.tabs.sent, ...d.tabs.reference]
}

/** 후속 관리에서 챙길 업무: 무응답 · 상대가 내용 바꿈 · 답장 안 함. 사이드바 · 홈 · 후속 관리가 같은 기준을 쓴다. */
export function needsFollowup(c: InboxCard): boolean {
  return c.no_reply || c.changed_unseen || c.nudge_days != null
}
