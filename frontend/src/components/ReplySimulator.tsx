import { useState } from 'react'
import { api } from '../api'

const PRESETS = [
  { key: 'delivery', label: '납기 단축 요청', desc: '“납기일을 10월 15일보다 앞당길 수 있을까요?”' },
  { key: 'accept', label: '발주 확정', desc: '“이 조건으로 발주 진행하겠습니다.”' },
  { key: 'injection', label: '악성 지시 포함', desc: 'Prompt Injection 방어 시연용' },
  { key: 'custom', label: '직접 입력', desc: '원하는 답변 내용을 작성' },
] as const

export default function ReplySimulator({ taskId, enabled, onSent }: { taskId: number; enabled: boolean; onSent: () => void }) {
  const [preset, setPreset] = useState<(typeof PRESETS)[number]['key']>('delivery')
  const [content, setContent] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const send = async () => {
    setBusy(true)
    setError('')
    try {
      await api.post(`/demo/tasks/${taskId}/simulate-reply`, { preset, content })
      setContent('')
      onSent()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card simulator">
      <div className="section-head">
        <h2>📨 고객 답변 도착 시뮬레이션</h2>
        <span className="tag">DEMO 이벤트</span>
      </div>
      <p className="muted small">가상 메일 시스템에 고객 답변을 생성합니다. 답변이 도착하면 Agent가 자동으로 다시 실행됩니다.</p>
      <div className="preset-grid">
        {PRESETS.map((p) => (
          <button key={p.key} type="button" className={`preset ${preset === p.key ? 'active' : ''}`} onClick={() => setPreset(p.key)}>
            <b>{p.label}</b>
            <span>{p.desc}</span>
          </button>
        ))}
      </div>
      {preset === 'custom' && (
        <textarea rows={3} maxLength={2000} value={content} placeholder="고객 답변 내용" onChange={(e) => setContent(e.target.value)} />
      )}
      {error && <div className="alert alert-red">{error}</div>}
      <button className="btn btn-primary btn-block" onClick={send} disabled={!enabled || busy || (preset === 'custom' && !content.trim())}>
        {busy ? '답변 생성 중…' : enabled ? '고객 답변 도착' : 'Agent 실행이 끝난 뒤 사용할 수 있습니다'}
      </button>
    </section>
  )
}
