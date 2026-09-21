import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { ButtonLabel, InlineAlert, PageLoader } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { NoBsTranslation } from '../components/NoBsTranslation'
import SelectMenu from '../components/SelectMenu'
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

// A requirement is either a plain skill or a condition over alternatives ("one of Java,
// Python, C++"). Both are editable here: a posting that offers a choice is ONE requirement,
// and splitting it into several is what made the match denominator wrong.
function toRow(item) {
  if (typeof item === 'string') return { skill: item, importance: 'required', type: 'skill', condition: null }

  const items = item.condition?.items ?? []
  const grouped = items.length > 1
  return {
    skill: item.skill ?? (grouped ? '' : items[0] ?? ''),
    importance: item.importance || 'required',
    type: item.type || 'skill',
    source_text: item.source_text ?? null,
    condition: grouped ? { operator: 'any_of', minimum: item.condition.minimum ?? 1, items } : null,
  }
}

function rowLabel(row) {
  if (!row.condition) return row.skill
  return row.source_text || row.condition.items.join(' or ')
}

function isSaveable(row) {
  return row.condition ? row.condition.items.length > 1 : row.skill.trim().length > 0
}

function toPayload(row) {
  // `type` is only sent when it isn't the default, so an ordinary skill keeps the payload it
  // has always had — but an eligibility row survives an edit instead of turning into a skill
  const type = row.type && row.type !== 'skill' ? { type: row.type } : {}

  if (row.condition) {
    return { ...type, importance: row.importance, source_text: row.source_text, condition: row.condition }
  }
  return { skill: row.skill.trim(), importance: row.importance, ...type }
}

// Combine the checked plain rows into one choice, landing where the first of them was.
function combineRows(rows, selected) {
  const chosen = rows.filter((row, index) => selected.has(index) && !row.condition && row.skill.trim())
  if (chosen.length < 2) return rows

  const items = []
  for (const row of chosen) {
    const skill = row.skill.trim()
    if (!items.some((existing) => existing.toLowerCase() === skill.toLowerCase())) items.push(skill)
  }
  if (items.length < 2) return rows

  const group = {
    skill: '',
    // the posting's words belonged to the rows being replaced, so the label is now the list
    source_text: null,
    // the strictest importance among the alternatives wins: a required choice stays required
    importance: chosen.some((row) => row.importance === 'required') ? 'required'
      : chosen.some((row) => row.importance === 'preferred') ? 'preferred' : 'nice_to_have',
    type: 'skill',
    condition: { operator: 'any_of', minimum: 1, items },
  }

  const firstIndex = rows.findIndex((row) => chosen.includes(row))
  return rows.reduce((next, row, index) => {
    if (index === firstIndex) next.push(group)
    else if (!chosen.includes(row)) next.push(row)
    return next
  }, [])
}

// Split a choice back into one row per alternative.
function ungroupRow(rows, index) {
  const row = rows[index]
  if (!row?.condition) return rows
  const expanded = row.condition.items.map((skill) => ({
    skill, importance: row.importance, type: row.type, condition: null, source_text: null,
  }))
  return [...rows.slice(0, index), ...expanded, ...rows.slice(index + 1)]
}

// Drop one alternative. A choice of one is not a choice, so it degrades to a plain row.
function dropAlternative(rows, index, item) {
  const row = rows[index]
  if (!row?.condition) return rows
  const items = row.condition.items.filter((value) => value !== item)
  if (items.length < 2) {
    return rows.map((current, i) => (i === index
      ? { skill: items[0] ?? '', importance: row.importance, type: row.type, condition: null, source_text: null }
      : current))
  }
  return rows.map((current, i) => (i === index
    ? {
        ...current,
        source_text: null,     // the quoted phrase no longer describes the shortened list
        condition: { ...current.condition, items, minimum: Math.min(current.condition.minimum, items.length) },
      }
    : current))
}


// Gates rather than skills: a degree, a start date, work authorization. They are carried so
// the job page can show them, but they are never scored and never tailored — nothing you
// write on a resume changes whether you have a degree. Listing them among tool names made
// them look like things the match score was holding against you.
function EligibilityList({ requirements, onRemove }) {
  const rows = requirements
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.type === 'eligibility')
  if (!rows.length) return null

  return (
    <div className="mt-5 border-t border-border pt-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
        Eligibility &mdash; not scored
      </p>
      <p className="mt-1.5 text-xs leading-5 text-muted">
        Conditions you either meet or you don&rsquo;t. They are kept on the job for reference and
        left out of the match entirely, because no wording can change them.
      </p>
      <ul className="mt-3 space-y-1.5">
        {rows.map(({ item, index }) => (
          <li key={index} className="flex items-start gap-2">
            <p className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-xs leading-5 text-charcoal">
              {rowLabel(item)}
            </p>
            <button
              type="button"
              className="grid h-11 w-8 shrink-0 place-items-center rounded-md text-muted transition-colors duration-150 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-obsidian"
              aria-label={`Remove eligibility condition ${index + 1}`}
              onClick={() => onRemove(index)}
            >
              <span aria-hidden="true">&times;</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}


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
  // indexes ticked for combining. Cleared whenever the list itself changes shape, because
  // an index that meant "Python" a moment ago can mean something else after a removal.
  const [selected, setSelected] = useState(() => new Set())

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
    setRequirements((draft.requirements || []).map(toRow))
  }, [draftQuery.data, navigate])

  // one place to change the list, so a stale tick can never survive a reshuffle
  function updateRequirements(next) {
    setRequirements(next)
    setSelected(new Set())
  }

  function toggleSelected(index) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const combinable = requirements.filter(
    (row, index) => selected.has(index) && !row.condition && row.skill.trim(),
  ).length >= 2

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
        requirements: requirements.filter(isSaveable).map(toPayload),
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
  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <header className="mb-8">
          <p className="eyebrow">Step 2 of 2</p>
          <h1 className="page-heading mt-2">Review job details</h1>
          <p className="mt-2 text-sm text-muted">Check the AI extraction before anything is saved to your tracker.</p>
        </header>

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
              <div className="text-sm text-ink">
                <p>Work type</p>
                <SelectMenu
                  className="mt-2"
                  ariaLabel="Work type"
                  value={workType}
                  onChange={setWorkType}
                  options={[
                    { label: 'Not specified', value: '' },
                    { label: 'Remote', value: 'remote' },
                    { label: 'Hybrid', value: 'hybrid' },
                    { label: 'In person', value: 'in_person' },
                  ]}
                />
              </div>
              <div className="text-sm text-ink">
                <p>Initial status</p>
                <SelectMenu
                  className="mt-2"
                  ariaLabel="Initial status"
                  value={status}
                  onChange={setStatus}
                  options={statusOptions.map(([label, optionValue]) => ({ label, value: optionValue }))}
                />
              </div>
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
              <NoBsTranslation text={draft.no_bs_translation} empty="" tone="text-white/80" />
            </section>

            <section className="surface-card p-5" aria-labelledby="draft-skills-heading">
              <h2 id="draft-skills-heading" className="text-base font-medium text-ink">Requirements</h2>
              <p className="mt-1 text-xs leading-5 text-muted">
                These drive the match score. Fix anything the analysis got wrong — a bad requirement
                follows this job around. If the posting offers a choice (&ldquo;one of Java, Python, or
                C++&rdquo;), tick those rows and combine them so it counts once, not several times.
              </p>

              <ul className="mt-4 space-y-2">
                {requirements.map((item, index) => (item.type === 'eligibility' ? null : item.condition ? (
                  // A choice is a block, not a row: cramming wrapping chips, a stepper and two
                  // controls onto one line left every part of it squeezed and hard to hit.
                  <li key={index} className="rounded-md border border-border bg-pure-white p-3">
                    <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
                      Any of these
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.condition.items.map((alternative) => (
                        <span
                          key={alternative}
                          className="inline-flex min-h-7 items-center gap-1.5 rounded-full border border-border bg-surface pl-2.5 pr-1.5 text-xs text-ink"
                        >
                          {alternative}
                          <button
                            type="button"
                            className="-m-1 grid h-6 w-6 place-items-center rounded-full p-1 text-muted transition-colors duration-150 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-obsidian"
                            aria-label={`Remove ${alternative} from requirement ${index + 1}`}
                            onClick={() => updateRequirements(dropAlternative(requirements, index, alternative))}
                          >
                            <span aria-hidden="true">×</span>
                          </button>
                        </span>
                      ))}
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-3 border-t border-border pt-3">
                      <label className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
                        Needs
                        <input
                          type="number"
                          min={1}
                          max={item.condition.items.length}
                          value={item.condition.minimum}
                          aria-label={`How many of requirement ${index + 1} are needed`}
                          onChange={(event) => {
                            const minimum = Math.min(
                              Math.max(Number(event.target.value) || 1, 1),
                              item.condition.items.length,
                            )
                            setRequirements((current) => current.map((r, i) => (
                              i === index ? { ...r, condition: { ...r.condition, minimum } } : r
                            )))
                          }}
                          className="control h-9 w-14 px-2 text-center text-sm"
                        />
                        of {item.condition.items.length}
                      </label>

                      <div className="ml-auto flex items-center gap-2">
                        <SelectMenu
                          value={item.importance}
                          ariaLabel={`Importance of ${rowLabel(item) || `requirement ${index + 1}`}`}
                          onChange={(nextImportance) => setRequirements((current) => current.map((r, i) => (
                            i === index ? { ...r, importance: nextImportance } : r
                          )))}
                          className="w-36 shrink-0"
                          options={[
                            { label: 'Required', value: 'required' },
                            { label: 'Preferred', value: 'preferred' },
                            { label: 'Nice to have', value: 'nice_to_have' },
                          ]}
                        />
                        <button
                          type="button"
                          className="flex min-h-11 items-center rounded-md px-2 text-xs text-charcoal transition-colors duration-150 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-obsidian"
                          onClick={() => updateRequirements(ungroupRow(requirements, index))}
                        >
                          Split apart
                        </button>
                        <button
                          type="button"
                          className="grid h-11 w-8 place-items-center rounded-md text-muted transition-colors duration-150 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-obsidian"
                          aria-label={`Remove requirement ${index + 1}`}
                          onClick={() => updateRequirements(requirements.filter((_, i) => i !== index))}
                        >
                          <span aria-hidden="true">×</span>
                        </button>
                      </div>
                    </div>

                    {item.source_text && (
                      <p className="mt-3 border-t border-border pt-3 text-xs leading-5 text-muted">
                        &ldquo;{item.source_text}&rdquo;
                      </p>
                    )}
                  </li>
                ) : (
                  <li key={index} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={selected.has(index)}
                      aria-label={`Select requirement ${index + 1} to combine`}
                      onChange={() => toggleSelected(index)}
                      className="size-4 shrink-0 accent-ink"
                    />
                    <input
                      value={item.skill}
                      maxLength={100}
                      aria-label={`Requirement ${index + 1}`}
                      onChange={(event) => setRequirements((current) => current.map((r, i) => (
                        i === index ? { ...r, skill: event.target.value } : r
                      )))}
                      className="control flex-1 px-3 py-2 text-sm"
                    />
                    <SelectMenu
                      value={item.importance}
                      ariaLabel={`Importance of ${rowLabel(item) || `requirement ${index + 1}`}`}
                      onChange={(nextImportance) => setRequirements((current) => current.map((r, i) => (
                        i === index ? { ...r, importance: nextImportance } : r
                      )))}
                      className="w-36 shrink-0"
                      options={[
                        { label: 'Required', value: 'required' },
                        { label: 'Preferred', value: 'preferred' },
                        { label: 'Nice to have', value: 'nice_to_have' },
                      ]}
                    />
                    <button
                      type="button"
                      className="grid h-11 w-8 shrink-0 place-items-center rounded-md text-muted transition-colors duration-150 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-obsidian"
                      aria-label={`Remove requirement ${index + 1}`}
                      onClick={() => updateRequirements(requirements.filter((_, i) => i !== index))}
                    >
                      <span aria-hidden="true">×</span>
                    </button>
                  </li>
                )))}
              </ul>

              <EligibilityList
                requirements={requirements}
                onRemove={(index) => updateRequirements(requirements.filter((_, i) => i !== index))}
              />

              <div className="mt-3 flex flex-wrap items-center gap-4">
                <button
                  className="text-xs text-muted hover:text-ink"
                  onClick={() => updateRequirements([
                    ...requirements, { skill: '', importance: 'required', type: 'skill', condition: null },
                  ])}
                >
                  + Add requirement
                </button>
                {combinable && (
                  <button
                    className="text-xs text-ink underline underline-offset-4 hover:text-muted"
                    onClick={() => updateRequirements(combineRows(requirements, selected))}
                  >
                    Combine {selected.size} into one choice
                  </button>
                )}
              </div>
            </section>
          </div>
        </div>

        <footer className="mt-6 grid gap-3 border-t border-border pt-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <div>
            {cancelMutation.error && <InlineAlert className="mb-2 max-w-md">{cancelMutation.error.message}</InlineAlert>}
            <button className="text-sm text-muted hover:text-ink" disabled={cancelMutation.isPending || confirmMutation.isPending} onClick={() => cancelMutation.mutate()}>
              <ButtonLabel pending={cancelMutation.isPending} pendingText="Discarding…">Discard draft</ButtonLabel>
            </button>
          </div>
          <div>
            {confirmMutation.error && <InlineAlert className="mb-2 sm:max-w-md">{confirmMutation.error.message}</InlineAlert>}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button className="secondary-button" disabled={confirmMutation.isPending || cancelMutation.isPending} onClick={() => navigate('/dashboard')}>Back</button>
              <button className="primary-button min-w-36" disabled={!title.trim() || confirmMutation.isPending || cancelMutation.isPending} onClick={() => confirmMutation.mutate()}>
                <ButtonLabel pending={confirmMutation.isPending} pendingText="Saving…">Confirm and save</ButtonLabel>
              </button>
            </div>
          </div>
        </footer>
      </main>
    </div>
  )
}
