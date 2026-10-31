import { api } from './api'
import type { InboxAlerts } from './types'

const STORAGE_KEY = 'followup.shownAlerts'
const MAX_KEYS = 500
const INTERVAL_MS = 60_000
const MAX_PER_POLL = 3 // 한 번에 띄우는 알림 수. 알림을 처음 허용했을 때 한꺼번에 쏟아지지 않게
const BASE_TITLE = 'FollowUp Agent'

function loadShown(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === 'string') : []
  } catch {
    return []
  }
}

function saveShown(keys: string[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(keys.slice(-MAX_KEYS)))
  } catch {
    // 저장소를 쓸 수 없으면 기록 없이 넘어간다.
  }
}

function canNotify() {
  return 'Notification' in window && Notification.permission === 'granted'
}

/** 1분마다 알릴 것을 가져와 탭 제목과 브라우저 알림을 갱신한다. 멈추는 함수를 돌려준다. */
export function startAlerts(navigate: (path: string) => void): () => void {
  let stopped = false

  const poll = async () => {
    let data: InboxAlerts
    try {
      data = await api.get<InboxAlerts>('/inbox/alerts')
    } catch {
      return // 다음 주기에 다시 시도한다.
    }
    if (stopped) return
    document.title = data.now_count > 0 ? `(${data.now_count}) ${BASE_TITLE}` : BASE_TITLE
    if (!canNotify()) return
    const shown = loadShown()
    const seen = new Set(shown)
    let changed = false
    let notified = 0
    for (const alert of data.alerts) {
      if (seen.has(alert.key)) continue
      if (notified >= MAX_PER_POLL) {
        // 넘친 알림은 띄우지 않고 띄운 것으로 기록한다. 같은 알림이 다음 주기에 다시 몰려오지 않게.
        seen.add(alert.key)
        shown.push(alert.key)
        changed = true
        continue
      }
      try {
        const n = new Notification(alert.text, { tag: alert.key })
        n.onclick = () => {
          window.focus()
          navigate(`/inbox/${alert.task_id}`)
          n.close()
        }
      } catch {
        continue // 띄우지 못했으면 기록하지 않는다.
      }
      seen.add(alert.key)
      shown.push(alert.key)
      changed = true
      notified += 1
    }
    if (changed) saveShown(shown)
  }

  void poll()
  const id = window.setInterval(() => void poll(), INTERVAL_MS)
  return () => {
    stopped = true
    window.clearInterval(id)
    document.title = BASE_TITLE
  }
}
