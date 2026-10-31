import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import Icon from '../components/Icon'
import StatusBadge from '../components/StatusBadge'
import Timeline from '../components/Timeline'
import MailActions, { type ComposePurpose } from '../components/mail/MailActions'
import MailBanners from '../components/mail/MailBanners'
import ReplyDraftCard from '../components/mail/ReplyDraftCard'
import SummaryCard from '../components/mail/SummaryCard'
import TodoChecklist from '../components/mail/TodoChecklist'
import { dateTime } from '../format'
import type { TaskDetail } from '../types'

export default function TaskDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState<TaskDetail | null>(null)
  const [error, setError] = useState('')
  const [calRetrying, setCalRetrying] = useState(false)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  // 답장 초안 입력칸. 업무를 옮기면 닫힌 것으로 본다.
  const [composeState, setComposeState] = useState<{ taskId: string | undefined; purpose: ComposePurpose } | null>(null)
  const compose = composeState && composeState.taskId === id ? composeState.purpose : null
  const onCompose = useCallback((purpose: ComposePurpose | null) => setComposeState(purpose ? { taskId: id, purpose } : null), [id])
  const seenFor = useRef<number | null>(null)

  // One-shot fetch, used right after a user action so the button feels instant and as a fallback when the stream closes.
  const load = useCallback(async () => {
    try {
      const d = await api.get<TaskDetail>(`/tasks/${id}`)
      setData(d)
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id])

  // 메일 업무 동작: 응답(TaskDetail)으로 화면을 바꾼다. 실패하면 ApiError를 그대로 던져 부품이 문구를 보여준다.
  const act = useCallback(
    async (path: string, body?: unknown) => {
      const d = await api.post<TaskDetail>(`/tasks/${id}${path}`, body)
      setData(d)
      setError('')
      return d
    },
    [id],
  )

  // 합치기 응답은 합친 대상의 상세다. 그 화면을 먼저 보여주고 주소를 옮긴다.
  const onMerged = useCallback(
    (target: TaskDetail) => {
      setData(target)
      navigate(`/tasks/${target.task.id}`)
    },
    [navigate],
  )

  // Live updates: the server pushes the whole task view immediately on connect and then whenever it changes (SSE).
  useEffect(() => {
    let source: EventSource | null = null
    let closed = false

    const connect = () => {
      if (closed) return
      source = new EventSource(`/api/tasks/${id}/events`)
      source.addEventListener('task', (ev) => {
        setData(JSON.parse((ev as MessageEvent<string>).data) as TaskDetail)
        setError('')
      })
      source.addEventListener('gone', () => {
        source?.close()
        setError('이 업무를 더 이상 찾을 수 없습니다. 삭제되었거나 다른 계정의 업무일 수 있습니다.')
      })
      source.onerror = () => {
        // The browser retries network drops by itself; an HTTP error (session expired, 404) closes the stream,
        // so surface it via a normal request and try the stream again later.
        if (source?.readyState === EventSource.CLOSED) {
          source = null
          load()
          clearTimeout(retryTimer.current)
          retryTimer.current = setTimeout(connect, 5000)
        }
      }
    }
    connect()

    return () => {
      closed = true
      clearTimeout(retryTimer.current)
      source?.close()
    }
  }, [id, load])

  // "내용 바뀜" 표시는 상세를 열면 끈다(설계 6.4). 한 번 연 업무에는 한 번만 보낸다.
  useEffect(() => {
    if (!data || String(data.task.id) !== id || !data.task.changed_unseen || seenFor.current === data.task.id) return
    seenFor.current = data.task.id
    api
      .post<TaskDetail>(`/tasks/${data.task.id}/seen`)
      .then((d) => setData((cur) => (cur && cur.task.id === d.task.id ? d : cur)))
      .catch(() => {
        // 표시가 남을 뿐이다. 다음에 열 때 다시 보낸다.
        seenFor.current = null
      })
  }, [data, id])

  // j / k: 다음 · 이전 업무, u: 받은 업무 (입력칸에서는 무시)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (e.ctrlKey || e.metaKey || e.altKey || (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable))) return
      const order = readOrder()
      const at = order.indexOf(Number(id))
      if (e.key === 'j' && at >= 0 && at < order.length - 1) navigate(`/tasks/${order[at + 1]}`)
      else if (e.key === 'k' && at > 0) navigate(`/tasks/${order[at - 1]}`)
      else if (e.key === 'u') navigate('/')
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id, navigate])

  const retryCalendar = async () => {
    setCalRetrying(true)
    try {
      setData(await api.post<TaskDetail>(`/tasks/${id}/calendar/retry`))
      setError('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setCalRetrying(false)
    }
  }

  if (!data) {
    return error ? (
      <div className="alert alert-red alert-action">
        <span>{error}</span>
        <Link className="btn btn-ghost btn-sm" to="/">
          받은 업무로
        </Link>
      </div>
    ) : (
      <div className="skeleton skeleton-detail" role="status" aria-label="불러오는 중">
        <div className="skeleton-row"><span /><span /><span /></div>
        <div className="skeleton-row"><span /><span /><span /></div>
      </div>
    )
  }

  const { task } = data
  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  const doneCount = task.plan.filter((p) => p.done).length
  const draft = data.mail?.draft ?? null
  const order = readOrder()
  const at = order.indexOf(task.id)
  const prevId = at > 0 ? order[at - 1] : null
  const nextId = at >= 0 && at < order.length - 1 ? order[at + 1] : null

  const calendarLine = task.calendar_state !== 'none' && (
    <div className="calendar-line small">
      {task.calendar_state === 'synced' && (
        <>
          <span>캘린더에 등록됨</span>
          {task.calendar_event_url.startsWith('https://') && (
            <a href={task.calendar_event_url} target="_blank" rel="noopener noreferrer">
              캘린더에서 열기
            </a>
          )}
        </>
      )}
      {task.calendar_state === 'pending' && <span className="muted">캘린더 반영 대기 중</span>}
      {task.calendar_state === 'deleted_by_user' && <span className="muted">캘린더에서 삭제된 일정입니다 (다시 만들지 않음)</span>}
      {task.calendar_state === 'error' && (
        <>
          <span className="text-red">{task.calendar_error}</span>
          <button className="btn btn-ghost btn-sm" onClick={retryCalendar} disabled={calRetrying}>
            {calRetrying ? '요청 중…' : '다시 시도'}
          </button>
        </>
      )}
    </div>
  )

  return (
    <div className="stack-lg">
      <div className="crumbs">
        <Link to="/">
          <Icon name="back" size={16} /> 받은 업무
        </Link>
        {at >= 0 && (
          <span className="pager">
            {prevId != null ? (
              <Link to={`/tasks/${prevId}`} title="이전 업무 (k)">
                이전 업무
              </Link>
            ) : (
              <span className="muted">이전 업무</span>
            )}
            <span className="muted num">
              {at + 1} / {order.length}
            </span>
            {nextId != null ? (
              <Link to={`/tasks/${nextId}`} title="다음 업무 (j)">
                다음 업무
              </Link>
            ) : (
              <span className="muted">다음 업무</span>
            )}
          </span>
        )}
      </div>

      {error && <div className="alert alert-red">{error}</div>}

      <div className="detail-grid">
        <div className="stack sheet-stack">
          <MailBanners key={task.id} data={data} act={act} onMerged={onMerged} onCompose={onCompose} />

          <nav className="jump" aria-label="이 업무 안에서 바로 가기">
            <a href="#todos">
              할 일 <span className="num">{doneCount}/{task.plan.length}</span>
            </a>
            <a href="#reply">{draft && draft.status !== 'sent' ? '답장 초안' : '답장'}</a>
            <a href="#mails">원본 메일 {data.emails.length}</a>
          </nav>

          <section className="card sheet-main">
            <div className="detail-title">
              <div>
                <h1>{task.title}</h1>
                <div className="muted small">업무 등록 {dateTime(task.created_at)}</div>
              </div>
              <div className="detail-title-right">{task.status !== 'COMPLETED' && <StatusBadge status={task.status} large />}</div>
            </div>

            <SummaryCard key={task.id} data={data} act={act} calendarLine={calendarLine} />
            {task.status === 'COMPLETED' && (
              <div className="complete-banner">
                <span className="stamp stamp-press" aria-hidden="true">
                  완료
                </span>
                <div className="complete-title">끝난 업무입니다</div>
              </div>
            )}
          </section>

          <TodoChecklist key={`todos-${task.id}`} task={task} act={act} />
          {draft && task.merged_into_id == null && (!closed || draft.status === 'unknown') && (
            <ReplyDraftCard key={`draft-${task.id}`} data={data} draft={draft} act={act} onRecompose={onCompose} />
          )}
          <MailActions
            key={`actions-${task.id}`}
            task={task}
            act={act}
            onCancelled={load}
            emphasizeReply={task.status !== 'WAITING' && task.plan.length > 0 && doneCount === task.plan.length}
            draft={draft}
            compose={compose}
            onCompose={onCompose}
          />

          {data.emails.length > 0 && (
            <details className="card mail-fold" id="mails">
              <summary>원본 메일 {data.emails.length}통</summary>
              <ul className="mail-thread">
                {data.emails.map((e) => (
                  <li key={e.id} className={`mail mail-${e.direction.toLowerCase()}`}>
                    <div className="mail-meta">
                      <span className={`tag ${e.direction === 'INBOUND' ? 'tag-blue' : 'tag-green'}`}>
                        {e.direction === 'INBOUND' ? '받은 메일' : '보낸 메일'}
                      </span>
                      <b>{e.subject}</b>
                      <span className="muted small nowrap">{dateTime(e.created_at)}</span>
                    </div>
                    <div className="muted small">
                      {e.sender} → {e.receiver}
                    </div>
                    <div className="mail-body">{e.content}</div>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>

        <aside className="stack sidebar">
          <section className="card timeline-card">
            <div className="section-head">
              <h2>처리 기록</h2>
              <span className="muted small num">{data.logs.length}건</span>
            </div>
            {data.logs.length === 0 ? (
              <p className="muted small">이 업무에서 한 일이 여기에 시간 순으로 남아요.</p>
            ) : (
              <Timeline logs={data.logs} />
            )}
          </section>
        </aside>
      </div>
    </div>
  )
}

function readOrder(): number[] {
  try {
    const v: unknown = JSON.parse(window.sessionStorage.getItem('followup.order') ?? '[]')
    return Array.isArray(v) ? v.filter((x): x is number => typeof x === 'number') : []
  } catch {
    return []
  }
}
