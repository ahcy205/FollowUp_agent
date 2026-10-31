import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { startAlerts } from './alerts'
import { api, setUnauthorizedHandler } from './api'
import { needsFollowup, openCards } from './followup'
import GoogleBanner from './components/GoogleBanner'
import Icon, { BrandMark, type IconName } from './components/Icon'
import Home from './pages/Home'
import Inbox from './pages/Inbox'
import Knowledge from './pages/Knowledge'
import Login from './pages/Login'
import Settings from './pages/Settings'
import type { Inbox as InboxData, User } from './types'

interface AuthState {
  user: User | null
  setUser: (u: User | null) => void
}

const AuthContext = createContext<AuthState>(null!)
export const useAuth = () => useContext(AuthContext)

type Badges = { todo: number; followup: number }

const LINKS: { to: string; label: string; icon: IconName; badge?: keyof Badges; end?: boolean }[] = [
  { to: '/', label: '홈', icon: 'home', end: true },
  { to: '/inbox', label: '받은 업무', icon: 'inbox', badge: 'todo' },
  { to: '/followup', label: '후속 관리', icon: 'reply', badge: 'followup' },
  { to: '/knowledge', label: '자료실', icon: 'folder' },
  { to: '/settings', label: '설정', icon: 'gear' },
]

/** 메뉴 옆 숫자: 처리할 일, 후속 관리(무응답 · 내용 바뀜). 받은 업무 응답에서 계산한다. */
function useBadges(): Badges {
  const section = useLocation().pathname.split('/')[1]
  const [badges, setBadges] = useState<Badges>({ todo: 0, followup: 0 })
  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .get<InboxData>('/inbox')
        .then((d) => {
          if (cancelled) return
          const followup = new Set(openCards(d).filter(needsFollowup).map((c) => c.task_id)).size
          setBadges({ todo: d.counts.todo, followup })
        })
        .catch(() => {})
    load()
    const id = window.setInterval(load, 60_000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [section])
  return badges
}

function Shell({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()
  const badges = useBadges()

  const logout = async () => {
    await api.post('/auth/logout').catch(() => {})
    setUser(null)
    navigate('/login')
  }

  return (
    <div className="app">
      <aside className="side" aria-label="주 메뉴">
        <NavLink to="/" className="side-brand" aria-label="FollowUp Agent 홈">
          <BrandMark size={26} />
          <span>FollowUp Agent</span>
        </NavLink>
        <nav className="side-nav">
          {LINKS.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end} title={l.label}>
              <Icon name={l.icon} className="side-icon" />
              <span className="side-label">{l.label}</span>
              {l.badge && badges[l.badge] > 0 && <span className="side-badge num">{badges[l.badge]}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <span className="side-user" title={user?.email}>
            {user?.email}
          </span>
          <button type="button" className="side-logout" onClick={logout} aria-label="로그아웃" title="로그아웃">
            <Icon name="logout" size={16} />
          </button>
        </div>
      </aside>
      <div className="main">
        <GoogleBanner />
        <main className={wide ? 'content content-wide' : 'content'}>{children}</main>
      </div>
    </div>
  )
}

// Routes each render their own Shell, so alerts run from one component that lives as long as the login.
function AlertsRunner() {
  const navigate = useNavigate()
  const navigateRef = useRef(navigate)
  useEffect(() => {
    navigateRef.current = navigate
  }, [navigate])
  useEffect(() => startAlerts((path) => navigateRef.current(path)), [])
  return null
}

function Protected({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  return <Shell wide={wide}>{children}</Shell>
}

/** 예전 주소 /tasks/:id 는 받은 업무의 상세 패널로 */
function TaskRedirect() {
  const { id } = useParams()
  return <Navigate to={`/inbox/${id}`} replace />
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    api
      .get<User>('/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading)
    return (
      <div className="boot" role="status">
        <BrandMark size={34} />
        <span>불러오는 중…</span>
      </div>
    )

  return (
    <AuthContext.Provider value={{ user, setUser }}>
      <BrowserRouter>
        {user && <AlertsRunner />}
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/" element={<Protected><Home /></Protected>} />
          <Route path="/inbox" element={<Protected wide><Inbox mode="inbox" /></Protected>} />
          <Route path="/inbox/:id" element={<Protected wide><Inbox mode="inbox" /></Protected>} />
          <Route path="/followup" element={<Protected wide><Inbox mode="followup" /></Protected>} />
          <Route path="/followup/:id" element={<Protected wide><Inbox mode="followup" /></Protected>} />
          <Route path="/tasks/:id" element={<TaskRedirect />} />
          <Route path="/knowledge" element={<Protected wide><Knowledge /></Protected>} />
          <Route path="/settings" element={<Protected><Settings /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthContext.Provider>
  )
}
