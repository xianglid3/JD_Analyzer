import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { apiFetch } from '../lib/api'

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

export default function JobDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [notes, setNotes] = useState('')
  const [status, setStatus] = useState('saved')
  const [deadline, setDeadline] = useState('')
  const [notice, setNotice] = useState(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const closeDeleteDialog = useCallback(() => setShowDeleteDialog(false), [])

  const jobQuery = useQuery({
    queryKey: ['job', id],
    queryFn: () => apiFetch(`/jobs/${id}`),
  })

  useEffect(() => {
    if (!jobQuery.data) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNotes(jobQuery.data.notes || '')
    setStatus(jobQuery.data.status)
    setDeadline(jobQuery.data.deadline || '')
  }, [jobQuery.data])

  const saveTracking = useMutation({
    mutationFn: () => apiFetch(`/jobs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ notes, status, deadline: deadline || null }),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', id] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
      setNotice({ tone: 'success', message: 'Tracking details saved.' })
    },
  })

  const deleteJob = useMutation({
    mutationFn: () => apiFetch(`/jobs/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
      navigate('/dashboard')
    },
  })

  if (jobQuery.isLoading) return <PageLoader label="Loading job details…" />

  if (jobQuery.isError) {
    return (
      <div className="app-main min-h-screen bg-surface">
        <NavBar />
        <main className="page-container grid min-h-[70vh] place-items-center text-center">
          <div className="animate-soft-in">
            <p className="text-base font-medium text-ink">Job not found</p>
            <p className="mt-1 text-sm text-muted">It may have been removed or belong to another account.</p>
            <button className="secondary-button mt-4" onClick={() => navigate('/dashboard')}>Back to dashboard</button>
          </div>
        </main>
      </div>
    )
  }

  const job = jobQuery.data
  const notesTooLong = notes.length > 5000
  const isDirty = notes !== (job.notes || '') || status !== job.status || deadline !== (job.deadline || '')
  const metadata = [job.company_name, job.location, job.work_type?.replace('_', ' ')].filter(Boolean)

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <button onClick={() => navigate('/dashboard')} className="mb-6 text-sm text-muted hover:text-ink">← Back to dashboard</button>

        <header className="mb-8">
          <p className="eyebrow">Job details</p>
          <h1 className="page-heading mt-2">{job.title || 'Untitled role'}</h1>
          {metadata.length > 0 && <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.06em] text-muted">{metadata.join(' · ')}</p>}

          <div className="mt-5 flex max-w-lg items-center gap-3" aria-label={`${job.match_score ?? 0}% skill match`}>
            <span className="shrink-0 text-sm font-medium text-ink">{job.match_score != null ? `${job.match_score}% match` : 'No match score'}</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-border">
              <div className="match-fill h-full rounded-full bg-primary" style={{ width: `${job.match_score || 0}%` }} />
            </div>
          </div>
        </header>

        {notice && <InlineAlert tone={notice.tone} className="mb-4">{notice.message}</InlineAlert>}
        {saveTracking.error && <InlineAlert className="mb-4">{saveTracking.error.message}</InlineAlert>}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="space-y-6">
            <section className="surface-card p-4">
              <h2 className="text-base font-medium text-ink">Summary</h2>
              <p className="mt-3 text-sm leading-6 text-ink">{job.summary || 'No summary was extracted.'}</p>
            </section>

            <section className="surface-card bg-obsidian p-4 text-white">
              <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-white/60">Plain-language read</p>
              <h2 className="mt-2 text-base font-medium text-white">No-BS translation</h2>
              <p className="mt-3 text-sm leading-6 text-white/85">{job.no_bs_translation || 'No translation was extracted.'}</p>
            </section>

            <section className="surface-card p-4">
              <h2 className="text-base font-medium text-ink">Key skills</h2>
              {job.skills.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {job.skills.map((skill) => (
                    <span key={skill} className="rounded-full border border-border bg-surface px-3 py-2 text-sm text-ink">{skill}</span>
                  ))}
                </div>
              ) : <p className="mt-2 text-sm text-muted">No concrete technical skills were listed.</p>}
            </section>

            {job.match_detail && (
              <section className="surface-card p-4">
                <h2 className="text-base font-medium text-ink">Match breakdown</h2>
                <div className="mt-4 grid gap-5 sm:grid-cols-2">
                  <div>
                    <p className="eyebrow">Matched</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {job.match_detail.matched.length > 0 ? job.match_detail.matched.map((skill) => (
                        <span key={skill} className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-ink"><span className="text-terminal-green">✓</span> {skill}</span>
                      )) : <span className="text-xs text-muted">None yet</span>}
                    </div>
                  </div>
                  <div>
                    <p className="eyebrow">Missing</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {job.match_detail.missing.length > 0 ? job.match_detail.missing.map((skill) => (
                        <span key={skill} className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted">○ {skill}</span>
                      )) : <span className="text-xs text-muted">Nothing missing</span>}
                    </div>
                  </div>
                </div>
              </section>
            )}

            {job.raw_description && (
              <details className="surface-card group p-4">
                <summary className="flex cursor-pointer list-none items-center justify-between text-sm text-ink">
                  View original job description
                  <span className="text-muted transition-transform group-open:rotate-180" aria-hidden="true">⌄</span>
                </summary>
                <p className="mt-4 whitespace-pre-wrap border-t border-border pt-4 text-sm leading-6 text-ink">{job.raw_description}</p>
              </details>
            )}
          </div>

          <aside className="surface-card h-fit p-4 lg:sticky lg:top-6">
            <h2 className="text-base font-medium text-ink">Tracking</h2>
            <div className="mt-4 space-y-4">
              <label className="block text-sm text-ink">
                Status
                <select value={status} onChange={(event) => { setStatus(event.target.value); setNotice(null) }} className="control mt-2 px-3 py-2.5 text-sm">
                  {statusOptions.map(([label, value]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>

              <label className="block text-sm text-ink">
                Deadline
                <input type="date" value={deadline} onChange={(event) => { setDeadline(event.target.value); setNotice(null) }} className="control mt-2 px-3 py-2.5 text-sm" />
              </label>

              <label className="block text-sm text-ink">
                Notes
                <textarea
                  value={notes}
                  maxLength={5100}
                  onChange={(event) => { setNotes(event.target.value); setNotice(null) }}
                  placeholder="Interview dates, recruiter contact, next steps…"
                  className="control mt-2 h-36 resize-y p-3 text-sm leading-5"
                />
                <span className={`mt-1 block text-right text-xs ${notesTooLong ? 'text-ink' : 'text-muted'}`}>{notes.length.toLocaleString()} / 5,000</span>
              </label>

              <button
                onClick={() => saveTracking.mutate()}
                disabled={saveTracking.isPending || notesTooLong || !isDirty}
                className="primary-button w-full"
              >
                <ButtonLabel pending={saveTracking.isPending} pendingText="Saving…">Save changes</ButtonLabel>
              </button>
              {!isDirty && !saveTracking.isPending && <p className="text-center text-xs text-muted">All changes saved</p>}

              <div className="border-t border-border pt-4 text-center">
                <button className="text-sm text-muted underline-offset-4 hover:text-ink hover:underline" onClick={() => { deleteJob.reset(); setShowDeleteDialog(true) }}>Delete job</button>
              </div>
            </div>
          </aside>
        </div>
      </main>

      <Dialog
        open={showDeleteDialog}
        title="Delete this job?"
        description="This permanently removes the analysis, notes, and tracking history."
        onClose={closeDeleteDialog}
        dismissible={!deleteJob.isPending}
        width="max-w-md"
        footer={(
          <>
            <button className="secondary-button" onClick={closeDeleteDialog} disabled={deleteJob.isPending}>Cancel</button>
            <button className="danger-button" onClick={() => deleteJob.mutate()} disabled={deleteJob.isPending}>
              <ButtonLabel pending={deleteJob.isPending} pendingText="Deleting…">Delete permanently</ButtonLabel>
            </button>
          </>
        )}
      >
        {deleteJob.error && <InlineAlert>{deleteJob.error.message}</InlineAlert>}
        <p className="text-sm leading-6 text-muted">You cannot undo this action.</p>
      </Dialog>
    </div>
  )
}
