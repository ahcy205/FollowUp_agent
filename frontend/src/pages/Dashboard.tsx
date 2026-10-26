import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge'
import { relative, STATUS_META } from '../format'
import type { Dashboard as DashboardData, Task, TaskStatus } from '../types'

const EXAMPLES = [
  'A회사에 견적서를 보내고 고객 답변이 오면 내용을 확인해서 내가 해야 할 일을 정리해줘.',
  'E테크에 서보 드라이버 견적서를 보내고, 답변이 오면 발주 여부를 정리해줘.',
]

const CARD_ORDER: TaskStatus[] = ['COMPLETED', 'WAITING', 'IN_PROGRESS', 'ACTION_REQUIRED']

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState('')
  const [goal, setGoal] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [filter, setFilter] = useState<TaskStatus | 'ALL'>('ALL')
  const navigate = useNavigate()

  const load = useCallback(() => {
    api
      .get<DashboardData>('/dashboard')
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [load])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (goal.trim().length < 5) return
    setSubmitting(true)
    setError('')
    try {
      const task = await api.post<Task>('/tasks', { goal })
      navigate(`/tasks/${task.id}`)
    } catch (err) {
      setError((err as Error).message)
      setSubmitting(false)
    }
  }

  const tasks = (data?.tasks ?? []).filter((t) => filter === 'ALL' || t.status === filter)
  const roots = tasks.filter((t) => t.origin !== 'agent')
  const followups = tasks.filter((t) => t.origin === 'agent')

  return (
    <div className="stack-lg">
      <section className="card goal-card">
        <div className="goal-head">
          <div>
            <h2>새 업무 목표</h2>
            <p className="muted">하나의 목표를 자연어로 입력하면 Agent가 필요한 도구를 골라 끝까지 후속조치합니다.</p>
          </div>
          {data && (
            <span className={`badge ${data.agent_configured ? 'badge-green' : 'badge-red'}`}>
              {data.agent_configured ? `LLM 연결됨 · ${data.model}` : 'LLM_API_KEY 미설정'}
            </span>
          )}
        </div>
        <form onSubmit={submit} className="goal-form">
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="예) A회사에 견적서를 보내고 고객 답변이 오면 내용을 확인해서 내가 해야 할 일을 정리해줘."
            rows={3}
            maxLength={1000}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(e)
            }}
          />
          <div className="goal-actions">
            <div className="chips">
              {EXAMPLES.map((ex) => (
                <button type="button" key={ex} className="chip" onClick={() => setGoal(ex)}>
                  {ex.length > 34 ? ex.slice(0, 34) + '…' : ex}
                </button>
              ))}
            </div>
            <button className="btn btn-primary" disabled={submitting || goal.trim().length < 5}>
              {submitting ? 'Agent 시작 중…' : 'Agent에게 맡기기 →'}
            </button>
          </div>
        </form>
        {error && <div className="alert alert-red">{error}</div>}
      </section>

      {data && data.pending_approvals > 0 && (
        <div className="alert alert-amber">
          ✋ 승인을 기다리는 Agent 행동이 <b>{data.pending_approvals}건</b> 있습니다. 업무를 열어 확인하세요.
        </div>
      )}

      <section className="stat-grid">
        {CARD_ORDER.map((s) => (
          <button key={s} className={`stat stat-${STATUS_META[s].tone} ${filter === s ? 'selected' : ''}`}
            onClick={() => setFilter(filter === s ? 'ALL' : s)}>
            <span className="stat-label">
              <span className={`dot dot-${STATUS_META[s].tone}`} /> {STATUS_META[s].short}
            </span>
            <span className="stat-value">{data?.counts[s] ?? '–'}</span>
          </button>
        ))}
      </section>

      <section className="card">
        <div className="section-head">
          <h2>전체 업무 {filter !== 'ALL' && <span className="muted">· {STATUS_META[filter].label}</span>}</h2>
          {filter !== 'ALL' && (
            <button className="btn btn-link btn-sm" onClick={() => setFilter('ALL')}>
              전체 보기
            </button>
          )}
        </div>
        {!data && !error && <p className="muted">불러오는 중…</p>}
        {data && roots.length === 0 && followups.length === 0 && <p className="muted">해당 상태의 업무가 없습니다.</p>}
        <ul className="task-list">
          {roots.map((t) => (
            <TaskRow key={t.id} task={t} />
          ))}
        </ul>
        {followups.length > 0 && (
          <>
            <h3 className="subhead">Agent가 만든 후속 업무</h3>
            <ul className="task-list">
              {followups.map((t) => (
                <TaskRow key={t.id} task={t} />
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  )
}

function TaskRow({ task }: { task: Task }) {
  const done = task.plan.filter((p) => p.done).length
  return (
    <li>
      <Link to={`/tasks/${task.id}`} className="task-row">
        <div className="task-main">
          <div className="task-title">
            {task.origin === 'agent' && <span className="tag">후속</span>}
            {task.priority === 'HIGH' && <span className="tag tag-red">긴급</span>}
            {task.title}
          </div>
          <div className="task-sub muted">
            {task.status === 'WAITING' && task.waiting_for ? `⏳ ${task.waiting_for} 대기 · ` : ''}
            {task.status === 'COMPLETED' ? task.current_step || task.result_summary : task.next_action || task.current_step}
          </div>
        </div>
        {task.plan.length > 0 && (
          <div className="mini-progress" title={`${done}/${task.plan.length} 하위 목표`}>
            <div style={{ width: `${(done / task.plan.length) * 100}%` }} />
          </div>
        )}
        <StatusBadge status={task.status} />
        <span className="muted small nowrap">{relative(task.updated_at)}</span>
      </Link>
    </li>
  )
}
