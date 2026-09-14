import { useState } from 'react'
import { api } from '../api'
import type { Approval, Customer } from '../types'

export default function ApprovalCard({ approval, customer, onDecided }: { approval: Approval; customer: Customer | null; onDecided: () => void }) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [comment, setComment] = useState('')
  const [error, setError] = useState('')
  const email = approval.email

  const decide = async (approved: boolean) => {
    setBusy(approved ? 'approve' : 'reject')
    setError('')
    try {
      await api.post(`/approvals/${approval.id}/decide`, { approved, comment })
      onDecided()
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }

  return (
    <section className="card approval-card">
      <div className="approval-head">
        <span className="approval-icon">✋</span>
        <div>
          <h2>AI가 다음 메일을 보내려고 합니다</h2>
          <p className="muted">외부로 나가는 행동은 사용자 승인 없이 실행되지 않습니다. · 사유: {approval.reason}</p>
        </div>
      </div>
      {email ? (
        <div className="mail-preview">
          <div className="mail-row">
            <span>수신자</span>
            <b>
              {customer ? `${customer.company_name} ${customer.contact_name}` : ''} &lt;{email.receiver}&gt;
            </b>
          </div>
          <div className="mail-row">
            <span>보내는 사람</span>
            <span>{email.sender}</span>
          </div>
          <div className="mail-row">
            <span>제목</span>
            <b>{email.subject}</b>
          </div>
          <div className="mail-body">{email.content}</div>
        </div>
      ) : (
        <pre className="code">{JSON.stringify(approval.tool_input, null, 2)}</pre>
      )}
      <input
        className="comment"
        placeholder="(선택) 취소 사유나 Agent에게 전달할 코멘트"
        value={comment}
        maxLength={500}
        onChange={(e) => setComment(e.target.value)}
      />
      {error && <div className="alert alert-red">{error}</div>}
      <div className="approval-actions">
        <button className="btn btn-ghost" onClick={() => decide(false)} disabled={!!busy}>
          {busy === 'reject' ? '취소 처리 중…' : '취소'}
        </button>
        <button className="btn btn-primary" onClick={() => decide(true)} disabled={!!busy}>
          {busy === 'approve' ? '발송 중…' : '승인하고 발송'}
        </button>
      </div>
    </section>
  )
}
