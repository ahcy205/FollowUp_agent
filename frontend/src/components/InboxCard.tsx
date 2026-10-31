import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { relative } from '../format'
import type { InboxCard as Card } from '../types'
import Icon from './Icon'

const PRIORITY: Record<string, { cls: string; mark: string; label: string }> = {
  HIGH: { cls: 'prio-high', mark: '상', label: '중요도 높음' },
  NORMAL: { cls: 'prio-mid', mark: '중', label: '중요도 보통' },
  LOW: { cls: 'prio-low', mark: '하', label: '중요도 낮음' },
}

/** 중요도 칸: 서식의 상 · 중 · 하. 색만으로 구분하지 않는다. */
export function PriorityMark({ priority }: { priority: string }) {
  const p = PRIORITY[priority] ?? PRIORITY.NORMAL
  return (
    <span className={`prio ${p.cls}`} role="img" aria-label={p.label} title={p.label}>
      {p.mark}
    </span>
  )
}

type Flag = { text: string; tone: 'red' | 'blue' | 'amber' | 'gray' }

function flags(card: Card): Flag[] {
  const out: Flag[] = []
  if (card.analysis === 'pending') out.push({ text: '분석 중', tone: 'gray' })
  if (card.analysis === 'failed') out.push({ text: '분석 실패', tone: 'red' })
  if (card.changed_unseen) out.push({ text: '내용 바뀜', tone: 'blue' })
  if (card.no_reply) out.push({ text: '답 없음 3일째', tone: 'amber' })
  if (card.nudge_days != null) out.push({ text: `${card.nudge_days}일째 답장 안 함`, tone: 'amber' })
  if (card.returned) out.push({ text: '다시 표시됨', tone: 'amber' })
  if (card.has_merge_candidates) out.push({ text: '합치기 제안', tone: 'blue' })
  return out
}

/** 업무 대장의 한 줄. 줄 전체가 업무 상세로 가는 링크다. */
export default function InboxCard({ card, extra }: { card: Card; extra?: ReactNode }) {
  const badge = card.deadline_badge
  const chips = flags(card)
  const due = badge.text || '기한 없음'
  const noDue = !badge.text || badge.text === '기한 없음'
  const progress = card.todo_total > 0 ? Math.round((card.todo_done / card.todo_total) * 100) : 0
  return (
    <div className="ledger-row">
      <Link to={`/tasks/${card.task_id}`} className="ledger-link">
        <span className="ledger-prio">
          <PriorityMark priority={card.priority} />
        </span>
        <span className="ledger-main">
          <span className="ledger-who">
            <b>{card.sender_name || '보낸 사람 모름'}</b>
            {card.sender_org && <span>{card.sender_org}</span>}
          </span>
          <span className="ledger-title">{card.summary || card.title}</span>
          {chips.length > 0 && (
            <span className="ledger-flags">
              {chips.map((c) => (
                <span key={c.text} className={`flag flag-${c.tone}`}>
                  {c.text}
                </span>
              ))}
            </span>
          )}
        </span>
        <span className="ledger-recv muted">{card.received_at ? relative(card.received_at) : ''}</span>
        <span className="ledger-todo" aria-label={card.todo_total > 0 ? `할 일 ${card.todo_total}개 중 ${card.todo_done}개 완료` : '할 일 없음'}>
          {card.todo_total > 0 ? (
            <>
              <span className="num">
                {card.todo_done}/{card.todo_total}
              </span>
              <span className="meter" aria-hidden="true">
                <span style={{ width: `${progress}%` }} />
              </span>
            </>
          ) : (
            <span className="muted">—</span>
          )}
        </span>
        <span className={`ledger-due${badge.late ? ' late' : ''}${noDue ? ' none' : ''}`}>
          <span className="num">{due}</span>
          {badge.estimated && <span className="due-note">확인 필요</span>}
        </span>
        <Icon name="chevron" size={16} className="ledger-go" />
      </Link>
      {extra}
    </div>
  )
}
