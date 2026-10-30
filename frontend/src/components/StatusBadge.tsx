import { STATUS_META } from '../format'
import type { TaskStatus } from '../types'

/** 상태 표시: 서식 위에 찍힌 작은 도장처럼 테두리 글자. 진행 중만 점이 깜빡인다. */
export default function StatusBadge({ status, large }: { status: TaskStatus; large?: boolean }) {
  const m = STATUS_META[status] ?? STATUS_META.PENDING
  return (
    <span className={`badge badge-${m.tone}${large ? ' badge-lg' : ''}`}>
      {status === 'IN_PROGRESS' && <span className="dot dot-blue pulse" aria-hidden="true" />}
      {m.label}
    </span>
  )
}
