import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import ApprovalCard from '../components/ApprovalCard'
import ReplySimulator from '../components/ReplySimulator'
import StatusBadge from '../components/StatusBadge'
import Timeline from '../components/Timeline'
import { dateTime, STATUS_META, TOOL_LABELS } from '../format'
import type { TaskDetail } from '../types'

export default function TaskDetailPage() {
  const { id } = useParams()
  const [data, setData] = useState<TaskDetail | null>(null)
  const [error, setError] = useState('')
  const [retrying, setRetrying] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const load = useCallback(() => {
    // Named so the polling reschedule can reference itself without capturing `load` mid-initialization.
    async function poll() {
      clearTimeout(timer.current)
      try {
        const d = await api.get<TaskDetail>(`/tasks/${id}`)
        setData(d)
        setError('')
        timer.current = setTimeout(poll, d.active_run?.status === 'RUNNING' ? 1200 : 4000)
      } catch (e) {
        setError((e as Error).message)
        timer.current = setTimeout(poll, 6000)
      }
    }
    return poll()
  }, [id])

  useEffect(() => {
    load()
    return () => clearTimeout(timer.current)
  }, [load])

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

  if (!data) {
    return error ? (
      <div className="alert alert-red">
        {error} <Link to="/">← 내 업무로</Link>
      </div>
    ) : (
      <p className="muted">불러오는 중…</p>
    )
  }

  const { task, active_run: run } = data
  const pending = data.approvals.find((a) => a.status === 'PENDING')
  const lastRun = data.runs[0]
  const failed = !run && lastRun && (lastRun.status === 'FAILED' || lastRun.status === 'STOPPED')
  const doneCount = task.plan.filter((p) => p.done).length
  const lastLog = data.logs[data.logs.length - 1]

  let live: string | undefined
  if (run?.status === 'RUNNING') {
    const lastTool = [...data.logs].reverse().find((l) => l.run_id === run.id && l.tool_name)
    live = `Agent 실행 중 · ${run.steps}/${data.max_steps} 단계${lastTool ? ` · 방금: ${TOOL_LABELS[lastTool.tool_name] ?? lastTool.tool_name}` : ' · 목표 분석 중'}`
  }

  return (
    <div className="stack-lg">
      <div className="crumbs">
        <Link to="/">← 내 업무</Link>
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
          <section className="card">
            <div className="detail-title">
              <div>
                <h1>{task.title}</h1>
                <div className="muted small">
                  생성 {dateTime(task.created_at)}
                  {data.customer && ` · 고객 ${data.customer.company_name} ${data.customer.contact_name}`}
                  {task.origin === 'agent' && ' · Agent가 생성한 후속 업무'}
                </div>
              </div>
              <StatusBadge status={task.status} large />
            </div>

            {task.status === 'COMPLETED' && (
              <div className="complete-banner">
                <div className="complete-title">🟢 {task.title} 완료</div>
                {task.result_summary && <p>{task.result_summary}</p>}
              </div>
            )}

            <dl className="facts">
              <div>
                <dt>목표</dt>
                <dd>{task.goal}</dd>
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
          </section>

          {failed && (
            <div className={`alert ${lastRun.status === 'STOPPED' ? 'alert-amber' : 'alert-red'} alert-action`}>
              <div>
                <b>{lastRun.status === 'STOPPED' ? '⚠️ Agent 실행 중단' : '⚠️ Agent 오류'}</b>
                <div>{lastRun.error}</div>
              </div>
              <button className="btn btn-primary btn-sm" onClick={retry} disabled={retrying}>
                {retrying ? '재시작 중…' : 'Agent 재시도'}
              </button>
            </div>
          )}

          {pending && <ApprovalCard approval={pending} customer={data.customer} onDecided={load} />}

          {task.plan.length > 0 && (
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

          {(data.can_simulate_reply || task.status === 'WAITING') && task.agent_enabled && task.status !== 'COMPLETED' && (
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

          {data.emails.length > 0 && (
            <section className="card">
              <h2>이 업무의 메일</h2>
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
            </section>
          )}
        </div>

        <aside className="stack sidebar">
          <section className="card activity">
            <h2>Agent Activity</h2>
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
                <span className="big">✋</span>
                <div>
                  <b>사용자 승인을 기다리는 중</b>
                  <div className="muted small">승인하면 Agent가 중단된 지점부터 이어서 실행합니다.</div>
                </div>
              </div>
            )}
            {!run && (
              <div className="activity-state idle">
                <span className="big">{task.status === 'COMPLETED' ? '🏁' : task.status === 'WAITING' ? '⏳' : '💤'}</span>
                <div>
                  <b>
                    {task.status === 'COMPLETED'
                      ? '목표 달성 — Agent 종료'
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

          <section className="card">
            <div className="section-head">
              <h2>Agent Timeline</h2>
              <span className="muted small">{data.logs.length}개 기록</span>
            </div>
            {data.logs.length === 0 && !live ? <p className="muted small">아직 Agent 기록이 없습니다.</p> : <Timeline logs={data.logs} live={live} />}
          </section>
        </aside>
      </div>
    </div>
  )
}
