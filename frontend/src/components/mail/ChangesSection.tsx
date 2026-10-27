import { useState } from 'react'
import { parseDate, shortDate } from '../../format'
import type { ChangeEntry, MailAct } from '../../types'

const LABELS: Record<ChangeEntry['field'], string> = {
  title: '제목',
  summary: '요약',
  requests: '요청',
  deadline: '기한',
  todo_add: '할 일',
  todo_update: '할 일',
}

function line(c: ChangeEntry) {
  if (c.status === 'question') return `확인 필요 — ${c.after || c.before}`
  if (c.status === 'suggested') return `상대가 기한을 ${c.after}로 바꿨습니다`
  if (c.field === 'todo_add') return `할 일 추가: ${c.after}`
  if (c.field === 'requests' && !c.before) return `요청 추가: ${c.after}`
  return `${LABELS[c.field]}: ${c.before || '없음'} → ${c.after}`
}

/** 받은 시각(ms). 없거나 잘못됐으면 0 — 가장 오래된 것으로 보고 화면에 시각을 쓰지 않는다. */
function receivedMs(e: ChangeEntry) {
  const t = typeof e.received_at === 'string' && e.received_at ? parseDate(e.received_at).getTime() : NaN
  return Number.isNaN(t) ? 0 : t
}

/** 메일별로 묶는다. 가장 최근에 받은 메일의 묶음이 맨 앞이다. */
function byMail(entries: ChangeEntry[]): ChangeEntry[][] {
  const groups = new Map<number, ChangeEntry[]>()
  for (const e of entries) groups.set(e.email_id, [...(groups.get(e.email_id) ?? []), e])
  const at = (g: ChangeEntry[]) => Math.max(...g.map(receivedMs))
  return [...groups.values()].sort((a, b) => at(b) - at(a) || b[0].id - a[0].id)
}

/** 요약 카드 안 "상대 답장으로 바뀐 내용" (설계 6.4). 가장 최근 메일의 변경만 펼치고 이전 것은 접는다. */
export default function ChangesSection({ log, act, closed }: { log: ChangeEntry[]; act: MailAct; closed: boolean }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const entries = log.filter((e) => e && typeof e.id === 'number')
  if (entries.length === 0) return null

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

  const [latest, ...older] = byMail(entries)
  const olderCount = older.reduce((n, g) => n + g.length, 0)

  const item = (c: ChangeEntry) => (
    <li key={c.id} className={`change-item change-${c.status}`}>
      <div className="change-line">
        <span className="change-text">{line(c)}</span>
        {c.status === 'undone' && <span className="tag">되돌림</span>}
        {!closed && c.status === 'applied' && (
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run(`undo-${c.id}`, () => act(`/changes/${c.id}/undo`))}>
            {busy === `undo-${c.id}` ? '되돌리는 중…' : '되돌리기'}
          </button>
        )}
        {!closed && c.status === 'suggested' && (
          <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => run(`apply-${c.id}`, () => act(`/changes/${c.id}/apply`))}>
            {busy === `apply-${c.id}` ? '반영하는 중…' : '반영하기'}
          </button>
        )}
      </div>
      {c.evidence && <div className="change-evidence small">"{c.evidence}"</div>}
      <div className="muted small">
        {c.sender || '보낸 사람 모름'}
        {receivedMs(c) > 0 && ` · ${shortDate(c.received_at)} 받음`}
      </div>
    </li>
  )

  return (
    <div className="summary-block changes">
      <b className="small">상대 답장으로 바뀐 내용</b>
      <ul className="change-list">{latest.map(item)}</ul>
      {olderCount > 0 && (
        <details className="changes-older">
          <summary className="small">이전 변경 {olderCount}건</summary>
          <ul className="change-list">{older.flat().map(item)}</ul>
        </details>
      )}
      {error && <div className="alert alert-red">{error}</div>}
    </div>
  )
}
