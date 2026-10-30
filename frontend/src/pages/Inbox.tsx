import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import Icon from '../components/Icon'
import InboxCard from '../components/InboxCard'
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

  const notConnected = !!google && (!google.enabled || google.status !== 'connected')
  const connected = google?.enabled && google.status === 'connected'

  const counts = data?.counts
  const errorFor = (key: string) => (rowError?.key === key ? <div className="alert alert-red small">{rowError.message}</div> : null)
  const today = new Date()
  const todayText = `${today.getFullYear()}. ${today.getMonth() + 1}. ${today.getDate()}. (${WEEKDAYS[today.getDay()]})`

  const tally: { key: string; label: string; value: number | undefined; tab: Tab; lead?: boolean; hint: string }[] = [
    { key: 'now', label: '지금 처리', value: counts?.now, tab: 'todo', lead: true, hint: '기한이 가깝거나 지났거나 중요도가 높은 업무' },
    { key: 'week', label: '이번 주', value: counts?.week, tab: 'todo', hint: '이번 주 안에 기한이 있는 업무' },
    { key: 'later', label: '나중에', value: counts?.later, tab: 'todo', hint: '기한이 멀거나 없는 업무' },
    { key: 'sent', label: '답 기다림', value: counts?.sent, tab: 'sent', hint: '답장을 보내고 상대 회신을 기다리는 업무' },
  ]

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>받은 업무</h1>
          <p className="page-sub">
            <span className="num">{todayText}</span> 기준 · 급한 순서로 정리했어요
          </p>
        </div>
        {connected && (
          <div className="sync-line">
            <span className="sync-state">
              <span className="live-dot" aria-hidden="true" />
              <span className="sync-mail">{google.google_email}</span>
            </span>
            <span className="muted">{google.last_synced_at ? `${relative(google.last_synced_at)} 확인` : '아직 확인 전'}</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={sync} disabled={syncing}>
              <Icon name="refresh" size={15} className={syncing ? 'spin' : ''} />
              {syncing ? '확인 중…' : '지금 확인'}
            </button>
          </div>
        )}
      </header>

      {notConnected && (
        <section className="notice notice-action">
          <div>
            <b>{google.enabled && google.status === 'reauth_required' ? 'Gmail 연결이 만료되었어요' : 'Gmail을 연결하면 여기서 시작돼요'}</b>
            <p>
              {google.enabled && google.status === 'reauth_required'
                ? '다시 연결하면 그동안 온 업무 메일을 이어서 정리합니다. 이미 만든 업무는 아래에 그대로 있습니다.'
                : '받은 메일 중 업무 메일만 골라 요약 · 할 일 · 기한을 정리하고, 기한은 캘린더에 올려 드립니다.'}
            </p>
          </div>
          <Link to="/settings" className="btn btn-primary btn-sm">
            {google.enabled && google.status === 'reauth_required' ? '다시 연결하기' : 'Gmail 연결하기'}
          </Link>
        </section>
      )}
      {syncError && <div className="alert alert-red">{syncError}</div>}
      {google?.status === 'connected' && google.last_sync_error && <div className="alert alert-red">{google.last_sync_error}</div>}
      {error && (
        <div className="alert alert-red alert-action">
          <span>받은 업무를 불러오지 못했습니다. {error}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
            다시 불러오기
          </button>
        </div>
      )}

      <section className="sheet">
        <div className="tally" role="group" aria-label="업무 합계">
          {tally.map((t) => (
            <button
              key={t.key}
              type="button"
              className={`tally-cell${t.lead ? ' lead' : ''}`}
              onClick={() => setTab(t.tab)}
              title={t.hint}
            >
              <span className="tally-label">{t.label}</span>
              <span className="tally-value num">
                {t.value ?? '–'}
                <small>건</small>
              </span>
            </button>
          ))}
        </div>

        <div className="tabs-line" role="tablist" aria-label="업무 구분">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
              {t.label}
              {counts && <span className="tab-count num">{counts[t.key]}</span>}
            </button>
          ))}
        </div>

        <div className="ledger">
          <div className="ledger-head" aria-hidden="true">
            <span>중요</span>
            <span>상대 · 업무</span>
            <span>할 일</span>
            <span>기한</span>
            <span />
          </div>
          {!data && !error && <LedgerSkeleton />}
          {data &&
            tab === 'todo' &&
            (counts?.todo === 0 ? (
              <Empty title="지금 처리할 업무가 없어요" body="새 업무 메일이 오면 기한과 중요도 순으로 여기에 올라옵니다. 답장을 보낸 업무는 '답장 보냄'에서 볼 수 있어요." />
            ) : (
              BUCKETS.filter((b) => data.tabs.todo[b.key].length > 0).map((b) => (
                <div key={b.key} className="ledger-group">
                  <div className={`ledger-group-head${b.key === 'now' ? ' now' : ''}`}>
                    <span>{b.label}</span>
                    <span className="num">{data.tabs.todo[b.key].length}건</span>
                  </div>
                  <CardList cards={data.tabs.todo[b.key]} />
                </div>
              ))
            ))}
          {data &&
            tab !== 'todo' &&
            (data.tabs[tab].length === 0 ? <Empty title={EMPTY[tab].title} body={EMPTY[tab].body} /> : <CardList cards={data.tabs[tab]} />)}
        </div>
      </section>

      {data && data.snoozed.length > 0 && (
        <details className="sheet fold">
          <summary>
            <span>나중에 보기</span>
            <span className="num muted">{data.snoozed.length}건</span>
          </summary>
          <div className="ledger">
            {data.snoozed.map((c) => {
              const key = `snooze-${c.task_id}`
              return (
                <InboxCard
                  key={c.task_id}
                  card={c}
                  extra={
                    <div className="ledger-extra">
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
        <details className="sheet fold">
          <summary>
            <span>업무 아님으로 분류된 메일</span>
            <span className="num muted">{data.not_work.length}건</span>
          </summary>
          {!data.triage_enabled && <div className="alert alert-amber fold-note">자동 판별이 꺼져 있어 업무로 분석할 수 없습니다.</div>}
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
                    {m.triage_reason && <div className="muted small">분류 이유: {m.triage_reason}</div>}
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

const WEEKDAYS = '일월화수목금토'

const EMPTY: Record<Exclude<Tab, 'todo'>, { title: string; body: string }> = {
  sent: { title: '답을 기다리는 업무가 없어요', body: '답장을 보내면 여기로 옮겨지고, 영업일 3일 동안 답이 없으면 다시 알려 드립니다.' },
  reference: { title: '참고용 업무가 없어요', body: '답장이 필요 없는 공지 · 안내 메일이 여기에 모입니다.' },
  done: { title: '완료한 업무가 없어요', body: '완료하거나 답장 없이 닫은 업무가 여기에 남습니다.' },
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <b>{title}</b>
      <p>{body}</p>
    </div>
  )
}

function LedgerSkeleton() {
  return (
    <div className="skeleton" role="status" aria-label="불러오는 중">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="skeleton-row">
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>
  )
}

function CardList({ cards }: { cards: Card[] }) {
  return (
    <>
      {cards.map((c) => (
        <InboxCard key={c.task_id} card={c} />
      ))}
    </>
  )
}
