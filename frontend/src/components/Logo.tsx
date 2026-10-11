/** QueryMind mark: a speech bubble holding three bars (a question that becomes a chart). */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect x="1" y="1" width="30" height="26" rx="8" fill="#3b5bdb" />
      <path d="M9 27 L9 31 L14 27 Z" fill="#3b5bdb" />
      <rect x="8.5" y="15" width="3.5" height="6" rx="1" fill="#ffffff" />
      <rect x="14.25" y="10" width="3.5" height="11" rx="1" fill="#ffffff" />
      <rect x="20" y="7" width="3.5" height="14" rx="1" fill="#9fb3ff" />
    </svg>
  )
}

export function Brand() {
  return (
    <span className="brand">
      <Logo />
      QueryMind
    </span>
  )
}
