import { STATUS_META } from '../format'
import type { TaskStatus } from '../types'

export default function StatusBadge({ status, large }: { status: TaskStatus; large?: boolean }) {
  const m = STATUS_META[status] ?? STATUS_META.PENDING
  return (
    <span className={`badge badge-${m.tone} ${large ? 'badge-lg' : ''}`}>
      <span className={`dot dot-${m.tone} ${status === 'IN_PROGRESS' ? 'pulse' : ''}`} />
      {m.label}
    </span>
  )
}
