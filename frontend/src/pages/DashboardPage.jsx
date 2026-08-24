import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useDeferredValue, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import NavBar from '../components/NavBar'

// status → tag colors (Tailwind default palette)
const statusColors = {
  saved: 'bg-gray-100 text-gray-700',
  applied: 'bg-indigo-50 text-indigo-700',
  interview: 'bg-amber-50 text-amber-700',
  offer: 'bg-green-50 text-green-700',
  rejected: 'bg-red-50 text-red-700',
  ghosted: 'bg-slate-100 text-slate-600',
  accepted: 'bg-emerald-50 text-emerald-700',
  decline: 'bg-rose-50 text-rose-700',
}

// table columns: [header label, job field to sort by]
const columns = [
  ['Title', 'title'],
  ['Company', 'company_name'],
  ['Location', 'location'],
  ['Type', 'work_type'],
  ['Match', 'match_score'],
  ['Status', 'status'],
  ['Added', 'created_at'],
]

export default function DashboardPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [text, setText] = useState('')          // JD text input
  const [showModal, setShowModal] = useState(false)
  const [search, setSearch] = useState('')      // table search
  const [statusFilter, setStatusFilter] = useState('all')   // clickable filter chips
  const [sortKey, setSortKey] = useState('created_at')      // which column to sort by
  const [sortDir, setSortDir] = useState('desc')            // 'asc' | 'desc'
  const [page, setPage] = useState(1)
  const deferredSearch = useDeferredValue(search)
  const analyzeRequestKey = useRef(null)

  // click a header: same column → flip direction; new column → start ascending
  function toggleSort(key) {
    setPage(1)
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
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
    },
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) =>
      apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
    },
  })

  const { data, isLoading, isError, isFetching } = useQuery({
    queryKey: ['jobs', page, deferredSearch, statusFilter, sortKey, sortDir],
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(page),
        sort: sortKey,
        direction: sortDir,
      })
      if (deferredSearch.trim()) params.set('search', deferredSearch.trim())
      if (statusFilter !== 'all') params.set('status', statusFilter)
      return apiFetch(`/jobs?${params.toString()}`)
    },
    placeholderData: (previousData) => previousData,
  })

  const { data: stats } = useQuery({
    queryKey: ['jobs-stats'],
    queryFn: () => apiFetch('/jobs/stats'),
  })

  if (isLoading) return <p className="p-8">Loading…</p>
  if (isError) return <p className="p-8">Failed to load jobs</p>

  const firstEntry = data.total === 0 ? 0 : (data.page - 1) * data.per_page + 1
  const lastEntry = Math.min(data.page * data.per_page, data.total)

  return (
    <div className="min-h-screen bg-surface">
      <NavBar />

      <div className="max-w-6xl mx-auto px-8 py-8">
        {/* pipeline summary chips */}
        {stats && (
          <div className="grid grid-cols-3 lg:grid-cols-9 gap-3 mb-8">
            {[
              ['Total', stats.total, 'all'],
              ['Saved', stats.by_status.saved, 'saved'],
              ['Applied', stats.by_status.applied, 'applied'],
              ['Interview', stats.by_status.interview, 'interview'],
              ['Offer', stats.by_status.offer, 'offer'],
              ['Rejected', stats.by_status.rejected, 'rejected'],
              ['Ghosted', stats.by_status.ghosted, 'ghosted'],
              ['Accepted', stats.by_status.accepted, 'accepted'],
              ['Decline', stats.by_status.decline, 'decline'],
            ].map(([label, value, key]) => (
              <button
                key={label}
                onClick={() => {
                  setStatusFilter(key)
                  setPage(1)
                }}
                className={`rounded-lg px-4 py-3 text-center bg-white border ${
                  statusFilter === key ? 'border-primary ring-1 ring-primary' : 'border-border'
                }`}
              >
                <div className="text-2xl font-bold text-ink">{value}</div>
                <div className="text-xs text-muted uppercase tracking-wide mt-1">{label}</div>
              </button>
            ))}
          </div>
        )}

        {/* header row: title + search + analyze */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h1 className="text-xl font-semibold text-ink">Your Job Descriptions</h1>
          <div className="flex items-center gap-3">
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setPage(1)
              }}
              placeholder="Search title or company…"
              className="border border-border rounded-md px-3 py-2 text-sm w-64 bg-white"
            />
            <button
              onClick={() => setShowModal(true)}
              className="bg-primary text-white rounded-md px-4 py-2 text-sm font-medium whitespace-nowrap"
            >
              + Analyze new JD
            </button>
          </div>
        </div>

        {/* jobs table */}
        <div className="bg-white border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="text-xs text-muted uppercase tracking-wide bg-surface border-b border-border">
                {columns.map(([label, key]) => (
                  <th
                    key={key}
                    onClick={() => toggleSort(key)}
                    className="px-4 py-3 font-medium cursor-pointer select-none hover:text-ink"
                  >
                    {label}
                    {sortKey === key && (sortDir === 'asc' ? ' ▲' : ' ▼')}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((job) => (
                <tr
                  key={job.id}
                  onClick={() => navigate(`/jobs/${job.id}`)}
                  className="border-b border-border last:border-0 hover:bg-surface cursor-pointer"
                >
                  <td className="px-4 py-3 font-medium text-ink">{job.title}</td>
                  <td className="px-4 py-3 text-muted">{job.company_name}</td>
                  <td className="px-4 py-3 text-muted">{job.location}</td>
                  <td className="px-4 py-3">
                    {job.work_type && (
                      <span className="inline-block rounded-full border border-border px-2 py-0.5 text-xs text-muted">
                        {job.work_type}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-semibold text-primary">
                    {job.match_score != null ? `${job.match_score}%` : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <select
                      value={job.status}
                      onChange={(e) => updateStatus.mutate({ id: job.id, status: e.target.value })}
                      onClick={(e) => e.stopPropagation()}
                      className={`rounded-full px-3 py-1 text-xs font-medium border-0 cursor-pointer ${statusColors[job.status] || ''}`}
                    >
                      <option value="saved">Saved</option>
                      <option value="applied">Applied</option>
                      <option value="interview">Interview</option>
                      <option value="offer">Offer</option>
                      <option value="rejected">Rejected</option>
                      <option value="ghosted">Ghosted</option>
                      <option value="accepted">Accepted</option>
                      <option value="decline">Declined</option>
                    </select>
                  </td>
                  <td className="px-4 py-3 text-muted text-sm">
                    {new Date(job.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {data.jobs.length === 0 && (
            <p className="px-4 py-8 text-center text-muted text-sm">No jobs found.</p>
          )}

          <div className="border-t border-border bg-surface px-4 py-3 flex items-center justify-between">
            <span className="text-xs text-muted">
              Showing {firstEntry} to {lastEntry} of {data.total} entries
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                aria-label="Previous page"
                title="Previous page"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={page <= 1 || isFetching}
                className="p-2 text-muted hover:text-ink disabled:opacity-30 disabled:cursor-not-allowed"
              >
                ‹
              </button>
              <button
                type="button"
                aria-label="Next page"
                title="Next page"
                onClick={() => setPage((current) => current + 1)}
                disabled={page >= data.total_pages || isFetching}
                className="p-2 text-muted hover:text-ink disabled:opacity-30 disabled:cursor-not-allowed"
              >
                ›
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* analyze modal */}
      {showModal && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center px-4"
          onClick={() => setShowModal(false)}
        >
          <div className="bg-white rounded-xl p-6 w-full max-w-lg shadow-lg" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-ink mb-1">Analyze a job description</h2>
            <p className="text-sm text-muted mb-3">Paste the full text below (50–10000 characters).</p>
            <textarea
              value={text}
              onChange={(e) => {
                setText(e.target.value)
                analyzeRequestKey.current = null
              }}
              placeholder="Paste a job description…"
              className="w-full h-48 border border-border rounded-md p-3 text-sm"
            />
            <p className={`text-xs mt-1 text-right ${text.length > 10000 || (text.length > 0 && text.length < 50) ? 'text-red-600' : 'text-muted'}`}>
              {text.length} / 10000
            </p>
            {createJob.error && (
              <p className="text-red-600 text-sm mt-2">{createJob.error.message}</p>
            )}
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setShowModal(false)}
                className="border border-border rounded-md px-4 py-2 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  analyzeRequestKey.current ??= crypto.randomUUID()
                  createJob.mutate({
                    description: text,
                    idempotencyKey: analyzeRequestKey.current,
                  })
                }}
                disabled={createJob.isPending || text.length < 50 || text.length > 10000}
                className="bg-primary text-white rounded-md px-4 py-2 text-sm font-medium disabled:opacity-50"
              >
                {createJob.isPending ? 'Analyzing…' : 'Analyze'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
