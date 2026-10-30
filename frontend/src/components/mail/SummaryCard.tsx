import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { localDateValue, localTimeValue, shortDate } from '../../format'
import type { MailAct, Priority, TaskDetail } from '../../types'
import ChangesSection from './ChangesSection'

const PRIORITIES: { value: Priority; cls: string; mark: string; label: string }[] = [
  { value: 'HIGH', cls: 'prio-high', mark: '상', label: '높음' },
  { value: 'NORMAL', cls: 'prio-mid', mark: '중', label: '보통' },
  { value: 'LOW', cls: 'prio-low', mark: '하', label: '낮음' },
]

const USER_DEADLINE_SOURCE = '사용자가 직접 정함' // backend task_actions.USER_DEADLINE_SOURCE

/** 요약 카드 본문 (설계 3.2절): 요약 · 보낸 사람 · 원하는 것 · 바뀐 내용(6단계) · 중요도 · 기한. 제목 카드 안에 들어간다. */
export default function SummaryCard({ data, act, calendarLine }: { data: TaskDetail; act: MailAct; calendarLine?: ReactNode }) {
  const { task, mail } = data
  const brief = task.brief
  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)
  const [date, setDate] = useState('')
  const [time, setTime] = useState('')

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError('')
    try {
      await fn()
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    } finally {
      setBusy('')
    }
  }

  const openEditor = () => {
    setDate(task.deadline_at ? localDateValue(task.deadline_at) : '')
    setTime(task.deadline_at && !task.deadline_all_day ? localTimeValue(task.deadline_at) : '')
    setError('')
    setEditing(true)
  }

  const save = async () => {
    if (!date) {
      setError('날짜를 고르세요.')
      return
    }
    if (await run('save', () => act('/deadline', time ? { date, time } : { date }))) setEditing(false)
  }

  const clear = async () => {
    if (await run('clear', () => act('/deadline', { clear: true }))) setEditing(false)
  }

  const cp = brief.counterpart
  const sender = cp?.name ? (cp.organization ? `${cp.name} (${cp.organization})` : cp.name) : ''
  const source = data.emails.find((e) => e.id === brief.email_id) ?? data.emails.find((e) => e.direction === 'INBOUND')
  const requests = (brief.requests ?? []).filter((r) => r.trim())
  const badge = mail?.deadline_badge
  const estimated = task.deadline_at != null && task.deadline_kind === 'estimated'
  const analyzing = brief.analysis === 'pending'

  return (
    <div className="summary">
      {analyzing ? (
        <p className="summary-text muted">메일을 분석하고 있어요. 끝나면 요약과 할 일이 여기에 나타납니다.</p>
      ) : (
        brief.summary && <p className="summary-text">{brief.summary}</p>
      )}

      <dl className="form-grid">
        <div className="form-row">
          <dt>상대</dt>
          <dd>
            <b>{sender || source?.sender || '보낸 사람 모름'}</b>
            {source && <span className="muted"> · {shortDate(source.created_at, false)} 받음</span>}
          </dd>
        </div>

        {(mail?.merged_sources.length ?? 0) > 0 && (
          <div className="form-row">
            <dt>합친 업무</dt>
            <dd>
              <ul className="merged-sources">
                {mail!.merged_sources.map((m) => (
                  <li key={m.task_id}>
                    <div className="muted small">
                      <Link to={`/tasks/${m.task_id}`}>
                        #{m.task_id} {m.title}
                      </Link>
                      {m.merged_at && ` · ${shortDate(m.merged_at, false)} 합침`}
                    </div>
                    {m.summary && <div>{m.summary}</div>}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}

        {requests.length > 0 && (
          <div className="form-row">
            <dt>원하는 것</dt>
            <dd>
              <ol className="summary-requests">
                {requests.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ol>
            </dd>
          </div>
        )}

        <ChangesSection log={task.change_log ?? []} act={act} closed={closed} />

        <div className="form-row">
          <dt>중요도</dt>
          <dd className="priority-row">
            <div className="priority-buttons" role="group" aria-label="중요도">
              {PRIORITIES.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  className={`priority-btn${task.priority === p.value ? ' active' : ''}`}
                  aria-pressed={task.priority === p.value}
                  disabled={closed || !!busy}
                  onClick={() => task.priority !== p.value && run(`p-${p.value}`, () => act('/priority', { priority: p.value }))}
                >
                  <span className={`prio ${p.cls}`} aria-hidden="true">
                    {p.mark}
                  </span>
                  {p.label}
                </button>
              ))}
            </div>
            {mail?.priority_source === 'user' && <span className="muted small">직접 정함</span>}
          </dd>
        </div>

        <div className={`form-row deadline-row${badge?.late ? ' late' : ''}`}>
          <dt>기한</dt>
          <dd className="deadline-box">
            {task.deadline_at ? (
              <div className="deadline-head">
                <b className="num">{shortDate(task.deadline_at, !task.deadline_all_day, true)}</b>
                {badge && <span className={`due-chip num${badge.late ? ' late' : ''}`}>{badge.text}</span>}
                {estimated && <span className="flag flag-amber">확인 필요</span>}
                {!closed && !editing && (
                  <span className="deadline-buttons">
                    {estimated && (
                      <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => run('confirm', () => act('/deadline', { confirm: true }))}>
                        {busy === 'confirm' ? '저장 중…' : '이 기한이 맞아요'}
                      </button>
                    )}
                    <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={openEditor}>
                      기한 수정
                    </button>
                  </span>
                )}
              </div>
            ) : (
              <div className="deadline-head">
                <span className="muted">기한 없음</span>
                {!closed && !editing && (
                  <span className="deadline-buttons">
                    <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={openEditor}>
                      기한 추가
                    </button>
                  </span>
                )}
              </div>
            )}

            {task.deadline_at &&
              task.deadline_source &&
              (task.deadline_source === USER_DEADLINE_SOURCE ? (
                <div className="muted small">직접 정한 기한</div>
              ) : (
                <div className="evidence small">근거 “{task.deadline_source}”</div>
              ))}
            {!task.deadline_at && brief.deadline_note && <div className="evidence small">메일에 적힌 기한 “{brief.deadline_note}”</div>}

            {editing && (
              <div className="deadline-edit">
                <div className="deadline-inputs">
                  <label>
                    날짜
                    <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
                  </label>
                  <label>
                    시각 (선택)
                    <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
                  </label>
                </div>
                <div className="inline-actions">
                  <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={save}>
                    {busy === 'save' ? '저장 중…' : '저장'}
                  </button>
                  {task.deadline_at && (
                    <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={clear}>
                      {busy === 'clear' ? '없애는 중…' : '기한 없애기'}
                    </button>
                  )}
                  <button type="button" className="btn btn-link btn-sm" disabled={!!busy} onClick={() => setEditing(false)}>
                    취소
                  </button>
                </div>
              </div>
            )}

            {calendarLine}
          </dd>
        </div>
      </dl>

      {error && <div className="alert alert-red">{error}</div>}
    </div>
  )
}
