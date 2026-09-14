import { useEffect, useState } from 'react'
import { api } from '../api'
import { dateTime } from '../format'
import type { Customer, Document, Email } from '../types'

type Tab = 'customers' | 'documents' | 'emails'

export default function Workspace() {
  const [tab, setTab] = useState<Tab>('customers')
  const [customers, setCustomers] = useState<Customer[]>([])
  const [documents, setDocuments] = useState<Document[]>([])
  const [emails, setEmails] = useState<Email[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([api.get<Customer[]>('/customers'), api.get<Document[]>('/documents'), api.get<Email[]>('/emails')])
      .then(([c, d, e]) => {
        setCustomers(c)
        setDocuments(d)
        setEmails(e)
      })
      .catch((e) => setError(e.message))
  }, [])

  const company = (id: number | null) => customers.find((c) => c.id === id)?.company_name ?? '—'

  return (
    <div className="stack-lg">
      <div>
        <h1>워크스페이스 데이터</h1>
        <p className="muted">Agent가 Tool로 접근하는 가상 데이터입니다. 모든 데이터는 내 계정에만 속하며 다른 사용자와 분리되어 있습니다.</p>
      </div>
      {error && <div className="alert alert-red">{error}</div>}
      <div className="tabs tabs-inline">
        <button className={tab === 'customers' ? 'active' : ''} onClick={() => setTab('customers')}>
          고객 {customers.length}
        </button>
        <button className={tab === 'documents' ? 'active' : ''} onClick={() => setTab('documents')}>
          문서 {documents.length}
        </button>
        <button className={tab === 'emails' ? 'active' : ''} onClick={() => setTab('emails')}>
          메일함 {emails.length}
        </button>
      </div>

      {tab === 'customers' && (
        <section className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>회사</th>
                <th>담당자</th>
                <th>이메일</th>
                <th>전화</th>
                <th>메모</th>
              </tr>
            </thead>
            <tbody>
              {customers.map((c) => (
                <tr key={c.id}>
                  <td><b>{c.company_name}</b></td>
                  <td>{c.contact_name}</td>
                  <td>{c.email}</td>
                  <td className="nowrap">{c.phone}</td>
                  <td className="muted small">{Object.values(c.meta).join(' · ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {tab === 'documents' && (
        <div className="doc-grid">
          {documents.map((d) => (
            <section key={d.id} className="card doc">
              <div className="section-head">
                <h3>{d.title}</h3>
                <span className="tag">{d.document_type}</span>
              </div>
              <div className="muted small">{company(d.customer_id)}</div>
              <pre className="doc-body">{d.content}</pre>
              {/지시|무시/.test(d.content) && <div className="alert alert-amber small">⚠️ Prompt Injection 시연용 악성 문구가 포함된 문서입니다.</div>}
            </section>
          ))}
        </div>
      )}

      {tab === 'emails' && (
        <section className="card">
          <ul className="mail-thread">
            {emails.map((e) => (
              <li key={e.id} className={`mail mail-${e.direction.toLowerCase()}`}>
                <div className="mail-meta">
                  <span className={`tag ${e.direction === 'INBOUND' ? 'tag-blue' : 'tag-green'}`}>{e.direction === 'INBOUND' ? '받은 메일' : '보낸 메일'}</span>
                  <b>{e.subject}</b>
                  <span className="muted small nowrap">{dateTime(e.created_at)}</span>
                </div>
                <div className="muted small">
                  {e.sender} → {e.receiver} · {company(e.customer_id)}
                </div>
                <div className="mail-body">{e.content}</div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
