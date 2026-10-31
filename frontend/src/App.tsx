import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { startAlerts } from './alerts'
import { api, setUnauthorizedHandler } from './api'
import GoogleBanner from './components/GoogleBanner'
import Icon, { BrandMark, type IconName } from './components/Icon'
import Inbox from './pages/Inbox'
import Knowledge from './pages/Knowledge'
import Login from './pages/Login'
import Settings from './pages/Settings'
import TaskDetailPage from './pages/TaskDetail'
import type { User } from './types'

interface AuthState {
  user: User | null
  setUser: (u: User | null) => void
}

const AuthContext = createContext<AuthState>(null!)
export const useAuth = () => useContext(AuthContext)

const LINKS: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: '/', label: '받은 업무', icon: 'inbox', end: true },
  { to: '/knowledge', label: '자료실', icon: 'folder' },
  { to: '/settings', label: '설정', icon: 'gear' },
]

function Shell({ children }: { children: React.ReactNode }) {
  const { user, setUser } = useAuth()
  const navigate = useNavigate()

  const logout = async () => {
    await api.post('/auth/logout').catch(() => {})
    setUser(null)
    navigate('/login')
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand" aria-label="FollowUp Agent 받은 업무로">
            <BrandMark />
            <span className="brand-name">
              FollowUp <span>Agent</span>
            </span>
          </NavLink>
          <nav className="nav" aria-label="주 메뉴">
            {LINKS.map((l) => (
              <NavLink key={l.to} to={l.to} end={l.end}>
                <Icon name={l.icon} className="nav-icon" />
                <span>{l.label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="topbar-right">
            <span className="user-chip" title={user?.email}>
              {user?.email}
            </span>
            <button className="btn btn-link btn-sm topbar-logout" onClick={logout} title="로그아웃" aria-label="로그아웃">
              <Icon name="logout" size={16} />
              <span>로그아웃</span>
            </button>
          </div>
        </div>
      </header>
      <GoogleBanner />
      <main className="container">{children}</main>
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

function Protected({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  return <Shell>{children}</Shell>
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
        <BrandMark size={36} />
        <span>불러오는 중…</span>
      </div>
    )

  return (
    <AuthContext.Provider value={{ user, setUser }}>
      <BrowserRouter>
        {user && <AlertsRunner />}
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/" element={<Protected><Inbox /></Protected>} />
          <Route path="/tasks/:id" element={<Protected><TaskDetailPage /></Protected>} />
          <Route path="/knowledge" element={<Protected><Knowledge /></Protected>} />
          <Route path="/settings" element={<Protected><Settings /></Protected>} />
          {/* 예전 주소(전체 업무 · 워크스페이스)는 받은 업무로 */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthContext.Provider>
  )
}
