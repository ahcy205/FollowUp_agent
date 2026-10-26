import type { TaskStatus } from './types'

export const STATUS_META: Record<TaskStatus, { label: string; tone: string; short: string }> = {
  COMPLETED: { label: '완료', short: '완료', tone: 'green' },
  WAITING: { label: '대기 중', short: '대기', tone: 'amber' },
  IN_PROGRESS: { label: '진행 중', short: '진행', tone: 'blue' },
  ACTION_REQUIRED: { label: '조치 필요', short: '조치 필요', tone: 'red' },
  BLOCKED: { label: '중단됨', short: '중단', tone: 'slate' },
  PENDING: { label: '시작 전', short: '시작 전', tone: 'gray' },
  CANCELLED: { label: '취소됨', short: '취소', tone: 'gray' },
}

export const TOOL_LABELS: Record<string, string> = {
  plan_goal: '목표 분석 · 계획 수립',
  search_customer: '고객 정보 조회',
  search_document: '문서 · 견적서 조회',
  draft_email: '이메일 초안 작성',
  send_email: '이메일 발송',
  check_email: '수신 메일 확인',
  analyze_email: '고객 답변 분석',
  create_followup_task: '후속 업무 생성',
  complete_plan_step: '하위 목표 달성',
  update_task_status: '업무 진행 상황 기록',
}

export const PERMISSIONS: Record<string, string> = {
  plan_goal: 'WRITE · 현재 업무',
  search_customer: 'READ',
  search_document: 'READ',
  draft_email: 'READ / CREATE',
  send_email: 'WRITE + 승인 필요',
  check_email: 'READ',
  analyze_email: 'READ',
  create_followup_task: 'WRITE',
  complete_plan_step: 'WRITE · 현재 업무',
  update_task_status: 'WRITE',
}

// The API returns UTC; SQLite drops the offset, so treat offset-less timestamps as UTC.
export function parseDate(iso: string) {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
}

export function time(iso: string) {
  return parseDate(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

export function dateTime(iso: string) {
  return parseDate(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

export function relative(iso: string) {
  const diff = (Date.now() - parseDate(iso).getTime()) / 1000
  if (diff < 60) return '방금 전'
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`
  return `${Math.floor(diff / 86400)}일 전`
}

export function tokens(n: number | undefined) {
  return (n ?? 0).toLocaleString('ko-KR')
}

const WEEKDAYS = '일월화수목금토'

// "10/30(금)" 또는 "10/30(금) 15:00". 시각이 없는(하루 종일) 기한은 날짜만 쓴다.
export function shortDate(iso: string, withTime = true) {
  const d = parseDate(iso)
  const day = `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAYS[d.getDay()]})`
  if (!withTime || (d.getHours() === 0 && d.getMinutes() === 0)) return day
  return `${day} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// <input type="date"> · <input type="time"> 값 (브라우저 시간대)
export function localDateValue(iso: string) {
  const d = parseDate(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function localTimeValue(iso: string) {
  const d = parseDate(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// datetime-local 값("2026-10-27T09:00")을 브라우저 시간대 오프셋을 붙인 ISO로 바꾼다.
export function withLocalOffset(value: string) {
  const d = new Date(value)
  const off = -d.getTimezoneOffset()
  const sign = off >= 0 ? '+' : '-'
  const abs = Math.abs(off)
  return `${localDateValue(d.toISOString())}T${localTimeValue(d.toISOString())}:00${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}
