import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader } from '../components/Feedback'
import NavBar from '../components/NavBar'
import SelectMenu from '../components/SelectMenu'
import { apiFetch } from '../lib/api'

// These two were called capability/communication until the rename. `match_detail` is stored
// JSONB, so jobs scored before it still carry the old keys and only pick up the new ones the
// next time the resume is saved. Reading both means nobody sees a blank score in between.
function fitScore(detail) {
  return detail?.fit_score ?? detail?.capability_score ?? null
}

function visibilityScore(detail) {
  return detail?.visibility_score ?? detail?.communication_score ?? null
}

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

const requirementState = {
  EXPLICIT: { label: 'explicit', className: 'text-terminal-green' },
  INFERRED: { label: 'inferred', className: 'text-terminal-green' },
  PARTIAL: { label: 'partial', className: 'text-charcoal' },
  NONE: { label: 'gap', className: 'text-muted' },
}

function ChevronIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="m6 8 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function TailorError({ error, className = '' }) {
  if (!error) return null
  return (
    <InlineAlert className={className}>
      {error.message}
      {error.reason === 'needs_confirmation' && (
        <Link className="ml-2 underline underline-offset-4" to="/resume">Review resume</Link>
      )}
    </InlineAlert>
  )
}

export default function JobDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [notes, setNotes] = useState('')
  const [status, setStatus] = useState('saved')
  const [deadline, setDeadline] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
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
    setSourceUrl(jobQuery.data.source_url || '')
  }, [jobQuery.data])

  const saveTracking = useMutation({
    mutationFn: () => apiFetch(`/jobs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        notes,
        status,
        deadline: deadline || null,
        source_url: sourceUrl.trim() || null,
      }),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', id] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
      setNotice({ tone: 'success', message: 'Tracking details saved.' })
    },
  })

  const runsQuery = useQuery({
    queryKey: ['job-tailoring-runs', id],
    queryFn: () => apiFetch(`/jobs/${id}/tailoring`),
    retry: false,
  })

  const tailorJob = useMutation({
    mutationFn: () => apiFetch(`/jobs/${id}/tailor`, { method: 'POST' }),
    // the run reports its own progress once we're there
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['job-tailoring-runs', id] })
      navigate(`/tailoring/${run.id}`)
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
  const isDirty = notes !== (job.notes || '')
    || status !== job.status
    || deadline !== (job.deadline || '')
    || sourceUrl !== (job.source_url || '')
  const metadata = [job.company_name, job.location, job.work_type?.replace('_', ' ')].filter(Boolean)
  const skills = job.skills || []

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <button onClick={() => navigate('/dashboard')} className="mb-6 text-sm text-muted hover:text-ink">← Back to dashboard</button>

        <header className="mb-8 flex flex-col gap-5 border-b border-border pb-8 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <p className="eyebrow">Job details</p>
            <h1 className="page-heading mt-2">{job.title || 'Untitled role'}</h1>
            {metadata.length > 0 && <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.06em] text-muted">{metadata.join(' · ')}</p>}
            {job.source_url && (
              <a
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-flex min-h-11 items-center text-sm text-ink underline decoration-border underline-offset-4 transition-colors hover:text-muted"
              >
                Open posting ↗
              </a>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 lg:hidden">
            <span className="text-sm font-medium text-ink">{job.match_score != null ? `${job.match_score}% match` : 'Not scored'}</span>
            <button className="primary-button" disabled={tailorJob.isPending} onClick={() => { tailorJob.reset(); tailorJob.mutate() }}>
              <ButtonLabel pending={tailorJob.isPending} pendingText="Starting…">Tailor resume</ButtonLabel>
            </button>
            <TailorError error={tailorJob.error} className="w-full" />
          </div>
        </header>

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-6">
            <div className="grid gap-4 md:grid-cols-2">
              <section className="surface-card p-5">
                <p className="eyebrow">At a glance</p>
                <h2 className="mt-2 text-base font-medium text-ink">Role summary</h2>
                <p className="mt-3 text-sm leading-6 text-charcoal">{job.summary || 'No summary was extracted.'}</p>
              </section>

              <section className="surface-card inverted-card p-5 text-white">
                <p className="font-mono text-[11px] uppercase tracking-[0.071em] text-white/60">Plain English</p>
                <h2 className="mt-2 text-base font-medium text-white">No-BS translation</h2>
                <p className="mt-3 text-sm leading-6 text-white/85">{job.no_bs_translation || 'No translation was extracted.'}</p>
              </section>
            </div>

            {job.match_detail && (
              <section className="surface-card overflow-hidden">
                <div className="border-b border-border p-5">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <div>
                      <p className="eyebrow">Resume evidence</p>
                      <h2 className="mt-2 text-base font-medium text-ink">Requirement breakdown</h2>
                    </div>
                    {fitScore(job.match_detail) != null && (
                      <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
                        fit {fitScore(job.match_detail)}%
                        {visibilityScore(job.match_detail) != null && (
                          <> · visible {visibilityScore(job.match_detail)}%</>
                        )}
                      </p>
                    )}
                  </div>
                  {fitScore(job.match_detail) != null && (
                    <p className="mt-2 max-w-2xl text-xs leading-5 text-muted">
                      Two separate things. Fit is how much of this job you can actually do — only new
                      experience moves it. Visibility is how much of that your resume states plainly —
                      that is the part tailoring can change.
                    </p>
                  )}
                </div>

                {job.match_detail.requirements ? (
                  <ul>
                    {job.match_detail.requirements.map((item) => {
                      const state = requirementState[item.state] || { label: item.state?.toLowerCase() || 'unknown', className: 'text-muted' }
                      const hasDetail = item.inferred_from?.length > 0 || item.evidence?.length > 0
                      return (
                        <li key={item.requirement} className="border-b border-border last:border-0">
                          <details className="group">
                            <summary className="flex min-h-14 cursor-pointer list-none items-center gap-3 px-5 py-3">
                              <span className="min-w-0 flex-1 text-sm text-ink">{item.requirement}</span>
                              {item.importance !== 'required' && (
                                <span className="hidden font-mono text-[10px] uppercase tracking-[0.08em] text-muted sm:inline">
                                  {item.importance.replace('_', ' ')}
                                </span>
                              )}
                              <span className={`font-mono text-[10px] uppercase tracking-[0.08em] ${state.className}`}>{state.label}</span>
                              {hasDetail ? <span className="text-muted"><ChevronIcon /></span> : <span className="w-4" aria-hidden="true" />}
                            </summary>
                            {hasDetail && (
                              <div className="border-t border-border bg-surface px-5 py-4">
                                {item.inferred_from?.length > 0 && <p className="text-xs text-muted">via {item.inferred_from.join(', ')}</p>}
                                {item.evidence?.length > 0 && (
                                  <blockquote className="mt-2 border-l border-ink pl-3 text-xs leading-5 text-charcoal">“{item.evidence[0].text}”</blockquote>
                                )}
                              </div>
                            )}
                          </details>
                        </li>
                      )
                    })}
                  </ul>
                ) : (
                  <div className="grid gap-5 p-5 sm:grid-cols-2">
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
                )}
              </section>
            )}

            {job.raw_description && (
              <details className="surface-card group p-5">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between text-sm text-ink">
                  View original job description
                  <span className="text-muted"><ChevronIcon /></span>
                </summary>
                <p className="mt-4 whitespace-pre-wrap border-t border-border pt-4 text-sm leading-6 text-ink">{job.raw_description}</p>
              </details>
            )}
          </div>

          <aside className="space-y-6">
            <section className="surface-card overflow-hidden">
              <div className="p-5">
                <p className="eyebrow">Resume fit</p>
                <div className="mt-2 flex items-end justify-between gap-4">
                  <p className="text-3xl font-normal tracking-[-0.05em] text-ink">{job.match_score != null ? `${job.match_score}%` : '—'}</p>
                  <p className="pb-1 text-xs text-muted">overall match</p>
                </div>
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-border" aria-label={`${job.match_score ?? 0}% skill match`}>
                  <div className="match-fill h-full rounded-full bg-primary" style={{ width: `${job.match_score || 0}%` }} />
                </div>

                {fitScore(job.match_detail) != null && (
                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <div className="rounded-md border border-border bg-surface p-3">
                      <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Fit</p>
                      <p className="mt-1 text-sm font-medium text-ink">{fitScore(job.match_detail)}%</p>
                    </div>
                    <div className="rounded-md border border-border bg-surface p-3">
                      <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Visible</p>
                      <p className="mt-1 text-sm font-medium text-ink">
                        {visibilityScore(job.match_detail) != null
                          ? `${visibilityScore(job.match_detail)}%`
                          : '—'}
                      </p>
                    </div>
                  </div>
                )}

                {job.match_detail?.eligibility?.length > 0 && (
                  <div className="mt-4 rounded-md border border-border bg-surface p-3">
                    <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Eligibility</p>
                    <ul className="mt-1 space-y-1 text-xs leading-5 text-ink">
                      {job.match_detail.eligibility.map((condition) => (
                        <li key={condition}>{condition}</li>
                      ))}
                    </ul>
                    <p className="mt-2 text-xs leading-5 text-muted">
                      Not scored and not tailored — these are gates you either meet or you don't.
                    </p>
                  </div>
                )}

                {job.match_detail?.hidden?.length > 0 && (
                  <p className="mt-3 text-xs leading-5 text-muted">
                    Your experience covers{' '}
                    <span className="text-ink">{job.match_detail.hidden.join(', ')}</span>, but the resume
                    never says so outright.
                  </p>
                )}

                <button className="primary-button mt-5 hidden w-full lg:inline-flex" disabled={tailorJob.isPending} onClick={() => { tailorJob.reset(); tailorJob.mutate() }}>
                  <ButtonLabel pending={tailorJob.isPending} pendingText="Starting…">Tailor resume</ButtonLabel>
                </button>
                <p className="mt-2 hidden text-xs leading-5 text-muted lg:block">Uses your saved resume evidence to suggest edits. Review every suggestion before accepting it.</p>
                <TailorError error={tailorJob.error} className="mt-3 hidden lg:block" />
              </div>

              {runsQuery.data?.runs?.length > 0 && (
                <div className="border-t border-border px-5 py-4">
                  <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Earlier runs</p>
                  <div className="mt-2 space-y-2">
                    {runsQuery.data.runs.slice(0, 4).map((run) => (
                      <Link
                        key={run.id}
                        to={`/tailoring/${run.id}`}
                        className="block text-xs leading-5 text-muted underline decoration-border underline-offset-4 hover:text-ink"
                      >
                        {new Date(run.started_at).toLocaleDateString()} · {run.edit_count} {run.edit_count === 1 ? 'suggestion' : 'suggestions'}
                        {run.gap_count > 0 && `, ${run.gap_count} ${run.gap_count === 1 ? 'gap' : 'gaps'}`}
                      </Link>
                    ))}
                  </div>
                </div>
              )}
            </section>

            <section className="surface-card p-5">
              <p className="eyebrow">From the posting</p>
              <h2 className="mt-2 text-base font-medium text-ink">Key skills</h2>
              {skills.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {skills.map((skill) => (
                    <span key={skill} className="rounded-full border border-border bg-surface px-2.5 py-1.5 text-xs text-ink">{skill}</span>
                  ))}
                </div>
              ) : <p className="mt-2 text-sm text-muted">No concrete technical skills were listed.</p>}
            </section>

            <section className="surface-card p-5">
              <p className="eyebrow">Application</p>
              <h2 className="mt-2 text-base font-medium text-ink">Tracking</h2>
              <div className="mt-4 space-y-4">
              <div className="text-sm text-ink">
                <p>Status</p>
                <SelectMenu
                  className="mt-2"
                  ariaLabel="Status"
                  value={status}
                  options={statusOptions.map(([label, optionValue]) => ({ label, value: optionValue }))}
                  onChange={(nextStatus) => { setStatus(nextStatus); setNotice(null); if (saveTracking.isError) saveTracking.reset() }}
                />
              </div>

              <label className="block text-sm text-ink">
                Deadline
                <input type="date" value={deadline} onChange={(event) => { setDeadline(event.target.value); setNotice(null); if (saveTracking.isError) saveTracking.reset() }} className="control mt-2 px-3 py-2.5 text-sm" />
              </label>

              <label className="block text-sm text-ink">
                Job posting URL
                <input
                  type="url"
                  maxLength={2048}
                  value={sourceUrl}
                  onChange={(event) => { setSourceUrl(event.target.value); setNotice(null); if (saveTracking.isError) saveTracking.reset() }}
                  placeholder="https://company.com/jobs/role"
                  className="control mt-2 px-3 py-2.5 text-sm"
                />
              </label>

              <label className="block text-sm text-ink">
                Notes
                <textarea
                  value={notes}
                  maxLength={5100}
                  onChange={(event) => { setNotes(event.target.value); setNotice(null); if (saveTracking.isError) saveTracking.reset() }}
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
              {saveTracking.error && <InlineAlert>{saveTracking.error.message}</InlineAlert>}
              {notice && <InlineAlert tone={notice.tone}>{notice.message}</InlineAlert>}

              <div className="border-t border-border pt-4 text-center">
                <button className="text-sm text-muted underline-offset-4 hover:text-ink hover:underline" onClick={() => { deleteJob.reset(); setShowDeleteDialog(true) }}>Delete job</button>
              </div>
              </div>
            </section>
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
