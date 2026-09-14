import { useState } from 'react'
import { api } from '../api'

export default function ResetDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const reset = async () => {
    setBusy(true)
    setError('')
    try {
      await api.post('/demo/reset')
      onDone()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>데모 데이터 초기화</h3>
        <p className="muted">
          내 계정의 업무, 이메일, Agent 로그, 승인 요청을 모두 삭제하고 초기 가상 데이터(고객 5 · 문서 6 · 메일 8 · 업무 5)를 다시
          생성합니다. 다른 사용자의 데이터에는 영향이 없습니다.
        </p>
        {error && <div className="alert alert-red">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button className="btn btn-danger" onClick={reset} disabled={busy}>
            {busy ? '초기화 중…' : '초기화'}
          </button>
        </div>
      </div>
    </div>
  )
}
