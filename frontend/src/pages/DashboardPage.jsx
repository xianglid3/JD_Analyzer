import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useDeferredValue, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader, Spinner } from '../components/Feedback'
import NavBar from '../components/NavBar'
import SelectMenu from '../components/SelectMenu'
import StatusFilterMenu from '../components/StatusFilterMenu'
import { SourceLink, TrashIcon } from '../components/SourceLink'
import { apiFetch } from '../lib/api'
import { toast } from '../lib/toast'
import { requestKey } from '../lib/requestKey'

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
  const [pendingDelete, setPendingDelete] = useState(null)
  const deferredSearch = useDeferredValue(search)
  const analyzeRequestKey = useRef(null)

  const closeModal = useCallback(() => {
    setShowModal(false)
  }, [])

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
    onSuccess: (_data, variables) => {
      toast.success('Link saved.')
      // the other direction of the same problem: the job page is not mounted, so its cached
      // copy would keep the old link until it remounted
      queryClient.setQueryData(['job', String(variables.id)], (job) => (
        job ? { ...job, source_url: variables.source_url } : job
      ))
    },
    onError: (error) => toast.error(error.message),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })

  const deleteJob = useMutation({
    mutationFn: (id) => apiFetch(`/jobs/${id}`, { method: 'DELETE' }),
    onSuccess: () => toast.success('Job deleted.'),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      setPendingDelete(null)
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
    },
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) =>
      apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    onSuccess: (_data, variables) => {
      toast.success('Status updated.')
      queryClient.setQueryData(['job', String(variables.id)], (job) => (
        job ? { ...job, status: variables.status } : job
      ))
    },
    onError: (error) => toast.error(error.message),
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
            <h1 className="page-heading mt-2">Your Jobs</h1>
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
            Analyze
          </button>
        </header>

        <div className="grid items-start gap-6">
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
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_170px_170px] xl:w-[42rem]">
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
                <StatusFilterMenu
                  options={summaryItems}
                  selected={statusFilters}
                  total={stats?.total}
                  onToggle={toggleStatus}
                  onClear={() => { setStatusFilters([]); setPage(1) }}
                />
              </div>
            </div>

            <div className="surface-card overflow-hidden">
              {/* One template, used by this header and by every row below it. Two copies drift
                  the moment a column is added, which is how the link column ended up with its
                  values under the wrong headings. */}
              <div className="job-grid hidden border-b border-border px-4 py-3 font-mono text-[11px] uppercase tracking-[0.06em] text-muted sm:grid">
                <span>Role</span>
                <span>Location</span>
                <span>Setup</span>
                <span>Added</span>
                <span>Fit</span>
                <span>Source</span>
                <span>Status</span>
                <span className="sr-only">Actions</span>
              </div>

              <ul className={jobsQuery.isFetching ? 'opacity-60' : ''} aria-busy={jobsQuery.isFetching}>
                {data.jobs.map((job, index) => (
                  <li
                    key={job.id}
                    className="group/row animate-soft-in border-b border-border p-4 last:border-0 hover:bg-surface"
                    style={{ animationDelay: `${Math.min(index * 25, 150)}ms` }}
                  >
                    <div className="job-grid">
                      <div className="min-w-0">
                        <Link to={`/jobs/${job.id}`} className="block truncate text-sm font-medium text-ink hover:underline hover:underline-offset-4">
                          {job.title || 'Untitled role'}
                        </Link>
                        <p className="mt-1 truncate text-xs text-muted">
                          {job.company_name || 'Company not listed'}
                        </p>
                      </div>

                      <p className="truncate text-xs text-muted" title={job.location || undefined}>
                        {job.location || 'Not listed'}
                      </p>

                      {/* its own column: remote-or-not is the thing people scan a list for, and
                          stacked behind the city it was the last thing they found */}
                      <div>
                        {job.work_type ? (
                          <span className="inline-flex items-center rounded-full border border-border bg-soft-paper px-2.5 py-1 text-xs capitalize text-muted">
                            {job.work_type.replace('_', ' ')}
                          </span>
                        ) : (
                          <span className="text-xs text-muted">—</span>
                        )}
                      </div>

                      <p className="text-xs text-muted">{new Date(job.created_at).toLocaleDateString()}</p>

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
                          updateLink.mutate({ id: job.id, source_url: sourceUrl })
                        }}
                      />

                      <div>
                        <SelectMenu
                          value={job.status}
                          onChange={(nextStatus) => {
                              updateStatus.mutate({ id: job.id, status: nextStatus })
                          }}
                          disabled={updateStatus.isPending && updateStatus.variables?.id === job.id}
                          ariaLabel={`Status for ${job.title || 'job'}`}
                          options={statusOptions.map(([label, value]) => ({ label, value }))}
                        />
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
