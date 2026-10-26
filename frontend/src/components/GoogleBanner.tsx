import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import type { GoogleStatus } from '../types'

export default function GoogleBanner() {
  const { pathname } = useLocation()
  const [reauth, setReauth] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .get<GoogleStatus>('/google/status')
      .then((s) => {
        if (!cancelled) setReauth(s.enabled && s.status === 'reauth_required')
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [pathname])

  if (!reauth || pathname.startsWith('/settings')) return null
  return (
    <div className="google-banner" role="alert">
      <div className="google-banner-inner">
        <span>Google 연결이 만료되었습니다.</span>
        <button type="button" className="btn btn-danger btn-sm" onClick={() => (window.location.href = '/api/google/connect')}>
          다시 연결
        </button>
      </div>
    </div>
  )
}
