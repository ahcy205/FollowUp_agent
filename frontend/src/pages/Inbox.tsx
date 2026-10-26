import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import InboxCard, { PriorityDot } from '../components/InboxCard'
import { dateTime, relative } from '../format'
import type { GoogleStatus, Inbox as InboxData, InboxCard as Card } from '../types'

type Tab = 'todo' | 'sent' | 'reference' | 'done'

const TABS: { key: Tab; label: string }[] = [
  { key: 'todo', label: '처리할 일' },
  { key: 'sent', label: '답장 보냄' },
  { key: 'reference', label: '참고용' },
  { key: 'done', label: '완료' },
]

const BUCKETS: { key: 'now' | 'week' | 'later'; label: string }[] = [
  { key: 'now', label: '지금 처리' },
  { key: 'week', label: '이번 주' },
  { key: 'later', label: '나중에' },
]

const REFRESH_MS = 30_000

export default function Inbox() {
  const navigate = useNavigate()
  const [data, setData] = useState<InboxData | null>(null)
  const [google, setGoogle] = useState<GoogleStatus | null>(null)
  const [googleLoaded, setGoogleLoaded] = useState(false)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('todo')
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null)

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
      .finally(() => setGoogleLoaded(true))
  }, [])

  useEffect(() => {
    load()
    const id = window.setInterval(load, REFRESH_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

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
      navigate(`/tasks/${res.task_id}`)
    } catch (e) {
      setRowError({ key, message: (e as Error).message })
      setBusy(null)
    }
  }

  const notConnected = !!google && (!google.enabled || google.status === 'disconnected')
  if (googleLoaded && notConnected) {
    return (
      <div className="stack-lg">
        <section className="card">
          <h2>Gmail이 연결되어 있지 않습니다</h2>
          <p className="muted">설정에서 Google 계정을 연결하면 받은 메일 중 업무가 여기에 정리됩니다.</p>
          <Link to="/settings" className="btn btn-primary btn-sm">
            설정으로 이동
          </Link>
        </section>
      </div>
    )
  }

  const counts = data?.counts
  const errorFor = (key: string) => (rowError?.key === key ? <div className="alert alert-red small">{rowError.message}</div> : null)

  return (
    <div className="stack-lg">
      <div className="inbox-status small">
        {google?.status === 'connected' ? (
          <>
            <span>
              <span className="dot dot-green" /> Gmail 연결됨 · {google.google_email}
            </span>
            <span className="muted">
              마지막 확인 {google.last_synced_at ? relative(google.last_synced_at) : '아직 없음'}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={sync} disabled={syncing}>
              {syncing ? '확인 중…' : '지금 확인'}
            </button>
          </>
        ) : google?.status === 'reauth_required' ? (
          <span>
            <span className="dot dot-red" /> Google 연결이 만료되어 새 메일을 가져오지 못합니다.
          </span>
        ) : null}
      </div>
      {syncError && <div className="alert alert-red">{syncError}</div>}
      {google?.status === 'connected' && google.last_sync_error && (
        <div className="alert alert-red">{google.last_sync_error}</div>
      )}
      {error && <div className="alert alert-red">받은 업무를 불러오지 못했습니다. {error}</div>}

      <section className="card">
        <div className="inbox-head">
          <div>
            <div className="inbox-now">지금 처리 {counts?.now ?? '–'}건</div>
            <div className="muted">
              이번 주 {counts?.week ?? '–'}건 · 나중에 {counts?.later ?? '–'}건
            </div>
          </div>
          <div className="inbox-legend muted small" aria-hidden="true">
            <PriorityDot priority="HIGH" /> 높음 <PriorityDot priority="NORMAL" /> 보통 <PriorityDot priority="LOW" /> 낮음
          </div>
        </div>

        <div className="tabs tabs-inline inbox-tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={tab === t.key ? 'active' : ''}
              aria-pressed={tab === t.key}
              onClick={() => setTab(t.key)}
            >
              {t.label} {counts ? counts[t.key] : ''}
            </button>
          ))}
        </div>

        {!data && !error && <p className="muted">불러오는 중…</p>}
        {data && tab === 'todo' && (
          counts?.todo === 0 ? (
            <p className="muted">지금 처리할 업무가 없습니다</p>
          ) : (
            BUCKETS.filter((b) => data.tabs.todo[b.key].length > 0).map((b) => (
              <div key={b.key}>
                <h3 className="subhead">{b.label}</h3>
                <CardList cards={data.tabs.todo[b.key]} />
              </div>
            ))
          )
        )}
        {data && tab !== 'todo' && (
          data.tabs[tab].length === 0 ? (
            <p className="muted">해당하는 업무가 없습니다</p>
          ) : (
            <div className="inbox-list-gap">
              <CardList cards={data.tabs[tab]} />
            </div>
          )
        )}
      </section>

      {data && data.snoozed.length > 0 && (
        <details className="card inbox-fold">
          <summary>나중에 보기 {data.snoozed.length}건</summary>
          <div className="inbox-list">
            {data.snoozed.map((c) => {
              const key = `snooze-${c.task_id}`
              return (
                <InboxCard
                  key={c.task_id}
                  card={c}
                  extra={
                    <div className="inbox-card-extra">
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
          </div>
        </details>
      )}

      {data && data.not_work.length > 0 && (
        <details className="card inbox-fold">
          <summary>업무 아님으로 분류된 메일 {data.not_work.length}건</summary>
          {!data.triage_enabled && (
            <div className="alert alert-amber settings-gap">자동 판별이 꺼져 있어 분석할 수 없습니다.</div>
          )}
          <ul className="not-work-list">
            {data.not_work.map((m) => {
              const key = `mail-${m.email_id}`
              return (
                <li key={m.email_id}>
                  <div className="not-work-main">
                    <div className="small">
                      <b>{m.sender}</b>
                      {m.received_at && <span className="muted"> · {dateTime(m.received_at)}</span>}
                    </div>
                    <div>{m.subject || '(제목 없음)'}</div>
                    {m.triage_reason && <div className="muted small">{m.triage_reason}</div>}
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
    </div>
  )
}

function CardList({ cards }: { cards: Card[] }) {
  return (
    <div className="inbox-list">
      {cards.map((c) => (
        <InboxCard key={c.task_id} card={c} />
      ))}
    </div>
  )
}
