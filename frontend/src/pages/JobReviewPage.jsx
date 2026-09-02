import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
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

export default function JobReviewPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [companyName, setCompanyName] = useState('')
  const [location, setLocation] = useState('')
  const [workType, setWorkType] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [status, setStatus] = useState('saved')
  const [deadline, setDeadline] = useState('')
  const [requirements, setRequirements] = useState([])

  const draftQuery = useQuery({
    queryKey: ['job-draft', id],
    queryFn: () => apiFetch(`/jobs/drafts/${id}`),
    retry: false,
  })

  useEffect(() => {
    if (!draftQuery.data) return
    const draft = draftQuery.data
    if (draft.confirmed_job_id) {
      navigate(`/jobs/${draft.confirmed_job_id}`, { replace: true })
      return
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTitle(draft.title || '')
    setCompanyName(draft.company_name || '')
    setLocation(draft.location || '')
    setWorkType(draft.work_type || '')
    setSourceUrl(draft.source_url || '')
    setRequirements(
      (draft.requirements || []).map((item) => (
        typeof item === 'string'
          ? { skill: item, importance: 'required' }
          : { skill: item.skill, importance: item.importance || 'required' }
      )),
    )
  }, [draftQuery.data, navigate])

  const confirmMutation = useMutation({
    mutationFn: () => apiFetch(`/jobs/drafts/${id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        title: title.trim(),
        company_name: companyName.trim() || null,
        location: location.trim() || null,
        work_type: workType || null,
        source_url: sourceUrl.trim() || null,
        status,
        deadline: deadline || null,
        requirements: requirements
          .filter((item) => item.skill.trim())
          .map((item) => ({ skill: item.skill.trim(), importance: item.importance })),
      }),
    }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs-stats'] })
      navigate(`/jobs/${data.id}`, { replace: true })
    },
  })

  const cancelMutation = useMutation({
    mutationFn: () => apiFetch(`/jobs/drafts/${id}`, { method: 'DELETE' }),
    onSuccess: () => navigate('/dashboard', { replace: true }),
  })

  if (draftQuery.isLoading) return <PageLoader label="Loading analysis draft…" />

  if (draftQuery.isError) {
    const expired = draftQuery.error?.status === 410
    return (
      <div className="app-main min-h-screen bg-surface">
        <NavBar />
        <main className="page-container grid min-h-[70vh] place-items-center">
          <div className="max-w-md text-center animate-soft-in">
            <p className="eyebrow">Analysis draft</p>
            <h1 className="page-heading mt-2">{expired ? 'This draft expired' : 'Draft unavailable'}</h1>
            <p className="mt-3 text-sm text-muted">
              {expired ? 'Drafts last 30 minutes. Analyze the job description again.' : draftQuery.error.message}
            </p>
            <button className="primary-button mt-5" onClick={() => navigate('/dashboard')}>Back to dashboard</button>
          </div>
        </main>
      </div>
    )
  }

  const draft = draftQuery.data
  const activeError = confirmMutation.error || cancelMutation.error

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <header className="mb-8">
          <p className="eyebrow">Step 2 of 2</p>
          <h1 className="page-heading mt-2">Review job details</h1>
          <p className="mt-2 text-sm text-muted">Check the AI extraction before anything is saved to your tracker.</p>
        </header>

        {activeError && <InlineAlert className="mb-4">{activeError.message}</InlineAlert>}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(300px,0.85fr)]">
          <section className="surface-card p-5" aria-labelledby="basic-details-heading">
            <div>
              <h2 id="basic-details-heading" className="text-base font-medium text-ink">Basic details</h2>
              <p className="mt-1 text-xs text-muted">Edit anything the analysis got wrong.</p>
            </div>

            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              <label className="block text-sm text-ink sm:col-span-2">
                Job title
                <input value={title} maxLength={300} onChange={(event) => setTitle(event.target.value)} className="control mt-2 px-3 py-2.5 text-sm" />
              </label>
              <label className="block text-sm text-ink">
                Company
                <input value={companyName} maxLength={300} onChange={(event) => setCompanyName(event.target.value)} className="control mt-2 px-3 py-2.5 text-sm" />
              </label>
              <label className="block text-sm text-ink">
                Location
                <input value={location} maxLength={300} onChange={(event) => setLocation(event.target.value)} className="control mt-2 px-3 py-2.5 text-sm" />
              </label>
              <label className="block text-sm text-ink">
                Work type
                <select value={workType} onChange={(event) => setWorkType(event.target.value)} className="control mt-2 px-3 py-2.5 text-sm">
                  <option value="">Not specified</option>
                  <option value="remote">Remote</option>
                  <option value="hybrid">Hybrid</option>
                  <option value="in_person">In person</option>
                </select>
              </label>
              <label className="block text-sm text-ink">
                Initial status
                <select value={status} onChange={(event) => setStatus(event.target.value)} className="control mt-2 px-3 py-2.5 text-sm">
                  {statusOptions.map(([label, value]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="block text-sm text-ink">
                Deadline <span className="text-muted">(optional)</span>
                <input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} className="control mt-2 px-3 py-2.5 text-sm" />
              </label>
              <label className="block text-sm text-ink sm:col-span-2">
                Job posting URL <span className="text-muted">(optional)</span>
                <input type="url" value={sourceUrl} maxLength={2048} onChange={(event) => setSourceUrl(event.target.value)} className="control mt-2 px-3 py-2.5 text-sm" />
              </label>
            </div>
          </section>

          <div className="space-y-6">
            <section className="surface-card p-5" aria-labelledby="analysis-summary-heading">
              <h2 id="analysis-summary-heading" className="text-base font-medium text-ink">Analysis summary</h2>
              <p className="mt-3 text-sm leading-6 text-charcoal">{draft.summary}</p>
            </section>

            <section className="surface-card inverted-card p-5 text-white" aria-labelledby="translation-heading">
              <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/60">Translation</p>
              <h2 id="translation-heading" className="mt-2 text-base font-medium">What this job really means</h2>
              <p className="mt-3 text-sm leading-6 text-white/80">{draft.no_bs_translation}</p>
            </section>

            <section className="surface-card p-5" aria-labelledby="draft-skills-heading">
              <h2 id="draft-skills-heading" className="text-base font-medium text-ink">Requirements</h2>
              <p className="mt-1 text-xs leading-5 text-muted">
                These drive the match score. Fix anything the analysis got wrong — a bad requirement
                follows this job around.
              </p>

              <ul className="mt-4 space-y-2">
                {requirements.map((item, index) => (
                  <li key={index} className="flex items-center gap-2">
                    <input
                      value={item.skill}
                      maxLength={100}
                      aria-label={`Requirement ${index + 1}`}
                      onChange={(event) => setRequirements((current) => current.map((r, i) => (
                        i === index ? { ...r, skill: event.target.value } : r
                      )))}
                      className="control flex-1 px-3 py-2 text-sm"
                    />
                    <select
                      value={item.importance}
                      aria-label={`Importance of ${item.skill || `requirement ${index + 1}`}`}
                      onChange={(event) => setRequirements((current) => current.map((r, i) => (
                        i === index ? { ...r, importance: event.target.value } : r
                      )))}
                      className="control min-h-11 w-40 px-3 py-2 text-sm"
                    >
                      <option value="required">Required</option>
                      <option value="preferred">Preferred</option>
                      <option value="nice_to_have">Nice to have</option>
                    </select>
                    <button
                      className="text-xs text-muted hover:text-ink"
                      aria-label={`Remove requirement ${index + 1}`}
                      onClick={() => setRequirements((current) => current.filter((_, i) => i !== index))}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>

              <button
                className="mt-3 text-xs text-muted hover:text-ink"
                onClick={() => setRequirements((current) => [...current, { skill: '', importance: 'required' }])}
              >
                + Add requirement
              </button>
            </section>
          </div>
        </div>

        <footer className="mt-6 flex flex-col-reverse gap-3 border-t border-border pt-5 sm:flex-row sm:items-center sm:justify-between">
          <button className="text-sm text-muted hover:text-ink" disabled={cancelMutation.isPending || confirmMutation.isPending} onClick={() => cancelMutation.mutate()}>
            <ButtonLabel pending={cancelMutation.isPending} pendingText="Discarding…">Discard draft</ButtonLabel>
          </button>
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <button className="secondary-button" disabled={confirmMutation.isPending || cancelMutation.isPending} onClick={() => navigate('/dashboard')}>Back</button>
            <button className="primary-button min-w-36" disabled={!title.trim() || confirmMutation.isPending || cancelMutation.isPending} onClick={() => confirmMutation.mutate()}>
              <ButtonLabel pending={confirmMutation.isPending} pendingText="Saving…">Confirm and save</ButtonLabel>
            </button>
          </div>
        </footer>
      </main>
    </div>
  )
}
