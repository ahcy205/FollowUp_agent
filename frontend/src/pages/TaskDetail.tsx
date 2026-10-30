import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../App'
import ApprovalCard from '../components/ApprovalCard'
import Icon from '../components/Icon'
import CompleteFollowupCard from '../components/CompleteFollowupCard'
import GoalEditor from '../components/GoalEditor'
import CancelTaskPanel from '../components/CancelTaskPanel'
import ReplySimulator from '../components/ReplySimulator'
import StatusBadge from '../components/StatusBadge'
import Timeline from '../components/Timeline'
import MailActions, { type ComposePurpose } from '../components/mail/MailActions'
import MailBanners from '../components/mail/MailBanners'
import ReplyDraftCard from '../components/mail/ReplyDraftCard'
import SummaryCard from '../components/mail/SummaryCard'
import TodoChecklist from '../components/mail/TodoChecklist'
import { dateTime, STATUS_META, tokens, TOOL_LABELS } from '../format'
import type { Run, TaskDetail, TokenUsage } from '../types'

export default function TaskDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const demo = useAuth().user?.demo_mode === true
  const [data, setData] = useState<TaskDetail | null>(null)
  const [error, setError] = useState('')
  const [retrying, setRetrying] = useState(false)
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

  const retry = async () => {
    setRetrying(true)
    try {
      await api.post(`/tasks/${id}/agent/retry`)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRetrying(false)
    }
  }

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
      <div className="alert alert-red">
        {error} <Link to="/tasks">← 전체 업무로</Link>
      </div>
    ) : (
      <div className="skeleton skeleton-detail" role="status" aria-label="불러오는 중">
        <div className="skeleton-row"><span /><span /><span /></div>
        <div className="skeleton-row"><span /><span /><span /></div>
      </div>
    )
  }

  const { task, active_run: run } = data
  const pending = data.approvals.find((a) => a.status === 'PENDING')
  const lastRun = data.runs[0]
  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  const failed = !run && lastRun && (lastRun.status === 'FAILED' || lastRun.status === 'STOPPED') && !closed
  const doneCount = task.plan.filter((p) => p.done).length
  const shownRun: Run | undefined = run ?? lastRun
  const taskUsage = data.runs.reduce<TokenUsage>(
    (acc, r) => ({
      total: (acc.total ?? 0) + (r.usage?.total ?? 0),
      turns: (acc.turns ?? 0) + (r.usage?.turns ?? 0),
    }),
    { total: 0, turns: 0 },
  )
  const lastLog = data.logs[data.logs.length - 1]
  const isMail = task.origin === 'mail'
  const draft = data.mail?.draft ?? null

  const calendarLine = task.calendar_state !== 'none' && (
    <div className="calendar-line small">
      {task.calendar_state === 'synced' && (
        <>
          <span>캘린더에 등록됨</span>
          {task.calendar_event_url.startsWith("https://") && (
            <a href={task.calendar_event_url} target="_blank" rel="noopener noreferrer">
              캘린더에서 열기
            </a>
          )}
        </>
      )}
      {task.calendar_state === 'pending' && <span className="muted">캘린더 반영 대기 중</span>}
      {task.calendar_state === 'deleted_by_user' && (
        <span className="muted">캘린더에서 삭제된 일정입니다 (다시 만들지 않음)</span>
      )}
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

  const mailThread = (
    <ul className="mail-thread">
      {data.emails.map((e) => (
        <li key={e.id} className={`mail mail-${e.direction.toLowerCase()}`}>
          <div className="mail-meta">
            <span className={`tag ${e.direction === 'INBOUND' ? 'tag-blue' : e.direction === 'DRAFT' ? '' : 'tag-green'}`}>
              {e.direction === 'INBOUND' ? '받은 메일' : e.direction === 'DRAFT' ? '초안' : '보낸 메일'}
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
  )

  let live: string | undefined
  if (run?.status === 'RUNNING') {
    const lastTool = [...data.logs].reverse().find((l) => l.run_id === run.id && l.tool_name)
    live = `Agent 실행 중 · ${run.steps}/${data.max_steps} 단계${lastTool ? ` · 방금: ${TOOL_LABELS[lastTool.tool_name] ?? lastTool.tool_name}` : ' · 목표 분석 중'}`
  }

  return (
    <div className="stack-lg">
      <div className="crumbs">
        {task.origin === 'mail' ? (
          <Link to="/">
            <Icon name="back" size={16} /> 받은 업무
          </Link>
        ) : (
          <Link to="/tasks">
            <Icon name="back" size={16} /> 전체 업무
          </Link>
        )}
        {data.parent && (
          <>
            <span>/</span>
            <Link to={`/tasks/${data.parent.id}`}>{data.parent.title}</Link>
          </>
        )}
      </div>

      {error && <div className="alert alert-red">{error}</div>}

      <div className="detail-grid">
        <div className="stack">
          {isMail && <MailBanners key={task.id} data={data} act={act} onMerged={onMerged} onCompose={onCompose} />}

          <section className="card sheet-main">
            <div className="detail-title">
              <div>
                <h1>{task.title}</h1>
                <div className="muted small">
                  {isMail ? '업무 등록' : '생성'} {dateTime(task.created_at)}
                  {data.customer && ` · 고객 ${data.customer.company_name} ${data.customer.contact_name}`}
                  {task.origin === 'agent' && ' · Agent가 생성한 후속 업무'}
                </div>
              </div>
              <div className="detail-title-right">
                <StatusBadge status={task.status} large />
                {!closed && !isMail && <CancelTaskPanel taskId={task.id} onCancelled={load} />}
              </div>
            </div>

            {task.status === 'COMPLETED' && (
              <div className="complete-banner">
                <span className="stamp stamp-press" aria-hidden="true">
                  완료
                </span>
                <div>
                  <div className="complete-title">끝난 업무입니다</div>
                  {task.result_summary && <p>{task.result_summary}</p>}
                </div>
              </div>
            )}

            {isMail ? (
              <SummaryCard key={task.id} data={data} act={act} calendarLine={calendarLine} />
            ) : (
              <dl className="facts">
                <div>
                  <dt>목표</dt>
                  <dd>
                    <GoalEditor taskId={task.id} goal={task.goal} editable={!closed && !run} onSaved={load} />
                  </dd>
                </div>
                <div>
                  <dt>현재 상태</dt>
                  <dd>
                    <b className={`text-${STATUS_META[task.status].tone}`}>{STATUS_META[task.status].label}</b>
                    {task.waiting_for && ` — ${task.waiting_for}`}
                  </dd>
                </div>
                {task.current_step && (
                  <div>
                    <dt>현재 단계</dt>
                    <dd>{task.current_step}</dd>
                  </div>
                )}
                {task.next_action && task.status !== 'COMPLETED' && (
                  <div>
                    <dt>다음 행동</dt>
                    <dd>{task.next_action}</dd>
                  </div>
                )}
              </dl>
            )}
          </section>

          {failed && (
            <div className={`alert ${lastRun.status === 'STOPPED' ? 'alert-amber' : 'alert-red'} alert-action`}>
              <div>
                <b>{lastRun.status === 'STOPPED' ? 'Agent 실행이 멈췄습니다' : 'Agent 오류가 났습니다'}</b>
                <div>{lastRun.error}</div>
              </div>
              <button className="btn btn-primary btn-sm" onClick={retry} disabled={retrying}>
                {retrying ? '재시작 중…' : 'Agent 재시도'}
              </button>
            </div>
          )}

          {pending && <ApprovalCard approval={pending} customer={data.customer} onDecided={load} />}

          {task.origin === 'agent' && !task.agent_enabled && task.status !== 'COMPLETED' && (
            <CompleteFollowupCard taskId={task.id} onDone={load} />
          )}

          {isMail && (
            <>
              <TodoChecklist key={`todos-${task.id}`} task={task} act={act} />
              {draft && task.merged_into_id == null && (!closed || draft.status === 'unknown') && (
                <ReplyDraftCard key={`draft-${task.id}`} data={data} draft={draft} act={act} onRecompose={onCompose} />
              )}
              <MailActions
                key={`actions-${task.id}`}
                task={task}
                act={act}
                onCancelled={load}
                emphasizeReply={task.plan.length > 0 && doneCount === task.plan.length}
                draft={draft}
                compose={compose}
                onCompose={onCompose}
              />
            </>
          )}

          {!isMail && task.plan.length > 0 && (
            <section className="card">
              <div className="section-head">
                <h2>진행 체크리스트</h2>
                <span className="muted small">
                  {doneCount}/{task.plan.length} 하위 목표 달성
                </span>
              </div>
              <div className="progress">
                <div style={{ width: `${(doneCount / task.plan.length) * 100}%` }} />
              </div>
              <ul className="checklist">
                {task.plan.map((p, i) => {
                  const current = !p.done && task.plan.slice(0, i).every((q) => q.done)
                  return (
                    <li key={p.id} className={p.done ? 'done' : current ? 'current' : ''}>
                      <span className="check">{p.done ? '✓' : current ? '→' : ''}</span>
                      <div>
                        <div>{p.title}</div>
                        {p.evidence && <div className="muted small">{p.evidence}</div>}
                      </div>
                    </li>
                  )
                })}
              </ul>
            </section>
          )}

          {demo && (data.can_simulate_reply || task.status === 'WAITING') && task.agent_enabled && !closed && (
            <ReplySimulator taskId={task.id} enabled={data.can_simulate_reply} onSent={load} />
          )}

          {data.followups.length > 0 && (
            <section className="card">
              <h2>Agent가 만든 후속 업무</h2>
              <ul className="task-list">
                {data.followups.map((f) => (
                  <li key={f.id}>
                    <Link to={`/tasks/${f.id}`} className="task-row">
                      <div className="task-main">
                        <div className="task-title">
                          {f.priority === 'HIGH' && <span className="tag tag-red">긴급</span>}
                          {f.title}
                        </div>
                        <div className="task-sub muted">{f.goal}</div>
                      </div>
                      <StatusBadge status={f.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {data.emails.length > 0 &&
            (isMail ? (
              <details className="card mail-fold">
                <summary>원본 메일 {data.emails.length}통</summary>
                {mailThread}
              </details>
            ) : (
              <section className="card">
                <h2>이 업무의 메일</h2>
                {mailThread}
              </section>
            ))}
        </div>

        <aside className="stack sidebar">
          {!isMail && (
          <section className="card activity">
            <h2>Agent 실행</h2>
            {run?.status === 'RUNNING' && (
              <div className="activity-state running">
                <span className="spinner" />
                <div>
                  <b>Agent가 작업 중입니다</b>
                  <div className="muted small">
                    단계 {run.steps}/{data.max_steps} · {lastLog?.reason ? lastLog.reason.slice(0, 80) : '다음 행동을 결정하는 중'}
                  </div>
                </div>
              </div>
            )}
            {run?.status === 'AWAITING_APPROVAL' && (
              <div className="activity-state waiting">
                <div>
                  <b>사용자 승인을 기다리는 중</b>
                  <div className="muted small">승인하면 Agent가 중단된 지점부터 이어서 실행합니다.</div>
                </div>
              </div>
            )}
            {!run && (
              <div className="activity-state idle">
                <div>
                  <b>
                    {task.status === 'COMPLETED'
                      ? '목표 달성 — Agent 종료'
                      : task.status === 'CANCELLED'
                        ? '사용자가 취소한 업무'
                        : task.status === 'WAITING'
                        ? `${task.waiting_for || '이벤트'} 대기 중`
                        : !task.agent_enabled
                          ? '사용자가 처리할 업무'
                          : 'Agent 대기 중'}
                  </b>
                  <div className="muted small">
                    {task.status === 'WAITING'
                      ? '새 이벤트가 도착하면 Agent가 자동으로 다시 판단합니다.'
                      : lastRun?.final_message
                        ? lastRun.final_message.slice(0, 160)
                        : '업무 상태가 저장되어 있습니다.'}
                  </div>
                </div>
              </div>
            )}
          </section>
          )}

          {shownRun && (shownRun.usage?.turns ?? 0) > 0 && (
            <section className="card usage">
              <div className="section-head">
                <h2>토큰 사용량</h2>
                <span className="muted small">Agent 턴 기준</span>
              </div>
              <div className="usage-main">
                <b>{tokens(shownRun.usage.total)}</b>
                <span className="muted small">{run ? '이번 실행' : '마지막 실행'} · LLM 호출 {shownRun.usage.turns}회</span>
              </div>
              <div className="usage-parts muted small">
                입력 {tokens(shownRun.usage.input)} · 출력 {tokens(shownRun.usage.output)}
                {(shownRun.usage.cache_read ?? 0) > 0 && ` · 캐시 ${tokens(shownRun.usage.cache_read)}`}
              </div>
              {data.runs.length > 1 && (
                <div className="usage-total muted small">
                  이 업무 누적 <b>{tokens(taskUsage.total)}</b> 토큰 · LLM 호출 {taskUsage.turns}회 · 실행 {data.runs.length}회
                </div>
              )}
            </section>
          )}

          <section className="card timeline-card">
            <div className="section-head">
              <h2>{isMail ? '처리 기록' : 'Agent 기록'}</h2>
              <span className="muted small num">{data.logs.length}건</span>
            </div>
            {data.logs.length === 0 && !live ? (
              <p className="muted small">{isMail ? '이 업무에서 한 일이 여기에 시간 순으로 남습니다.' : '아직 Agent 기록이 없습니다.'}</p>
            ) : (
              <Timeline logs={data.logs} live={live} />
            )}
          </section>
        </aside>
      </div>
    </div>
  )
}
