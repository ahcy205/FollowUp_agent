import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { dateTime } from '../format'
import type { GoogleStatus } from '../types'

const REASONS: Record<string, string> = {
  denied: 'Google 동의가 취소되었습니다.',
  state: '연결 요청이 만료되었거나 올바르지 않습니다. 다시 시도해 주세요.',
  scopes: '필요한 권한(메일 읽기 · 발송 · 캘린더)이 빠졌습니다. 모든 항목에 체크하고 다시 연결해 주세요.',
  no_refresh_token: "Google에서 장기 토큰을 받지 못했습니다. Google 계정의 '타사 연결'에서 이 앱을 삭제한 뒤 다시 연결해 주세요.",
  exchange: 'Google 응답 처리 중 오류가 났습니다. 잠시 후 다시 시도해 주세요.',
}

const connect = () => {
  window.location.href = '/api/google/connect'
}

export default function Settings() {
  const [params] = useSearchParams()
  const [status, setStatus] = useState<GoogleStatus | null>(null)
  const [error, setError] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleteMail, setDeleteMail] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  const load = useCallback(() => {
    api
      .get<GoogleStatus>('/google/status')
      .then(setStatus)
      .catch((e: Error) => setError(e.message))
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
    setError('')
    try {
      setStatus(await api.post<GoogleStatus>('/google/sync'))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  const disconnect = async () => {
    setDisconnecting(true)
    setError('')
    try {
      await api.post('/google/disconnect', { delete_mail: deleteMail })
      setConfirming(false)
      setDeleteMail(false)
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setDisconnecting(false)
    }
  }

  const g = params.get('google')
  const reason = params.get('reason') ?? ''

  return (
    <div className="stack-lg">
      <div>
        <h1>설정</h1>
        <p className="muted">Google 계정을 연결하면 받은 편지함의 메일을 주기적으로 가져옵니다.</p>
      </div>
      {g === 'connected' && <div className="alert alert-green">연결되었습니다. 첫 메일을 가져오는 중입니다.</div>}
      {g === 'error' && <div className="alert alert-red">{REASONS[reason] ?? REASONS.exchange}</div>}
      {error && <div className="alert alert-red">{error}</div>}
      <div className="card">
        <h2>Google 계정</h2>
        {!status ? (
          <div className="muted">불러오는 중…</div>
        ) : !status.enabled ? (
          <div className="muted">서버에 Google 설정이 없습니다. README의 Google 연결 준비를 따라 주세요.</div>
        ) : status.status === 'disconnected' ? (
          <button type="button" className="btn" onClick={connect}>
            Google 계정 연결
          </button>
        ) : status.status === 'reauth_required' ? (
          <div className="alert alert-red alert-action">
            <span>Google 연결이 만료되었습니다. 다시 연결해 주세요.</span>
            <button type="button" className="btn btn-danger btn-sm" onClick={connect}>
              다시 연결
            </button>
          </div>
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
            {status.last_sync_error && (
              <div className="alert alert-red" style={{ marginTop: 12 }}>
                {status.last_sync_error}
              </div>
            )}
            {status.recent.length > 0 && (
              <ul className="recent-mails">
                {status.recent.slice(0, 5).map((m, i) => (
                  <li key={i} className="small">
                    <b>{m.sender}</b>
                    <span>{m.subject}</span>
                    <span className="muted">{m.received_at ? dateTime(m.received_at) : ''}</span>
                  </li>
                ))}
              </ul>
            )}
            <div className="approval-actions" style={{ marginTop: 16 }}>
              <button type="button" className="btn btn-sm" onClick={sync} disabled={syncing}>
                {syncing ? '확인 중…' : '지금 확인'}
              </button>
              {!confirming && (
                <button type="button" className="btn btn-link btn-sm" onClick={() => setConfirming(true)}>
                  연결 해제
                </button>
              )}
            </div>
            {confirming && (
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
            )}
          </>
        )}
      </div>
    </div>
  )
}
