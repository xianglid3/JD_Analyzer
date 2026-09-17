import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

function ChevronIcon({ open }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      className={`h-3.5 w-3.5 shrink-0 text-muted transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="m4 6 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/**
 * The pipeline filter, as a menu rather than a permanent column.
 *
 * It was a 240px sidebar holding eight checkboxes that most sessions never touch, taking width
 * from the table that is the actual page. Checkboxes rather than a single-select because the
 * question is "show me interviews *and* offers", and the counts stay visible because knowing
 * there are zero offers is half of what the control is for.
 *
 * Portalled for the same reason dialogs are: the page column carries a transform after its
 * entrance animation, which would otherwise become the containing block for this fixed panel.
 */
export default function StatusFilterMenu({ options, selected, onToggle, onClear, total }) {
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState(null)

  useEffect(() => {
    if (!open) return undefined

    function place() {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.max(rect.width, 232)
      const left = Math.min(Math.max(8, rect.right - width), Math.max(8, window.innerWidth - width - 8))
      setPosition({ left, top: rect.bottom + 6, width })
    }

    function onPointerDown(event) {
      if (menuRef.current?.contains(event.target) || triggerRef.current?.contains(event.target)) return
      setOpen(false)
    }

    function onKeyDown(event) {
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }

    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const label = selected.length === 0
    ? 'All statuses'
    : `${selected.length} ${selected.length === 1 ? 'status' : 'statuses'}`

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Filter by pipeline status"
        onClick={() => setOpen((value) => !value)}
        className="control flex min-h-11 w-full items-center justify-between gap-2 px-3 text-sm text-ink"
      >
        <span className="truncate">{label}</span>
        <ChevronIcon open={open} />
      </button>

      {open && position && createPortal((
        <div
          ref={menuRef}
          role="group"
          aria-label="Pipeline status"
          className="dialog-panel fixed z-50 max-h-[22rem] overflow-y-auto rounded-md border border-border bg-soft-paper p-2 shadow-sm"
          style={{ left: position.left, top: position.top, width: position.width }}
        >
          {options.map(([optionLabel, count, key]) => {
            const checked = selected.includes(key)
            return (
              <label
                key={key}
                className={`flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-md px-3 text-sm transition-colors ${
                  checked ? 'bg-surface text-ink' : 'text-charcoal hover:bg-surface hover:text-ink'
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggle(key)}
                  className="size-4 shrink-0 accent-obsidian"
                />
                <span className="flex-1">{optionLabel}</span>
                <span className="font-mono text-xs text-muted">{count ?? 0}</span>
              </label>
            )
          })}

          <div className="flex items-center justify-between border-t border-border px-3 pt-2 text-xs text-muted">
            <span>{selected.length === 0 ? `${total ?? 0} total` : 'Any selected status'}</span>
            {selected.length > 0 && (
              <button
                type="button"
                className="underline-offset-4 hover:text-ink hover:underline"
                onClick={onClear}
              >
                Clear
              </button>
            )}
          </div>
        </div>
      ), document.body)}
    </>
  )
}
