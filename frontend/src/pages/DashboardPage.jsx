import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useDeferredValue, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader, Spinner } from '../components/Feedback'
import NavBar from '../components/NavBar'
import SelectMenu from '../components/SelectMenu'
import { apiFetch } from '../lib/api'
import { requestKey } from '../lib/requestKey'

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

function TrashIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}

// The posting link, short enough to sit in a row. Hovering (or focusing) reveals edit and copy
// to its right; clicking the link itself just opens the posting, which is what it is for.
function SourceLink({ job, onSave, saving }) {
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


const sortOptions = [
  ['Recently added', 'created_at', 'desc'],
  ['Oldest added', 'created_at', 'asc'],
  ['Highest match', 'match_score', 'desc'],
  ['Lowest match', 'match_score', 'asc'],
  ['Job title A–Z', 'title', 'asc'],
  ['Company A–Z', 'company_name', 'asc'],
]

const statusOptions = [
  ['Saved', 'saved'],
  ['Applied', 'applied'],
  ['Interview', 'interview'],
  ['Offer', 'offer'],
  ['Rejected', 'rejected'],
  ['Ghosted', 'ghosted'],
  ['Accepted', 'accepted'],
  ['Declined', 'decline'],
]

function PlusIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M10 4v12M4 10h12" strokeLinecap="round" />
    </svg>
  )
}

function SearchIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="8.5" cy="8.5" r="4.75" />
      <path d="m12 12 4 4" strokeLinecap="round" />
    </svg>
  )
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [showModal, setShowModal] = useState(false)
  const [search, setSearch] = useState('')
  const [statusFilters, setStatusFilters] = useState([])
  const [sortKey, setSortKey] = useState('created_at')
  const [sortDir, setSortDir] = useState('desc')
  const [page, setPage] = useState(1)
  const [notice, setNotice] = useState(null)
  const [pendingDelete, setPendingDelete] = useState(null)
  const deferredSearch = useDeferredValue(search)
  const analyzeRequestKey = useRef(null)

  const closeModal = useCallback(() => {
    setShowModal(false)
  }, [])

  useEffect(() => {
    if (!notice) return undefined
    const timeout = window.setTimeout(() => setNotice(null), 3200)
    return () => window.clearTimeout(timeout)
  }, [notice])

  const createDraft = useMutation({
    mutationFn: ({ description, sourceUrl: requestSourceUrl, idempotencyKey }) =>
      apiFetch('/jobs/drafts', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify({ description, source_url: requestSourceUrl.trim() || null }),
      }),
    onSuccess: (data) => {
      analyzeRequestKey.current = null
      setText('')
      setSourceUrl('')
      setShowModal(false)
      navigate(data.confirmed ? `/jobs/${data.id}` : `/jobs/review/${data.id}`)
    },
  })

  const updateLink = useMutation({
    mutationFn: ({ id, source_url: sourceUrl }) =>
      apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ source_url: sourceUrl }) }),
    onSuccess: (_data, variables) => setNotice({ jobId: variables.id, tone: 'success', message: 'Link saved.' }),
    onError: (error, variables) => setNotice({ jobId: variables.id, tone: 'error', message: error.message }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })

  const deleteJob = useMutation({
    mutationFn: (id) => apiFetch(`/jobs/${id}`, { method: 'DELETE' }),
    onError: (error, id) => setNotice({ jobId: id, tone: 'error', message: error.message }),
    onSettled: () => {
      setPendingDelete(null)
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
    },
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) =>
      apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    onSuccess: (_data, variables) => setNotice({ jobId: variables.id, tone: 'success', message: 'Status updated.' }),
    onError: (error, variables) => setNotice({ jobId: variables.id, tone: 'error', message: error.message }),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
    },
  })

  const jobsQuery = useQuery({
    queryKey: ['jobs', page, deferredSearch, statusFilters.join(','), sortKey, sortDir],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), sort: sortKey, direction: sortDir })
      if (deferredSearch.trim()) params.set('search', deferredSearch.trim())
      statusFilters.forEach((status) => params.append('status', status))
      return apiFetch(`/jobs?${params.toString()}`)
    },
    placeholderData: (previousData) => previousData,
  })

  const statsQuery = useQuery({
    queryKey: ['jobs-stats'],
    queryFn: () => apiFetch('/jobs/stats'),
  })

  if (jobsQuery.isLoading) return <PageLoader label="Loading your job tracker…" />

  if (jobsQuery.isError) {
    return (
      <div className="app-main bg-surface">
        <NavBar />
        <main className="page-container grid min-h-[70vh] place-items-center">
          <div className="text-center animate-soft-in">
            <p className="text-base font-medium text-ink">We couldn’t load your jobs.</p>
            <p className="mt-1 text-sm text-muted">Check your connection and try again.</p>
            <button className="secondary-button mt-4" onClick={() => jobsQuery.refetch()}>Try again</button>
          </div>
        </main>
      </div>
    )
  }

  const data = jobsQuery.data
  const stats = statsQuery.data
  const firstEntry = data.total === 0 ? 0 : (data.page - 1) * data.per_page + 1
  const lastEntry = Math.min(data.page * data.per_page, data.total)
  const summaryItems = stats ? [
    ...statusOptions.map(([label, key]) => [label, stats.by_status[key], key]),
  ] : []

  function toggleStatus(status) {
    setStatusFilters((current) => (
      current.includes(status)
        ? current.filter((value) => value !== status)
        : [...current, status]
    ))
    setPage(1)
  }

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="eyebrow">Job workspace</p>
            <h1 className="page-heading mt-2">Your job descriptions</h1>
            <p className="mt-2 text-sm text-muted">Analyze roles, compare skills, and track every application.</p>
          </div>
          <button
            className="primary-button shrink-0"
            onClick={() => {
              createDraft.reset()
              setShowModal(true)
            }}
          >
            <span className="mr-2"><PlusIcon /></span>
            Analyze new JD
          </button>
        </header>

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_240px]">
          <section aria-labelledby="jobs-table-title" className="min-w-0">
            <div className="mb-3 flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
              <div>
                <h2 id="jobs-table-title" className="text-base font-medium text-ink">Applications</h2>
                <p className="mt-1 text-xs text-muted">
                  {statusFilters.length === 0
                    ? 'Every role you are tracking'
                    : `${statusFilters.length} ${statusFilters.length === 1 ? 'status' : 'statuses'} selected`}
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_180px] xl:w-[32rem]">
                <label className="relative block">
                  <span className="sr-only">Search jobs</span>
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"><SearchIcon /></span>
                  <input
                    value={search}
                    onChange={(event) => {
                      setSearch(event.target.value)
                      setPage(1)
                    }}
                    placeholder="Search title or company"
                    className="control min-h-11 py-2.5 pl-9 pr-3 text-sm"
                  />
                </label>
                <SelectMenu
                  ariaLabel="Sort applications"
                  value={`${sortKey}:${sortDir}`}
                  options={sortOptions.map(([label, key, direction]) => ({ label, value: `${key}:${direction}` }))}
                  onChange={(nextValue) => {
                      const [nextKey, nextDirection] = nextValue.split(':')
                      setSortKey(nextKey)
                      setSortDir(nextDirection)
                      setPage(1)
                  }}
                />
              </div>
            </div>

            <div className="surface-card overflow-hidden">
              <div className="hidden grid-cols-[minmax(0,1.5fr)_minmax(9rem,0.8fr)_7rem_9rem] gap-4 border-b border-border px-4 py-3 font-mono text-[11px] uppercase tracking-[0.06em] text-muted sm:grid">
                <span>Role</span>
                <span>Work setup</span>
                <span>Fit</span>
                <span>Status</span>
              </div>

              <ul className={jobsQuery.isFetching ? 'opacity-60' : ''} aria-busy={jobsQuery.isFetching}>
                {data.jobs.map((job, index) => (
                  <li
                    key={job.id}
                    className="group/row animate-soft-in border-b border-border p-4 last:border-0 hover:bg-surface"
                    style={{ animationDelay: `${Math.min(index * 25, 150)}ms` }}
                  >
                    <div className="grid gap-4 sm:grid-cols-[minmax(0,1.4fr)_minmax(8rem,0.7fr)_6rem_minmax(0,1fr)_9rem_2.25rem] sm:items-center">
                      <div className="min-w-0">
                        <Link to={`/jobs/${job.id}`} className="block truncate text-sm font-medium text-ink hover:underline hover:underline-offset-4">
                          {job.title || 'Untitled role'}
                        </Link>
                        <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted">
                          <span>{job.company_name || 'Company not listed'}</span>
                          <span aria-hidden="true">·</span>
                          <span>Added {new Date(job.created_at).toLocaleDateString()}</span>
                        </p>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                        <span>{job.location || 'Location not listed'}</span>
                        {job.work_type && <span className="rounded-full border border-border bg-soft-paper px-2.5 py-1">{job.work_type.replace('_', ' ')}</span>}
                      </div>

                      <div aria-label={job.match_score != null ? `${job.match_score}% match` : 'No match score'}>
                        <p className="text-sm font-medium text-ink">{job.match_score != null ? `${job.match_score}%` : '—'}</p>
                        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-border">
                          <div className="h-full rounded-full bg-primary" style={{ width: `${job.match_score || 0}%` }} />
                        </div>
                      </div>

                      <SourceLink
                        job={job}
                        saving={updateLink.isPending && updateLink.variables?.id === job.id}
                        onSave={(sourceUrl) => {
                          setNotice(null)
                          updateLink.mutate({ id: job.id, source_url: sourceUrl })
                        }}
                      />

                      <div>
                        <SelectMenu
                          value={job.status}
                          onChange={(nextStatus) => {
                            setNotice(null)
                            updateStatus.mutate({ id: job.id, status: nextStatus })
                          }}
                          disabled={updateStatus.isPending && updateStatus.variables?.id === job.id}
                          ariaLabel={`Status for ${job.title || 'job'}`}
                          options={statusOptions.map(([label, value]) => ({ label, value }))}
                        />
                        {notice?.jobId === job.id && (
                          <p
                            role={notice.tone === 'error' ? 'alert' : 'status'}
                            className={`mt-1.5 text-xs ${notice.tone === 'error' ? 'text-red-700' : 'text-terminal-green'}`}
                          >
                            {notice.tone === 'success' && <span aria-hidden="true">✓ </span>}
                            {notice.message}
                          </p>
                        )}
                      </div>

                      {/* aligned with the title, and only visible on hover or focus: deleting a
                          job is not something to leave one stray click away on every row */}
                      <div className="flex justify-end self-start sm:self-center">
                        <button
                          type="button"
                          className="icon-button text-muted opacity-0 transition-opacity duration-150 hover:text-red-700 focus-visible:opacity-100 group-hover/row:opacity-100"
                          aria-label={`Delete ${job.title || 'job'}`}
                          onClick={() => setPendingDelete(job)}
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>

              {data.jobs.length === 0 && (
                <div className="px-4 py-12 text-center animate-soft-in">
                  <p className="text-sm font-medium text-ink">No matching jobs</p>
                  <p className="mt-1 text-xs text-muted">Try another search, change the pipeline filter, or analyze a new description.</p>
                </div>
              )}

              <footer className="flex items-center justify-between border-t border-border px-4 py-3 font-mono text-[11px] uppercase tracking-[0.04em] text-muted">
                <span>Showing {firstEntry}–{lastEntry} of {data.total}</span>
                <div className="flex items-center gap-1">
                  {jobsQuery.isFetching && <Spinner size="sm" />}
                  <button
                    className="icon-button"
                    aria-label="Previous page"
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                    disabled={page <= 1 || jobsQuery.isFetching}
                  >‹</button>
                  <button
                    className="icon-button"
                    aria-label="Next page"
                    onClick={() => setPage((current) => current + 1)}
                    disabled={page >= data.total_pages || jobsQuery.isFetching}
                  >›</button>
                </div>
              </footer>
            </div>
          </section>

          <aside aria-labelledby="pipeline-title" className="surface-card overflow-hidden lg:sticky lg:top-20">
            <div className="flex items-end justify-between gap-3 border-b border-border px-4 py-3">
              <div>
                <p className="eyebrow">Filter</p>
                <h2 id="pipeline-title" className="mt-1 text-base font-medium text-ink">Pipeline status</h2>
              </div>
              {statusFilters.length > 0 && (
                <button
                  type="button"
                  className="min-h-11 px-1 text-xs text-muted underline-offset-4 hover:text-ink hover:underline"
                  onClick={() => {
                    setStatusFilters([])
                    setPage(1)
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            {statsQuery.isLoading ? (
              <div className="space-y-2 p-3">
                {Array.from({ length: 6 }, (_, index) => <div key={index} className="skeleton h-11" />)}
              </div>
            ) : (
              <fieldset className="p-2">
                <legend className="sr-only">Filter applications by pipeline status</legend>
                {summaryItems.map(([label, value, key]) => {
                  const selected = statusFilters.includes(key)
                  return (
                    <label
                      key={key}
                      className={`flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-md px-3 text-sm transition-colors ${
                        selected ? 'bg-surface text-ink' : 'text-charcoal hover:bg-surface hover:text-ink'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => toggleStatus(key)}
                        className="size-4 shrink-0 accent-obsidian"
                      />
                      <span className="flex-1">{label}</span>
                      <span className="font-mono text-xs text-muted">{value ?? 0}</span>
                    </label>
                  )
                })}
                <p className="px-3 pb-2 pt-1 text-xs leading-5 text-muted">
                  {statusFilters.length === 0 ? `${stats?.total ?? 0} total applications` : 'Showing any selected status'}
                </p>
              </fieldset>
            )}
          </aside>
        </div>
      </main>

      <Dialog
        open={pendingDelete !== null}
        width="max-w-md"
        title="Delete this job?"
        description="The analysis, match, and every tailoring run for it go too. This cannot be undone."
        onClose={() => setPendingDelete(null)}
        dismissible={!deleteJob.isPending}
        footer={(
          <>
            <button
              className="secondary-button"
              onClick={() => setPendingDelete(null)}
              disabled={deleteJob.isPending}
            >
              Cancel
            </button>
            <button
              className="danger-button"
              onClick={() => deleteJob.mutate(pendingDelete.id)}
              disabled={deleteJob.isPending}
            >
              <ButtonLabel pending={deleteJob.isPending} pendingText="Deleting…">Delete</ButtonLabel>
            </button>
          </>
        )}
      >
        <p className="text-sm text-ink">{pendingDelete?.title || 'Untitled role'}</p>
        <p className="mt-1 text-xs text-muted">{pendingDelete?.company_name || 'Company not listed'}</p>
      </Dialog>

      <Dialog
        open={showModal}
        title="Analyze a job description"
        description="Paste the full posting to extract requirements and compare them with your saved skills."
        onClose={closeModal}
        dismissible={!createDraft.isPending}
        footer={(
          <>
            <button className="secondary-button" onClick={closeModal} disabled={createDraft.isPending}>Cancel</button>
            <button
              className="primary-button min-w-28"
              disabled={createDraft.isPending || text.trim().length < 50 || text.trim().length > 10000}
              onClick={() => {
                analyzeRequestKey.current ??= requestKey()
                createDraft.mutate({ description: text, sourceUrl, idempotencyKey: analyzeRequestKey.current })
              }}
            >
              <ButtonLabel pending={createDraft.isPending} pendingText="Analyzing…">Analyze</ButtonLabel>
            </button>
          </>
        )}
      >
        <label className="block text-sm text-ink" htmlFor="job-source-url">
          Job posting URL <span className="text-muted">(optional)</span>
        </label>
        <input
          id="job-source-url"
          type="url"
          maxLength={2048}
          disabled={createDraft.isPending}
          value={sourceUrl}
          onChange={(event) => {
            setSourceUrl(event.target.value)
            analyzeRequestKey.current = null
            createDraft.reset()
          }}
          placeholder="https://company.com/jobs/role"
          className="control mt-2 px-3 py-2.5 text-sm"
        />
        <label className="mt-4 block text-sm text-ink" htmlFor="job-description">Job description</label>
        <textarea
          id="job-description"
          autoFocus
          data-dialog-autofocus
          disabled={createDraft.isPending}
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            analyzeRequestKey.current = null
            createDraft.reset()
          }}
          onKeyDown={(event) => {
            const length = text.trim().length
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !createDraft.isPending && length >= 50 && length <= 10000) {
              event.preventDefault()
              analyzeRequestKey.current ??= requestKey()
              createDraft.mutate({ description: text, sourceUrl, idempotencyKey: analyzeRequestKey.current })
            }
          }}
          placeholder="Paste a job description here…"
          className="control mt-2 h-64 resize-y p-4 text-sm leading-6"
        />
        <div className="mt-2 flex items-start justify-between gap-4 text-xs text-muted">
          <span>Minimum 50 characters · ⌘/Ctrl + Enter to analyze</span>
          <span className={text.length > 10000 ? 'text-ink' : ''}>{text.length.toLocaleString()} / 10,000</span>
        </div>
        {createDraft.error && <InlineAlert className="mt-4">{createDraft.error.message}</InlineAlert>}
      </Dialog>
    </div>
  )
}
