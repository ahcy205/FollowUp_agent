import { time } from '../format'
import type { ActionLog } from '../types'

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
  switch (log.action_type) {
    case 'TASK_CANCELLED':
      return { title: '업무 취소', tone: 'gray', detail: log.reason }
    case 'SECURITY_WARNING': {
      const patterns = (log.output_data.patterns as string[] | undefined)?.join(', ')
      return { title: '보안 경고 · 메일 속 지시를 따르지 않음', tone: 'red', detail: patterns ? `${log.reason}\n탐지한 문구: ${patterns}` : log.reason }
    }
    default: {
      const label = MAIL_LABELS[log.action_type]
      return label ? { title: label[0], tone: label[1], detail: log.reason } : { title: log.reason || log.action_type, tone: 'gray' }
    }
  }
}

export default function Timeline({ logs }: { logs: ActionLog[] }) {
  const items = [...logs].reverse()
  return (
    <ol className="timeline">
      {items.map((log) => {
        const v = view(log)
        return (
          <li key={log.id} className={`tl-item tl-${v.tone}`}>
            <span className={`tl-mark tl-${v.tone}`} aria-hidden="true" />
            <div className="tl-body">
              <div className="tl-head">
                <span className="tl-title">{v.title}</span>
                <span className="tl-time">{time(log.created_at)}</span>
              </div>
              {v.detail && <div className="tl-detail">{v.detail}</div>}
              {log.risk_level === 'HIGH' && <span className="flag flag-red tl-risk">위험도 높음</span>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
