// 직접 그린 선 아이콘(24 격자, 선 1.6). 이모지 · 유니코드 기호 대신 쓴다.
const PATHS: Record<string, string> = {
  inbox: 'M4 13.5 6.2 6.4A2 2 0 0 1 8.1 5h7.8a2 2 0 0 1 1.9 1.4L20 13.5M4 13.5V18a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4.5M4 13.5h4.5l1.2 2h4.6l1.2-2H20',
  list: 'M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01',
  folder: 'M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2.2h7.4a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z',
  gear: 'M12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6zM19.4 13.5l1.4 1.1-1.6 2.8-1.7-.6a6.6 6.6 0 0 1-1.9 1.1L15.3 20h-3.2l-.3-1.9a6.6 6.6 0 0 1-1.9-1.1l-1.7.6-1.6-2.8 1.4-1.1a6.7 6.7 0 0 1 0-2.2L6.6 10.4 8.2 7.6l1.7.6A6.6 6.6 0 0 1 11.8 7l.3-1.9h3.2l.3 1.9a6.6 6.6 0 0 1 1.9 1.1l1.7-.6 1.6 2.8-1.4 1.1a6.7 6.7 0 0 1 0 2.1z',
  grid: 'M4.5 4.5h6v6h-6zM13.5 4.5h6v6h-6zM4.5 13.5h6v6h-6zM13.5 13.5h6v6h-6z',
  refresh: 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4',
  back: 'M14.5 6 8.5 12l6 6',
  logout: 'M14 7V5.5a1.5 1.5 0 0 0-1.5-1.5h-7A1.5 1.5 0 0 0 4 5.5v13A1.5 1.5 0 0 0 5.5 20h7a1.5 1.5 0 0 0 1.5-1.5V17M10 12h10M17 9l3 3-3 3',
  calendar: 'M5 6.5h14a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7.5a1 1 0 0 1 1-1zM4 10.5h16M8.5 4v4M15.5 4v4',
  upload: 'M12 15.5V4.5M7.5 9 12 4.5 16.5 9M4.5 15v3.5a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1V15',
  mail: 'M4.5 6h15a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zM4 7l8 6 8-6',
  chevron: 'M9.5 6l6 6-6 6',
  check: 'M5.5 12.5l4 4 9-9',
  home: 'M4 11.5 12 5l8 6.5M6 10v9h4.5v-5h3v5H18v-9',
  reply: 'M10 8 5 12.5l5 4.5M5.5 12.5H15a4.5 4.5 0 0 1 4.5 4.5v1',
  search: 'M10.5 4.5a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM15 15l5 5',
  close: 'M6 6l12 12M18 6 6 18',
  lock: 'M7 11V8.5a5 5 0 0 1 10 0V11M6 11h12v9H6z',
  spark: 'M12 4v4M12 16v4M4 12h4M16 12h4M7 7l2 2M15 15l2 2M17 7l-2 2M9 15l-2 2',
  alert: 'M12 4 3 19h18zM12 10v4M12 16.5v.5',
  up: 'M6 15l6-6 6 6',
  down: 'M6 9l6 6 6-6',
  file: 'M7 3.5h6.5L18 8v12.5H7zM13 3.5V8h5',
}

export type IconName = keyof typeof PATHS

export default function Icon({ name, size = 18, className = '' }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={`icon ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}

/** 로고 표식: 둥근 사각 도장 안의 FU. 주홍 인주색 하나. */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect x="2" y="2" width="28" height="28" rx="7" fill="none" stroke="currentColor" strokeWidth="2.4" />
      <path d="M9 22V10.5h6M9 16h5M18.5 10.5v7.2a3.3 3.3 0 0 0 6.6 0v-7.2" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
