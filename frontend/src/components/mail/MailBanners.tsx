import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import { dateTime } from '../../format'
import type { MailAct, TaskDetail } from '../../types'

/** 맨 위 안내 띠 (설계 3.1절). 해당하는 것만 위에서부터 보여준다. */
export default function MailBanners({
  data,
  act,
  onMerged,
}: {
  data: TaskDetail
  act: MailAct
  onMerged: (target: TaskDetail) => void
}) {
  const { task, mail } = data
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  if (!mail) return null

  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  const name = task.brief.counterpart?.name?.trim()

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  // 합치기 응답은 합친 대상(A)의 상세다. 화면은 그 업무로 옮긴다.
  const merge = (intoId: number) =>
    run(`merge-${intoId}`, async () => onMerged(await api.post<TaskDetail>(`/tasks/${task.id}/merge`, { into_task_id: intoId })))

  const failed = task.brief.analysis === 'failed' && !closed
  const candidates = closed ? [] : mail.merge_candidates
  const hasAny =
    failed || candidates.length > 0 || mail.undoable_merges.length > 0 || mail.nudge_days != null || mail.is_snoozed || mail.merged_into_id != null
  if (!hasAny && !error) return null

  return (
    <div className="mail-banners">
      {error && <div className="alert alert-red">{error}</div>}

      {mail.merged_into_id != null && (
        <div className="alert alert-blue alert-action">
          <span>업무 #{mail.merged_into_id}에 합쳐진 업무입니다.</span>
          <Link className="btn btn-ghost btn-sm" to={`/tasks/${mail.merged_into_id}`}>
            합친 업무로 가기
          </Link>
        </div>
      )}

      {failed && (
        <div className="alert alert-red alert-action">
          <span>자동 분석에 실패했습니다.</span>
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('reanalyze', () => act('/reanalyze'))}>
            {busy === 'reanalyze' ? '요청 중…' : '다시 분석'}
          </button>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="alert alert-blue">
          <div className="mail-banner-title">{name ? `${name} 님의 진행 중 업무가 있습니다` : '같은 상대의 진행 중 업무가 있습니다'}</div>
          <ul className="merge-list">
            {candidates.map((c) => (
              <li key={c.task_id}>
                <span className="merge-title">{c.title}</span>
                <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => merge(c.task_id)}>
                  {busy === `merge-${c.task_id}` ? '합치는 중…' : '이 업무에 합치기'}
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('dismiss', () => act('/merge/dismiss'))}>
            {busy === 'dismiss' ? '요청 중…' : '따로 두기'}
          </button>
        </div>
      )}

      {mail.undoable_merges.map((m) => (
        <div key={m.source_id} className="alert alert-green alert-action">
          <span>
            업무 #{m.source_id} '{m.title}'이(가) 합쳐졌습니다
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={!!busy}
            onClick={() => run(`undo-${m.source_id}`, () => act('/merge/undo', { source_id: m.source_id }))}
          >
            {busy === `undo-${m.source_id}` ? '되돌리는 중…' : '되돌리기'}
          </button>
        </div>
      ))}

      {mail.nudge_days != null && !closed && <div className="alert alert-amber">{mail.nudge_days}일째 답장하지 않았습니다.</div>}

      {mail.is_snoozed && mail.wake_at && (
        <div className="alert alert-slate alert-action">
          <span>{dateTime(mail.wake_at)}에 다시 표시됩니다.</span>
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('unsnooze', () => act('/unsnooze'))}>
            {busy === 'unsnooze' ? '요청 중…' : '지금 보기'}
          </button>
        </div>
      )}
    </div>
  )
}

/** 상대 답장 없음 안내 (6단계에서 연결). 이번 단계에서는 만들어만 두고 렌더하지 않는다. */
export function CounterpartSilentBanner({ onFollowUp, onClose, busy }: { onFollowUp: () => void; onClose: () => void; busy?: boolean }) {
  return (
    <div className="alert alert-amber alert-action">
      <span>상대 답장 없음 — 다시 연락할까요?</span>
      <span className="mail-banner-buttons">
        <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={onFollowUp}>
          다시 연락하기
        </button>
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={onClose}>
          완료로 닫기
        </button>
      </span>
    </div>
  )
}
