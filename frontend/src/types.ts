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
  created_at: string
  updated_at: string
  completed_at: string | null
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
}

export interface Dashboard {
  counts: Record<TaskStatus, number>
  tasks: Task[]
  pending_approvals: number
  agent_configured: boolean
  model: string
}
