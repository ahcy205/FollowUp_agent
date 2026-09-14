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
        <h1>
          목표만 말하세요.
          <br />
          끝날 때까지 AI가 추적합니다.
        </h1>
        <p>
          고객 조회 → 견적서 확인 → 메일 작성 → <b>승인 후 발송</b> → 답변 대기 → 답변 분석 → 후속 업무 생성까지.
          <br />
          Agent는 업무 상태를 기억하고, 새 이벤트가 오면 다시 판단합니다.
        </p>
        <ul className="hero-points">
          <li>🧭 목표 기반 Agent Loop · Tool Calling</li>
          <li>🛡️ 사용자별 데이터 격리 · 발송 전 사용자 승인</li>
          <li>🧾 모든 행동을 타임라인으로 기록</li>
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
            ? '가입하면 내 계정 전용 가상 고객·문서·메일 데이터가 생성됩니다. 실제 메일은 발송되지 않습니다.'
            : '계정이 없다면 회원가입 탭에서 바로 만들 수 있습니다.'}
        </p>
      </form>
    </div>
  )
}
