import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import Icon from '../components/Icon'
import InboxCard from '../components/InboxCard'
import { openCards } from '../followup'
import TaskPanel from '../components/TaskPanel'
import { dateTime, relative } from '../format'
import type { GoogleStatus, Inbox as InboxData, InboxCard as Card } from '../types'

type Tab = 'todo' | 'sent' | 'reference' | 'done'
type Mode = 'inbox' | 'followup'

const TABS: { key: Tab; label: string }[] = [
  { key: 'todo', label: '처리할 일' },
  { key: 'sent', label: '답 기다림' },
  { key: 'reference', label: '참고용' },
  { key: 'done', label: '완료' },
]

const BUCKETS: { key: 'now' | 'week' | 'later'; label: string }[] = [
  { key: 'now', label: '지금 처리' },
  { key: 'week', label: '이번 주' },
  { key: 'later', label: '나중에' },
]

const EMPTY: Record<Tab, { title: string; body: string }> = {
  todo: { title: '지금 처리할 업무가 없어요', body: '새 업무 메일이 오면 기한과 중요도 순으로 여기에 올라옵니다.' },
  sent: { title: '답을 기다리는 업무가 없어요', body: '답장을 보내면 여기로 옮겨지고, 영업일 3일 동안 답이 없으면 다시 알려 드려요.' },
  reference: { title: '참고용 업무가 없어요', body: '답장이 필요 없는 공지 · 안내 메일이 여기에 모입니다.' },
  done: { title: '완료한 업무가 없어요', body: '완료하거나 답장 없이 닫은 업무가 여기에 남습니다.' },
}

const REFRESH_MS = 30_000

type Group = { key: string; label: string; tone?: 'red' | 'amber' | 'blue'; hint?: string; cards: Card[] }

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

function matches(card: Card, q: string) {
  if (!q) return true
  const hay = `${card.sender_name} ${card.sender_org} ${card.summary} ${card.title}`.toLowerCase()
  return q
    .toLowerCase()
    .split(/\s+/)
    .every((w) => hay.includes(w))
}

/** 후속 관리: 무응답 · 내용 바뀜 · 답장 안 함 · 답 기다림 (받은 업무 응답에서 고른다) */
function followupGroups(d: InboxData): Group[] {
  const all = openCards(d)
  const seen = new Set<number>()
  const take = (pred: (c: Card) => boolean) =>
    all.filter((c) => {
      if (seen.has(c.task_id) || !pred(c)) return false
      seen.add(c.task_id)
      return true
    })
  return [
    { key: 'no-reply', label: '답이 없어요', tone: 'red', hint: '보낸 지 영업일 3일째', cards: take((c) => c.no_reply) },
    { key: 'changed', label: '상대가 내용을 바꿨어요', tone: 'blue', hint: '전후 비교 · 근거 · 되돌리기', cards: take((c) => c.changed_unseen) },
    { key: 'nudge', label: '아직 답장하지 않았어요', tone: 'amber', hint: '받은 지 며칠 지난 업무', cards: take((c) => c.nudge_days != null) },
    { key: 'waiting', label: '답 기다리는 중', hint: '답장을 보낸 업무', cards: d.tabs.sent.filter((c) => !seen.has(c.task_id)) },
  ]
}

export default function Inbox({ mode }: { mode: Mode }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const base = mode === 'followup' ? '/followup' : '/inbox'
  const [data, setData] = useState<InboxData | null>(null)
  const [google, setGoogle] = useState<GoogleStatus | null>(null)
  const [error, setError] = useState('')
  const [params] = useSearchParams()
  const [tab, setTab] = useState<Tab>(() => TABS.find((t) => t.key === params.get('tab'))?.key ?? 'todo')
  const [query, setQuery] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const [help, setHelp] = useState(false)

  const load = useCallback(() => {
    api
      .get<InboxData>('/inbox')
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e: Error) => setError(e.message))
    api
      .get<GoogleStatus>('/google/status')
      .then(setGoogle)
      .catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const t = window.setInterval(load, REFRESH_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(t)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  // 상세에서 한 동작(상태 · 탭 이동)을 목록에도 곧 반영한다
  useEffect(() => {
    if (!id) return
    const t = window.setTimeout(load, 1500)
    return () => window.clearTimeout(t)
  }, [id, load])

  const [tabFor, setTabFor] = useState<string | null>(null)
  if (mode === 'inbox' && id && data && tabFor !== id) {
    setTabFor(id)
    const n = Number(id)
    const has = (k: Tab) => (k === 'todo' ? [...data.tabs.todo.now, ...data.tabs.todo.week, ...data.tabs.todo.later] : data.tabs[k]).some((c) => c.task_id === n)
    const owner = has(tab) ? tab : TABS.find((t) => has(t.key))?.key
    if (owner && owner !== tab) setTab(owner)
  }

  const groups: Group[] = useMemo(() => {
    if (!data) return []
    if (mode === 'followup') return followupGroups(data)
    if (tab === 'todo') return BUCKETS.map((b) => ({ key: b.key, label: b.label, tone: b.key === 'now' ? ('red' as const) : undefined, cards: data.tabs.todo[b.key] }))
    return [{ key: tab, label: '', cards: data.tabs[tab] }]
  }, [data, mode, tab])

  const q = query.trim()
  const visible: Group[] = groups.map((g) => ({ ...g, cards: g.cards.filter((c) => matches(c, q)) })).filter((g) => g.cards.length > 0)
  const order = visible.flatMap((g) => g.cards.map((c) => c.task_id))
  const orderKey = order.join(',')
  const inList = id ? order.indexOf(Number(id)) : -1
  const [held, setHeld] = useState<{ key: string; order: number[] }>({ key: '', order: [] })
  if (inList >= 0 && held.key !== orderKey) setHeld({ key: orderKey, order })
  // 목록에서 빠진 업무: 직전 순서에서 앞뒤로 아직 목록에 있는 업무를 찾는다
  const was = id && inList < 0 ? held.order.indexOf(Number(id)) : -1
  const before = was >= 0 ? held.order.slice(0, was).reverse().find((t) => order.includes(t)) : undefined
  const after = was >= 0 ? held.order.slice(was + 1).find((t) => order.includes(t)) : undefined
  const at = inList
  const open = useCallback((taskId: number) => navigate(`${base}/${taskId}`), [navigate, base])
  const close = useCallback(() => navigate(base), [navigate, base])
  const prevId = at > 0 ? order[at - 1] : before
  const nextId = at >= 0 ? (at < order.length - 1 ? order[at + 1] : undefined) : was >= 0 ? after : undefined
  const prev = prevId != null ? () => open(prevId) : undefined
  const next = nextId != null ? () => open(nextId) : undefined

  // 키보드: j/k 다음 · 이전, Esc 닫기, / 검색 (입력칸에서는 Esc로 빠져나오기만)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (isTyping(e.target)) {
        if (e.key === 'Escape') (e.target as HTMLElement).blur()
        return
      }
      if (e.key === '/') {
        searchRef.current?.focus()
        e.preventDefault()
      } else if (e.key === '?') {
        setHelp((h) => !h)
      } else if (e.key === 'Escape' && help) {
        setHelp(false)
      } else if (e.key === 'Escape' && id) {
        close()
      } else if ((e.key === 'j' || e.key === 'k') && order.length > 0) {
        const to = e.key === 'j' ? nextId : prevId
        if (to != null) open(to)
        else if (at < 0 && was < 0) open(order[0])
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [order, at, was, prevId, nextId, id, open, close, help])

  // 고른 줄이 목록에서 보이게
  useEffect(() => {
    if (!id) return
    document.querySelector(`.row-link[data-task="${id}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [id])

  const sync = async () => {
    setSyncing(true)
    setSyncError('')
    try {
      setGoogle(await api.post<GoogleStatus>('/google/sync'))
      load()
    } catch (e) {
      setSyncError((e as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  const unsnooze = async (taskId: number) => {
    const key = `snooze-${taskId}`
    setBusy(key)
    setRowError(null)
    try {
      await api.post(`/tasks/${taskId}/unsnooze`)
      load()
    } catch (e) {
      setRowError({ key, message: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const promote = async (emailId: number) => {
    const key = `mail-${emailId}`
    setBusy(key)
    setRowError(null)
    try {
      const res = await api.post<{ task_id: number }>(`/emails/${emailId}/promote`)
      load()
      open(res.task_id)
    } catch (e) {
      setRowError({ key, message: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const notConnected = !!google && (!google.enabled || google.status !== 'connected')
  const connected = google?.enabled && google.status === 'connected'
  const counts = data?.counts
  const errorFor = (key: string) => (rowError?.key === key ? <div className="alert alert-red small">{rowError.message}</div> : null)
  const followCount = mode === 'followup' ? groups.reduce((n, g) => n + (g.key === 'waiting' ? 0 : g.cards.length), 0) : 0

  return (
    <div className={`split${id ? ' has-panel' : ''}`}>
      <section className="list-pane" aria-label={mode === 'followup' ? '후속 관리 목록' : '받은 업무 목록'}>
        <header className="pane-head">
          <div className="pane-title">
            <h1>{mode === 'followup' ? '후속 관리' : '받은 업무'}</h1>
            {mode === 'followup' ? (
              <span className="muted small">
                챙길 업무 <b className="num">{followCount}</b>건
              </span>
            ) : (
              connected && (
                <button type="button" className="sync" onClick={sync} disabled={syncing} title="Gmail에서 새 메일 확인">
                  <span className="live-dot" aria-hidden="true" />
                  <span>{syncing ? '확인 중…' : google.last_synced_at ? `${relative(google.last_synced_at)} 확인` : '아직 확인 전'}</span>
                  <Icon name="refresh" size={14} className={syncing ? 'spin' : ''} />
                </button>
              )
            )}
          </div>
          <label className="search">
            <Icon name="search" size={16} />
            <input ref={searchRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="보낸 사람 · 내용 검색" aria-label="목록 검색" />
            <kbd>/</kbd>
          </label>
          {help && (
            <div className="shortcut-help" role="note" aria-label="단축키">
              <ul>
                <li><kbd>j</kbd> <kbd>k</kbd> 다음 · 이전 업무</li>
                <li><kbd>Enter</kbd> 고른 줄 열기</li>
                <li><kbd>Esc</kbd> 상세 닫기</li>
                <li><kbd>/</kbd> 목록 검색</li>
                <li><kbd>?</kbd> 이 안내 열고 닫기</li>
              </ul>
              <button type="button" className="icon-btn" onClick={() => setHelp(false)} aria-label="단축키 안내 닫기">
                <Icon name="close" size={16} />
              </button>
            </div>
          )}
          {mode === 'inbox' && (
            <div className="seg" role="group" aria-label="업무 구분">
              {TABS.map((t) => (
                <button key={t.key} type="button" aria-pressed={tab === t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
                  {t.label}
                  {counts && <span className="num">{counts[t.key]}</span>}
                </button>
              ))}
            </div>
          )}
        </header>

        <div className="list-scroll">
          {notConnected && mode === 'inbox' && (
            <div className="notice">
              <b>{google.enabled && google.status === 'reauth_required' ? 'Gmail 연결이 만료되었어요' : 'Gmail을 연결해 주세요'}</b>
              <p>{google.enabled && google.status === 'reauth_required' ? '다시 연결하면 그동안 온 업무 메일을 이어서 정리합니다.' : '업무 메일만 골라 요약 · 할 일 · 기한을 정리해 드려요.'}</p>
              <Link to="/settings" className="btn btn-primary btn-sm">
                {google.enabled && google.status === 'reauth_required' ? '다시 연결하기' : 'Gmail 연결하기'}
              </Link>
            </div>
          )}
          {syncError && <div className="alert alert-red">{syncError}</div>}
          {google?.status === 'connected' && google.last_sync_error && <div className="alert alert-red">{google.last_sync_error}</div>}
          {error && (
            <div className="alert alert-red alert-action">
              <span>목록을 불러오지 못했습니다. {error}</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
                다시 불러오기
              </button>
            </div>
          )}

          {!data && !error && (
            <div className="skeleton" role="status" aria-label="불러오는 중">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="skeleton-row">
                  <span />
                  <span />
                  <span />
                </div>
              ))}
            </div>
          )}

          {data && visible.length === 0 && (
            <div className="empty">
              {q ? (
                <>
                  <b>"{q}"에 맞는 업무가 없어요</b>
                  <p>보낸 사람 이름 · 회사 · 요약으로 찾습니다.</p>
                </>
              ) : mode === 'followup' ? (
                <>
                  <b>챙길 업무가 없어요</b>
                  <p>답이 없거나 상대가 내용을 바꾼 업무가 생기면 여기에 모여요.</p>
                </>
              ) : (
                <>
                  <b>{EMPTY[tab].title}</b>
                  <p>{EMPTY[tab].body}</p>
                </>
              )}
            </div>
          )}

          {visible.map((g) => (
            <div key={g.key} className="group">
              {g.label && (
                <div className={`group-head${g.tone ? ` tone-${g.tone}` : ''}`}>
                  <span>{g.label}</span>
                  <span className="num">{g.cards.length}</span>
                  {g.hint && <span className="group-hint">{g.hint}</span>}
                </div>
              )}
              {g.cards.map((c) => (
                <InboxCard key={c.task_id} card={c} to={`${base}/${c.task_id}`} active={String(c.task_id) === id} />
              ))}
            </div>
          ))}

          {mode === 'inbox' && data && data.snoozed.length > 0 && (
            <details className="fold">
              <summary>
                <span>나중에 보기로 미룬 업무</span>
                <span className="muted num">{data.snoozed.length}</span>
              </summary>
              {data.snoozed.map((c) => {
                const key = `snooze-${c.task_id}`
                return (
                  <InboxCard
                    key={c.task_id}
                    card={c}
                    to={`${base}/${c.task_id}`}
                    active={String(c.task_id) === id}
                    extra={
                      <div className="row-extra">
                        <span className="muted small">{dateTime(c.wake_at)}에 다시 표시</span>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => unsnooze(c.task_id)} disabled={busy === key}>
                          {busy === key ? '처리 중…' : '지금 보기'}
                        </button>
                        {errorFor(key)}
                      </div>
                    }
                  />
                )
              })}
            </details>
          )}

          {mode === 'inbox' && data && data.not_work.length > 0 && (
            <details className="fold">
              <summary>
                <span>업무 아님으로 분류된 메일</span>
                <span className="muted num">{data.not_work.length}</span>
              </summary>
              {!data.triage_enabled && <div className="alert alert-amber fold-note">자동 판별이 꺼져 있어 업무로 분석할 수 없습니다.</div>}
              <ul className="plain-list">
                {data.not_work.map((m) => {
                  const key = `mail-${m.email_id}`
                  return (
                    <li key={m.email_id}>
                      <div className="plain-main">
                        <div className="small">
                          <b>{m.sender}</b>
                          {m.received_at && <span className="muted"> · {dateTime(m.received_at)}</span>}
                        </div>
                        <div>{m.subject || '(제목 없음)'}</div>
                        {m.triage_reason && (
                          <div className="muted small">
                            <span className="ai-tag">AI 판단</span> {m.triage_reason}
                          </div>
                        )}
                        {errorFor(key)}
                      </div>
                      {data.triage_enabled && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => promote(m.email_id)} disabled={busy === key}>
                          {busy === key ? '분석 중…' : '업무로 처리'}
                        </button>
                      )}
                    </li>
                  )
                })}
              </ul>
            </details>
          )}
          <p className="key-hint muted small">
            <kbd>j</kbd> <kbd>k</kbd> 이동 · <kbd>Esc</kbd> 닫기 · <kbd>/</kbd> 검색
          </p>
        </div>
      </section>

      <section className="detail-pane" aria-label="업무 상세">
        {id ? (
          <TaskPanel id={id} onClose={close} onOpen={open} onPrev={prev} onNext={next} position={at >= 0 ? `${at + 1} / ${order.length}` : undefined} />
        ) : (
          <div className="pane-empty">
            <Icon name="inbox" size={28} />
            <b>업무를 고르면 여기에 열려요</b>
            <p className="muted small">요약 · 할 일 · 답장 초안을 목록을 떠나지 않고 처리할 수 있어요.</p>
          </div>
        )}
      </section>
    </div>
  )
}
