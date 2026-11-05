export type Stat = {
  key: string
  label: string
  value: number | undefined
  /** 값이 0보다 클 때만 쓰는 강조색 */
  tone?: 'red' | 'amber' | 'blue'
  note?: string
}

/**
 * 페이지 상단 현황 칸. 누르면 그것만 보이고, 다시 누르면 전체로 돌아간다.
 * 받은 업무 · 후속 관리 · 자료실이 같은 모양을 쓴다.
 */
export default function StatStrip({ items, active, onPick, label }: { items: Stat[]; active: string | null; onPick: (key: string | null) => void; label: string }) {
  return (
    <div className="stat-strip" role="group" aria-label={label}>
      {items.map((s) => {
        const on = active === s.key
        const tone = s.tone && (s.value ?? 0) > 0 ? ` stat-${s.tone}` : ''
        return (
          <button
            key={s.key}
            type="button"
            className={`stat${tone}${on ? ' active' : ''}`}
            aria-pressed={on}
            title={on ? '다시 누르면 전체를 봐요' : `${s.label}만 보기`}
            onClick={() => onPick(on ? null : s.key)}
          >
            <span className="stat-label">{s.label}</span>
            <span className="stat-value num">{s.value ?? '–'}</span>
            {s.note && <span className="stat-note">{s.note}</span>}
          </button>
        )
      })}
    </div>
  )
}
