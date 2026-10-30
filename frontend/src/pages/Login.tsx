import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../App'
import { BrandMark } from '../components/Icon'
import { PriorityMark } from '../components/InboxCard'
import type { User } from '../types'

// 첫 화면에서 앱이 하는 일을 보여 주는 예시 대장. 가상 데이터이고 화면에 "예시"로 표시한다.
const SAMPLE = [
  { prio: 'HIGH', who: '김민수 · 한빛전자', title: 'TS-200 100개 견적과 납기 회신', todo: '1/3', due: '오늘', late: false },
  { prio: 'HIGH', who: '이서연 · 대성테크', title: 'PC-10 납품일을 11/13으로 당길 수 있는지', todo: '0/2', due: 'D+1 지남', late: true },
  { prio: 'NORMAL', who: '박지훈 · 그린솔루션', title: 'HM-30 샘플 2개 발송', todo: '0/2', due: 'D-4', late: false },
]

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
      <section className="login-hero">
        <div className="brand brand-lg">
          <BrandMark size={34} />
          <span className="brand-name">
            FollowUp <span>Agent</span>
          </span>
        </div>
        <h1>
          받은편지함을 뒤지지 않아도
          <br />
          오늘 할 일이 보입니다.
        </h1>
        <p className="login-lead">
          Gmail에서 업무 메일만 골라 상대가 원하는 것 · 할 일 · 기한으로 정리하고, 답장을 보낸 뒤 상대가 답할 때까지 챙깁니다.
        </p>

        <div className="sample" aria-label="예시 업무 대장">
          <div className="sample-head">
            <span>업무 대장</span>
            <span className="sample-tag">예시</span>
          </div>
          {SAMPLE.map((r) => (
            <div key={r.title} className="sample-row">
              <PriorityMark priority={r.prio} />
              <span className="sample-main">
                <span className="sample-who">{r.who}</span>
                <span className="sample-title">{r.title}</span>
              </span>
              <span className="sample-todo num">{r.todo}</span>
              <span className={`sample-due num${r.late ? ' late' : ''}`}>{r.due}</span>
            </div>
          ))}
        </div>

        <dl className="login-points">
          <div>
            <dt>받은 뒤</dt>
            <dd>요약 · 할 일 · 기한을 만들고 기한은 캘린더에 올립니다</dd>
          </div>
          <div>
            <dt>답장</dt>
            <dd>근거 있는 숫자만 채운 초안을, 내가 확인해야 보냅니다</dd>
          </div>
          <div>
            <dt>보낸 뒤</dt>
            <dd>답이 없거나 상대가 내용을 바꾸면 알려 드립니다</dd>
          </div>
        </dl>
      </section>

      <form className="login-card" onSubmit={submit}>
        <h2>{mode === 'login' ? '로그인' : '계정 만들기'}</h2>
        <div className="tabs-line tabs-compact" role="tablist">
          <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>
            로그인
          </button>
          <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>
            회원가입
          </button>
        </div>
        <label>
          이메일
          <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          <span>
            비밀번호 {mode === 'register' && <span className="muted">(8자 이상)</span>}
          </span>
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
        <button className="btn btn-primary btn-block btn-lg" disabled={busy}>
          {busy ? '처리 중…' : mode === 'login' ? '로그인' : '가입하고 시작하기'}
        </button>
        <p className="muted small">
          {mode === 'register' ? '가입한 뒤 설정에서 Gmail을 연결하면 업무 메일을 가져옵니다.' : '계정이 없다면 회원가입 탭에서 바로 만들 수 있어요.'}
        </p>
      </form>
    </div>
  )
}
