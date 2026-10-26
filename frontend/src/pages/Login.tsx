import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../App'
import type { User } from '../types'

export default function Login() {
  const { setUser } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const user = await api.post<User>(`/auth/${mode}`, { email, password })
      setUser(user)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-hero">
        <div className="brand brand-lg">
          <span className="brand-mark">✓</span>
          <span>
            FollowUp <b>Agent</b>
          </span>
        </div>
        <h1>업무 메일, 놓치지 않게.</h1>
        <p>Gmail의 업무 메일을 정리하고, 할 일과 기한을 챙기고, 캘린더에 올립니다.</p>
        <ul className="hero-points">
          <li>📬 업무 메일만 골라 요약하고 할 일 · 기한을 정리합니다</li>
          <li>📅 기한은 Google 캘린더 일정과 알림으로</li>
          <li>🛡️ 답장은 내가 확인한 뒤에만 보냅니다</li>
        </ul>
      </div>

      <form className="card login-card" onSubmit={submit}>
        <div className="tabs">
          <button type="button" className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>
            로그인
          </button>
          <button type="button" className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>
            회원가입
          </button>
        </div>
        <label>
          이메일
          <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          비밀번호 {mode === 'register' && <span className="muted">(8자 이상)</span>}
          <input
            type="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password}
            minLength={8}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <div className="alert alert-red">{error}</div>}
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? '처리 중…' : mode === 'login' ? '로그인' : '가입하고 시작하기'}
        </button>
        <p className="muted small">
          {mode === 'register'
            ? '가입한 뒤 설정에서 Gmail을 연결하면 업무 메일을 가져옵니다.'
            : '계정이 없다면 회원가입 탭에서 바로 만들 수 있습니다.'}
        </p>
      </form>
    </div>
  )
}
