import { useState } from 'react'
import { localDateValue, localTimeValue, withLocalOffset } from '../../format'
import type { MailAct, Task } from '../../types'
import CancelTaskPanel from '../CancelTaskPanel'

type Panel = 'none' | 'snooze' | 'close'

/** 처리할 일 탭 업무인지 (backend inbox.tab_of와 같은 규칙). 나중에 보기는 이 업무에만 보인다. */
function isTodoTab(task: Task) {
  if (task.status === 'ACTION_REQUIRED' || task.status === 'IN_PROGRESS' || task.status === 'BLOCKED') return true
  const analysis = task.brief?.analysis
  return task.status === 'PENDING' && (analysis === 'pending' || analysis === 'failed')
}

/** 버튼 줄 (설계 3.4절): 나중에 보기 · 답장 필요 없음으로 닫기 · 업무 취소 · 답장 초안 만들기(6단계). */
export default function MailActions({
  task,
  act,
  onCancelled,
  emphasizeReply,
}: {
  task: Task
  act: MailAct
  onCancelled: () => void
  emphasizeReply: boolean
}) {
  const [panel, setPanel] = useState<Panel>('none')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [custom, setCustom] = useState(false)
  const [until, setUntil] = useState('')
  const [reason, setReason] = useState('')

  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  if (closed) return null

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError('')
    try {
      await fn()
      setPanel('none')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const toggle = (p: Panel) => {
    setError('')
    setCustom(false)
    setPanel(panel === p ? 'none' : p)
  }

  const snoozeCustom = () => {
    if (!until) {
      setError('다시 볼 날짜와 시각을 고르세요.')
      return
    }
    run('custom', () => act('/snooze', { until: withLocalOffset(until) }))
  }

  const now = new Date()
  const nowIso = now.toISOString()
  const minValue = `${localDateValue(nowIso)}T${localTimeValue(nowIso)}`
  const presets = [
    ...(now.getHours() < 18 ? [{ key: 'evening', label: '오늘 오후 6시' }] : []),
    { key: 'tomorrow', label: '내일 오전 9시' },
    { key: 'next_monday', label: '다음 주 월요일 오전 9시' },
  ]

  return (
    <section className="card mail-actions">
      <div className="mail-action-row">
        <span className="reply-draft">
          <button type="button" className={`btn btn-sm ${emphasizeReply ? 'btn-primary' : 'btn-ghost'}`} disabled>
            답장 초안 만들기
          </button>
          <span className="muted small">6단계에서 제공</span>
        </span>
        {isTodoTab(task) && (
          <button type="button" className="btn btn-ghost btn-sm" aria-expanded={panel === 'snooze'} onClick={() => toggle('snooze')}>
            나중에 보기
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-sm" aria-expanded={panel === 'close'} onClick={() => toggle('close')}>
          답장 필요 없음으로 닫기
        </button>
        <span className="mail-cancel">
          <CancelTaskPanel taskId={task.id} onCancelled={onCancelled} />
        </span>
      </div>

      {panel === 'snooze' && isTodoTab(task) && (
        <div className="inline-panel">
          <b>언제 다시 볼까요?</b>
          <div className="snooze-choices">
            {presets.map((p) => (
              <button key={p.key} type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run(p.key, () => act('/snooze', { preset: p.key }))}>
                {busy === p.key ? '요청 중…' : p.label}
              </button>
            ))}
            <button type="button" className={`btn btn-sm ${custom ? 'btn-primary' : 'btn-ghost'}`} disabled={!!busy} aria-expanded={custom} onClick={() => setCustom(!custom)}>
              직접 선택
            </button>
          </div>
          {custom && (
            <div className="snooze-custom">
              <input type="datetime-local" value={until} min={minValue} aria-label="다시 볼 날짜와 시각" onChange={(e) => setUntil(e.target.value)} />
              <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={snoozeCustom}>
                {busy === 'custom' ? '요청 중…' : '이때 다시 보기'}
              </button>
            </div>
          )}
          <div className="muted small">기한 하루 전 오전 9시가 먼저 오면 그때 다시 나타납니다.</div>
        </div>
      )}

      {panel === 'close' && (
        <div className="inline-panel">
          <b>답장 없이 이 업무를 닫을까요?</b>
          <textarea rows={2} maxLength={500} value={reason} placeholder="(선택) 닫는 이유" onChange={(e) => setReason(e.target.value)} />
          <div className="inline-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => run('close', () => act('/close', { reason: reason.trim() }))}>
              {busy === 'close' ? '닫는 중…' : '닫기'}
            </button>
            <button type="button" className="btn btn-link btn-sm" disabled={!!busy} onClick={() => setPanel('none')}>
              취소
            </button>
          </div>
        </div>
      )}

      {error && <div className="alert alert-red">{error}</div>}
    </section>
  )
}
