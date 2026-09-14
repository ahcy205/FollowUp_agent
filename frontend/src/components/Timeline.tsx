import { PERMISSIONS, STATUS_META, time, TOOL_LABELS } from '../format'
import type { ActionLog, TaskStatus } from '../types'

interface ItemView {
  icon: string
  title: string
  tone: string
  detail?: string
}

function view(log: ActionLog): ItemView {
  const tool = TOOL_LABELS[log.tool_name] ?? log.tool_name
  switch (log.action_type) {
    case 'AGENT_STARTED':
      return { icon: '▶', title: String(log.reason || 'Agent 시작'), tone: 'blue' }
    case 'AGENT_THOUGHT':
      return { icon: '💭', title: 'Agent 판단', tone: 'thought', detail: log.reason }
    case 'TOOL_CALL':
      return log.status === 'ERROR'
        ? { icon: '✕', title: `${tool} 실패`, tone: 'red', detail: String(log.output_data.error ?? '') }
        : { icon: '✓', title: tool, tone: 'green', detail: log.reason }
    case 'APPROVAL_REQUESTED':
      return { icon: '✋', title: `사용자 승인 요청 · ${tool}`, tone: 'amber', detail: log.reason }
    case 'APPROVAL_DECIDED':
      return log.status === 'APPROVED'
        ? { icon: '👍', title: '사용자가 승인함', tone: 'green' }
        : { icon: '🚫', title: '사용자가 취소함', tone: 'red', detail: String(log.output_data.comment ?? '') }
    case 'STATUS_CHANGED': {
      const to = STATUS_META[log.input_data.to as TaskStatus]?.label ?? String(log.input_data.to)
      const from = STATUS_META[log.input_data.from as TaskStatus]?.label ?? String(log.input_data.from)
      return { icon: '⇄', title: `업무 상태 ${from} → ${to}`, tone: 'violet', detail: log.reason }
    }
    case 'EVENT_RECEIVED':
      return { icon: '📨', title: '새 이벤트', tone: 'blue', detail: log.reason }
    case 'SECURITY_WARNING':
      return { icon: '🛡️', title: '보안 경고 · 의심 지시문 무시', tone: 'red', detail: `${log.reason}\n${(log.output_data.patterns as string[] | undefined)?.join(', ') ?? ''}` }
    case 'SECURITY_BLOCK':
      return { icon: '🛡️', title: '보안 정책으로 차단', tone: 'red', detail: log.reason }
    case 'TOOL_BLOCKED':
      return { icon: '⛔', title: '허용되지 않은 도구 호출 차단', tone: 'red', detail: log.reason }
    case 'STEP_LIMIT':
    case 'TIMEOUT':
      return { icon: '⚠️', title: 'Agent 실행 중단 (안전 제한)', tone: 'red', detail: log.reason }
    case 'LLM_WAIT':
      return { icon: '⏳', title: 'LLM 사용 한도 대기', tone: 'amber', detail: log.reason }
    case 'AGENT_FAILED':
      return { icon: '⚠️', title: 'Agent 오류', tone: 'red', detail: log.reason }
    case 'AGENT_FINISHED':
      return { icon: '■', title: 'Agent 실행 종료', tone: 'gray', detail: log.reason }
    default:
      return { icon: '•', title: log.action_type, tone: 'gray', detail: log.reason }
  }
}

export default function Timeline({ logs, live }: { logs: ActionLog[]; live?: string }) {
  // A status change is shown once via its STATUS_CHANGED entry (logged just before the tool call);
  // a final thought is repeated by AGENT_FINISHED.
  const items = logs
    .filter((l, i) => {
      const prev = logs[i - 1]
      if (l.action_type === 'TOOL_CALL' && l.tool_name === 'update_task_status' && prev?.action_type === 'STATUS_CHANGED') return false
      const next = logs[i + 1]
      return !(l.action_type === 'AGENT_THOUGHT' && next?.action_type === 'AGENT_FINISHED' && next.reason === l.reason)
    })
    .reverse()
  return (
    <ol className="timeline">
      {live && (
        <li className="tl-item tl-live">
          <span className="tl-icon tl-blue">
            <span className="spinner" />
          </span>
          <div className="tl-body">
            <div className="tl-title">{live}</div>
          </div>
        </li>
      )}
      {items.map((log) => {
        const v = view(log)
        const hasData = Object.keys(log.input_data).length > 0 || Object.keys(log.output_data).length > 0
        return (
          <li key={log.id} className={`tl-item tl-${v.tone}`}>
            <span className={`tl-icon tl-${v.tone}`}>{v.icon}</span>
            <div className="tl-body">
              <div className="tl-head">
                <span className="tl-title">{v.title}</span>
                <span className="tl-time">{time(log.created_at)}</span>
              </div>
              {v.detail && <div className={`tl-detail ${v.tone === 'thought' ? 'thought' : ''}`}>{v.detail}</div>}
              <div className="tl-meta">
                {log.tool_name && PERMISSIONS[log.tool_name] && <span className="perm">{PERMISSIONS[log.tool_name]}</span>}
                {log.risk_level === 'HIGH' && <span className="perm perm-red">위험도 HIGH</span>}
                {hasData && log.action_type !== 'AGENT_THOUGHT' && (
                  <details>
                    <summary>입력 / 결과 보기</summary>
                    <pre>{JSON.stringify({ input: log.input_data, output: log.output_data }, null, 2)}</pre>
                  </details>
                )}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
