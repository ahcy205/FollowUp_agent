import { useState } from 'react'
import { api } from '../api'

export default function CancelTaskPanel({ taskId, onCancelled }: { taskId: number; onCancelled: () => void }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const cancel = async () => {
    setBusy(true)
    setError('')
    try {
      await api.post(`/tasks/${taskId}/cancel`, { reason: reason.trim() })
      setOpen(false)
      onCancelled()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button type="button" className="btn btn-link btn-sm" onClick={() => setOpen(true)} title="업무를 취소합니다. 이력은 남고 Agent는 멈춥니다.">
        업무 취소
      </button>
    )
  }
  return (
    <div className="cancel-panel">
      <b>이 업무를 취소할까요?</b>
      <div className="muted small">실행 중인 Agent는 멈추고 대기 중인 승인은 무효가 됩니다. 이력은 그대로 남습니다.</div>
      <input value={reason} maxLength={500} placeholder="(선택) 취소 사유" onChange={(e) => setReason(e.target.value)} />
      {error && <div className="alert alert-red">{error}</div>}
      <div className="approval-actions">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)} disabled={busy}>
          돌아가기
        </button>
        <button type="button" className="btn btn-danger btn-sm" onClick={cancel} disabled={busy}>
          {busy ? '취소 중…' : '취소 확정'}
        </button>
      </div>
    </div>
  )
}
