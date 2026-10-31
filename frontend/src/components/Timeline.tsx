import { PERMISSIONS, STATUS_META, time, TOOL_LABELS } from '../format'
import type { ActionLog, TaskStatus } from '../types'

interface ItemView {
  title: string
  tone: string
  detail?: string
}

// 메일 업무 기록 (백엔드 mail/* · knowledge/* 의 action_type). [제목, 색]
const MAIL_LABELS: Record<string, [string, string]> = {
  MAIL_INTAKE: ['업무 메일로 접수', 'blue'],
  MAIL_ATTACHED: ['같은 스레드의 새 메일', 'blue'],
  EMAIL_PROMOTED: ['업무로 처리하기로 함', 'blue'],
  TASK_CHANGED: ['상대 답장으로 내용 바뀜', 'blue'],
  CHANGES_FAILED: ['바뀐 내용 분석 실패', 'red'],
  REANALYZE_REQUESTED: ['다시 분석 요청', 'gray'],
  DEADLINE_CHANGED: ['기한 바꿈', 'blue'],
  PRIORITY_CHANGED: ['중요도 바꿈', 'blue'],
  TODO_CHANGED: ['할 일 고침', 'gray'],
  TODOS_COMPLETED: ['남은 할 일 모두 완료', 'green'],
  TASK_SNOOZED: ['나중에 보기로 미룸', 'gray'],
  TASK_UNSNOOZED: ['다시 보기', 'gray'],
  TASK_MERGED: ['다른 업무와 합침', 'blue'],
  TASK_UNMERGED: ['합치기 되돌림', 'gray'],
  MERGE_ANALYSIS_FALLBACK: ['합친 내용 기본 규칙으로 정리', 'amber'],
  TASK_CLOSED: ['답장 없이 닫음', 'green'],
  REPLY_DRAFT_CREATED: ['답장 초안 만듦', 'blue'],
  REPLY_DRAFT_UPDATED: ['답장 초안 고침', 'gray'],
  REPLY_DRAFT_DELETED: ['답장 초안 지움', 'gray'],
  REPLY_SENT: ['답장 보냄', 'green'],
  REPLY_SEND_FAILED: ['답장 보내기 실패', 'red'],
  REPLY_SEND_UNKNOWN: ['발송 결과 확인 중', 'amber'],
  REPLY_SEND_NOT_SENT: ['보내지 않은 것으로 확인', 'amber'],
  NO_REPLY_ASKED: ['상대 답 없음 알림', 'amber'],
  NO_REPLY_WAIT: ['더 기다리기로 함', 'gray'],
  CALENDAR_DELETED_BY_USER: ['캘린더에서 일정이 지워짐', 'gray'],
  KNOWLEDGE_ADDED: ['자료 올림', 'gray'],
}

function view(log: ActionLog): ItemView {
  const tool = TOOL_LABELS[log.tool_name] ?? log.tool_name
  switch (log.action_type) {
    case 'AGENT_STARTED':
      return { title: String(log.reason || 'Agent 시작'), tone: 'blue' }
    case 'AGENT_THOUGHT':
      return { title: 'Agent 판단', tone: 'thought', detail: log.reason }
    case 'TOOL_CALL':
      return log.status === 'ERROR'
        ? { title: `${tool} 실패`, tone: 'red', detail: String(log.output_data.error ?? '') }
        : { title: tool, tone: 'green', detail: log.reason }
    case 'APPROVAL_REQUESTED':
      return { title: `사용자 승인 요청 · ${tool}`, tone: 'amber', detail: log.reason }
    case 'APPROVAL_DECIDED':
      if (log.status !== 'APPROVED') return { title: '사용자가 취소함', tone: 'red', detail: String(log.output_data.comment ?? '') }
      return log.output_data.edited
        ? { title: '사용자가 내용을 수정한 뒤 승인함', tone: 'green', detail: '수정 전 내용은 아래 "입력 / 결과 보기"에서 확인할 수 있습니다.' }
        : { title: '사용자가 승인함', tone: 'green' }
    case 'STATUS_CHANGED': {
      const to = STATUS_META[log.input_data.to as TaskStatus]?.label ?? String(log.input_data.to)
      const from = STATUS_META[log.input_data.from as TaskStatus]?.label ?? String(log.input_data.from)
      return { title: `업무 상태 ${from} → ${to}`, tone: 'blue', detail: log.reason }
    }
    case 'TASK_CANCELLED':
      return { title: '사용자가 업무를 취소함', tone: 'gray', detail: log.reason }
    case 'GOAL_UPDATED':
      return { title: '사용자가 목표를 수정함', tone: 'blue', detail: `변경 전: ${String(log.input_data.from ?? '')}\n변경 후: ${String(log.input_data.to ?? '')}` }
    case 'USER_COMPLETED':
      return { title: '사용자가 완료 처리', tone: 'green', detail: log.reason }
    case 'FOLLOWUP_COMPLETED':
      return { title: '후속 업무 완료', tone: 'green', detail: log.reason }
    case 'EVENT_RECEIVED':
      return { title: '새 이벤트', tone: 'blue', detail: log.reason }
    case 'SECURITY_WARNING':
      return { title: '보안 경고 · 의심 지시문 무시', tone: 'red', detail: `${log.reason}\n${(log.output_data.patterns as string[] | undefined)?.join(', ') ?? ''}` }
    case 'SECURITY_BLOCK':
      return { title: '보안 정책으로 차단', tone: 'red', detail: log.reason }
    case 'TOOL_BLOCKED':
      return { title: '허용되지 않은 도구 호출 차단', tone: 'red', detail: log.reason }
    case 'STEP_LIMIT':
    case 'TIMEOUT':
      return { title: 'Agent 실행 중단 (안전 제한)', tone: 'red', detail: log.reason }
    case 'LLM_RETRY':
      return { title: 'LLM 일시 오류 · 자동 재시도', tone: 'amber', detail: log.reason }
    case 'LLM_WAIT':
      return { title: 'LLM 사용 한도 대기', tone: 'amber', detail: log.reason }
    case 'AGENT_FAILED':
      return { title: 'Agent 오류', tone: 'red', detail: log.reason }
    case 'AGENT_FINISHED':
      return { title: 'Agent 실행 종료', tone: 'gray', detail: log.reason }
    default: {
      const label = MAIL_LABELS[log.action_type]
      return label
        ? { title: label[0], tone: label[1], detail: log.reason }
        : { title: log.reason || log.action_type, tone: 'gray' }
    }
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
          <span className="tl-mark tl-blue">
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
            <span className={`tl-mark tl-${v.tone}`} aria-hidden="true" />
            <div className="tl-body">
              <div className="tl-head">
                <span className="tl-title">{v.title}</span>
                <span className="tl-time">{time(log.created_at)}</span>
              </div>
              {v.detail && <div className={`tl-detail ${v.tone === 'thought' ? 'thought' : ''}`}>{v.detail}</div>}
              <div className="tl-meta">
                {log.tool_name && PERMISSIONS[log.tool_name] && <span className="perm">{PERMISSIONS[log.tool_name]}</span>}
                {log.risk_level === 'HIGH' && <span className="perm perm-red">위험도 HIGH</span>}
                {hasData && log.action_type !== 'AGENT_THOUGHT' && !(log.action_type in MAIL_LABELS) && (
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
