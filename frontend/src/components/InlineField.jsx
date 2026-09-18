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
 * Free-text places, via `leaflet-geosearch`'s OpenStreetMap provider.
 *
 * The library owns the parts that were fiddly by hand: building the query, normalising results
 * across providers, and returning a stable shape. Swapping to Google or Mapbox later is a
 * one-line provider change rather than a rewrite of this hook.
 *
 * Fires as soon as typing pauses (250ms), from two characters up. The public OSM endpoint is
 * rate-limited to roughly a request a second, and one pause means one request, so this stays
 * well inside it. It only ever sees what is typed into a location
 * box — never the posting or the resume — and failure is silence: no suggestions, and the field
 * takes whatever the user types. A geocoder being down must not stop someone writing "Remote".
 */
// Short enough to fire on a real pause in typing rather than after one, long enough that
// "char" does not become four requests. Only one request is ever in flight per pause, which is
// what the public OSM endpoint's ~1/second limit actually cares about.
const PAUSE_MS = 250
const MIN_QUERY = 2

// Loaded the first time someone edits a location, not on every page view: statically imported
// it added ~155KB to the main bundle, which is a lot to carry for a field most sessions never
// touch. The import is cached, so the wait happens once.
let providerPromise = null

function geocoder() {
  if (!providerPromise) {
    providerPromise = import('leaflet-geosearch').then(({ OpenStreetMapProvider }) => (
      new OpenStreetMapProvider({ params: { addressdetails: 1, 'accept-language': 'en', limit: 5 } })
    ))
  }
  return providerPromise
}

function usePlaceSuggestions(query, enabled) {
  const [places, setPlaces] = useState([])
  const [searching, setSearching] = useState(false)

  const text = query.trim()
  const asking = enabled && text.length >= MIN_QUERY

  useEffect(() => {
    if (!asking) return undefined

    let live = true
    // set inside the timer, not before it: "searching" should mean a request is in flight, and
    // setting state synchronously in an effect body cascades a render for nothing
    const timer = setTimeout(async () => {
      if (!live) return
      setSearching(true)
      try {
        const results = await (await geocoder()).search({ query: text })
        if (!live) return
        setPlaces([...new Set(results.map(shortPlaceName).filter(Boolean))].slice(0, 5))
      } catch {
        if (live) setPlaces([])
      } finally {
        if (live) setSearching(false)
      }
    }, PAUSE_MS)

    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [text, asking])

  // derived, not stored: clearing the list in an effect would set state during render and
  // cascade a second one for nothing
  return { places: asking ? places : [], searching: asking && searching }
}

// "Charlotte, Mecklenburg County, North Carolina, 28202, United States" is not a location a
// resume would ever say. Keep the place, the region, and the country when it is not the US.
function shortPlaceName(result) {
  const address = result.raw?.address || {}
  const place = address.city || address.town || address.village || address.hamlet
    || address.county || result.raw?.name
  const region = address.state || address.region
  const isUS = address.country_code?.toUpperCase() === 'US'
  return [place, region, isUS ? undefined : address.country].filter(Boolean).join(', ')
    || result.label?.split(',')[0]
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
  const { places, searching } = usePlaceSuggestions(draft, suggest && editing)
  const [active, setActive] = useState(-1)

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

  function choose(place) {
    onSave(place)
    setDraft(place)
    setEditing(false)
  }

  const shell = 'flex min-h-11 items-center gap-1.5 rounded-[0.625rem] border border-border bg-soft-paper px-3 transition-[width] duration-200 ease-out'

  if (editing) {
    return (
      // relative, because the suggestion list hangs off this box and has to count as inside it
      <div ref={box} className={`relative ${grow ? growWidth : width}`}>
        <form
          className={`${shell} animate-soft-in w-full focus-within:border-obsidian`}
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
            onChange={(event) => { setDraft(event.target.value); setActive(-1) }}
            placeholder={placeholder}
            aria-label={label}
            autoComplete="off"
            role={suggest ? 'combobox' : undefined}
            aria-expanded={suggest ? places.length > 0 : undefined}
            aria-controls={suggest ? listId : undefined}
            aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
            onKeyDown={(event) => {
              if (!suggest || places.length === 0) return
              // arrow keys move through the list, Enter takes the highlighted one — a
              // suggestion list reachable only by mouse is half a control
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                setActive((index) => (index + 1) % places.length)
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                setActive((index) => (index <= 0 ? places.length - 1 : index - 1))
              } else if (event.key === 'Enter' && active >= 0) {
                event.preventDefault()
                choose(places[active])
              }
            }}
            // the ring is drawn by the box around it, which is the shape the user sees
            className="min-w-0 flex-1 self-stretch bg-transparent text-sm text-ink outline-none"
          />
          {searching && <span className="shrink-0 text-[11px] text-muted">…</span>}
          <button type="submit" className="shrink-0 px-1 text-xs text-ink hover:underline" disabled={saving}>
            {value ? 'Save' : 'Confirm'}
          </button>
        </form>

        {/* A plain list, not a `datalist`. The native one renders outside this subtree, so
            clicking a suggestion registered as a click *away* — which abandoned the edit and
            is exactly why picking a city never saved. */}
        {suggest && places.length > 0 && (
          <ul
            id={listId}
            role="listbox"
            className="animate-soft-in absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-[0.625rem] border border-border bg-soft-paper py-1 shadow-sm"
          >
            {places.map((place, index) => (
              <li key={place} id={`${listId}-${index}`} role="option" aria-selected={index === active}>
                <button
                  type="button"
                  className={`flex w-full items-center px-3 py-2 text-left text-sm ${
                    index === active ? 'bg-surface text-ink' : 'text-charcoal hover:bg-surface hover:text-ink'
                  }`}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(place)}
                >
                  {place}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
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
