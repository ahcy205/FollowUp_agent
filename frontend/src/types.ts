export type TaskStatus = 'PENDING' | 'IN_PROGRESS' | 'WAITING' | 'ACTION_REQUIRED' | 'COMPLETED' | 'BLOCKED' | 'CANCELLED'

export interface User {
  id: number
  email: string
}

export interface PlanStep {
  id: number
  title: string
  done: boolean
  evidence: string
  due_at?: string | null
  source?: 'agent' | 'user'
  due_note?: string
  merged_from?: number
}

export type Priority = 'HIGH' | 'NORMAL' | 'LOW'

// 메일 업무의 분석 결과(task.brief). 다른 업무는 빈 객체다.
export interface MailBrief {
  analysis?: 'pending' | 'done' | 'failed'
  email_id?: number
  summary?: string
  requests?: string[]
  counterpart?: { name?: string; organization?: string }
  needs_reply?: boolean
  priority?: string
  deadline_note?: string
}

export interface Task {
  id: number
  title: string
  goal: string
  status: TaskStatus
  current_step: string
  next_action: string
  waiting_for: string
  priority: string
  origin: string
  agent_enabled: boolean
  parent_task_id: number | null
  customer_id: number | null
  plan: PlanStep[]
  result_summary: string
  brief: MailBrief
  deadline_at: string | null
  deadline_all_day: boolean
  deadline_kind: string
  deadline_source: string
  snoozed_until: string | null
  priority_source: string
  merged_into_id: number | null
  created_at: string
  updated_at: string
  completed_at: string | null
  calendar_state: 'none' | 'pending' | 'synced' | 'deleted_by_user' | 'error'
  calendar_event_url: string
  calendar_error: string
}

export interface Customer {
  id: number
  company_name: string
  contact_name: string
  email: string
  phone: string
  meta: Record<string, string>
}

export interface Document {
  id: number
  customer_id: number | null
  title: string
  content: string
  document_type: string
  created_at: string
}

export interface Email {
  id: number
  customer_id: number | null
  task_id: number | null
  sender: string
  receiver: string
  subject: string
  content: string
  direction: 'INBOUND' | 'OUTBOUND' | 'DRAFT'
  processed: boolean
  created_at: string
  sent_at: string | null
}

export interface ActionLog {
  id: number
  run_id: number | null
  action_type: string
  tool_name: string
  input_data: Record<string, unknown>
  output_data: Record<string, unknown>
  reason: string
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH'
  status: string
  created_at: string
}

export interface Approval {
  id: number
  task_id: number
  tool_name: string
  tool_input: Record<string, unknown>
  reason: string
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED'
  user_comment: string
  created_at: string
  decided_at: string | null
  email: Email | null
}

export interface TokenUsage {
  input?: number
  output?: number
  cache_read?: number
  total?: number
  turns?: number
}

export interface Run {
  id: number
  trigger: string
  status: 'RUNNING' | 'AWAITING_APPROVAL' | 'FINISHED' | 'STOPPED' | 'FAILED'
  steps: number
  usage: TokenUsage
  final_message: string
  error: string
  created_at: string
  finished_at: string | null
}

export interface TaskDetail {
  task: Task
  customer: Customer | null
  parent: Task | null
  followups: Task[]
  emails: Email[]
  logs: ActionLog[]
  approvals: Approval[]
  runs: Run[]
  active_run: Run | null
  max_steps: number
  can_simulate_reply: boolean
  mail?: MailExtras | null
}

export interface MailExtras {
  is_snoozed: boolean
  wake_at: string | null
  returned: boolean
  nudge_days: number | null
  merge_candidates: { task_id: number; title: string }[]
  undoable_merges: { source_id: number; title: string }[]
  merged_sources: { task_id: number; title: string; summary: string; merged_at: string | null }[]
  merged_into_id: number | null
  deadline_badge: DeadlineBadge
  priority_source: string
}

// 메일 업무 동작 하나를 보내고 응답(TaskDetail)으로 화면을 바꾼다. 실패하면 ApiError를 던진다.
export type MailAct = (path: string, body?: unknown) => Promise<TaskDetail>

export interface Dashboard {
  counts: Record<TaskStatus, number>
  tasks: Task[]
  pending_approvals: number
  agent_configured: boolean
  model: string
}

export interface GoogleRecentMail {
  subject: string
  sender: string
  received_at: string | null
  triage: 'pending' | 'work' | 'not_work' | 'filtered' | null
  triage_reason: string
  task_id: number | null
}

export interface GoogleStatus {
  enabled: boolean
  status: 'disconnected' | 'connected' | 'reauth_required'
  google_email: string | null
  scopes: string[]
  last_synced_at: string | null
  last_sync_error: string
  mail_count: number
  recent: GoogleRecentMail[]
  triage_counts: Record<'pending' | 'work' | 'not_work' | 'filtered', number>
  triage_enabled: boolean
}

export interface DeadlineBadge {
  text: string
  late: boolean
  estimated: boolean
}

export interface InboxCard {
  task_id: number
  title: string
  summary: string
  sender_name: string
  sender_org: string
  priority: string
  deadline_badge: DeadlineBadge
  todo_done: number
  todo_total: number
  nudge_days: number | null
  returned: boolean
  has_merge_candidates: boolean
  analysis: string
  received_at: string | null
}

export interface SnoozedCard extends InboxCard {
  wake_at: string
}

export interface NotWorkMail {
  email_id: number
  sender: string
  subject: string
  triage: string
  triage_reason: string
  received_at: string | null
}

export interface InboxCounts {
  now: number
  week: number
  later: number
  todo: number
  sent: number
  reference: number
  done: number
}

export interface Inbox {
  counts: InboxCounts
  tabs: {
    todo: { now: InboxCard[]; week: InboxCard[]; later: InboxCard[] }
    sent: InboxCard[]
    reference: InboxCard[]
    done: InboxCard[]
  }
  snoozed: SnoozedCard[]
  not_work: NotWorkMail[]
  triage_enabled: boolean
}

export interface InboxAlert {
  key: string
  kind: string
  task_id: number
  text: string
}

export interface InboxAlerts {
  now_count: number
  alerts: InboxAlert[]
}
