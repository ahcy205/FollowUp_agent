import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api, setUnauthorizedHandler } from './api'
import ResetDialog from './components/ResetDialog'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import TaskDetailPage from './pages/TaskDetail'
import Workspace from './pages/Workspace'
import type { User } from './types'

interface AuthState {
  user: User | null
  setUser: (u: User | null) => void
  refreshKey: number
  bumpRefresh: () => void
}

const AuthContext = createContext<AuthState>(null!)
export const useAuth = () => useContext(AuthContext)

function Shell({ children }: { children: React.ReactNode }) {
  const { user, setUser, bumpRefresh } = useAuth()
  const [resetOpen, setResetOpen] = useState(false)
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
          <NavLink to="/" className="brand">
            <span className="brand-mark">✓</span>
            <span>
              FollowUp <b>Agent</b>
            </span>
          </NavLink>
          <nav className="nav">
            <NavLink to="/" end>
              내 업무
            </NavLink>
            <NavLink to="/workspace">워크스페이스 데이터</NavLink>
          </nav>
          <div className="topbar-right">
            <button className="btn btn-ghost btn-sm" onClick={() => setResetOpen(true)} title="데모 데이터를 초기 상태로 되돌립니다">
              ↺ Demo Reset
            </button>
            <span className="user-chip" title={user?.email}>
              {user?.email}
            </span>
            <button className="btn btn-link btn-sm" onClick={logout}>
              로그아웃
            </button>
          </div>
        </div>
      </header>
      <main className="container">{children}</main>
      {resetOpen && (
        <ResetDialog
          onClose={() => setResetOpen(false)}
          onDone={() => {
            setResetOpen(false)
            bumpRefresh()
            navigate('/')
          }}
        />
      )}
    </div>
  )
}

function Protected({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  return <Shell>{children}</Shell>
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)
  const bumpRefresh = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    api
      .get<User>('/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="boot">불러오는 중…</div>

  return (
    <AuthContext.Provider value={{ user, setUser, refreshKey, bumpRefresh }}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/" element={<Protected><Dashboard key={refreshKey} /></Protected>} />
          <Route path="/tasks/:id" element={<Protected><TaskDetailPage key={refreshKey} /></Protected>} />
          <Route path="/workspace" element={<Protected><Workspace key={refreshKey} /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthContext.Provider>
  )
}
