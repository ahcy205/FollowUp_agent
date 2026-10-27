import { useEffect, useState } from 'react'
import { parseDate } from '../../format'
import type { Email, MailAct, ReplyDraft, TaskDetail } from '../../types'

const UNKNOWN_CONFIRM_MS = 5 * 60_000 // backend reply_send.UNKNOWN_CONFIRM_AFTER
const PLACEHOLDER_RE = /\[\[[^[\]]{1,80}\]\]/g // backend draft_checks.PLACEHOLDER_RE

function placeholdersOf(body: string): string[] {
  return body.match(PLACEHOLDER_RE) ?? []
}

/** "Re: Re:  제목" → "제목". 제목 비교용. */
function bareSubject(subject: string) {
  let s = subject.replace(/\s+/g, ' ').trim()
  while (s.slice(0, 3).toLowerCase() === 're:') s = s.slice(3).trim()
  return s
}

/** 답장이 이어질 스레드의 제목: 가장 최근에 받은 메일 제목 (backend reply_draft._subject와 같은 기준). */
function threadSubject(emails: Email[]): string {
  const inbound = emails.filter((e) => e.direction === 'INBOUND')
  if (inbound.length === 0) return ''
  const latest = inbound.reduce((a, b) => (parseDate(b.created_at) > parseDate(a.created_at) ? b : a))
  return bareSubject(latest.subject)
}

function snapshot(d: ReplyDraft) {
  return JSON.stringify([d.subject, d.body, [...d.cc_selected].sort()])
}

type Step = 'edit' | 'confirm-send' | 'confirm-delete'

/**
 * 답장 초안 카드 (설계 3.1 · 3.2). 받는 사람은 서버가 정하고 여기서는 고칠 수 없다.
 * 고친 내용은 화면에만 있다가 "저장" 또는 "보내기"를 누를 때 서버에 저장한다.
 * 서버 초안이 바뀌면(다시 만들기 · 다른 탭) 고치지 않은 화면만 새 값으로 바꾼다.
 */
export default function ReplyDraftCard({
  data,
  draft,
  act,
  onRecompose,
}: {
  data: TaskDetail
  draft: ReplyDraft
  act: MailAct
  onRecompose: (purpose: 'reply' | 'followup') => void
}) {
  const [subject, setSubject] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  const [cc, setCc] = useState<string[]>(draft.cc_selected)
  const [base, setBase] = useState(() => snapshot(draft))
  const [step, setStep] = useState<Step>('edit')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [now, setNow] = useState(() => Date.now())

  const local = JSON.stringify([subject, body, [...cc].sort()])
  const dirty = local !== base
  const server = snapshot(draft)
  if (server !== base) {
    // 서버 초안이 바뀌었다. 고치던 내용이 없으면 새 초안을 보여준다.
    setBase(server)
    if (!dirty) {
      setSubject(draft.subject)
      setBody(draft.body)
      setCc(draft.cc_selected)
    }
  }

  // 결과 불분명: 5분이 되면 "보낸편지함 확인" 단계로 넘어가도록 한 번 다시 그린다.
  const unknownAt = draft.status === 'unknown' && draft.unknown_since ? parseDate(draft.unknown_since).getTime() : null
  useEffect(() => {
    if (unknownAt == null) return
    const wait = unknownAt + UNKNOWN_CONFIRM_MS - Date.now()
    const t = setTimeout(() => setNow(Date.now()), wait > 0 ? wait + 1000 : 0) // 0: 화면을 열기 전에 이미 5분이 지났다
    return () => clearTimeout(t)
  }, [unknownAt])

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError('')
    try {
      await fn()
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    } finally {
      setBusy('')
    }
  }

  // 서버가 다듬은 값(앞뒤 공백 등)으로 화면을 맞춘다. 그래야 저장 뒤에 "고친 내용 있음"으로 남지 않는다.
  const save = async () => {
    const d = await act('/reply-draft/update', { subject: subject.trim(), body, cc_selected: cc })
    const saved = d.mail?.draft
    if (saved) {
      setSubject(saved.subject)
      setBody(saved.body)
      setCc(saved.cc_selected)
      setBase(snapshot(saved))
    }
  }

  const startSend = async () => {
    if (dirty && !(await run('save', save))) return
    setStep('confirm-send')
  }

  const send = async () => {
    await run('send', () => act('/reply-draft/send'))
    setStep('edit')
  }

  const remove = async () => {
    if (await run('delete', () => act('/reply-draft/delete'))) setStep('edit')
  }

  const editable = draft.status === 'draft'
  const blanks = placeholdersOf(body)
  const base_ = threadSubject(data.emails)
  const subjectDrift = editable && base_ !== '' && !bareSubject(subject).startsWith(base_)
  const w = draft.warnings
  // 초안을 만들거나 되돌린 뒤 보낸 답장이 확인됐다. 서버도 보내기를 409로 막는다.
  const replyAt = data.mail?.reply_sent_at
  const sentLater = replyAt != null && parseDate(replyAt).getTime() > parseDate(draft.updated_at).getTime()
  const confirmable = unknownAt != null && now >= unknownAt + UNKNOWN_CONFIRM_MS

  if (draft.status === 'sent') {
    // 보낸 직후(답장 보냄 상태)에만 알린다. 상대가 다시 답장해 처리할 일로 오면 감춘다.
    return data.task.status === 'WAITING' ? (
      <div className="alert alert-green">답장을 보냈습니다. 보낸 메일은 아래 원본 메일에서 볼 수 있습니다.</div>
    ) : null
  }

  return (
    <section className="card reply-card">
      <div className="section-head">
        <h2>답장 초안</h2>
        {draft.purpose === 'followup' && <span className="tag tag-amber">다시 연락</span>}
      </div>

      <dl className="draft-fields">
        <div>
          <dt>받는 사람</dt>
          <dd>{draft.to_addr}</dd>
        </div>
        {draft.cc_candidates.length > 0 && (
          <div>
            <dt>참조</dt>
            <dd className="draft-cc">
              {draft.cc_candidates.map((addr) => (
                <label key={addr} className="draft-cc-item">
                  <input
                    type="checkbox"
                    checked={cc.includes(addr)}
                    disabled={!editable || !!busy}
                    onChange={(e) => setCc(e.target.checked ? [...cc, addr] : cc.filter((c) => c !== addr))}
                  />
                  {addr}
                </label>
              ))}
            </dd>
          </div>
        )}
      </dl>

      <label className="draft-label">
        제목
        <input value={subject} maxLength={300} disabled={!editable || !!busy} onChange={(e) => setSubject(e.target.value)} />
      </label>
      {subjectDrift && <div className="muted small text-amber">제목을 바꾸면 Gmail에서 다른 대화로 보일 수 있습니다.</div>}

      <label className="draft-label">
        본문
        <textarea className="draft-body" rows={12} maxLength={20000} value={body} disabled={!editable || !!busy} onChange={(e) => setBody(e.target.value)} />
      </label>

      {blanks.length > 0 && (
        <div className="draft-blanks">
          <b className="small">남은 빈칸 {blanks.length}개</b>
          <ul>
            {blanks.map((b, i) => (
              <li key={i}>
                <mark>{b}</mark>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(w.unsupported.length > 0 || w.outside_contacts.length > 0 || w.suspicious.length > 0) && (
        <div className="draft-warnings">
          {w.suspicious.length > 0 && (
            <div className="alert alert-red">
              <b>보안 경고</b> — 메일 속 지시를 따르지 않았습니다: {w.suspicious.join(', ')}
            </div>
          )}
          {w.unsupported.length > 0 && (
            <div className="alert alert-amber">
              <b>근거를 찾지 못한 내용</b> — {w.unsupported.join(', ')}. 맞는지 확인해 주세요.
            </div>
          )}
          {w.outside_contacts.length > 0 && (
            <div className="alert alert-amber">
              <b>스레드 밖 연락처</b> — {w.outside_contacts.join(', ')}. 이 대화에 없던 주소 · 전화번호입니다.
            </div>
          )}
          {dirty && <div className="muted small">경고는 저장한 내용 기준입니다. 저장하면 다시 확인합니다.</div>}
        </div>
      )}

      {draft.status === 'sending' && <div className="alert alert-slate">보내는 중입니다…</div>}

      {draft.status === 'unknown' && !confirmable && (
        <div className="alert alert-slate">
          <b>발송 여부를 확인하는 중입니다</b>
          <div className="small">Gmail 보낸편지함과 맞춰 보고 있습니다. 확인되면 자동으로 바뀝니다.</div>
        </div>
      )}

      {draft.status === 'unknown' && confirmable && (
        <div className="alert alert-amber">
          <b>Gmail 보낸편지함을 확인해 주세요</b>
          <div className="draft-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => run('sent', () => act('/reply-draft/confirm', { sent: true }))}>
              {busy === 'sent' ? '요청 중…' : '보내진 걸 확인했어요'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('not-sent', () => act('/reply-draft/confirm', { sent: false }))}>
              {busy === 'not-sent' ? '요청 중…' : '안 보내졌어요'}
            </button>
          </div>
        </div>
      )}

      {editable && sentLater && (
        <div className="alert alert-amber">이미 답장을 보낸 것으로 확인됐습니다. 초안을 지우거나 다시 만들어 주세요.</div>
      )}

      {editable && step === 'confirm-send' && !sentLater && (
        <div className="inline-panel draft-confirm">
          <span>
            <b>{draft.to_addr}</b>에게 보냅니다 · 참조 {cc.length}명
          </span>
          <div className="draft-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={send}>
              {busy === 'send' ? '보내는 중…' : '정말 보내기'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => setStep('edit')}>
              취소
            </button>
          </div>
        </div>
      )}

      {editable && step === 'confirm-delete' && (
        <div className="inline-panel draft-confirm">
          <span>이 초안을 지울까요?</span>
          <div className="draft-actions">
            <button type="button" className="btn btn-primary btn-sm" disabled={!!busy} onClick={remove}>
              {busy === 'delete' ? '지우는 중…' : '지우기'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => setStep('edit')}>
              취소
            </button>
          </div>
        </div>
      )}

      {editable && step === 'edit' && (
        <div className="draft-actions">
          <button type="button" className="btn btn-primary btn-sm" disabled={!!busy || blanks.length > 0 || sentLater} onClick={startSend}>
            {busy === 'save' ? '저장 중…' : '보내기'}
          </button>
          {blanks.length > 0 && <span className="muted small">빈칸 {blanks.length}개를 채워야 보낼 수 있습니다</span>}
          {dirty && (
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run('save', save)}>
              저장
            </button>
          )}
          <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => onRecompose(draft.purpose)}>
            다시 만들기
          </button>
          <button type="button" className="btn btn-link btn-sm" disabled={!!busy} onClick={() => setStep('confirm-delete')}>
            초안 지우기
          </button>
        </div>
      )}

      {error && <div className="alert alert-red">{error}</div>}
    </section>
  )
}
