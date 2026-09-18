import { useEffect, useId, useRef, useState } from 'react'

export function GlobeIcon({ className = 'size-3.5' }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={`${className} shrink-0 text-muted`} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18Z" />
    </svg>
  )
}

export function PinIcon({ className = 'size-3.5' }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={`${className} shrink-0 text-muted`} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 21s7-5.5 7-11a7 7 0 1 0-14 0c0 5.5 7 11 7 11Z" />
      <circle cx="12" cy="10" r="2.5" />
    </svg>
  )
}

export function PenIcon({ className = 'size-3.5' }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  )
}

export function CopyIcon({ className = 'size-3.5' }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  )
}

export function TrashIcon({ className = 'size-4' }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}

/**
 * Free-text places, from OpenStreetMap's public geocoder.
 *
 * Chosen for having no key to manage and no bill to watch — which matters for a field nobody
 * types in more than once per job. It is rate-limited to roughly one request a second, hence
 * the 400ms debounce and the three-character floor; and it only ever sees the few words being
 * typed into a location box, never the posting or the resume.
 *
 * Failure is silence: no suggestions, and the field still takes whatever the user types. A
 * geocoder being down must not stop someone writing "Remote".
 */
function usePlaceSuggestions(query, enabled) {
  const [places, setPlaces] = useState([])

  const text = query.trim()
  const asking = enabled && text.length >= 3

  useEffect(() => {
    if (!asking) return undefined

    const controller = new AbortController()
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&limit=5&addressdetails=1&q=${encodeURIComponent(text)}`,
          { signal: controller.signal, headers: { Accept: 'application/json' } },
        )
        if (!response.ok) return
        const results = await response.json()
        setPlaces([...new Set(results.map(shortPlaceName).filter(Boolean))])
      } catch {
        setPlaces([])
      }
    }, 400)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [text, asking])

  // derived, not stored: clearing the list in an effect would set state during render and
  // cascade a second one for nothing
  return asking ? places : []
}

// "Charlotte, Mecklenburg County, North Carolina, 28202, United States" is not a location a
// resume would ever say. Keep the place, the region and the country.
function shortPlaceName(result) {
  const address = result.address || {}
  const place = address.city || address.town || address.village || address.hamlet
    || address.county || result.name
  const region = address.state || address.region
  const country = address.country_code?.toUpperCase()
  return [place, region, country && country !== 'US' ? address.country : undefined]
    .filter(Boolean)
    .join(', ') || result.display_name?.split(',')[0]
}

/**
 * One editable value, shown as a compact field: icon, text, copy, pen.
 *
 * Reading and editing are the same control rather than a control and a form — the box keeps
 * its shape and widens, so nothing around it moves. Clicking away or pressing Escape abandons
 * the edit, which is what every inline editor does and what stops a half-typed value following
 * the user around. There is no Cancel button for the same reason: the gesture already exists.
 */
export function InlineField({
  value,
  onSave,
  saving,
  icon: Icon,
  label,
  placeholder,
  emptyText = 'Not set',
  href,
  display,
  copyable = false,
  suggest = false,
  grow = false,
  width = 'w-full',
  growWidth = 'w-[26rem] max-w-[60vw]',
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value || '')
  const [copied, setCopied] = useState(false)
  const box = useRef(null)
  const listId = useId()
  const places = usePlaceSuggestions(draft, suggest && editing)

  useEffect(() => {
    if (!editing) return undefined

    function abandon() {
      setDraft(value || '')
      setEditing(false)
    }
    function onPointerDown(event) {
      if (!box.current?.contains(event.target)) abandon()
    }
    function onKeyDown(event) {
      if (event.key === 'Escape') abandon()
    }

    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [editing, value])

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)      // a denied clipboard permission is not worth an error state
    }
  }

  const shell = 'flex min-h-11 items-center gap-1.5 rounded-[0.625rem] border border-border bg-soft-paper px-3 transition-[width] duration-200 ease-out'

  if (editing) {
    return (
      <form
        ref={box}
        className={`${shell} animate-soft-in focus-within:border-obsidian ${grow ? growWidth : width}`}
        onSubmit={(event) => {
          event.preventDefault()
          onSave(draft.trim() || null)
          setEditing(false)
        }}
      >
        <Icon />
        <input
          autoFocus
          value={draft}
          list={suggest ? listId : undefined}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={placeholder}
          aria-label={label}
          // the ring is drawn by the box around it, which is the shape the user sees
          className="min-w-0 flex-1 self-stretch bg-transparent text-sm text-ink outline-none"
        />
        {suggest && (
          <datalist id={listId}>
            {places.map((place) => <option key={place} value={place} />)}
          </datalist>
        )}
        <button type="submit" className="shrink-0 px-1 text-xs text-ink hover:underline" disabled={saving}>
          {value ? 'Save' : 'Add'}
        </button>
      </form>
    )
  }

  return (
    <div className={`${shell} animate-soft-in hover:border-ash ${grow ? width : width}`}>
      <Icon />

      {value ? (
        href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="min-w-0 flex-1 truncate text-sm text-charcoal hover:text-ink hover:underline hover:underline-offset-4"
            title={value}
          >
            {display || value}
          </a>
        ) : (
          <span className="min-w-0 flex-1 truncate text-sm text-ink" title={value}>{display || value}</span>
        )
      ) : (
        <span className="min-w-0 flex-1 truncate text-sm text-muted">{emptyText}</span>
      )}

      {copyable && value && (
        <button
          type="button"
          className="shrink-0 px-1 text-muted hover:text-ink"
          aria-label={copied ? 'Copied' : `Copy ${label}`}
          onClick={copy}
        >
          {copied ? <span className="text-[11px] text-terminal-green" aria-hidden="true">✓</span> : <CopyIcon />}
        </button>
      )}

      <button
        type="button"
        className="shrink-0 px-1 text-muted hover:text-ink"
        aria-label={value ? `Edit ${label}` : `Add ${label}`}
        onClick={() => { setDraft(value || ''); setEditing(true) }}
      >
        {value ? <PenIcon /> : <span className="text-xs">Add</span>}
      </button>
    </div>
  )
}
