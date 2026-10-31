import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { relative } from '../format'
import type { InboxCard as Card } from '../types'

const PRIORITY: Record<string, { cls: string; label: string }> = {
  HIGH: { cls: 'prio-high', label: '중요도 높음' },
  NORMAL: { cls: 'prio-mid', label: '중요도 보통' },
  LOW: { cls: 'prio-low', label: '중요도 낮음' },
}

/** 중요도 표시: 막대 3칸(높음 3 · 보통 2 · 낮음 1). 이름은 화면 낭독기와 툴팁으로. */
export function PriorityMark({ priority }: { priority: string }) {
  const p = PRIORITY[priority] ?? PRIORITY.NORMAL
  return (
    <span className={`prio ${p.cls}`} role="img" aria-label={p.label} title={p.label}>
      <i />
      <i />
      <i />
    </span>
  )
}

type Flag = { text: string; tone: 'red' | 'blue' | 'amber' | 'gray' }

function flags(card: Card): Flag[] {
  const out: Flag[] = []
  if (card.analysis === 'pending') out.push({ text: 'AI 분석 중', tone: 'gray' })
  if (card.analysis === 'failed') out.push({ text: '분석 실패', tone: 'red' })
  if (card.changed_unseen) out.push({ text: '내용 바뀜', tone: 'blue' })
  if (card.no_reply) out.push({ text: '답 없음 3일째', tone: 'amber' })
  if (card.nudge_days != null) out.push({ text: `${card.nudge_days}일째 답장 안 함`, tone: 'amber' })
  if (card.returned) out.push({ text: '다시 표시됨', tone: 'amber' })
  if (card.has_merge_candidates) out.push({ text: '합치기 제안', tone: 'blue' })
  return out
}

/** 업무 목록의 한 줄. 줄 전체가 상세 패널을 여는 링크다. */
export default function InboxCard({ card, to, active, extra }: { card: Card; to: string; active?: boolean; extra?: ReactNode }) {
  const badge = card.deadline_badge
  const chips = flags(card)
  const noDue = !badge.text || badge.text === '기한 없음'
  return (
    <div className={`row${active ? ' active' : ''}${card.changed_unseen ? ' unseen' : ''}`}>
      <Link to={to} className="row-link" aria-current={active ? 'true' : undefined} data-task={card.task_id}>
        <PriorityMark priority={card.priority} />
        <span className="row-main">
          <span className="row-top">
            <b className="row-who">{card.sender_name || '보낸 사람 모름'}</b>
            {card.sender_org && <span className="row-org">{card.sender_org}</span>}
            <span className="row-when muted">{card.received_at ? relative(card.received_at) : ''}</span>
          </span>
          <span className="row-title">{card.summary || card.title}</span>
          {(chips.length > 0 || card.todo_total > 0) && (
            <span className="row-flags">
              {card.todo_total > 0 && (
                <span className={`pill pill-todo${card.todo_done === card.todo_total ? ' done' : ''}`}>
                  할 일 <span className="num">{card.todo_done}/{card.todo_total}</span>
                </span>
              )}
              {chips.map((c) => (
                <span key={c.text} className={`pill pill-${c.tone}`}>
                  {c.text}
                </span>
              ))}
            </span>
          )}
        </span>
        <span className={`row-due num${badge.late ? ' late' : ''}${noDue ? ' none' : ''}`}>
          {noDue ? '기한 없음' : badge.text}
          {badge.estimated && <span className="row-due-note">확인 필요</span>}
        </span>
      </Link>
      {extra}
    </div>
  )
}
