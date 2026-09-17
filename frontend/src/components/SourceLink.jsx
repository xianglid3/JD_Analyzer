import { useState } from 'react'

function PenIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
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

// The posting link, short enough to sit in a row or beside a heading. Hovering (or focusing) reveals edit and copy
// to its right; clicking the link itself just opens the posting, which is what it is for.
export function SourceLink({ job, onSave, saving }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(job.source_url || '')
  const [copied, setCopied] = useState(false)

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
        className="flex items-center gap-1"
        onSubmit={(event) => {
          event.preventDefault()
          onSave(value.trim() || null)
          setEditing(false)
        }}
      >
        <input
          autoFocus
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="https://…"
          aria-label={`Posting link for ${job.title || 'job'}`}
          className="control min-w-0 flex-1 px-2 py-1 text-xs"
        />
        <button type="submit" className="icon-button" aria-label="Save link" disabled={saving}>✓</button>
        <button
          type="button"
          className="icon-button"
          aria-label="Cancel"
          onClick={() => { setValue(job.source_url || ''); setEditing(false) }}
        >
          ×
        </button>
      </form>
    )
  }

  return (
    <div className="group/link flex min-w-0 items-center gap-1">
      {job.source_url ? (
        <a
          href={job.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="min-w-0 truncate text-xs text-charcoal underline underline-offset-4 hover:text-ink"
          title={job.source_url}
        >
          {job.source_url.replace(/^https?:\/\/(www\.)?/, '')}
        </a>
      ) : (
        <span className="truncate text-xs text-muted">No link</span>
      )}

      {/* revealed on hover, and on keyboard focus — otherwise these are unreachable without a
          mouse, which is the usual cost of hiding controls behind :hover */}
      <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover/link:opacity-100">
        <button
          type="button"
          className="icon-button size-7"
          aria-label={job.source_url ? `Edit link for ${job.title || 'job'}` : `Add a link for ${job.title || 'job'}`}
          onClick={() => { setValue(job.source_url || ''); setEditing(true) }}
        >
          <PenIcon />
        </button>
        {job.source_url && (
          <button
            type="button"
            className="icon-button size-7"
            aria-label={copied ? 'Link copied' : `Copy link for ${job.title || 'job'}`}
            onClick={copy}
          >
            {copied ? <span className="text-[11px] text-terminal-green" aria-hidden="true">✓</span> : <CopyIcon />}
          </button>
        )}
      </span>
    </div>
  )
}
