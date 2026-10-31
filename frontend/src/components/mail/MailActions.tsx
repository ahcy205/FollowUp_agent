import { useEffect, useRef, useState } from 'react'
import { localDateValue, localTimeValue, withLocalOffset } from '../../format'
import type { MailAct, ReplyDraft, Task } from '../../types'
import CancelTaskPanel from '../CancelTaskPanel'

type Panel = 'none' | 'snooze' | 'close'

/** 처리할 일 탭 업무인지 (backend inbox.tab_of와 같은 규칙). 나중에 보기는 이 업무에만 보인다. */
function isTodoTab(task: Task) {
  if (task.status === 'ACTION_REQUIRED' || task.status === 'IN_PROGRESS' || task.status === 'BLOCKED') return true
  const analysis = task.brief?.analysis
  return task.status === 'PENDING' && (analysis === 'pending' || analysis === 'failed')
}

export type ComposePurpose = 'reply' | 'followup'

/**
 * 버튼 줄 (설계 3.4절): 답장 초안 만들기 · 나중에 보기 · 답장 필요 없음으로 닫기 · 업무 취소.
 * 초안 입력칸(설계 2.1)은 여기서 열린다. 안내 띠의 "다시 연락하기"와 초안 카드의 "다시 만들기"도 compose로 연다.
 */
export default function MailActions({
  task,
  act,
  onCancelled,
  emphasizeReply,
  draft,
  compose,
  onCompose,
}: {
  task: Task
  act: MailAct
  onCancelled: () => void
  emphasizeReply: boolean
  draft: ReplyDraft | null
  compose: ComposePurpose | null
  onCompose: (purpose: ComposePurpose | null) => void
}) {
  const [panel, setPanel] = useState<Panel>('none')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [custom, setCustom] = useState(false)
  const [until, setUntil] = useState('')
  const [reason, setReason] = useState('')
  const [userInput, setUserInput] = useState('')
  const composeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (compose) composeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [compose])

  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  if (closed) return null
  const canDraft = task.merged_into_id == null
  const locked = draft?.status === 'sending' || draft?.status === 'unknown'
  const showDraftButton = canDraft && !locked && draft?.status !== 'draft'
  const remaining = task.plan.filter((p) => !p.done).length

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
    <section className="card mail-actions" id={draft && draft.status === 'draft' ? undefined : 'reply'}>
      <div className="mail-action-row">
        <span className="reply-draft">
          {showDraftButton && (
            <>
              <button
                type="button"
                className={`btn btn-sm ${emphasizeReply ? 'btn-primary' : 'btn-ghost'}`}
                aria-expanded={compose != null}
                onClick={() => {
                  setError('')
                  onCompose(compose ? null : 'reply')
                }}
              >
                답장 초안 만들기
              </button>
              {remaining > 0 && <span className="muted small">아직 끝나지 않은 할 일이 {remaining}개 있습니다</span>}
            </>
          )}
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

      {compose && canDraft && !locked && (
        <div className="inline-panel" ref={composeRef}>
          <b>{compose === 'followup' ? '다시 연락하는 메일에 넣을 내용' : '답장에 넣을 내용'}</b>
          <textarea
            rows={4}
            maxLength={2000}
            value={userInput}
            placeholder="(선택) 예: 납기는 11/15로 확정, 단가는 기존과 같음"
            onChange={(e) => setUserInput(e.target.value)}
          />
          <div className="muted small">할 일 메모는 자동으로 들어갑니다. 더 넣을 내용이 있으면 적어 주세요.</div>
          {draft?.status === 'draft' && <div className="muted small">지금 있는 초안은 새 초안으로 바뀝니다.</div>}
          <div className="inline-actions">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={!!busy}
              onClick={() =>
                run('draft', async () => {
                  await act('/reply-draft', { purpose: compose, user_input: userInput.trim() })
                  setUserInput('')
                  onCompose(null)
                })
              }
            >
              {busy === 'draft' ? '초안을 만드는 중…' : '초안 만들기'}
            </button>
            <button type="button" className="btn btn-link btn-sm" disabled={!!busy} onClick={() => onCompose(null)}>
              취소
            </button>
          </div>
        </div>
      )}

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
