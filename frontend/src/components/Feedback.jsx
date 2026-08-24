export function Spinner({ size = 'md' }) {
  const sizeClass = size === 'sm' ? 'h-3.5 w-3.5' : 'h-5 w-5'
  return (
    <span
      aria-hidden="true"
      className={`inline-block rounded-full border-2 border-current border-r-transparent animate-spin ${sizeClass}`}
    />
  )
}

export function PageLoader({ label = 'Loading…' }) {
  return (
    <div className="min-h-screen bg-surface grid place-items-center" role="status">
      <div className="flex items-center gap-3 text-sm text-muted animate-soft-in">
        <Spinner />
        <span>{label}</span>
      </div>
    </div>
  )
}

export function InlineAlert({ tone = 'error', children, className = '' }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={`rounded-xl border border-border bg-soft-paper px-3 py-2.5 text-sm text-ink animate-soft-in ${className}`}
    >
      <span className="mr-2 text-muted" aria-hidden="true">{tone === 'success' ? '✓' : '!'}</span>
      {children}
    </div>
  )
}

export function ButtonLabel({ pending, pendingText, children }) {
  return (
    <span className="inline-flex items-center justify-center gap-2">
      {pending && <Spinner size="sm" />}
      {pending ? pendingText : children}
    </span>
  )
}
