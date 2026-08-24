import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useDeferredValue, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader, Spinner } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { apiFetch } from '../lib/api'

const columns = [
  ['Title', 'title'],
  ['Company', 'company_name'],
  ['Location', 'location'],
  ['Type', 'work_type'],
  ['Match', 'match_score'],
  ['Status', 'status'],
  ['Added', 'created_at'],
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

export default function DashboardPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const [showModal, setShowModal] = useState(false)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
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

  function toggleSort(key) {
    setPage(1)
    if (sortKey === key) setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const createJob = useMutation({
    mutationFn: ({ description, idempotencyKey }) =>
      apiFetch('/jobs', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify({ description }),
      }),
    onSuccess: () => {
      analyzeRequestKey.current = null
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
      setPage(1)
      setText('')
      setShowModal(false)
      setNotice({ tone: 'success', message: 'Analysis saved to your dashboard.' })
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
    queryKey: ['jobs', page, deferredSearch, statusFilter, sortKey, sortDir],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), sort: sortKey, direction: sortDir })
      if (deferredSearch.trim()) params.set('search', deferredSearch.trim())
      if (statusFilter !== 'all') params.set('status', statusFilter)
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
    ['Total', stats.total, 'all'],
    ...statusOptions.map(([label, key]) => [label, stats.by_status[key], key]),
  ] : []

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs text-muted">Job workspace</p>
            <h1 className="mt-1 text-base font-medium text-ink">Your job descriptions</h1>
            <p className="mt-1 text-sm text-muted">Analyze roles, compare skills, and track every application.</p>
          </div>
          <button
            className="primary-button shrink-0"
            onClick={() => {
              createJob.reset()
              setShowModal(true)
            }}
          >
            <span className="mr-2 text-base" aria-hidden="true">+</span>
            Analyze new JD
          </button>
        </header>

        {notice && <InlineAlert tone={notice.tone} className="mb-4">{notice.message}</InlineAlert>}

        <section aria-label="Application summary" className="mb-8">
          {statsQuery.isLoading ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {Array.from({ length: 6 }, (_, index) => <div key={index} className="skeleton h-20" />)}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {summaryItems.map(([label, value, key]) => {
                const selected = statusFilter === key
                return (
                  <button
                    key={key}
                    onClick={() => {
                      setStatusFilter(key)
                      setPage(1)
                    }}
                    aria-pressed={selected}
                    className={`rounded-2xl px-4 py-3 text-left ${
                      selected ? 'bg-deep-teal text-white' : 'border border-border bg-soft-paper text-ink hover:border-ash'
                    }`}
                  >
                    <span className={`block text-xs ${selected ? 'text-white/75' : 'text-muted'}`}>{label}</span>
                    <span className="mt-1 block text-base font-medium">{value ?? 0}</span>
                  </button>
                )
              })}
            </div>
          )}
        </section>

        <section aria-labelledby="jobs-table-title">
          <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <h2 id="jobs-table-title" className="text-base font-medium text-ink">Applications</h2>
            <label className="relative block w-full sm:w-72">
              <span className="sr-only">Search jobs</span>
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true">⌕</span>
              <input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value)
                  setPage(1)
                }}
                placeholder="Search title or company"
                className="control py-2.5 pl-9 pr-3 text-sm"
              />
            </label>
          </div>

          <div className="surface-card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted">
                    {columns.map(([label, key]) => (
                      <th key={key} className="px-4 py-3 font-normal">
                        <button
                          onClick={() => toggleSort(key)}
                          className="inline-flex items-center gap-1 hover:text-ink"
                          aria-label={`Sort by ${label}`}
                        >
                          {label}
                          {sortKey === key && <span aria-hidden="true">{sortDir === 'asc' ? '↑' : '↓'}</span>}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className={jobsQuery.isFetching ? 'opacity-60' : ''}>
                  {data.jobs.map((job, index) => (
                    <tr
                      key={job.id}
                      onClick={() => navigate(`/jobs/${job.id}`)}
                      onKeyDown={(event) => event.target === event.currentTarget && event.key === 'Enter' && navigate(`/jobs/${job.id}`)}
                      tabIndex={0}
                      aria-label={`Open ${job.title || 'job'} details`}
                      className="animate-soft-in border-b border-border last:border-0 hover:bg-surface"
                      style={{ animationDelay: `${Math.min(index * 25, 150)}ms` }}
                    >
                      <td className="px-4 py-3 font-medium text-ink">{job.title || 'Untitled role'}</td>
                      <td className="px-4 py-3 text-muted">{job.company_name || '—'}</td>
                      <td className="px-4 py-3 text-muted">{job.location || '—'}</td>
                      <td className="px-4 py-3">
                        {job.work_type ? <span className="rounded-full border border-border px-2.5 py-1 text-xs text-muted">{job.work_type.replace('_', ' ')}</span> : '—'}
                      </td>
                      <td className="px-4 py-3 font-medium text-ink">{job.match_score != null ? `${job.match_score}%` : '—'}</td>
                      <td className="px-4 py-3">
                        <select
                          value={job.status}
                          onChange={(event) => updateStatus.mutate({ id: job.id, status: event.target.value })}
                          onClick={(event) => event.stopPropagation()}
                          disabled={updateStatus.isPending && updateStatus.variables?.id === job.id}
                          aria-label={`Status for ${job.title || 'job'}`}
                          className="rounded-full border border-border bg-surface px-2.5 py-1 text-xs text-ink"
                        >
                          {statusOptions.map(([label, value]) => <option key={value} value={value}>{label}</option>)}
                        </select>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted">{new Date(job.created_at).toLocaleDateString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {data.jobs.length === 0 && (
              <div className="px-4 py-12 text-center animate-soft-in">
                <p className="text-sm font-medium text-ink">No matching jobs</p>
                <p className="mt-1 text-xs text-muted">Try another search or analyze a new description.</p>
              </div>
            )}

            <footer className="flex items-center justify-between border-t border-border px-4 py-3 text-xs text-muted">
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
      </main>

      <Dialog
        open={showModal}
        title="Analyze a job description"
        description="Paste the full posting to extract requirements and compare them with your saved skills."
        onClose={closeModal}
        dismissible={!createJob.isPending}
        footer={(
          <>
            <button className="secondary-button" onClick={closeModal} disabled={createJob.isPending}>Cancel</button>
            <button
              className="primary-button min-w-28"
              disabled={createJob.isPending || text.trim().length < 50 || text.trim().length > 10000}
              onClick={() => {
                analyzeRequestKey.current ??= crypto.randomUUID()
                createJob.mutate({ description: text, idempotencyKey: analyzeRequestKey.current })
              }}
            >
              <ButtonLabel pending={createJob.isPending} pendingText="Analyzing…">Analyze</ButtonLabel>
            </button>
          </>
        )}
      >
        <label className="block text-sm text-ink" htmlFor="job-description">Job description</label>
        <textarea
          id="job-description"
          autoFocus
          disabled={createJob.isPending}
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            analyzeRequestKey.current = null
            createJob.reset()
          }}
          onKeyDown={(event) => {
            const length = text.trim().length
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !createJob.isPending && length >= 50 && length <= 10000) {
              event.preventDefault()
              analyzeRequestKey.current ??= crypto.randomUUID()
              createJob.mutate({ description: text, idempotencyKey: analyzeRequestKey.current })
            }
          }}
          placeholder="Paste a job description here…"
          className="control mt-2 h-64 resize-y p-4 text-sm leading-6"
        />
        <div className="mt-2 flex items-start justify-between gap-4 text-xs text-muted">
          <span>Minimum 50 characters · ⌘/Ctrl + Enter to analyze</span>
          <span className={text.length > 10000 ? 'text-ink' : ''}>{text.length.toLocaleString()} / 10,000</span>
        </div>
        {createJob.error && <InlineAlert className="mt-4">{createJob.error.message}</InlineAlert>}
      </Dialog>
    </div>
  )
}
