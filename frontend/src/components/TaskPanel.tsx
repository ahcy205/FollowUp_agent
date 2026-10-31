import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { dateTime, shortDate } from '../format'
import type { TaskDetail } from '../types'
import Icon from './Icon'
import StatusBadge from './StatusBadge'
import Timeline from './Timeline'
import MailActions, { type ComposePurpose } from './mail/MailActions'
import MailBanners from './mail/MailBanners'
import ReplyDraftCard from './mail/ReplyDraftCard'
import SummaryCard from './mail/SummaryCard'
import TodoChecklist from './mail/TodoChecklist'

/**
 * 업무 상세 패널. 받은 업무 · 후속 관리의 오른쪽(휴대폰은 전체 화면)에 열린다.
 * 서버가 SSE로 상세 전체를 보내고, 동작 응답(TaskDetail)으로도 화면을 바꾼다.
 */
export default function TaskPanel({
  id,
  onClose,
  onOpen,
  onPrev,
  onNext,
  position,
}: {
  id: string
  onClose: () => void
  onOpen: (taskId: number) => void
  onPrev?: () => void
  onNext?: () => void
  position?: string
}) {
  const [data, setData] = useState<TaskDetail | null>(null)
  const [error, setError] = useState('')
  const [calRetrying, setCalRetrying] = useState(false)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  // 답장 초안 입력칸. 업무를 옮기면 닫힌 것으로 본다.
  const [composeState, setComposeState] = useState<{ taskId: string; purpose: ComposePurpose } | null>(null)
  const compose = composeState && composeState.taskId === id ? composeState.purpose : null
  const onCompose = useCallback((purpose: ComposePurpose | null) => setComposeState(purpose ? { taskId: id, purpose } : null), [id])
  const seenFor = useRef<number | null>(null)

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

  // 합치기 응답은 합친 대상의 상세다. 그 화면을 먼저 보여주고 그 업무를 연다.
  const onMerged = useCallback(
    (target: TaskDetail) => {
      setData(target)
      onOpen(target.task.id)
    },
    [onOpen],
  )

  // 실시간 갱신(SSE): 연결 즉시 전체 상세, 이후 바뀔 때마다
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
        // 네트워크 끊김은 브라우저가 다시 붙는다. HTTP 오류(세션 만료 · 404)는 스트림을 닫으므로 한 번 불러오고 다시 붙는다.
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
        seenFor.current = null
      })
  }, [data, id])

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

  const head = (
    <div className="panel-bar">
      <button type="button" className="icon-btn" onClick={onClose} aria-label="상세 닫기" title="닫기 (Esc)">
        <Icon name="close" size={18} />
      </button>
      <div className="panel-nav">
        {position && <span className="muted small num">{position}</span>}
        <button type="button" className="icon-btn" onClick={onPrev} disabled={!onPrev} aria-label="이전 업무" title="이전 업무 (k)">
          <Icon name="up" size={18} />
        </button>
        <button type="button" className="icon-btn" onClick={onNext} disabled={!onNext} aria-label="다음 업무" title="다음 업무 (j)">
          <Icon name="down" size={18} />
        </button>
      </div>
    </div>
  )

  if (!data || String(data.task.id) !== id) {
    return (
      <div className="panel">
        {head}
        {error ? (
          <div className="panel-body">
            <div className="alert alert-red">{error}</div>
          </div>
        ) : (
          <div className="panel-body">
            <div className="skeleton" role="status" aria-label="불러오는 중">
              <div className="skeleton-row"><span /><span /><span /></div>
              <div className="skeleton-row"><span /><span /><span /></div>
              <div className="skeleton-row"><span /><span /><span /></div>
            </div>
          </div>
        )}
      </div>
    )
  }

  const { task, mail } = data
  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  const doneCount = task.plan.filter((p) => p.done).length
  const draft = mail?.draft ?? null
  const cp = task.brief.counterpart
  const sender = cp?.name ? (cp.organization ? `${cp.name} · ${cp.organization}` : cp.name) : ''
  const source = data.emails.find((e) => e.id === task.brief.email_id) ?? data.emails.find((e) => e.direction === 'INBOUND')

  const calendarLine = task.calendar_state !== 'none' && (
    <div className="calendar-line small">
      <Icon name="calendar" size={15} />
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
      {task.calendar_state === 'deleted_by_user' && <span className="muted">캘린더에서 삭제된 일정 (다시 만들지 않음)</span>}
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
    <div className="panel">
      {head}
      <div className="panel-body">
        <header className="detail-head">
          <div className="detail-meta">
            <StatusBadge status={task.status} />
            {mail?.deadline_badge?.text && mail.deadline_badge.text !== '기한 없음' && (
              <span className={`due-chip num${mail.deadline_badge.late ? ' late' : ''}`}>{mail.deadline_badge.text}</span>
            )}
            <span className="muted small">업무 등록 {dateTime(task.created_at)}</span>
          </div>
          <h1>{task.title}</h1>
          <div className="detail-from">
            <span className="avatar" aria-hidden="true">
              {(cp?.name || source?.sender || '?').trim().charAt(0)}
            </span>
            <span>
              <b>{sender || source?.sender || '보낸 사람 모름'}</b>
              {source && <span className="muted"> · {shortDate(source.created_at)} 받음</span>}
            </span>
          </div>
        </header>

        {error && <div className="alert alert-red">{error}</div>}

        <MailBanners key={task.id} data={data} act={act} onMerged={onMerged} onCompose={onCompose} />

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

        {task.status === 'COMPLETED' && (
          <div className="done-note">
            <Icon name="check" size={18} />
            <span>끝난 업무입니다. 새 메일이 오면 다시 열립니다.</span>
          </div>
        )}

        <section className="card" aria-labelledby={`sum-${task.id}`}>
          <div className="card-head">
            <h2 id={`sum-${task.id}`}>요약</h2>
            <span className="ai-tag" title="AI가 메일을 읽고 정리한 내용">AI 정리</span>
          </div>
          <SummaryCard key={task.id} data={data} act={act} calendarLine={calendarLine} />
        </section>

        <TodoChecklist key={`todos-${task.id}`} task={task} act={act} />

        {draft && task.merged_into_id == null && (!closed || draft.status === 'unknown') && (
          <ReplyDraftCard key={`draft-${task.id}`} data={data} draft={draft} act={act} onRecompose={onCompose} />
        )}

        {data.emails.length > 0 && (
          <details className="card fold" id="mails">
            <summary>
              <span>원본 메일</span>
              <span className="muted num">{data.emails.length}통</span>
            </summary>
            <ul className="mail-thread">
              {data.emails.map((e) => (
                <li key={e.id} className={`mail mail-${e.direction.toLowerCase()}`}>
                  <div className="mail-meta">
                    <span className={`pill ${e.direction === 'INBOUND' ? 'pill-blue' : 'pill-green'}`}>{e.direction === 'INBOUND' ? '받은 메일' : '보낸 메일'}</span>
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

        <details className="card fold">
          <summary>
            <span>처리 기록</span>
            <span className="muted num">{data.logs.length}건</span>
          </summary>
          <div className="fold-body">
            {data.logs.length === 0 ? <p className="muted small">이 업무에서 한 일이 여기에 시간 순으로 남아요.</p> : <Timeline logs={data.logs} />}
          </div>
        </details>
      </div>
    </div>
  )
}
