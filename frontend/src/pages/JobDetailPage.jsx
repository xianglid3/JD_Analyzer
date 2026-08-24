import { useParams, useNavigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import NavBar from '../components/NavBar'

export default function JobDetailPage() {
  const { id } = useParams()
  const [notes, setNotes] = useState('')

  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const saveNotes = useMutation({
    mutationFn: () => apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ notes }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['job', id] }),
  })

  const deleteJob = useMutation({
    mutationFn: () => apiFetch(`/jobs/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      navigate('/dashboard')
    },
  })

  const { data: job, isLoading, isError } = useQuery({
    queryKey: ['job', id],
    queryFn: () => apiFetch(`/jobs/${id}`),
  })

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (job?.notes) setNotes(job.notes)
  }, [job])

  if (isLoading) return <p className="p-8">Loading…</p>
  if (isError) return <p className="p-8">Job not found.</p>

  return (
    <div className="min-h-screen bg-surface">
      <NavBar />

      <div className="max-w-6xl mx-auto px-8 py-8">
        <button onClick={() => navigate('/dashboard')} className="text-sm text-muted hover:text-ink mb-4">
          ← Back to Dashboard
        </button>

        <h1 className="text-2xl font-bold text-ink">{job.title}</h1>
        <p className="text-muted mt-1">
          {job.company_name} • {job.location} • {job.work_type}
        </p>

        {/* match bar */}
        <div className="flex items-center gap-3 mt-4 mb-6">
          <span className="text-primary font-bold text-lg">
            {job.match_score != null ? `${job.match_score}%` : '—'}
          </span>
          <div className="flex-1 h-2 bg-white border border-border rounded-full overflow-hidden">
            <div className="h-full bg-primary" style={{ width: `${job.match_score || 0}%` }} />
          </div>
        </div>

        <div className="grid lg:grid-cols-3 gap-6">
          {/* main column */}
          <div className="lg:col-span-2 space-y-6">
            <div className="bg-white border border-border rounded-lg p-6">
              <h2 className="font-semibold text-ink mb-2">Summary</h2>
              <p className="text-ink">{job.summary}</p>
            </div>

            <div className="bg-white border border-border rounded-lg p-6">
              <h2 className="font-semibold text-primary mb-2">No-BS Translation</h2>
              <p className="text-ink bg-surface rounded-md p-3">{job.no_bs_translation}</p>
            </div>

            <div className="bg-white border border-border rounded-lg p-6">
              <h2 className="font-semibold text-ink mb-3">Skills</h2>
              <div className="flex flex-wrap gap-2">
                {job.skills.map((s) => (
                  <span key={s} className="bg-surface rounded-full px-3 py-1 text-sm">{s}</span>
                ))}
              </div>
            </div>

            {job.raw_description && (
              <details className="bg-white border border-border rounded-lg p-4">
                <summary className="cursor-pointer text-sm text-muted">View original job description</summary>
                <p className="mt-3 text-sm text-ink whitespace-pre-wrap">{job.raw_description}</p>
              </details>
            )}
          </div>

          {/* tracking sidebar */}
          <div className="bg-white border border-border rounded-lg p-6 h-fit">
            <h2 className="font-semibold text-ink mb-4">Tracking</h2>

            <label className="block text-sm text-muted mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Interview dates, recruiter contact, thoughts…"
              className="w-full h-32 border border-border rounded-md p-2 text-sm mb-3"
            />
            <button
              onClick={() => saveNotes.mutate()}
              disabled={saveNotes.isPending}
              className="w-full bg-primary text-white rounded-md py-2 text-sm font-medium disabled:opacity-50"
            >
              {saveNotes.isPending ? 'Saving…' : 'Save notes'}
            </button>

            <div className="border-t border-border mt-6 pt-4">
              <button onClick={() => deleteJob.mutate()} className="w-full text-red-600 text-sm">
                Delete job
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
