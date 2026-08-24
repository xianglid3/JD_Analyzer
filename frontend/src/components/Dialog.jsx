import { useEffect, useId, useRef } from 'react'

export default function Dialog({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  dismissible = true,
  width = 'max-w-2xl',
}) {
  const titleId = useId()
  const descriptionId = useId()
  const panel = useRef(null)

  useEffect(() => {
    if (!open) return undefined

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusableSelector = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
    const focusable = [...panel.current.querySelectorAll(focusableSelector)]
    focusable[0]?.focus()

    function handleKeyDown(event) {
      if (event.key === 'Escape' && dismissible) onClose()
      if (event.key !== 'Tab' || focusable.length === 0) return

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [dismissible, onClose, open])

  if (!open) return null

  return (
    <div
      className="dialog-backdrop fixed inset-0 z-50 grid place-items-center bg-black/35 px-4 py-6"
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) onClose()
      }}
    >
      <section
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className={`dialog-panel flex max-h-full w-full ${width} flex-col overflow-hidden rounded-2xl border border-border bg-soft-paper`}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 id={titleId} className="text-lg font-medium text-ink">{title}</h2>
            {description && <p id={descriptionId} className="mt-1 text-sm leading-5 text-muted">{description}</p>}
          </div>
          {dismissible && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="icon-button -mr-2 -mt-1"
            >
              ×
            </button>
          )}
        </header>
        <div className="overflow-y-auto px-5 py-5">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-4">{footer}</footer>}
      </section>
    </div>
  )
}
