import { useState } from 'react'

function GlobeIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-3.5 shrink-0 text-muted" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18Z" />
    </svg>
  )
}

function CopyIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  )
}

export function TrashIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}

/**
 * The posting link as one control, not three things sharing a row.
 *
 * It was a link with a gap and then two icons that appeared on hover, which read as separate
 * elements that happened to be adjacent — and the gap had to be wide enough to reserve space
 * for icons that were not there. Bounded together in a single bordered field, the way Notion
 * shows a link, the reserved space becomes the control's own shape and nothing shifts.
 *
 * The actions stay visible rather than waiting for hover: they are inside the field now, so
 * they cost no extra width, and a control that appears on hover cannot be reached by keyboard
 * or touch at all.
 */
export function SourceLink({ job, onSave, saving, grow = false }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(job.source_url || '')
  const [copied, setCopied] = useState(false)

  const width = grow ? 'w-72' : 'w-full'

  async function copy() {
    try {
      await navigator.clipboard.writeText(job.source_url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)      // a denied clipboard permission is not worth an error state
    }
  }

  if (editing) {
    return (
      <form
        // wider while editing, and right-aligned in the header, so it grows leftwards into
        // empty space rather than pushing the status and delete around
        className={`flex min-h-11 items-center gap-1.5 rounded-md border border-border bg-soft-paper px-3 transition-[width] duration-200 ease-out ${
          grow ? 'w-[26rem] max-w-[60vw]' : 'w-full'
        }`}
        onSubmit={(event) => {
          event.preventDefault()
          onSave(value.trim() || null)
          setEditing(false)
        }}
      >
        <GlobeIcon />
        <input
          autoFocus
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="https://…"
          aria-label={`Posting link for ${job.title || 'job'}`}
          className="min-w-0 flex-1 self-stretch bg-transparent text-sm text-ink outline-none"
        />
        <button type="submit" className="shrink-0 px-1 text-xs text-ink hover:underline" disabled={saving}>
          Save
        </button>
        <button
          type="button"
          className="shrink-0 px-1 text-xs text-muted hover:text-ink"
          onClick={() => { setValue(job.source_url || ''); setEditing(false) }}
        >
          Cancel
        </button>
      </form>
    )
  }

  return (
    <div
      className={`flex min-h-11 items-center gap-1.5 rounded-md border border-border bg-soft-paper px-3 transition-[width] duration-200 ease-out ${width}`}
    >
      <GlobeIcon />

      {job.source_url ? (
        <a
          href={job.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="min-w-0 flex-1 truncate text-sm text-charcoal hover:text-ink hover:underline hover:underline-offset-4"
          title={job.source_url}
        >
          {job.source_url.replace(/^https?:\/\/(www\.)?/, '')}
        </a>
      ) : (
        <span className="min-w-0 flex-1 truncate text-sm text-muted">No link</span>
      )}

      {job.source_url && (
        <button
          type="button"
          className="shrink-0 px-1 text-muted hover:text-ink"
          aria-label={copied ? 'Link copied' : `Copy link for ${job.title || 'job'}`}
          onClick={copy}
        >
          {copied ? <span className="text-[11px] text-terminal-green" aria-hidden="true">✓</span> : <CopyIcon />}
        </button>
      )}

      <button
        type="button"
        className="shrink-0 px-1 text-xs text-muted hover:text-ink"
        aria-label={job.source_url ? `Edit link for ${job.title || 'job'}` : `Add a link for ${job.title || 'job'}`}
        onClick={() => { setValue(job.source_url || ''); setEditing(true) }}
      >
        {job.source_url ? 'Edit' : 'Add'}
      </button>
    </div>
  )
}
