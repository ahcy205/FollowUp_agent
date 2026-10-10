import { useState } from 'react'
import { api } from '../api'

export default function GoalEditor({ taskId, goal, editable, onSaved }: { taskId: number; goal: string; editable: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(goal)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      await api.post(`/tasks/${taskId}/goal`, { goal: draft.trim() })
      setEditing(false)
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!editing) {
    return (
      <div className="goal-view">
        <span>{goal}</span>
        {editable && (
          <button
            type="button"
            className="btn btn-link btn-sm"
            onClick={() => {
              setDraft(goal)
              setEditing(true)
            }}
          >
            ✎ 수정
          </button>
        )}
      </div>
    )
  }
  const changed = draft.trim() !== goal && draft.trim().length >= 5
  return (
    <div className="goal-edit">
      <textarea rows={3} maxLength={1000} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="업무 목표" />
      <div className="muted small">저장하면 Agent가 새 목표로 계획을 다시 세웁니다. 이미 달성한 하위 목표는 제목이 같으면 유지됩니다.</div>
      {error && <div className="alert alert-red">{error}</div>}
      <div className="approval-actions">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(false)} disabled={busy}>
          닫기
        </button>
        <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={busy || !changed}>
          {busy ? '저장 중…' : '저장하고 Agent 재실행'}
        </button>
      </div>
    </div>
  )
}
