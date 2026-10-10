import { useState } from 'react'
import { api } from '../api'

export default function CompleteFollowupCard({ taskId, onDone }: { taskId: number; onDone: () => void }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const complete = async () => {
    setBusy(true)
    setError('')
    try {
      await api.post(`/tasks/${taskId}/complete`, { note })
      onDone()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <section className="card followup-complete">
      <div className="section-head">
        <h2>✅ 이 업무 완료 처리</h2>
        <span className="tag">사용자 처리</span>
      </div>
      <p className="muted small">Agent가 정리해 준 후속 업무입니다. 처리한 뒤 결과를 남기면 원래 업무의 타임라인에도 기록됩니다.</p>
      <textarea
        rows={2}
        maxLength={1500}
        value={note}
        placeholder="(선택) 처리 결과 메모 — 예) 생산팀 확인 결과 10월 8일 납품 가능, 추가 비용 없음"
        onChange={(e) => setNote(e.target.value)}
      />
      {error && <div className="alert alert-red">{error}</div>}
      <div className="approval-actions">
        <button className="btn btn-primary" onClick={complete} disabled={busy}>
          {busy ? '처리 중…' : '완료 처리'}
        </button>
      </div>
    </section>
  )
}
