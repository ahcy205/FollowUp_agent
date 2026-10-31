import { useEffect, useRef, useState, type FormEvent } from 'react'
import { shortDate } from '../../format'
import type { MailAct, PlanStep, Task } from '../../types'

const UNDO_MS = 5000

// 되돌리기로 보내는 항목은 서버 TodoItemIn 필드만 담는다(extra 필드는 422).
function restorable(p: PlanStep) {
  const item: Record<string, unknown> = {
    id: p.id,
    title: p.title,
    done: p.done,
    evidence: p.evidence ?? '',
    due_at: p.due_at ?? null,
    source: p.source === 'agent' ? 'agent' : 'user',
  }
  if (p.due_note) item.due_note = p.due_note
  if (typeof p.merged_from === 'number') item.merged_from = p.merged_from
  return item
}

/** 할 일 체크리스트 (설계 3.3절). 화면 상태(메모 편집 · 되돌리기)는 data가 바뀌어도(SSE) 유지된다. */
export default function TodoChecklist({ task, act }: { task: Task; act: MailAct }) {
  const closed = task.status === 'COMPLETED' || task.status === 'CANCELLED'
  const plan = task.plan
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [memoOpen, setMemoOpen] = useState<number | null>(null)
  const [memo, setMemo] = useState('')
  const [title, setTitle] = useState('')
  const [undo, setUndo] = useState<{ item: PlanStep; index: number } | null>(null)
  const undoTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => clearTimeout(undoTimer.current), [])

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

  const remove = async (item: PlanStep) => {
    const index = plan.findIndex((p) => p.id === item.id)
    if (index < 0) return
    if (await run(`del-${item.id}`, () => act('/todos', { op: 'delete', id: item.id }))) {
      if (memoOpen === item.id) setMemoOpen(null)
      clearTimeout(undoTimer.current)
      setUndo({ item, index })
      undoTimer.current = setTimeout(() => setUndo(null), UNDO_MS)
    }
  }

  const restore = async () => {
    if (!undo) return
    clearTimeout(undoTimer.current)
    const { item, index } = undo
    await run('restore', () => act('/todos', { op: 'restore', item: restorable(item), index }))
    setUndo(null)
  }

  const saveMemo = async (id: number) => {
    if (await run(`memo-${id}`, () => act('/todos', { op: 'memo', id, memo }))) setMemoOpen(null)
  }

  const add = async (e: FormEvent) => {
    e.preventDefault()
    const t = title.trim()
    if (!t) return
    if (await run('add', () => act('/todos', { op: 'add', title: t }))) setTitle('')
  }

  const doneCount = plan.filter((p) => p.done).length
  const allDone = plan.length > 0 && doneCount === plan.length

  return (
    <section className="card" id="todos">
      <div className="section-head">
        <h2>할 일</h2>
        {plan.length > 0 && (
          <span className="muted small">
            {doneCount}/{plan.length} 완료
          </span>
        )}
      </div>
      {plan.length > 0 && (
        <div className="progress">
          <div style={{ width: `${(doneCount / plan.length) * 100}%` }} />
        </div>
      )}

      {plan.length === 0 && !undo && <p className="muted small">할 일이 없습니다.</p>}

      <ul className="todo-list">
        {plan.map((p) => (
          <li key={p.id} className={p.done ? 'done' : ''}>
            <div className="todo-row">
              <input
                type="checkbox"
                className="todo-check"
                checked={p.done}
                disabled={closed || !!busy}
                aria-label={`${p.title} ${p.done ? '완료 취소' : '완료'}`}
                onChange={(e) => run(`toggle-${p.id}`, () => act('/todos', { op: 'toggle', id: p.id, done: e.target.checked }))}
              />
              <div className="todo-main">
                <div className="todo-title">{p.title}</div>
                {(p.due_at || p.due_note) && (
                  <div className="muted small">{p.due_at ? `${shortDate(p.due_at)}까지` : `기한: ${p.due_note}`}</div>
                )}
                {p.evidence && memoOpen !== p.id && <div className="todo-memo small">메모: {p.evidence}</div>}
              </div>
              {!closed && (
                <div className="todo-buttons">
                  <button
                    type="button"
                    className="btn btn-link btn-sm"
                    disabled={!!busy}
                    aria-label={`${p.title} 메모`}
                    aria-expanded={memoOpen === p.id}
                    onClick={() => {
                      if (memoOpen === p.id) {
                        setMemoOpen(null)
                      } else {
                        setMemo(p.evidence)
                        setMemoOpen(p.id)
                      }
                    }}
                  >
                    메모
                  </button>
                  <button type="button" className="btn btn-link btn-sm" disabled={!!busy} aria-label={`${p.title} 삭제`} onClick={() => remove(p)}>
                    {busy === `del-${p.id}` ? '삭제 중…' : '삭제'}
                  </button>
                </div>
              )}
            </div>
            {memoOpen === p.id && !closed && (
              <div className="todo-memo-edit">
                <textarea rows={2} maxLength={1000} value={memo} aria-label={`${p.title} 처리 메모`} placeholder="처리 메모 (예: 단가 12,000원 적용)" onChange={(e) => setMemo(e.target.value)} />
                <div className="inline-actions">
                  <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => saveMemo(p.id)}>
                    {busy === `memo-${p.id}` ? '저장 중…' : '메모 저장'}
                  </button>
                  <button type="button" className="btn btn-link btn-sm" disabled={!!busy} onClick={() => setMemoOpen(null)}>
                    취소
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>

      {undo && (
        <div className="todo-undo small" role="status">
          <span>삭제했습니다</span>
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={restore}>
            {busy === 'restore' ? '되돌리는 중…' : '되돌리기'}
          </button>
        </div>
      )}

      {error && <div className="alert alert-red todo-error">{error}</div>}

      {allDone && !closed && task.status !== 'WAITING' && (
        <div className="alert alert-green todo-all-done">
          할 일을 모두 끝냈습니다. 이제 답장을 보낼 차례입니다 — 아래 <b>답장 초안 만들기</b>를 눌러 주세요.
        </div>
      )}

      {!closed && (
        <form className="todo-add" onSubmit={add}>
          <input value={title} maxLength={300} placeholder="할 일 추가" aria-label="새 할 일" onChange={(e) => setTitle(e.target.value)} />
          <button type="submit" className="btn btn-ghost btn-sm" disabled={!!busy || !title.trim()}>
            {busy === 'add' ? '추가 중…' : '추가'}
          </button>
        </form>
      )}
    </section>
  )
}
