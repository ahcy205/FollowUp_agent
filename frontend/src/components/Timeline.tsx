import { MAIL_LABELS } from '../activity'
import { time } from '../format'
import type { ActionLog } from '../types'

interface ItemView {
  title: string
  tone: string
  detail?: string
}

function view(log: ActionLog): ItemView {
  switch (log.action_type) {
    case 'TASK_CANCELLED':
      return { title: '업무 취소', tone: 'gray', detail: log.reason }
    case 'SECURITY_WARNING': {
      const patterns = (log.output_data.patterns as string[] | undefined)?.join(', ')
      return { title: '보안 경고 · 메일 속 지시를 따르지 않음', tone: 'red', detail: patterns ? `${log.reason}\n탐지한 문구: ${patterns}` : log.reason }
    }
    default: {
      const label = MAIL_LABELS[log.action_type]
      return label ? { title: label[0], tone: label[1], detail: log.reason } : { title: log.reason || log.action_type, tone: 'gray' }
    }
  }
}

export default function Timeline({ logs }: { logs: ActionLog[] }) {
  const items = [...logs].reverse()
  return (
    <ol className="timeline">
      {items.map((log) => {
        const v = view(log)
        return (
          <li key={log.id} className={`tl-item tl-${v.tone}`}>
            <span className={`tl-mark tl-${v.tone}`} aria-hidden="true" />
            <div className="tl-body">
              <div className="tl-head">
                <span className="tl-title">{v.title}</span>
                <span className="tl-time">{time(log.created_at)}</span>
              </div>
              {v.detail && <div className="tl-detail">{v.detail}</div>}
              {log.risk_level === 'HIGH' && <span className="flag flag-red tl-risk">위험도 높음</span>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
