import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { NoBsTranslation } from '../components/NoBsTranslation'
import SelectMenu from '../components/SelectMenu'
import { InlineField, PenIcon, PinIcon } from '../components/InlineField'
import { SourceLink, TrashIcon } from '../components/SourceLink'
import { apiFetch } from '../lib/api'
import { toast } from '../lib/toast'

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
  // not "partial" — that reads as "partly matched", and this state means the resume shows a
  // related skill and not this one. A PostgreSQL bullet is not a partial Redis match.
  PARTIAL: { label: 'related experience', className: 'text-muted' },
  NONE: { label: 'gap', className: 'text-muted' },
}

// what a saved field is called in the toast, so "Location saved." reads as a sentence
const SAVED_LABELS = { title: 'Title', location: 'Location', source_url: 'Link', status: 'Status' }

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
  const [notice, setNotice] = useState(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [pendingRun, setPendingRun] = useState(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
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

  // Status and the posting link save the moment they change, from the header. This form is
  // what is left: the things you type, which need a Save because half a sentence is not an
  // edit anyone meant to make.
  const quickSave = useMutation({
    mutationFn: (fields) => apiFetch(`/jobs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }),
    onSuccess: (_data, fields) => {
      toast.success(`${SAVED_LABELS[Object.keys(fields)[0]] || 'Change'} saved.`)
      // Patch the list's cache as well as invalidating it. Invalidation only refetches queries
      // that are *mounted*, and the dashboard is not — it would keep showing the old link until
      // something happened to remount it, which is exactly what "not synced" looked like.
      queryClient.setQueriesData({ queryKey: ['jobs'] }, (cached) => (
        cached?.jobs
          ? { ...cached, jobs: cached.jobs.map((job) => (
              String(job.id) === String(id) ? { ...job, ...fields } : job
            )) }
          : cached
      ))
    },
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['job', id] })
      // the dashboard lists the same fields; without this it keeps showing the old link and
      // status until something else happens to refetch it
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
    },
  })

  const saveTracking = useMutation({
    mutationFn: () => apiFetch(`/jobs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        notes,
        deadline: deadline || null,
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

  const deleteRun = useMutation({
    mutationFn: (runId) => apiFetch(`/tailoring/runs/${runId}`, { method: 'DELETE' }),
    onSuccess: () => toast.success('Tailoring run deleted.'),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      setPendingRun(null)
      queryClient.invalidateQueries({ queryKey: ['job-tailoring-runs', id] })
    },
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
  const isDirty = notes !== (job.notes || '') || deadline !== (job.deadline || '')
  const skills = job.skills || []

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <button onClick={() => navigate('/dashboard')} className="mb-6 text-sm text-muted hover:text-ink">← Back to dashboard</button>

        <header className="mb-8 flex flex-col gap-5 border-b border-border pb-8 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0 flex-1">
            <p className="eyebrow">Job details</p>

            {editingTitle ? (
              <form
                className="mt-2 flex items-center gap-2"
                onSubmit={(event) => {
                  event.preventDefault()
                  quickSave.mutate({ title: titleDraft.trim() || null })
                  setEditingTitle(false)
                }}
              >
                <input
                  autoFocus
                  value={titleDraft}
                  onChange={(event) => setTitleDraft(event.target.value)}
                  onBlur={() => { setTitleDraft(job.title || ''); setEditingTitle(false) }}
                  onKeyDown={(event) => {
                    if (event.key !== 'Escape') return
                    setTitleDraft(job.title || '')
                    setEditingTitle(false)
                  }}
                  aria-label="Job title"
                  maxLength={300}
                  className="control min-w-0 flex-1 px-3 py-2 text-2xl font-medium text-ink"
                />
                <button type="submit" className="shrink-0 px-1 text-sm text-ink hover:underline">Save</button>
              </form>
            ) : (
              <div className="mt-2 flex items-center gap-2">
                <h1 className="page-heading min-w-0 truncate">{job.title || 'Untitled role'}</h1>
                <button
                  type="button"
                  aria-label="Edit job title"
                  className="shrink-0 px-1 text-muted hover:text-ink"
                  onClick={() => { setTitleDraft(job.title || ''); setEditingTitle(true) }}
                >
                  <PenIcon className="size-4" />
                </button>
              </div>
            )}

            <div className="mt-2 flex flex-wrap items-center gap-3">
              {job.company_name && (
                <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-muted">{job.company_name}</p>
              )}
              {/* the location is model output too, and often simply absent from a posting */}
              <InlineField
                value={job.location || ''}
                onSave={(next) => quickSave.mutate({ location: next })}
                saving={quickSave.isPending}
                icon={PinIcon}
                label="location"
                placeholder="City, State"
                emptyText="No location"
                copyable
                suggest
                width="w-60"
              />
              {job.work_type && (
                <span className="rounded-full border border-border bg-soft-paper px-2.5 py-1 text-xs text-muted">
                  {job.work_type.replace('_', ' ')}
                </span>
              )}
            </div>
          </div>

          {/* The three things you act on rather than read: where the posting is, where you are
              with it, and getting rid of it. They belong beside the title, not buried in a form
              below the fold — and each saves on the spot, so there is nothing to remember. */}
          <div className="flex shrink-0 items-center justify-end gap-3">
            <SourceLink
              job={job}
              grow
              saving={quickSave.isPending && quickSave.variables?.source_url !== undefined}
              onSave={(nextUrl) => quickSave.mutate({ source_url: nextUrl })}
            />
            <SelectMenu
              className="w-36 shrink-0"
              ariaLabel="Status"
              value={status}
              options={statusOptions.map(([label, optionValue]) => ({ label, value: optionValue }))}
              onChange={(nextStatus) => { setStatus(nextStatus); quickSave.mutate({ status: nextStatus }) }}
            />
            <button
              type="button"
              className="icon-button shrink-0 text-muted hover:text-red-700"
              aria-label="Delete job"
              onClick={() => { deleteJob.reset(); setShowDeleteDialog(true) }}
            >
              <TrashIcon />
            </button>
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
                <NoBsTranslation text={job.no_bs_translation} />
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
                    <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-5 text-ink marker:text-muted">
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
                      <div key={run.id} className="group/run flex items-center gap-2">
                        <Link
                          to={`/tailoring/${run.id}`}
                          className="min-w-0 flex-1 truncate text-xs leading-5 text-muted underline decoration-border underline-offset-4 hover:text-ink"
                        >
                          {new Date(run.started_at).toLocaleDateString()} · {run.edit_count} {run.edit_count === 1 ? 'suggestion' : 'suggestions'}
                          {run.gap_count > 0 && `, ${run.gap_count} ${run.gap_count === 1 ? 'gap' : 'gaps'}`}
                        </Link>
                        {/* on hover or keyboard focus — a delete sitting permanently beside a
                            link is one slip away from losing a run that cost real money */}
                        <button
                          type="button"
                          className="icon-button size-7 shrink-0 text-muted opacity-0 transition-opacity duration-150 hover:text-red-700 focus-visible:opacity-100 group-hover/run:opacity-100"
                          aria-label={`Delete run from ${new Date(run.started_at).toLocaleDateString()}`}
                          disabled={deleteRun.isPending}
                          onClick={() => setPendingRun(run)}
                        >
                          <TrashIcon />
                        </button>
                      </div>
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
              <label className="block text-sm text-ink">
                Deadline
                <input type="date" value={deadline} onChange={(event) => { setDeadline(event.target.value); setNotice(null); if (saveTracking.isError) saveTracking.reset() }} className="control mt-2 px-3 py-2.5 text-sm" />
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

      <Dialog
        open={pendingRun !== null}
        title="Delete this tailoring run?"
        description="Its suggestions, questions and trace go with it. The job and your resume are untouched."
        onClose={() => setPendingRun(null)}
        dismissible={!deleteRun.isPending}
        width="max-w-md"
        footer={(
          <>
            <button className="secondary-button" onClick={() => setPendingRun(null)} disabled={deleteRun.isPending}>Cancel</button>
            <button className="danger-button" onClick={() => deleteRun.mutate(pendingRun.id)} disabled={deleteRun.isPending}>
              <ButtonLabel pending={deleteRun.isPending} pendingText="Deleting…">Delete run</ButtonLabel>
            </button>
          </>
        )}
      >
        {deleteRun.error && <InlineAlert className="mb-3">{deleteRun.error.message}</InlineAlert>}
        <p className="text-sm text-ink">
          {pendingRun && new Date(pendingRun.started_at).toLocaleDateString()} · {pendingRun?.edit_count}{' '}
          {pendingRun?.edit_count === 1 ? 'suggestion' : 'suggestions'}
        </p>
        <p className="mt-1 text-xs text-muted">Accepted wording you already downloaded is not affected.</p>
      </Dialog>
    </div>
  )
}
