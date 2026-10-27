import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import { dateTime } from '../../format'
import type { MailAct, TaskDetail } from '../../types'
import type { ComposePurpose } from './MailActions'

const KEEP_TODOS_KEY = 'followup.keepTodos' // "그대로 두기"를 누른 업무: {task_id: reply_sent_at}

function keptTodos(taskId: number, sentAt: string | null): boolean {
  try {
    const raw = window.localStorage.getItem(KEEP_TODOS_KEY)
    const map: unknown = raw ? JSON.parse(raw) : {}
    return !!sentAt && typeof map === 'object' && map !== null && (map as Record<string, unknown>)[taskId] === sentAt
  } catch {
    return false
  }
}

function keepTodos(taskId: number, sentAt: string | null) {
  try {
    const raw = window.localStorage.getItem(KEEP_TODOS_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : {}
    const map = typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : {}
    window.localStorage.setItem(KEEP_TODOS_KEY, JSON.stringify({ ...map, [taskId]: sentAt }))
  } catch {
    // 저장소를 쓸 수 없으면 이번 화면에서만 숨긴다.
  }
}

/** 맨 위 안내 띠 (설계 3.1 · 5 · 6.5 · 7.3절). 해당하는 것만 위에서부터 보여준다. */
export default function MailBanners({
  data,
  act,
  onMerged,
  onCompose,
}: {
  data: TaskDetail
  act: MailAct
  onMerged: (target: TaskDetail) => void
  onCompose: (purpose: ComposePurpose) => void
}) {
  const { task, mail } = data
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [keptFor, setKeptFor] = useState<string | null>(null) // 저장소를 못 써도 이번 화면에서는 숨긴다
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
  const judged = candidates.filter((c) => c.evidence) // "수정 요청으로 보임" 판단 (설계 7.3)
  const sameSender = candidates.filter((c) => !c.evidence) // 판단을 못 했을 때의 5단계 규칙
  const noReply = mail.no_reply && !closed
  const thanks = task.status === 'WAITING' && task.brief.thanks_email_id != null
  const todosKept = mail.reply_sent_at != null && (keptFor === mail.reply_sent_at || keptTodos(task.id, mail.reply_sent_at))
  const remaining = task.plan.filter((p) => !p.done).length
  const todosPrompt = mail.remaining_todos_prompt && remaining > 0 && !todosKept && !closed
  const hasAny =
    failed ||
    candidates.length > 0 ||
    mail.undoable_merges.length > 0 ||
    mail.nudge_days != null ||
    mail.is_snoozed ||
    mail.merged_into_id != null ||
    mail.merge_analyzing ||
    noReply ||
    thanks ||
    todosPrompt
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

      {noReply && (
        <div className="alert alert-amber alert-action">
          <span>보낸 지 영업일 3일째 답이 없습니다</span>
          <span className="mail-banner-buttons">
            <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => onCompose('followup')}>
              다시 연락하기
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('wait', () => act('/no-reply', { choice: 'wait' }))}>
              {busy === 'wait' ? '요청 중…' : '더 기다리기'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('nr-close', () => act('/no-reply', { choice: 'close' }))}>
              {busy === 'nr-close' ? '닫는 중…' : '완료로 닫기'}
            </button>
          </span>
        </div>
      )}

      {thanks && (
        <div className="alert alert-green alert-action">
          <span>상대가 답장했습니다(답장 필요 없음)</span>
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('thanks-close', () => act('/close', { reason: '' }))}>
            {busy === 'thanks-close' ? '닫는 중…' : '완료로 닫기'}
          </button>
        </div>
      )}

      {todosPrompt && (
        <div className="alert alert-slate alert-action">
          <span>남은 할 일을 완료로 정리할까요?</span>
          <span className="mail-banner-buttons">
            <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => run('complete-all', () => act('/todos/complete-all'))}>
              {busy === 'complete-all' ? '정리하는 중…' : '모두 완료'}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!!busy}
              onClick={() => {
                keepTodos(task.id, mail.reply_sent_at)
                setKeptFor(mail.reply_sent_at)
              }}
            >
              그대로 두기
            </button>
          </span>
        </div>
      )}

      {mail.merge_analyzing && <div className="alert alert-slate">합친 내용을 분석하는 중입니다. 끝나면 바뀐 내용이 요약에 나타납니다.</div>}

      {judged.map((c) => (
        <div key={c.task_id} className="alert alert-blue alert-action">
          <span>
            업무 #{c.task_id}({c.title})의 수정 요청으로 보입니다 — '{c.evidence}'
          </span>
          <span className="mail-banner-buttons">
            <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => merge(c.task_id)}>
              {busy === `merge-${c.task_id}` ? '합치는 중…' : '이 업무에 합치기'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('dismiss', () => act('/merge/dismiss'))}>
              {busy === 'dismiss' ? '요청 중…' : '따로 두기'}
            </button>
          </span>
        </div>
      ))}

      {sameSender.length > 0 && (
        <div className="alert alert-blue">
          <div className="mail-banner-title">{name ? `${name} 님의 진행 중 업무가 있습니다` : '같은 상대의 진행 중 업무가 있습니다'}</div>
          <ul className="merge-list">
            {sameSender.map((c) => (
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
