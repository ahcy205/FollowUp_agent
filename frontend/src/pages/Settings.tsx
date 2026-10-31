import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../App'
import { dateTime } from '../format'
import type { GoogleRecentMail, GoogleStatus } from '../types'

const REASONS: Record<string, string> = {
  denied: 'Google 동의가 취소되었습니다.',
  state: '연결 요청이 만료되었거나 올바르지 않습니다. 다시 시도해 주세요.',
  scopes: '필요한 권한(메일 읽기 · 발송 · 캘린더)이 빠졌습니다. 모든 항목에 체크하고 다시 연결해 주세요.',
  no_refresh_token: "Google에서 장기 토큰을 받지 못했습니다. Google 계정의 '타사 연결'에서 이 앱을 삭제한 뒤 다시 연결해 주세요.",
  exchange: 'Google 응답 처리 중 오류가 났습니다. 잠시 후 다시 시도해 주세요.',
}

const TRIAGE_TAGS: Record<string, { label: string; cls: string }> = {
  work: { label: '업무', cls: 'tag-green' },
  not_work: { label: '업무 아님', cls: '' },
  filtered: { label: '자동 필터', cls: '' },
  pending: { label: '판별 대기', cls: 'tag-amber' },
}

function TriageTag({ mail }: { mail: GoogleRecentMail }) {
  const t = TRIAGE_TAGS[mail.triage ?? '']
  if (!t) return null
  const cls = `tag ${t.cls}`.trim()
  if (mail.triage === 'work' && mail.task_id != null) {
    return (
      <Link to={`/tasks/${mail.task_id}`} className={cls} title={mail.triage_reason || undefined}>
        {t.label}
      </Link>
    )
  }
  return (
    <span className={cls} title={mail.triage_reason || undefined}>
      {t.label}
    </span>
  )
}

type Permission = NotificationPermission | 'unsupported'

const PERMISSION_LABELS: Record<Permission, string> = {
  granted: '허용됨',
  denied: '거부됨',
  default: '아직 정하지 않음',
  unsupported: '지원하지 않음',
}

function currentPermission(): Permission {
  return 'Notification' in window ? Notification.permission : 'unsupported'
}

function BrowserAlerts() {
  const [permission, setPermission] = useState<Permission>(currentPermission)
  const [asking, setAsking] = useState(false)

  const ask = async () => {
    setAsking(true)
    try {
      setPermission(await Notification.requestPermission())
    } catch {
      setPermission(currentPermission())
    } finally {
      setAsking(false)
    }
  }

  return (
    <div className="card">
      <h2>브라우저 알림</h2>
      <p className="muted">앱이 열려 있을 때 기한이 다가온 업무, 다시 표시된 업무, 답장하지 않은 업무를 알려 드립니다.</p>
      {permission === 'unsupported' ? (
        <div className="muted">이 브라우저는 알림을 지원하지 않습니다.</div>
      ) : (
        <>
          <dl className="settings-list">
            <dt>현재 상태</dt>
            <dd>{PERMISSION_LABELS[permission]}</dd>
          </dl>
          {permission === 'denied' && (
            <div className="alert alert-amber settings-gap">브라우저 설정에서 이 사이트의 알림을 허용해 주세요.</div>
          )}
          {permission === 'default' && (
            <div className="approval-actions settings-actions">
              <button type="button" className="btn btn-ghost btn-sm" onClick={ask} disabled={asking}>
                {asking ? '요청 중…' : '알림 허용'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

const connect = () => {
  window.location.href = '/api/google/connect'
}

export default function Settings() {
  const { user } = useAuth()
  const [params] = useSearchParams()
  const [status, setStatus] = useState<GoogleStatus | null>(null)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleteMail, setDeleteMail] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  const load = useCallback(() => {
    api
      .get<GoogleStatus>('/google/status')
      .then((s) => {
        setStatus(s)
        setLoadError('')
      })
      .catch((e: Error) => setLoadError(e.message))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const connected = status?.status === 'connected'
  useEffect(() => {
    if (!connected) return
    const id = window.setInterval(load, 30000)
    return () => window.clearInterval(id)
  }, [connected, load])

  const sync = async () => {
    setSyncing(true)
    setActionError('')
    try {
      setStatus(await api.post<GoogleStatus>('/google/sync'))
    } catch (e) {
      setActionError((e as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  const disconnect = async () => {
    setDisconnecting(true)
    setActionError('')
    try {
      await api.post('/google/disconnect', { delete_mail: deleteMail })
      setConfirming(false)
      setDeleteMail(false)
      load()
    } catch (e) {
      setActionError((e as Error).message)
    } finally {
      setDisconnecting(false)
    }
  }

  const g = params.get('google')
  const reason = params.get('reason') ?? ''
  const firstMailFetched = !!status && (status.mail_count > 0 || !!status.last_synced_at)

  // Shared by the connected and reauth_required states: a stale connection must be removable too.
  const disconnectButton = !confirming && (
    <button type="button" className="btn btn-link btn-sm" onClick={() => setConfirming(true)}>
      연결 해제
    </button>
  )
  const disconnectConfirm = confirming && (
    <div className="disconnect-confirm">
      <b>Google 연결을 해제할까요?</b>
      <div className="muted small">해제하면 더 이상 메일을 가져오지 않습니다.</div>
      <label className="check-row small">
        <input type="checkbox" checked={deleteMail} onChange={(e) => setDeleteMail(e.target.checked)} />
        저장된 Gmail 메일도 삭제
      </label>
      <div className="approval-actions">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirming(false)} disabled={disconnecting}>
          돌아가기
        </button>
        <button type="button" className="btn btn-danger btn-sm" onClick={disconnect} disabled={disconnecting}>
          {disconnecting ? '해제 중…' : '연결 해제 확정'}
        </button>
      </div>
    </div>
  )

  return (
    <div className="page page-narrow">
      <header className="page-head">
        <div>
          <h1>설정</h1>
          <p className="page-sub">
            {user?.email && (
              <>
                <b>{user.email}</b>로 로그인했어요.{' '}
              </>
            )}
            Google 계정을 연결하면 받은편지함의 업무 메일을 자동으로 정리합니다.
          </p>
        </div>
      </header>
      {g === 'connected' && (
        <div className="alert alert-green">
          {firstMailFetched ? '연결되었습니다.' : '연결되었습니다. 첫 메일을 가져오는 중입니다.'}
        </div>
      )}
      {g === 'error' && <div className="alert alert-red">{REASONS[reason] ?? REASONS.exchange}</div>}
      {actionError && <div className="alert alert-red">{actionError}</div>}
      <div className="card">
        <h2>Google 계정</h2>
        {!status ? (
          loadError ? (
            <div className="stack-lg">
              <div className="muted">상태를 불러오지 못했습니다. {loadError}</div>
              <div>
                <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
                  다시 시도
                </button>
              </div>
            </div>
          ) : (
            <div className="muted">불러오는 중…</div>
          )
        ) : !status.enabled ? (
          <div className="muted">서버에 Google 설정이 없습니다. README의 Google 연결 준비를 따라 주세요.</div>
        ) : status.status === 'disconnected' ? (
          <div className="connect-box">
            <p className="muted">메일 읽기 · 답장 보내기 · 캘린더 일정 권한을 요청합니다. 답장은 내가 확인해야만 나갑니다.</p>
            <button type="button" className="btn btn-primary" onClick={connect}>
              Google 계정 연결
            </button>
          </div>
        ) : status.status === 'reauth_required' ? (
          <>
            <div className="alert alert-red alert-action">
              <span>Google 연결이 만료되었습니다. 다시 연결해 주세요.</span>
              <button type="button" className="btn btn-danger btn-sm" onClick={connect}>
                다시 연결
              </button>
            </div>
            {disconnectButton && <div className="approval-actions settings-actions">{disconnectButton}</div>}
            {disconnectConfirm}
          </>
        ) : (
          <>
            <dl className="settings-list">
              <dt>연결된 주소</dt>
              <dd>{status.google_email}</dd>
              <dt>받은 권한</dt>
              <dd>{status.scopes.join(', ')}</dd>
              <dt>마지막 확인</dt>
              <dd>{status.last_synced_at ? dateTime(status.last_synced_at) : '아직 없음'}</dd>
              <dt>가져온 메일</dt>
              <dd>{status.mail_count}통</dd>
            </dl>
            {status.mail_count > 0 && (
              <div className="muted small triage-summary">
                업무 {status.triage_counts.work} · 업무 아님 {status.triage_counts.not_work} · 자동 필터{' '}
                {status.triage_counts.filtered} · 판별 대기 {status.triage_counts.pending}
              </div>
            )}
            {!status.triage_enabled && (
              <div className="alert alert-amber settings-gap">
                업무 메일 자동 판별이 꺼져 있습니다. <code>backend/.env</code>에서 <code>MAIL_TRIAGE_ENABLED=true</code>로 켜면
                메일 내용이 LLM으로 전송되어 분석됩니다.
              </div>
            )}
            {status.last_sync_error && (
              <div className="alert alert-red settings-gap">{status.last_sync_error}</div>
            )}
            {loadError && <div className="muted small settings-gap">상태를 새로 불러오지 못했습니다. {loadError}</div>}
            {status.recent.length > 0 && (
              <ul className="recent-mails">
                {status.recent.slice(0, 5).map((m, i) => (
                  <li key={i} className="small">
                    <b>{m.sender}</b>
                    <span>{m.subject}</span>
                    <span className="muted">{m.received_at ? dateTime(m.received_at) : ''}</span>
                    {m.triage && <TriageTag mail={m} />}
                  </li>
                ))}
              </ul>
            )}
            <div className="approval-actions settings-actions">
              <button type="button" className="btn btn-ghost btn-sm" onClick={sync} disabled={syncing}>
                {syncing ? '확인 중…' : '지금 확인'}
              </button>
              {disconnectButton}
            </div>
            {disconnectConfirm}
          </>
        )}
      </div>
      <BrowserAlerts />
    </div>
  )
}
