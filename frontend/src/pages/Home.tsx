import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { activityTitle } from '../activity'
import { api } from '../api'
import { useAuth } from '../App'
import Icon from '../components/Icon'
import InboxCard from '../components/InboxCard'
import { needsFollowup, openCards } from '../followup'
import { dateTime, relative, shortDate } from '../format'
import type { ActivityItem, GoogleStatus, Inbox as InboxData, InboxCard as Card } from '../types'

const TRIAGE: Record<string, { label: string; cls: string }> = {
  work: { label: '업무', cls: 'pill-blue' },
  not_work: { label: '업무 아님', cls: 'pill-gray' },
  filtered: { label: '자동 필터', cls: 'pill-gray' },
  pending: { label: '판별 대기', cls: 'pill-amber' },
}

function isDueSoon(c: Card) {
  const t = c.deadline_badge.text
  return c.deadline_badge.late || t === '오늘' || t === '내일' || t === 'D-1'
}

/** 홈: 오늘 처리할 것 · 기한 임박 · 답 기다림 · 후속 필요 · 완료, AI 처리 상태, 새로 가져온 메일, 최근 업무 활동 */
/** "이름 <주소>"에서 이름만. 이름이 없으면 주소. */
function senderName(raw: string): string {
  const m = raw.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/)
  if (!m) return raw
  return m[1].trim() || m[2].trim()
}

export default function Home() {
  const { user } = useAuth()
  const [inbox, setInbox] = useState<InboxData | null>(null)
  const [google, setGoogle] = useState<GoogleStatus | null>(null)
  const [activity, setActivity] = useState<ActivityItem[] | null>(null)
  const [error, setError] = useState('')
  const [syncing, setSyncing] = useState(false)

  const load = useCallback(() => {
    api
      .get<InboxData>('/inbox')
      .then((d) => {
        setInbox(d)
        setError('')
      })
      .catch((e: Error) => setError(e.message))
    api.get<GoogleStatus>('/google/status').then(setGoogle).catch(() => {})
    api
      .get<{ items: ActivityItem[] }>('/activity?limit=12')
      .then((r) => setActivity(r.items))
      .catch(() => setActivity([]))
  }, [])

  useEffect(() => {
    load()
    const t = window.setInterval(load, 30_000)
    return () => window.clearInterval(t)
  }, [load])

  const sync = async () => {
    setSyncing(true)
    try {
      setGoogle(await api.post<GoogleStatus>('/google/sync'))
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  const todo = inbox ? [...inbox.tabs.todo.now, ...inbox.tabs.todo.week, ...inbox.tabs.todo.later] : []
  const all = inbox ? openCards(inbox) : []
  const dueSoon = todo.filter(isDueSoon)
  const followup = all.filter(needsFollowup)
  const pendingAnalysis = all.filter((c) => c.analysis === 'pending').length
  const failedAnalysis = all.filter((c) => c.analysis === 'failed').length
  const today = shortDate(new Date().toISOString(), false)
  const connected = google?.enabled && google.status === 'connected'

  const tiles = [
    { label: '오늘 처리할 업무', value: inbox?.counts.now, to: '/inbox', tone: 'navy', note: '기한이 가깝거나 중요한 업무' },
    { label: '기한 임박', value: inbox ? dueSoon.length : undefined, to: '/inbox', tone: dueSoon.some((c) => c.deadline_badge.late) ? 'red' : 'plain', note: '지났거나 오늘 · 내일까지' },
    { label: '답장 대기', value: inbox?.counts.sent, to: '/followup', tone: 'plain', note: '답장을 보내고 기다리는 중' },
    { label: '후속 필요', value: inbox ? followup.length : undefined, to: '/followup', tone: followup.length > 0 ? 'amber' : 'plain', note: '무응답 · 내용 바뀜 · 답장 안 함' },
    { label: '완료', value: inbox?.counts.done, to: '/inbox?tab=done', tone: 'plain', note: '완료 · 닫은 업무' },
  ]

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>
            오늘의 업무<span className="page-date num">{today}</span>
          </h1>
        </div>
        <Link to="/inbox" className="btn btn-primary">
          받은 업무 열기
        </Link>
      </header>

      {error && (
        <div className="alert alert-red alert-action">
          <span>정보를 불러오지 못했습니다. {error}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
            다시 불러오기
          </button>
        </div>
      )}

      <section className="tiles" aria-label="업무 현황">
        {tiles.map((t) => (
          <Link key={t.label} to={t.to} className={`tile tile-${t.tone}`}>
            <span className="tile-label">{t.label}</span>
            <span className="tile-value num">{t.value ?? '–'}</span>
            <span className="tile-note">{t.note}</span>
          </Link>
        ))}
      </section>

      <div className="home-grid">
        <section className="card" aria-labelledby="h-now">
          <div className="card-head">
            <h2 id="h-now">지금 처리할 업무</h2>
            <Link to="/inbox" className="link-sm">
              전체 보기
            </Link>
          </div>
          {!inbox ? (
            <div className="skeleton">
              <div className="skeleton-row"><span /><span /><span /></div>
              <div className="skeleton-row"><span /><span /><span /></div>
            </div>
          ) : inbox.tabs.todo.now.length + dueSoon.length === 0 ? (
            <div className="empty compact">
              <b>지금 급한 업무가 없어요</b>
              <p>기한이 가까운 업무가 생기면 여기에 먼저 보여 드려요.</p>
            </div>
          ) : (
            <div className="rows-compact">
              {[...new Map([...inbox.tabs.todo.now, ...dueSoon].map((c) => [c.task_id, c])).values()].slice(0, 6).map((c) => (
                <InboxCard key={c.task_id} card={c} to={`/inbox/${c.task_id}`} />
              ))}
            </div>
          )}
        </section>

        <section className="card" aria-labelledby="h-ai">
          <div className="card-head">
            <h2 id="h-ai">AI 처리 상태</h2>
            {connected && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={sync} disabled={syncing}>
                <Icon name="refresh" size={14} className={syncing ? 'spin' : ''} />
                {syncing ? '확인 중…' : '지금 확인'}
              </button>
            )}
          </div>
          <dl className="status-list">
            <div>
              <dt>Gmail</dt>
              <dd>
                {!google ? (
                  '확인 중…'
                ) : connected ? (
                  <>
                    <span className="state ok">연결됨</span> {google.last_synced_at ? `${relative(google.last_synced_at)} 확인` : '아직 확인 전'}
                  </>
                ) : google.enabled && google.status === 'reauth_required' ? (
                  <>
                    <span className="state bad">만료</span> <Link to="/settings">다시 연결</Link>
                  </>
                ) : (
                  <>
                    <span className="state off">연결 안 됨</span> <Link to="/settings">연결하기</Link>
                  </>
                )}
              </dd>
            </div>
            <div>
              <dt>업무 메일 판별</dt>
              <dd>
                {google?.triage_enabled ? <span className="state ok">켜짐</span> : <span className="state off">꺼짐</span>}
                {google && google.triage_counts.pending > 0 && <span className="muted"> · 판별 대기 {google.triage_counts.pending}통</span>}
              </dd>
            </div>
            <div>
              <dt>업무 분석</dt>
              <dd>
                {pendingAnalysis > 0 ? <span className="state wait">분석 중 {pendingAnalysis}</span> : <span className="state ok">밀린 것 없음</span>}
                {failedAnalysis > 0 && (
                  <>
                    {' '}
                    <span className="state bad">실패 {failedAnalysis}</span> <span className="muted small">업무에서 다시 분석</span>
                  </>
                )}
              </dd>
            </div>
            <div>
              <dt>자료로 답장 채우기</dt>
              <dd>{user?.knowledge_enabled ? <span className="state ok">켜짐</span> : <span className="state off">꺼짐</span>}</dd>
            </div>
            {google?.last_sync_error && (
              <div>
                <dt>최근 오류</dt>
                <dd className="text-red">{google.last_sync_error}</dd>
              </div>
            )}
          </dl>
        </section>

        <section className="card" aria-labelledby="h-new">
          <div className="card-head">
            <h2 id="h-new">새로 가져온 메일</h2>
            {google && google.mail_count > 0 && (
              <span className="muted small">
                업무 {google.triage_counts.work} · 업무 아님 {google.triage_counts.not_work} · 자동 필터 {google.triage_counts.filtered}
              </span>
            )}
          </div>
          {!google ? (
            <div className="skeleton">
              <div className="skeleton-row"><span /><span /><span /></div>
            </div>
          ) : google.recent.length === 0 ? (
            <div className="empty compact">
              <b>아직 가져온 메일이 없어요</b>
              <p>{connected ? 'Gmail에 새 메일이 오면 90초 안에 여기에 보여요.' : '설정에서 Gmail을 연결하면 시작돼요.'}</p>
            </div>
          ) : (
            <ul className="feed">
              {google.recent.slice(0, 6).map((m, i) => {
                const t = TRIAGE[m.triage ?? '']
                const body = (
                  <>
                    <span className="feed-main">
                      <b title={m.sender}>{senderName(m.sender)}</b>
                      <span>{m.subject || '(제목 없음)'}</span>
                    </span>
                    <span className="feed-side">
                      {t && <span className={`pill ${t.cls}`}>{t.label}</span>}
                      <span className="muted small num">{m.received_at ? relative(m.received_at) : ''}</span>
                    </span>
                  </>
                )
                return <li key={i}>{m.task_id != null ? <Link to={`/inbox/${m.task_id}`}>{body}</Link> : <div>{body}</div>}</li>
              })}
            </ul>
          )}
        </section>

        <section className="card" aria-labelledby="h-act">
          <div className="card-head">
            <h2 id="h-act">최근 업무 활동</h2>
          </div>
          {activity === null ? (
            <div className="skeleton">
              <div className="skeleton-row"><span /><span /><span /></div>
            </div>
          ) : activity.length === 0 ? (
            <div className="empty compact">
              <b>아직 활동이 없어요</b>
              <p>업무를 접수하거나 답장을 보내면 여기에 시간 순으로 남아요.</p>
            </div>
          ) : (
            <ol className="activity">
              {activity.map((a) => (
                <li key={a.id}>
                  <span className={`act-dot${a.risk_level === 'HIGH' ? ' red' : ''}`} aria-hidden="true" />
                  <Link to={`/inbox/${a.task_id}`} className="act-main">
                    <b>{activityTitle(a.action_type, a.reason)}</b>
                    <span className="muted">{a.task_title}</span>
                  </Link>
                  <span className="muted small num nowrap">{dateTime(a.created_at)}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  )
}
