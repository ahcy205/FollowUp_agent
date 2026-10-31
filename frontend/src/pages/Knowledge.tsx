import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import Icon from '../components/Icon'
import { dateTime, shortDate } from '../format'
import type { KnowledgeDetail, KnowledgeHit, KnowledgeList, KnowledgeSource, KnowledgeUploadResult } from '../types'

type Visibility = KnowledgeSource['visibility']

const ACCEPT = '.xlsx,.csv,.txt,.md,.pdf,.docx' // backend knowledge.extract.SUPPORTED

const VISIBILITY_LABELS: Record<Visibility, string> = {
  shareable: '답장에 써도 됨',
  internal: '내부용',
}

const STATUS: Record<KnowledgeSource['status'], { label: string; cls: string }> = {
  indexed: { label: '준비됨', cls: 'pill-green' },
  flagged: { label: '의심 부분 뺌', cls: 'pill-amber' },
  failed: { label: '실패', cls: 'pill-red' },
}

const DRAFT_STATUS: Record<string, string> = { draft: '작성 중', sending: '보내는 중', sent: '보냄', unknown: '발송 확인 중' }

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

function Uploader({ onUploaded, onClose }: { onUploaded: (sources: KnowledgeSource[]) => void; onClose: () => void }) {
  const [visibility, setVisibility] = useState<Visibility>('internal')
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [results, setResults] = useState<KnowledgeUploadResult[]>([])
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = async (files: File[]) => {
    if (files.length === 0 || uploading) return
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    form.append('visibility', visibility)
    setUploading(true)
    setError('')
    setResults([])
    try {
      const res = await api.upload<{ results: KnowledgeUploadResult[] }>('/knowledge/sources', form)
      setResults(res.results)
      onUploaded(res.results.flatMap((r) => (r.source ? [r.source] : [])))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <section className="card uploader" aria-labelledby="up-title">
      <div className="card-head">
        <h2 id="up-title">자료 올리기</h2>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="올리기 닫기">
          <Icon name="close" size={16} />
        </button>
      </div>
      <fieldset className="radio-cards" disabled={uploading}>
        <legend className="sr-only">공유 등급</legend>
        <label className={`radio-card${visibility === 'internal' ? ' on' : ''}`}>
          <input type="radio" name="visibility" checked={visibility === 'internal'} onChange={() => setVisibility('internal')} />
          <span>
            <b>내부용</b>
            <span className="muted small">답장에 쓰지 않고, 초안에 들어가면 경고해요. 처음 올리는 자료는 이쪽이 안전해요.</span>
          </span>
        </label>
        <label className={`radio-card${visibility === 'shareable' ? ' on' : ''}`}>
          <input type="radio" name="visibility" checked={visibility === 'shareable'} onChange={() => setVisibility('shareable')} />
          <span>
            <b>답장에 써도 됨</b>
            <span className="muted small">초안을 만들 때 찾아서 값을 채우고 출처 줄을 보여 줘요.</span>
          </span>
        </label>
      </fieldset>
      <label
        className={`drop${dragOver ? ' drag-over' : ''}${uploading ? ' busy' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          upload(Array.from(e.dataTransfer.files))
        }}
      >
        <input ref={inputRef} type="file" multiple accept={ACCEPT} disabled={uploading} onChange={(e) => upload(Array.from(e.target.files ?? []))} />
        <Icon name="upload" size={22} />
        <b>{uploading ? '올리는 중…' : '파일을 끌어다 놓거나 눌러서 고르세요'}</b>
        <span className="muted small">XLSX · CSV · TXT · MD · PDF(글자가 든 것) · DOCX, 파일 하나 10MB까지</span>
      </label>
      {error && <div className="alert alert-red">{error}</div>}
      {results.length > 0 && (
        <ul className="results">
          {results.map((r, i) => (
            <li key={i} className="small">
              <b>{r.filename || '이름 없는 파일'}</b> {r.ok ? <span className="text-green">올렸어요</span> : <span className="text-red">{r.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function SourceDetail({
  id,
  enabled,
  onClose,
  onChanged,
  onDeleted,
}: {
  id: number
  enabled: boolean
  onClose: () => void
  onChanged: (s: KnowledgeSource) => void
  onDeleted: (id: number) => void
}) {
  const [detail, setDetail] = useState<KnowledgeDetail | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .get<KnowledgeDetail>(`/knowledge/sources/${id}`)
      .then((d) => !cancelled && setDetail(d))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [id])

  const changeVisibility = async (to: Visibility) => {
    setBusy('visibility')
    setError('')
    try {
      const s = await api.post<KnowledgeSource>(`/knowledge/sources/${id}/visibility`, { visibility: to })
      onChanged(s)
      setDetail((d) => (d ? { ...d, ...s } : d))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const remove = async () => {
    setBusy('delete')
    setError('')
    try {
      await api.post(`/knowledge/sources/${id}/delete`)
      onDeleted(id)
    } catch (e) {
      setError((e as Error).message)
      setBusy('')
    }
  }

  return (
    <div className="panel">
      <div className="panel-bar">
        <button type="button" className="icon-btn" onClick={onClose} aria-label="상세 닫기" title="닫기">
          <Icon name="close" size={18} />
        </button>
      </div>
      <div className="panel-body">
        {!detail ? (
          error ? (
            <div className="alert alert-red">{error}</div>
          ) : (
            <div className="skeleton">
              <div className="skeleton-row"><span /><span /><span /></div>
              <div className="skeleton-row"><span /><span /><span /></div>
            </div>
          )
        ) : (
          <>
            <header className="detail-head">
              <div className="detail-meta">
                <span className={`pill ${detail.visibility === 'shareable' ? 'pill-blue' : 'pill-gray'}`}>{VISIBILITY_LABELS[detail.visibility]}</span>
                <span className={`pill ${STATUS[detail.status].cls}`}>{STATUS[detail.status].label}</span>
              </div>
              <h1 className="break">{detail.title}</h1>
              <p className="muted small">
                {detail.file_type.toUpperCase()} · {fileSize(detail.size)} · {dateTime(detail.created_at)} 올림 · 덩어리 {detail.chunk_count}개
              </p>
            </header>

            {error && <div className="alert alert-red">{error}</div>}
            {detail.status === 'failed' && detail.error && <div className="alert alert-red">{detail.error}</div>}
            {(detail.notes?.formula_without_value ?? 0) > 0 && (
              <div className="alert alert-amber">계산 값이 없는 칸 {detail.notes.formula_without_value}개 — Excel에서 열어 저장한 뒤 다시 올려 주세요.</div>
            )}
            {(detail.notes?.truncated_rows ?? 0) > 0 && <div className="alert alert-slate">긴 행 {detail.notes.truncated_rows}개를 줄여 저장했어요.</div>}
            {detail.flagged_count > 0 && <div className="alert alert-amber">AI에게 지시하는 문장이 든 부분 {detail.flagged_count}곳은 검색에서 뺐어요.</div>}

            <section className="card">
              <div className="card-head">
                <h2>공유 등급</h2>
              </div>
              <div className="seg seg-wide" role="group" aria-label="공유 등급">
                {(['internal', 'shareable'] as Visibility[]).map((v) => (
                  <button
                    key={v}
                    type="button"
                    aria-pressed={detail.visibility === v}
                    className={detail.visibility === v ? 'active' : ''}
                    disabled={!enabled || detail.status === 'failed' || !!busy || detail.visibility === v}
                    onClick={() => changeVisibility(v)}
                  >
                    {VISIBILITY_LABELS[v]}
                  </button>
                ))}
              </div>
              <p className="muted small">
                {detail.visibility === 'shareable'
                  ? '답장 초안을 만들 때 이 자료에서 값을 찾아 채워요. 내부용으로 바꾸면 이 자료를 참고한 초안은 다시 만들어야 보낼 수 있어요.'
                  : '어디로도 보내지 않아요. 초안에 이 자료의 값이 들어가면 보내기 전에 경고해요.'}
              </p>
            </section>

            <section className="card">
              <div className="card-head">
                <h2>참고한 답장</h2>
                <span className="muted small num">{detail.referenced_by.length}건</span>
              </div>
              {detail.referenced_by.length === 0 ? (
                <p className="muted small">아직 이 자료를 참고한 답장 초안이 없어요.</p>
              ) : (
                <ul className="ref-list">
                  {detail.referenced_by.map((r) => (
                    <li key={r.task_id}>
                      <Link to={`/inbox/${r.task_id}`}>{r.task_title}</Link>
                      <span className="pill pill-gray">{DRAFT_STATUS[r.draft_status] ?? r.draft_status}</span>
                      <span className="muted small num">{shortDate(r.updated_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <details className="card fold" open={detail.chunks.length <= 3}>
              <summary>
                <span>뽑은 내용</span>
                <span className="muted num">{detail.chunks.length}덩어리</span>
              </summary>
              <div className="fold-body chunks">
                {detail.chunks.map((c) => (
                  <div key={c.ordinal} className={`chunk${c.flagged ? ' flagged' : ''}`}>
                    <div className="chunk-head">
                      <span className="num">#{c.ordinal + 1}</span>
                      {c.flagged && <span className="pill pill-amber">검색에서 뺌</span>}
                    </div>
                    <ol>
                      {c.lines.map((l, i) => (
                        <li key={i}>{l}</li>
                      ))}
                    </ol>
                  </div>
                ))}
              </div>
            </details>

            <div className="danger-zone">
              {confirming ? (
                <>
                  <span>이 자료와 초안 속 사본을 지울까요? 이미 보낸 메일은 그대로예요.</span>
                  <button type="button" className="btn btn-danger btn-sm" disabled={!!busy} onClick={remove}>
                    {busy === 'delete' ? '지우는 중…' : '지우기'}
                  </button>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => setConfirming(false)}>
                    취소
                  </button>
                </>
              ) : (
                <button type="button" className="btn btn-link btn-sm text-red" onClick={() => setConfirming(true)}>
                  자료 지우기
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function SearchTest({ enabled }: { enabled: boolean }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async (e: React.FormEvent) => {
    e.preventDefault()
    const query = q.trim()
    if (!query) return
    setBusy(true)
    setError('')
    try {
      setHits((await api.post<{ hits: KnowledgeHit[] }>('/knowledge/search', { q: query })).hits)
    } catch (err) {
      setError((err as Error).message.replace(/^Value error, /, ''))
      setHits(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="search-title">
      <div className="card-head">
        <h2 id="search-title">검색 시험</h2>
        <span className="muted small">답장 초안이 찾는 방식 그대로</span>
      </div>
      <form className="search-row" onSubmit={run}>
        <label className="search grow">
          <Icon name="search" size={16} />
          <input value={q} maxLength={200} onChange={(e) => setQ(e.target.value)} placeholder="예: TS-200 단가" aria-label="검색어" disabled={!enabled} />
        </label>
        <button type="submit" className="btn btn-primary btn-sm" disabled={!enabled || busy || !q.trim()}>
          {busy ? '찾는 중…' : '찾기'}
        </button>
      </form>
      <p className="muted small">"답장에 써도 됨" 자료에서만 찾아요. 검색은 이 PC 안에서만 하고 검색어는 남기지 않아요.</p>
      {error && <div className="alert alert-red">{error}</div>}
      {hits &&
        (hits.length === 0 ? (
          <p className="muted small">찾은 부분이 없어요. 품번 · 품목명처럼 자료에 실제로 있는 낱말로 찾아 보세요.</p>
        ) : (
          <ol className="hits">
            {hits.map((h, i) => (
              <li key={`${h.source_id}-${h.chunk_ordinal}`}>
                <div className="hit-head">
                  <span className="num muted">{i + 1}</span>
                  <b className="break">{h.title}</b>
                  <span className="muted small num">#{h.chunk_ordinal + 1} · 점수 {h.score}</span>
                </div>
                <ul className="hit-lines">
                  {h.lines.map((l, j) => (
                    <li key={j}>{l}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        ))}
    </section>
  )
}

/** 자료실: 목록(표) · 상세 패널 · 검색 시험. 스위치가 꺼져 있어도 목록 보기와 지우기는 된다(RAG 설계 3.4). */
export default function Knowledge() {
  const [list, setList] = useState<KnowledgeList | null>(null)
  const [loadError, setLoadError] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const [uploading, setUploading] = useState(false)

  const load = useCallback(() => {
    api
      .get<KnowledgeList>('/knowledge/sources')
      .then((l) => {
        setList(l)
        setLoadError('')
      })
      .catch((e: Error) => setLoadError(e.message))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const replace = (s: KnowledgeSource) => setList((l) => (l ? { ...l, sources: l.sources.map((x) => (x.id === s.id ? { ...x, ...s } : x)) } : l))
  const removeOne = (id: number) => {
    setList((l) => (l ? { ...l, sources: l.sources.filter((x) => x.id !== id) } : l))
    setSelected(null)
  }
  const addMany = (added: KnowledgeSource[]) =>
    setList((l) => {
      if (!l) return l
      const ids = new Set(added.map((s) => s.id))
      return { ...l, sources: [...[...added].reverse(), ...l.sources.filter((s) => !ids.has(s.id))] }
    })

  const shareable = list?.sources.filter((s) => s.visibility === 'shareable').length ?? 0

  return (
    <div className={`split split-knowledge${selected != null ? ' has-panel' : ''}`}>
      <section className="list-pane list-pane-wide" aria-label="자료 목록">
        <header className="pane-head">
          <div className="pane-title">
            <h1>자료실</h1>
            {list?.enabled && (
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setUploading((u) => !u)} aria-expanded={uploading}>
                <Icon name="upload" size={15} /> 자료 올리기
              </button>
            )}
          </div>
          <p className="muted small">단가표 · 안내문을 올려 두면 답장 초안을 만들 때 찾아서 씁니다. 원본 파일은 저장하지 않고 뽑아낸 글만 남겨요.</p>
        </header>

        <div className="list-scroll pad">
          {list && !list.enabled && (
            <div className="notice">
              <b>자료 기능이 꺼져 있어요</b>
              <p>
                올린 자료를 보거나 지울 수만 있어요. <code>backend/.env</code>에서 <code>KNOWLEDGE_ENABLED=true</code>로 켜면 올리기 · 검색 · 초안 채우기가 돼요.
              </p>
            </div>
          )}

          {uploading && list?.enabled && <Uploader onUploaded={addMany} onClose={() => setUploading(false)} />}

          <dl className="share-table" aria-label="공유 등급별로 자료가 가는 곳">
            <div>
              <dt>
                <span className="pill pill-blue">답장에 써도 됨</span> <span className="num">{shareable}</span>
              </dt>
              <dd>초안을 만들 때 검색된 부분만 초안용 LLM으로 보냅니다. Gemini 무료 등급이면 보낸 내용이 Google의 제품 개선에 쓰일 수 있어요.</dd>
            </div>
            <div>
              <dt>
                <span className="pill pill-gray">내부용</span> <span className="num">{(list?.sources.length ?? 0) - shareable}</span>
              </dt>
              <dd>어디로도 보내지 않습니다. 초안에 내부용 값이 들어가면 보내기 전에 확인을 받아요.</dd>
            </div>
          </dl>

          {!list ? (
            loadError ? (
              <div className="alert alert-red alert-action">
                <span>자료 목록을 불러오지 못했습니다. {loadError}</span>
                <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
                  다시 시도
                </button>
              </div>
            ) : (
              <div className="skeleton">
                <div className="skeleton-row"><span /><span /><span /></div>
                <div className="skeleton-row"><span /><span /><span /></div>
              </div>
            )
          ) : list.sources.length === 0 ? (
            <div className="empty">
              <b>아직 올린 자료가 없어요</b>
              <p>단가표나 배송 · 결제 안내처럼 답장에 자주 쓰는 자료를 올려 보세요.</p>
              {list.enabled && (
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setUploading(true)}>
                  자료 올리기
                </button>
              )}
            </div>
          ) : (
            <div className="table-box">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>이름</th>
                    <th>형식</th>
                    <th>공유 등급</th>
                    <th>상태</th>
                    <th className="r">크기</th>
                    <th className="r">올린 날</th>
                  </tr>
                </thead>
                <tbody>
                  {list.sources.map((s) => (
                    <tr key={s.id} className={selected === s.id ? 'active' : ''}>
                      <td>
                        <button type="button" className="cell-link" onClick={() => setSelected(s.id)}>
                          <Icon name="file" size={16} />
                          <span className="break">{s.title}</span>
                        </button>
                      </td>
                      <td className="muted small">{s.file_type}</td>
                      <td>
                        <span className={`pill ${s.visibility === 'shareable' ? 'pill-blue' : 'pill-gray'}`}>{VISIBILITY_LABELS[s.visibility]}</span>
                      </td>
                      <td>
                        <span className={`pill ${STATUS[s.status].cls}`}>{STATUS[s.status].label}</span>
                      </td>
                      <td className="r num muted">{fileSize(s.size)}</td>
                      <td className="r num muted">{shortDate(s.created_at, false)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {list && list.sources.length > 0 && <SearchTest enabled={list.enabled} />}
        </div>
      </section>

      <section className="detail-pane" aria-label="자료 상세">
        {selected != null ? (
          <SourceDetail key={selected} id={selected} enabled={!!list?.enabled} onClose={() => setSelected(null)} onChanged={replace} onDeleted={removeOne} />
        ) : (
          <div className="pane-empty">
            <Icon name="folder" size={28} />
            <b>자료를 고르면 여기에 열려요</b>
            <p className="muted small">뽑은 내용, 공유 등급, 이 자료를 참고한 답장을 볼 수 있어요.</p>
          </div>
        )}
      </section>
    </div>
  )
}
