import { useState } from 'react'
import { api } from '../api'
import type { Approval, Customer } from '../types'

export default function ApprovalCard({ approval, customer, onDecided }: { approval: Approval; customer: Customer | null; onDecided: () => void }) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [comment, setComment] = useState('')
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)
  const email = approval.email
  const [subject, setSubject] = useState(email?.subject ?? '')
  const [body, setBody] = useState(email?.content ?? '')

  const edited = !!email && (subject.trim() !== email.subject || body.trim() !== email.content)
  const editInvalid = editing && (subject.trim().length < 2 || body.trim().length < 10)

  const decide = async (approved: boolean) => {
    setBusy(approved ? 'approve' : 'reject')
    setError('')
    try {
      const payload: Record<string, unknown> = { approved, comment }
      if (approved && edited) {
        payload.subject = subject.trim()
        payload.body = body.trim()
      }
      await api.post(`/approvals/${approval.id}/decide`, payload)
      onDecided()
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }

  const cancelEdit = () => {
    setSubject(email?.subject ?? '')
    setBody(email?.content ?? '')
    setEditing(false)
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
        <div className={`mail-preview ${editing ? 'editing' : ''}`}>
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
            {editing ? (
              <input value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} aria-label="메일 제목" />
            ) : (
              <b>{subject}</b>
            )}
          </div>
          {editing ? (
            <textarea className="mail-edit" rows={8} maxLength={5000} value={body} onChange={(e) => setBody(e.target.value)} aria-label="메일 본문" />
          ) : (
            <div className="mail-body">{body}</div>
          )}
          <div className="mail-tools">
            {editing ? (
              <>
                <span className="muted small">수신자는 고객 정보에서 서버가 결정하므로 바꿀 수 없습니다.</span>
                <button type="button" className="btn btn-link btn-sm" onClick={cancelEdit}>
                  원래 내용으로
                </button>
              </>
            ) : (
              <>
                {edited && <span className="tag tag-blue">수정됨</span>}
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(true)} disabled={!!busy}>
                  ✎ 내용 수정
                </button>
              </>
            )}
          </div>
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
        <button className="btn btn-primary" onClick={() => decide(true)} disabled={!!busy || editInvalid}>
          {busy === 'approve' ? '발송 중…' : edited ? '수정한 내용으로 발송' : '승인하고 발송'}
        </button>
      </div>
    </section>
  )
}
