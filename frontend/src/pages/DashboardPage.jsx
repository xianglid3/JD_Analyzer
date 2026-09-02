import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useDeferredValue, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader, Spinner } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { apiFetch } from '../lib/api'

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

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) =>
      apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    onSuccess: () => setNotice({ tone: 'success', message: 'Application status updated.' }),
    onError: (error) => setNotice({ tone: 'error', message: error.message }),
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

        {notice && <InlineAlert tone={notice.tone} className="mb-4">{notice.message}</InlineAlert>}

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
                <label>
                  <span className="sr-only">Sort applications</span>
                  <select
                    className="control min-h-11 px-3 py-2.5 text-sm"
                    value={`${sortKey}:${sortDir}`}
                    onChange={(event) => {
                      const [nextKey, nextDirection] = event.target.value.split(':')
                      setSortKey(nextKey)
                      setSortDir(nextDirection)
                      setPage(1)
                    }}
                  >
                    {sortOptions.map(([label, key, direction]) => (
                      <option key={`${key}:${direction}`} value={`${key}:${direction}`}>{label}</option>
                    ))}
                  </select>
                </label>
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
                    className="animate-soft-in border-b border-border p-4 last:border-0 hover:bg-surface"
                    style={{ animationDelay: `${Math.min(index * 25, 150)}ms` }}
                  >
                    <div className="grid gap-4 sm:grid-cols-[minmax(0,1.5fr)_minmax(9rem,0.8fr)_7rem_9rem] sm:items-center">
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

                      <select
                        value={job.status}
                        onChange={(event) => updateStatus.mutate({ id: job.id, status: event.target.value })}
                        disabled={updateStatus.isPending && updateStatus.variables?.id === job.id}
                        aria-label={`Status for ${job.title || 'job'}`}
                        className="control min-h-11 px-3 py-2 text-sm text-ink"
                      >
                        {statusOptions.map(([label, value]) => <option key={value} value={value}>{label}</option>)}
                      </select>
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
                analyzeRequestKey.current ??= crypto.randomUUID()
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
              analyzeRequestKey.current ??= crypto.randomUUID()
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
