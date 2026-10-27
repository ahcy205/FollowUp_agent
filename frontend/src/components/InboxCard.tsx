import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { InboxCard as Card } from '../types'

const PRIORITY: Record<string, { cls: string; label: string }> = {
  HIGH: { cls: 'pdot-high', label: '중요도 높음' },
  NORMAL: { cls: 'pdot-mid', label: '중요도 보통' },
  LOW: { cls: 'pdot-low', label: '중요도 낮음' },
}

export function PriorityDot({ priority }: { priority: string }) {
  const p = PRIORITY[priority] ?? PRIORITY.NORMAL
  return <span className={`pdot ${p.cls}`} role="img" aria-label={p.label} title={p.label} />
}

function situations(card: Card): string[] {
  const out: string[] = []
  if (card.analysis === 'pending') out.push('분석 중')
  if (card.analysis === 'failed') out.push('분석 실패')
  if (card.nudge_days != null) out.push(`${card.nudge_days}일째 답장 안 함`)
  if (card.returned) out.push('다시 표시됨')
  if (card.no_reply) out.push('보낸 지 영업일 3일째 답이 없습니다')
  if (card.changed_unseen) out.push('내용 바뀜')
  if (card.has_merge_candidates) out.push('합치기 제안')
  return out
}

export default function InboxCard({ card, extra }: { card: Card; extra?: ReactNode }) {
  const badge = card.deadline_badge
  const sender = card.sender_org ? `${card.sender_name} (${card.sender_org})` : card.sender_name
  const chips = situations(card)
  return (
    <div className="inbox-card">
      <Link to={`/tasks/${card.task_id}`} className="inbox-card-link">
        <PriorityDot priority={card.priority} />
        <div className="inbox-card-body">
          <div className="inbox-card-top">
            <span className="inbox-sender">{sender || '보낸 사람 모름'}</span>
            <span className={`deadline-badge${badge.late ? ' late' : ''}`}>
              {badge.text}
              {badge.estimated ? ' · 확인 필요' : ''}
            </span>
          </div>
          <div className="inbox-summary">{card.summary || card.title}</div>
          {(card.todo_total > 0 || chips.length > 0) && (
            <div className="inbox-meta">
              {card.todo_total > 0 && (
                <span className="muted">
                  할 일 {card.todo_done}/{card.todo_total}
                </span>
              )}
              {chips.map((c) => (
                <span key={c} className={`tag${c === '분석 실패' ? ' tag-red' : c === '합치기 제안' || c === '내용 바뀜' ? ' tag-blue' : ' tag-amber'}`}>
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>
      </Link>
      {extra}
    </div>
  )
}
