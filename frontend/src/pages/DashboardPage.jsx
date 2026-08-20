import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useState } from 'react'
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
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [text, setText] = useState('')          // JD text input
  const [showModal, setShowModal] = useState(false)
  const [search, setSearch] = useState('')      // table search

  const createJob = useMutation({
    mutationFn: (description) =>
      apiFetch('/jobs', { method: 'POST', body: JSON.stringify({ description }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
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

  const { data, isLoading, isError } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => apiFetch('/jobs'),
  })

  const { data: stats } = useQuery({
    queryKey: ['jobs-stats'],
    queryFn: () => apiFetch('/jobs/stats'),
  })

  if (isLoading) return <p className="p-8">Loading…</p>
  if (isError) return <p className="p-8">Failed to load jobs</p>

  const filtered = data.jobs.filter((job) =>
    `${job.title || ''} ${job.company_name || ''}`.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="min-h-screen bg-surface">
      <NavBar />

      <div className="max-w-6xl mx-auto px-8 py-8">
        {/* pipeline summary chips */}
        {stats && (
          <div className="grid grid-cols-3 lg:grid-cols-6 gap-3 mb-8">
            {[
              ['Total', stats.total],
              ['Saved', stats.by_status.saved],
              ['Applied', stats.by_status.applied],
              ['Interview', stats.by_status.interview],
              ['Offer', stats.by_status.offer],
              ['Rejected', stats.by_status.rejected],
            ].map(([label, value]) => (
              <div key={label} className="bg-white border border-border rounded-lg px-4 py-3 text-center">
                <div className="text-2xl font-bold text-ink">{value}</div>
                <div className="text-xs text-muted uppercase tracking-wide mt-1">{label}</div>
              </div>
            ))}
          </div>
        )}

        {/* header row: title + search + analyze */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h1 className="text-xl font-semibold text-ink">Your Job Descriptions</h1>
          <div className="flex items-center gap-3">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
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
                <th className="px-4 py-3 font-medium">Title</th>
                <th className="px-4 py-3 font-medium">Company</th>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Match</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Added</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((job) => (
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
                    </select>
                  </td>
                  <td className="px-4 py-3 text-muted text-sm">
                    {new Date(job.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {filtered.length === 0 && (
            <p className="px-4 py-8 text-center text-muted text-sm">No jobs found.</p>
          )}
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
              onChange={(e) => setText(e.target.value)}
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
                onClick={() => createJob.mutate(text)}
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
