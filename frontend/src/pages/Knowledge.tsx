import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import Icon from '../components/Icon'
import { shortDate } from '../format'
import type { KnowledgeList, KnowledgeSource, KnowledgeUploadResult } from '../types'

type Visibility = KnowledgeSource['visibility']

const ACCEPT = '.xlsx,.csv,.txt,.md,.pdf,.docx' // backend knowledge.extract.SUPPORTED

const VISIBILITY_LABELS: Record<Visibility, string> = {
  shareable: '답장에 써도 됨',
  internal: '내부용',
}

const STATUS_TAGS: Record<KnowledgeSource['status'], { label: string; cls: string }> = {
  indexed: { label: '완료', cls: 'tag-green' },
  flagged: { label: '의심', cls: 'tag-amber' },
  failed: { label: '실패', cls: 'tag-red' },
}

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

function SourceCard({
  source,
  enabled,
  onChanged,
  onDeleted,
}: {
  source: KnowledgeSource
  enabled: boolean
  onChanged: (s: KnowledgeSource) => void
  onDeleted: (id: number) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const status = STATUS_TAGS[source.status]
  const other: Visibility = source.visibility === 'shareable' ? 'internal' : 'shareable'
  const formula = source.notes?.formula_without_value ?? 0
  const truncated = source.notes?.truncated_rows ?? 0

  const changeVisibility = async () => {
    setBusy('visibility')
    setError('')
    try {
      onChanged(await api.post<KnowledgeSource>(`/knowledge/sources/${source.id}/visibility`, { visibility: other }))
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
      await api.post(`/knowledge/sources/${source.id}/delete`)
      onDeleted(source.id)
    } catch (e) {
      setError((e as Error).message)
      setBusy('')
    }
  }

  return (
    <li className="card knowledge-card">
      <div className="knowledge-head">
        <b className="knowledge-title">{source.title}</b>
        <span className="knowledge-tags">
          <span className={`tag ${source.visibility === 'shareable' ? 'tag-blue' : ''}`.trim()}>{VISIBILITY_LABELS[source.visibility]}</span>
          <span className={`tag ${status.cls}`}>{status.label}</span>
        </span>
      </div>
      <div className="muted small">
        {source.file_type.toUpperCase()} · {fileSize(source.size)} · {shortDate(source.created_at, false)} 올림
        {source.status !== 'failed' && <> · 덩어리 {source.chunk_count}개</>}
      </div>

      {source.status === 'failed' && source.error && <div className="alert alert-red">{source.error}</div>}
      {source.status === 'flagged' && (
        <div className="alert alert-amber">의심 — 주입 문구가 든 부분 {source.flagged_count}곳을 뺐습니다</div>
      )}
      {formula > 0 && (
        <div className="muted small text-amber">계산 값이 없는 칸 {formula}개 — Excel에서 열어 저장한 뒤 다시 올려 주세요</div>
      )}
      {truncated > 0 && <div className="muted small">긴 행 {truncated}개를 줄여 저장했습니다</div>}
      {source.visibility === 'internal' && source.status !== 'failed' && (
        <div className="muted small">답장에 쓰려면 '답장에 써도 됨'으로 바꾸세요</div>
      )}

      {confirming ? (
        <div className="inline-panel knowledge-confirm">
          <span>정말 지울까요?</span>
          <div className="draft-actions">
            <button type="button" className="btn btn-danger btn-sm" disabled={!!busy} onClick={remove}>
              {busy === 'delete' ? '지우는 중…' : '지우기'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => setConfirming(false)}>
              취소
            </button>
          </div>
        </div>
      ) : (
        <div className="draft-actions">
          {enabled && source.status !== 'failed' && (
            <button type="button" className="btn btn-ghost btn-sm" disabled={!!busy} onClick={changeVisibility}>
              {busy === 'visibility' ? '바꾸는 중…' : `'${VISIBILITY_LABELS[other]}'으로 바꾸기`}
            </button>
          )}
          <button type="button" className="btn btn-link btn-sm" disabled={!!busy} onClick={() => setConfirming(true)}>
            지우기
          </button>
        </div>
      )}
      {error && <div className="alert alert-red">{error}</div>}
    </li>
  )
}

function Uploader({ onUploaded }: { onUploaded: (sources: KnowledgeSource[]) => void }) {
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
    <div className="card stack">
      <h2>자료 올리기</h2>
      <fieldset className="knowledge-visibility" disabled={uploading}>
        <legend className="small muted">공유 등급</legend>
        <label className="check-row">
          <input type="radio" name="visibility" checked={visibility === 'shareable'} onChange={() => setVisibility('shareable')} />
          <span>
            <b>답장에 써도 됨</b>: 상대에게 보내는 답장에 인용될 수 있습니다
          </span>
        </label>
        <label className="check-row">
          <input type="radio" name="visibility" checked={visibility === 'internal'} onChange={() => setVisibility('internal')} />
          <span>
            <b>내부용</b>: 답장에 쓰지 않습니다
          </span>
        </label>
      </fieldset>
      <label
        className={`knowledge-drop${dragOver ? ' drag-over' : ''}${uploading ? ' busy' : ''}`}
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
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          disabled={uploading}
          onChange={(e) => upload(Array.from(e.target.files ?? []))}
        />
        <Icon name="upload" size={22} className="drop-icon" />
        <b>{uploading ? '올리는 중…' : '파일을 끌어다 놓거나 눌러서 고르세요'}</b>
        <span className="muted small">XLSX · CSV · TXT · MD · PDF(글자가 든 것) · DOCX, 파일 하나 10MB까지. 여러 개를 한 번에 올릴 수 있습니다.</span>
      </label>
      {error && <div className="alert alert-red">{error}</div>}
      {results.length > 0 && (
        <ul className="knowledge-results">
          {results.map((r, i) => (
            <li key={i} className="small">
              <b>{r.filename || '이름 없는 파일'}</b>{' '}
              {r.ok ? <span className="text-green">올렸습니다</span> : <span className="text-red">{r.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** 자료실 (RAG 설계 2.1). 스위치가 꺼져 있어도 목록 보기와 지우기는 된다(3.4). */
export default function Knowledge() {
  const [list, setList] = useState<KnowledgeList | null>(null)
  const [loadError, setLoadError] = useState('')

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

  const replace = (s: KnowledgeSource) =>
    setList((l) => (l ? { ...l, sources: l.sources.map((x) => (x.id === s.id ? s : x)) } : l))
  const removeOne = (id: number) => setList((l) => (l ? { ...l, sources: l.sources.filter((x) => x.id !== id) } : l))
  const addMany = (added: KnowledgeSource[]) =>
    setList((l) => {
      if (!l) return l
      const ids = new Set(added.map((s) => s.id))
      return { ...l, sources: [...[...added].reverse(), ...l.sources.filter((s) => !ids.has(s.id))] }
    })

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>자료실</h1>
          <p className="page-sub">단가표 · 안내문을 올려 두면 답장 초안을 만들 때 찾아서 씁니다. 원본 파일은 저장하지 않고 뽑아낸 글만 남겨요.</p>
        </div>
      </header>
      <dl className="share-table" aria-label="공유 등급별로 자료가 가는 곳">
        <div>
          <dt>답장에 써도 됨</dt>
          <dd>
            초안을 만들 때 검색된 부분만 초안용 LLM으로 보냅니다. Gemini 무료 등급이면 보낸 내용이 Google의 제품 개선에 쓰일 수 있어요(메일 분석과 같은
            조건).
          </dd>
        </div>
        <div>
          <dt>내부용</dt>
          <dd>어디로도 보내지 않습니다. 초안에 내부용 값이 들어가면 보내기 전에 경고합니다.</dd>
        </div>
      </dl>

      {!list ? (
        loadError ? (
          <div className="card stack">
            <div className="muted">자료 목록을 불러오지 못했습니다. {loadError}</div>
            <div>
              <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
                다시 시도
              </button>
            </div>
          </div>
        ) : (
          <div className="skeleton" role="status" aria-label="불러오는 중">
            <div className="skeleton-row">
              <span />
              <span />
              <span />
            </div>
          </div>
        )
      ) : (
        <>
          {list.enabled ? (
            <Uploader onUploaded={addMany} />
          ) : (
            <div className="alert alert-amber">
              자료 기능이 꺼져 있습니다. 올린 자료를 보거나 지울 수만 있습니다. <code>backend/.env</code>에서{' '}
              <code>KNOWLEDGE_ENABLED=true</code>로 켜면 자료를 올리고 답장 초안에 쓸 수 있습니다.
            </div>
          )}
          <div className="stack">
            <h2>올린 자료 {list.sources.length}개</h2>
            {list.sources.length === 0 ? (
              <div className="empty">
                <b>아직 올린 자료가 없어요</b>
                <p>단가표나 배송 · 결제 안내처럼 답장에 자주 쓰는 자료를 올려 보세요. 처음 올린 자료는 '내부용'으로 두는 것이 안전합니다.</p>
              </div>
            ) : (
              <ul className="knowledge-list">
                {list.sources.map((s) => (
                  <SourceCard key={s.id} source={s} enabled={list.enabled} onChanged={replace} onDeleted={removeOne} />
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  )
}
