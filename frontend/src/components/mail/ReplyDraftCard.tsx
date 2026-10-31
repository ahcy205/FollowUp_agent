import { useEffect, useState } from 'react'
import { parseDate } from '../../format'
import type { DraftSourceChunk, Email, MailAct, ReplyDraft, TaskDetail } from '../../types'

const UNKNOWN_CONFIRM_MS = 5 * 60_000 // backend reply_send.UNKNOWN_CONFIRM_AFTER
// backend reply_send.NEED_INTERNAL_ACK: 초안 뒤에 생긴 내부용 값을 서버가 보내기 직전에 찾았다
const NEED_INTERNAL_ACK = "내부용 자료의 값이 들어 있습니다. 내용을 확인하고 '알고 보냅니다'를 체크해 주세요."
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

/** "8000원" → "8,000원". 서버는 내부용 값을 천 단위 구분 없이 저장한다.
 * 0으로 시작하는 수(품번 등) · 소수부 · "-"로 이은 번호("010-1234", "1234-5678")는 그대로 둔다. */
function displayValue(value: string) {
  return value.replace(/(?<![\d.,-])[1-9]\d{3,}(?![\d-])/g, (n) => Number(n).toLocaleString('ko-KR'))
}

/** 자료 올린 날짜 "10/20" */
function uploadedMd(iso: string | undefined) {
  if (!iso) return '날짜 모름'
  const d = parseDate(iso)
  return Number.isNaN(d.getTime()) ? '날짜 모름' : `${d.getMonth() + 1}/${d.getDate()}`
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
  // 확인 단계는 그 단계를 연 때의 서버 초안(updated_at)에만 걸린다. 초안이 바뀌면 편집으로 돌아간다.
  const [stepState, setStepState] = useState<{ step: Step; at: string }>({ step: 'edit', at: draft.updated_at })
  const step: Step = stepState.at === draft.updated_at ? stepState.step : 'edit'
  const setStep = (s: Step, at: string = draft.updated_at) => setStepState({ step: s, at })
  // "알고 보냅니다" 체크는 체크한 때의 서버 초안에만 걸린다. 초안이 바뀌거나 확인 단계를 다시 열면 풀린다.
  const [ack, setAck] = useState<{ at: string; on: boolean }>({ at: '', on: false })
  const acked = ack.on && ack.at === draft.updated_at
  // 서버가 보내기 직전에 내부용 값을 찾아 막은 초안(updated_at). 저장된 경고에 없어도 체크를 보인다.
  const [serverNeedsAckAt, setServerNeedsAckAt] = useState('')
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
    return saved
  }

  const startSend = async () => {
    let at = draft.updated_at
    if (dirty) {
      let saved: ReplyDraft | null | undefined
      if (!(await run('save', async () => (saved = await save())))) return
      if (saved) at = saved.updated_at
    }
    setAck({ at: '', on: false })
    setStep('confirm-send', at)
  }

  const send = async () => {
    let missingAck = false
    const at = draft.updated_at
    await run('send', () =>
      // 확인(체크 포함)은 화면이 본 그 버전에만 걸린다: 그사이 초안이 바뀌면 서버가 409로 막는다
      act('/reply-draft/send', { expected_updated_at: at, ...(needsAck ? { internal_ack: acked } : {}) }).catch((e: Error) => {
        missingAck = e.message === NEED_INTERNAL_ACK
        throw e
      }),
    )
    setAck({ at: '', on: false })
    if (missingAck) {
      setServerNeedsAckAt(at) // 확인 단계를 연 채로 두고 체크를 보인다
      return
    }
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
  const src = draft.sources ?? {}
  const chunks = src.chunks ?? []
  const facts = src.facts ?? []
  const internal = src.internal_values ?? []
  const sourcesChanged = src.sources_changed === true
  const needsAck = internal.length > 0 || (w.internal_values ?? 0) > 0 || serverNeedsAckAt === draft.updated_at
  const chunkByRef = new Map<string, DraftSourceChunk>(chunks.map((c) => [c.ref, c]))
  const usedRefs = new Set(facts.map((f) => f.ref))
  // 초안을 만들거나 되돌린 뒤 보낸 답장이 확인됐다. 서버도 보내기를 409로 막는다.
  const replyAt = data.mail?.reply_sent_at
  const sentLater = draft.replied_after === true
    || (replyAt != null && parseDate(replyAt).getTime() > parseDate(draft.updated_at).getTime())
  const confirmable = unknownAt != null && now >= unknownAt + UNKNOWN_CONFIRM_MS

  if (draft.status === 'sent') {
    // 보낸 직후(답장 보냄 상태)에만 알린다. 상대가 다시 답장해 처리할 일로 오면 감춘다.
    return data.task.status === 'WAITING' ? (
      <div className="sent-note">
        <span className="stamp stamp-press" aria-hidden="true">
          보냄
        </span>
        <div>
          <b>답장을 보냈어요</b>
          <div className="muted small">상대 답을 기다립니다. 보낸 메일은 아래 원본 메일에서 볼 수 있어요.</div>
        </div>
      </div>
    ) : null
  }

  return (
    <section className="card reply-card" id="reply">
      <div className="section-head">
        <h2>답장</h2>
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
                    disabled={!editable || !!busy || step !== 'edit'}
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
        <input value={subject} maxLength={300} disabled={!editable || !!busy || step !== 'edit'} onChange={(e) => setSubject(e.target.value)} />
      </label>
      {subjectDrift && <div className="muted small text-amber">제목을 바꾸면 Gmail에서 다른 대화로 보일 수 있습니다.</div>}

      <label className="draft-label">
        본문
        <textarea className="draft-body" rows={12} maxLength={20000} value={body} disabled={!editable || !!busy || step !== 'edit'} onChange={(e) => setBody(e.target.value)} />
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

      {facts.length > 0 && (
        <div className="draft-facts">
          <b className="small">자료에서 가져온 내용 {facts.length}개</b>
          <ul>
            {facts.map((f, i) => {
              const c = chunkByRef.get(f.ref)
              return (
                <li key={i} className="small">
                  <b>{f.text}</b> — {c ? `${c.title} (${uploadedMd(c.uploaded_at)} 올림)` : '자료'} · <q className="muted">{f.line_text}</q>
                  {f.other_item && <span className="tag tag-amber">다른 품목의 값일 수 있습니다</span>}
                  {f.checked === 'manual' && <span className="tag tag-amber">직접 확인 필요</span>}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {chunks.length > 0 && (
        <details className="draft-sources">
          <summary className="small">참고한 자료 {chunks.length}개</summary>
          <ul>
            {chunks.map((c) => (
              <li key={c.ref} className={`small${usedRefs.has(c.ref) ? '' : ' draft-source-unused'}`}>
                {c.title} ({uploadedMd(c.uploaded_at)} 올림){usedRefs.has(c.ref) ? '' : ' · 쓰이지 않음'}
              </li>
            ))}
          </ul>
        </details>
      )}

      {src.search_failed && <div className="muted small">자료를 찾지 못해 메모와 입력만으로 만들었습니다</div>}

      {(w.unsupported.length > 0 || w.outside_contacts.length > 0 || w.suspicious.length > 0 || needsAck || sourcesChanged) && (
        <div className="draft-warnings">
          {sourcesChanged && (
            <div className="alert alert-amber">
              <b>참고한 자료가 바뀌었습니다</b> — 초안을 다시 만들어 주세요
            </div>
          )}
          {needsAck && (
            <div className="alert alert-red">
              <b>내부용 자료에 있는 값</b>
              {internal.length > 0 ? (
                <ul className="draft-internal">
                  {internal.map((v, i) => (
                    <li key={i}>
                      {displayValue(v.value)} — {v.titles.join(', ')}
                    </li>
                  ))}
                </ul>
              ) : (
                <> — 보내기 전에 내용을 확인해 주세요.</>
              )}
            </div>
          )}
          {w.suspicious.length > 0 && (
            <div className="alert alert-red">
              <b>보안 경고</b> — 메일 · 자료 속 지시를 따르지 않았습니다: {w.suspicious.join(', ')}
            </div>
          )}
          {w.unsupported.length > 0 && (
            <div className="alert alert-amber">
              <b>근거를 찾지 못한 값</b>{' '}
              {w.unsupported.map((v) => (
                <span key={v} className="value-chip">
                  {v}
                </span>
              ))}{' '}
              메모나 자료에 없는 값이에요. 맞는지 확인해 주세요.
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
          <b className="confirm-title">보내기 전에 한 번 더 확인해 주세요</b>
          <dl className="confirm-grid">
            <div>
              <dt>받는 사람</dt>
              <dd>{draft.to_addr}</dd>
            </div>
            <div>
              <dt>참조</dt>
              <dd>{draft.cc_selected.length > 0 ? draft.cc_selected.join(', ') : '없음'}</dd>
            </div>
            <div>
              <dt>제목</dt>
              <dd>{draft.subject}</dd>
            </div>
            {w.unsupported.length > 0 && (
              <div className="confirm-warn">
                <dt>확인 안 된 값</dt>
                <dd>
                  {w.unsupported.map((v) => (
                    <span key={v} className="value-chip">
                      {v}
                    </span>
                  ))}
                  <span className="small"> 메모 · 자료에 근거가 없는 값이에요. 맞는지 확인했나요?</span>
                </dd>
              </div>
            )}
          </dl>
          {needsAck && (
            <label className="check-row small text-red">
              <input
                type="checkbox"
                checked={acked}
                disabled={!!busy}
                onChange={(e) => setAck({ at: draft.updated_at, on: e.target.checked })}
              />
              내부용 자료의 값이 들어 있습니다 — 알고 보냅니다
            </label>
          )}
          <div className="draft-actions">
            <button
              type="button"
              className="btn btn-seal"
              disabled={!!busy || dirty || blanks.length > 0 || sentLater || sourcesChanged || (needsAck && !acked)}
              onClick={send}
            >
              {busy === 'send' ? '보내는 중…' : '이대로 보내기'}
            </button>
            <button type="button" className="btn btn-ghost" disabled={!!busy} onClick={() => setStep('edit')}>
              고치러 돌아가기
            </button>
          </div>
        </div>
      )}

      {editable && step === 'confirm-delete' && (
        <div className="inline-panel draft-confirm">
          <span>이 초안을 지울까요?</span>
          <div className="draft-actions">
            <button type="button" className="btn btn-danger btn-sm" disabled={!!busy} onClick={remove}>
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
          <button type="button" className="btn btn-seal btn-sm" disabled={!!busy || blanks.length > 0 || sentLater || sourcesChanged} onClick={startSend}>
            {busy === 'save' ? '저장 중…' : '보내기'}
          </button>
          {blanks.length > 0 && <span className="muted small">빈칸 {blanks.length}개를 채워야 보낼 수 있습니다</span>}
          {sourcesChanged && <span className="muted small">초안을 다시 만들어야 보낼 수 있습니다</span>}
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
