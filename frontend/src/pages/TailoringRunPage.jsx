import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import Dialog from '../components/Dialog'
import { ButtonLabel, InlineAlert, PageLoader, Spinner } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { apiFetch, apiUrl } from '../lib/api'

// only the first line is generic; after that each one describes a tool call that ran
const OPENING_LINES = ['Reading the job posting…', 'Reading your resume evidence…', 'Planning what to look for…']

function describe(step) {
  if (step.tool === 'search_resume') return `Searching your resume for “${step.arguments?.query ?? ''}”…`
  if (step.tool === 'propose_edit') return `Drafting a rewrite for ${step.arguments?.requirement ?? 'a requirement'}…`
  if (step.tool === 'merge_bullets') return `Combining related bullets for ${step.arguments?.requirement ?? 'a requirement'}…`
  if (step.tool === 'request_detail') return `Checking what detail is needed for ${step.arguments?.requirement ?? 'a requirement'}…`
  if (step.tool === 'flag_gap') return `No evidence for ${step.arguments?.requirement ?? 'a requirement'} — flagging it…`
  return 'Thinking…'
}

const outcomeLabels = {
  rewrite: 'rewrite candidate',
  strengthen: 'impact detail',
  surface_skill: 'surface in Skills',
  keep: 'already represented',
  confirm: 'needs confirmation',
  inferred_only: 'shown indirectly',
  gap: 'evidence gap',
}

function ChevronIcon({ open = false }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      className={`h-4 w-4 shrink-0 transition-transform duration-150 ${open ? 'rotate-180' : 'group-open:rotate-180'}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="m4 6 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function DownloadIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M10 2.75v9.5m0 0 3.5-3.5m-3.5 3.5-3.5-3.5M3.5 16.25h13" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// Two formats do not need a dropdown. A menu here added a click, hid both options behind a
// chevron, and made the heaviest element on the page a control that does nothing by itself.
function DownloadActions({ runId, ordering }) {
  const formats = [
    ['html', 'HTML', 'print to PDF'],
    ['tex', 'LaTeX', 'open in Overleaf'],
  ]

  return (
    <div className="mt-5 border-t border-border pt-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Download</p>
      <div className="mt-2 grid grid-cols-2 gap-2">
        {/* apiUrl, not a bare path: the frontend and the API are different hosts, so
            "/api/..." resolves against the frontend and 404s there without ever reaching the
            backend. Every other request goes through apiFetch, which already handles this — a
            plain anchor is the one place that has to remember. */}
        {formats.map(([extension, label, hint]) => (
          <a
            key={extension}
            href={apiUrl(`/tailoring/runs/${runId}/resume.${extension}?ordering=${ordering}`)}
            aria-label={`Download ${label}`}
            className="flex min-h-11 flex-col justify-center rounded-md border border-border bg-pure-white px-3 py-2 transition-colors duration-150 hover:border-obsidian focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-obsidian"
          >
            <span className="flex items-center gap-1.5 text-sm text-ink">
              <DownloadIcon />
              {label}
            </span>
            <span className="mt-0.5 text-xs text-muted">{hint}</span>
          </a>
        ))}
      </div>
    </div>
  )
}

const GAP_GROUPS = [
  ['required', 'Required'],
  ['preferred', 'Preferred'],
  ['nice_to_have', 'Nice to have'],
  ['other', 'Other'],
]

function GapsPanel({ gaps, outcomes, entries, runId, onSaved }) {
  const importanceByRequirement = new Map(
    (outcomes || []).map((item) => [item.requirement, item.importance || 'other']),
  )
  const groups = GAP_GROUPS.map(([key, label]) => ({
    key,
    label,
    gaps: gaps.filter((gap) => (importanceByRequirement.get(gap.requirement) || 'other') === key),
  })).filter((group) => group.gaps.length > 0)

  return (
    <section className="surface-card overflow-hidden" aria-labelledby="gaps-heading">
      <div className="p-5">
        <h2 id="gaps-heading" className="text-base font-medium text-ink">
          Gaps {gaps.length > 0 && <span className="text-muted">({gaps.length})</span>}
        </h2>
        <p className="mt-1 text-xs leading-5 text-muted">
          Nothing in your resume supports these. If one is wrong, say where you used it —
          we read a resume, and a resume is not the whole truth about you.
        </p>
      </div>
      {gaps.length === 0 ? (
        <p className="border-t border-border px-5 py-4 text-xs text-muted">No evidence gaps in the fit assessment.</p>
      ) : (
        <div className="border-t border-border">
          {groups.map((group, index) => (
            <GapAccordion
              key={group.key}
              group={group}
              defaultOpen={index === 0}
              entries={entries}
              runId={runId}
              onSaved={onSaved}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function GapAccordion({ group, defaultOpen, entries, runId, onSaved }) {
  const [open, setOpen] = useState(defaultOpen)
  const panelId = `gap-group-${group.key}`
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        aria-controls={panelId}
        aria-expanded={open}
        aria-label={`${group.label} gaps (${group.gaps.length})`}
        className="flex min-h-11 w-full items-center justify-between gap-3 px-5 py-3 text-left text-sm text-ink"
        onClick={() => setOpen((value) => !value)}
      >
        <span>{group.label} <span className="text-muted">({group.gaps.length})</span></span>
        <ChevronIcon open={open} />
      </button>
      {open && (
        <ul id={panelId} className="animate-soft-in space-y-3 px-5 pb-4">
          {group.gaps.map((gap) => (
            <li key={gap.id} className="border-t border-border pt-3">
              {/* a chip, like every other skill on this page — a requirement is a term, and
                  reading it as a sentence made the two panels look unrelated */}
              <span className="inline-flex min-h-7 items-center rounded-full border border-border bg-surface px-2.5 text-xs text-ink">
                {gap.requirement}
              </span>
              {gap.note && <p className="mt-1.5 text-xs leading-5 text-muted">{gap.note}</p>}
              {/* we read a resume, not a person — a gap can simply be us being wrong */}
              <UsedItHere skill={gap.requirement} entries={entries} runId={runId} onSaved={onSaved} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Requirements the resume claims in a keyword list but never demonstrates.
//
// These used to be classified `keep`, which renders as nothing — so a run could look empty
// while the fit engine knew the single most useful thing about the resume: that Python is
// proven by a word in a list rather than by any work. Nothing here can be auto-written,
// because only the candidate knows which project it belongs to.
// "I used this on…" — the answer to both of the fit engine's dead ends.
//
// A gap we got wrong, and a skill proven only by a keyword list, are the same missing fact:
// which project it belongs to. The resume cannot supply it, because the resume is what the
// user typed one afternoon, not the whole truth about them. So ask.
function UsedItHere({ skill, entries, runId, onSaved }) {
  const [step, setStep] = useState(null)        // null = closed, 1 = which, 2 = what
  const [entryId, setEntryId] = useState(null)
  const [detail, setDetail] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  if (!entries.length) return null

  const entry = entries.find((item) => item.id === entryId)

  function close() {
    setStep(null)
    setError(null)
  }

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      await apiFetch(`/tailoring/runs/${runId}/surface`, {
        method: 'POST',
        body: JSON.stringify({ skill, entry_id: entryId, detail }),
      })
      setSaved(true)
      close()
      onSaved?.()
    } catch (saveError) {
      setError(saveError)
    } finally {
      setSaving(false)
    }
  }

  if (saved) {
    return (
      <p className="mt-1.5 text-xs leading-5 text-muted" role="status">
        Added — the proposed wording is at the bottom of the edits, waiting for your approval.
      </p>
    )
  }

  return (
    <>
      <button
        type="button"
        className="mt-1 flex min-h-11 items-center text-xs leading-5 text-charcoal underline underline-offset-4 transition-colors duration-150 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-obsidian"
        onClick={() => setStep(1)}
      >
        I used this — where?
      </button>

      <Dialog
        open={step !== null}
        onClose={close}
        width="max-w-lg"
        title={step === 1 ? `Where did you use ${skill}?` : `What did you do with ${skill}?`}
        description={step === 1
          ? 'Pick the project or role it belongs to. We will never write it anywhere you did not pick.'
          : `On ${entry?.name ?? 'that entry'}. Your own words are the evidence for whatever this adds.`}
        footer={step === 1 ? (
          <>
            <button type="button" className="secondary-button" onClick={close}>Cancel</button>
            <button
              type="button"
              className="primary-button"
              disabled={!entryId}
              onClick={() => setStep(2)}
            >
              Next
            </button>
          </>
        ) : (
          <>
            <button type="button" className="secondary-button" onClick={() => setStep(1)}>Back</button>
            <button
              type="button"
              className="primary-button"
              disabled={saving || detail.trim().length < 10}
              onClick={submit}
            >
              <ButtonLabel pending={saving} pendingText="Writing…">Add to my resume</ButtonLabel>
            </button>
          </>
        )}
      >
        {error && <InlineAlert className="mb-3">{error.message}</InlineAlert>}

        {step === 1 ? (
          <ul className="space-y-1">
            {entries.map((item) => (
              <li key={item.id}>
                <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink">
                  <input
                    type="radio"
                    name="used-it-entry"
                    className="size-4 shrink-0 accent-ink"
                    checked={entryId === item.id}
                    onChange={() => setEntryId(item.id)}
                  />
                  {item.name}
                </label>
              </li>
            ))}
          </ul>
        ) : (
          <label className="block text-sm text-ink">
            What you actually did with it
            <textarea
              data-dialog-autofocus
              value={detail}
              onChange={(event) => setDetail(event.target.value)}
              rows={4}
              maxLength={500}
              placeholder={`e.g. Used ${skill} to track releases and roll back a bad deploy`}
              className="control mt-2 p-3 text-sm leading-5"
            />
            {/* the claim checker reads the answer, not the question — "yes" supports nothing */}
            <span className="mt-1 block text-xs leading-5 text-muted">
              Name what it applied to and anything concrete about it. A bare yes cannot support
              a line on your resume, so nothing will be written from one.
            </span>
          </label>
        )}
      </Dialog>
    </>
  )
}

function KeywordOnlyPanel({ items, entries, runId, onSaved }) {
  if (!items.length) return null
  return (
    <section className="surface-card p-5" aria-labelledby="keyword-only-heading">
      <h2 id="keyword-only-heading" className="text-base font-medium text-ink">
        Claimed, but not shown <span className="text-muted">({items.length})</span>
      </h2>
      <p className="mt-1 text-xs leading-5 text-muted">
        This job asks for these and your resume lists them — but no bullet shows you using
        them. A recruiter reads work, not keyword lists. Adding each to the project you
        actually used it on is the highest-value change here, and only you know which that is.
      </p>
      <ul className="mt-4 space-y-2.5">
        {items.map((item) => {
          // A grouped requirement is labelled with the posting's whole sentence, which is
          // right on the job page and useless as a chip. What the reader needs here is the
          // skill itself, so show the alternatives that actually matched.
          const named = item.satisfied_by?.length ? item.satisfied_by : [item.requirement]
          return (
            <li key={item.position}>
              <div className="flex flex-wrap gap-1.5">
                {named.map((name) => (
                  <span
                    key={name}
                    className="inline-flex min-h-7 items-center rounded-full border border-border bg-surface px-2.5 text-xs text-ink"
                  >
                    {name}
                  </span>
                ))}
              </div>
              {item.satisfied_by?.length > 0 && item.requirement !== named[0] && (
                <p className="mt-1 text-xs leading-5 text-muted">{item.requirement}</p>
              )}
              <UsedItHere skill={named[0]} entries={entries} runId={runId} onSaved={onSaved} />
            </li>
          )
        })}
      </ul>
    </section>
  )
}

const CANDIDATE_STATES = {
  handled: { label: 'Handled', tone: 'text-ink' },
  // "kept" is a decision the agent made and explained; "skipped" is the run finding nothing to
  // work with. Same shelf, different meanings, so they do not share a label.
  kept: { label: 'Kept as-is', tone: 'text-ink' },
  skipped: { label: 'Nothing to edit', tone: 'text-muted' },
  needs_review: { label: 'Needs you', tone: 'text-ink' },
  pending: { label: 'Not reached', tone: 'text-muted' },
  active: { label: 'Not reached', tone: 'text-muted' },
}

// What the run was ASKED to do, and what became of each piece.
//
// This replaces a "Needs your confirmation" panel whose Keep/Ignore buttons saved nothing and
// which restated what the job page already showed. The useful version is not a decision the
// user has to make — it is an honest account of whether the work actually happened.
function WorkPanel({ work, candidates }) {
  if (!work?.assigned) return null
  const unfinished = work.unfinished + work.needs_review

  return (
    <section className="surface-card p-5" aria-labelledby="work-heading">
      <h2 id="work-heading" className="text-base font-medium text-ink">
        What this run worked on <span className="text-muted">({work.assigned})</span>
      </h2>
      <p className="mt-1 text-xs leading-5 text-muted">
        {unfinished === 0
          ? 'Everything the planner assigned was handled.'
          : `${work.handled} of ${work.assigned} handled. The rest is listed below with why.`}
      </p>
      <ul className="mt-4 space-y-3">
        {candidates.map((item) => {
          const state = CANDIDATE_STATES[item.status] || { label: item.status, tone: 'text-muted' }
          return (
            <li key={item.id} className="border-t border-border pt-3 first:border-0 first:pt-0">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm text-ink">{item.requirement}</p>
                <span className={`shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] ${state.tone}`}>
                  {state.label}
                </span>
              </div>
              {item.outcome && (
                <p className="mt-1 text-xs leading-5 text-muted">{item.outcome}</p>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}


// While a run is in flight the page is a step in a process, not a place to browse.
//
// The sidebar, the back link and the download card were all still on screen while tailoring
// was mid-question, which invites the user to wander off in the middle of something they are
// being asked to finish. Everything but the current step is removed until the run stops.
// A run has two phases and only one of them is steps. The reviewer makes one model call per
// resume bullet and can be most of a run's wall clock; `steps_used` is 0 throughout, so a step
// counter is not merely uninformative there, it reads as "nothing is happening".
function reviewPhase(run) {
  const progress = run?.review_progress
  if (!progress || !progress.total) return null
  if (progress.reviewed >= progress.total) return null      // reviewing is done; steps begin
  return progress
}

function StepRail({ run }) {
  const reviewing = reviewPhase(run)
  const total = reviewing ? reviewing.total : (run?.max_steps ?? 12)
  const used = reviewing
    ? reviewing.reviewed
    : Math.min(run?.steps_used ?? 0, run?.max_steps ?? 12)
  const percent = Math.max(8, (used / Math.max(total, 1)) * 100)

  return (
    <div className="mb-8">
      <div className="flex items-baseline justify-between gap-3">
        <p className="eyebrow">Tailoring paused</p>
        <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
          {reviewing ? `reviewing ${used} of ${total} bullets` : `step ${used} of ${total}`}
        </span>
      </div>
      <div
        className="mt-3 h-1 overflow-hidden rounded-full bg-border"
        role="progressbar"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={reviewing ? 'Reviewing resume bullets' : 'Tailoring progress'}
      >
        <div
          className="h-full rounded-full bg-obsidian transition-[width] duration-700 ease-out"
          style={{ width: `${Math.max(6, percent)}%` }}
        />
      </div>
    </div>
  )
}

function RunProgress({ run }) {
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setTick((value) => value + 1), 2200)
    return () => clearInterval(timer)
  }, [])

  const reviewing = reviewPhase(run)
  const done = run?.trace?.length ?? 0
  // history only, since the newest call is already the headline
  const recent = reviewing ? [] : (run?.trace?.slice(0, -1).slice(-3).reverse() ?? [])
  const headline = reviewing
    ? `Reviewing resume bullets: ${reviewing.reviewed} of ${reviewing.total}`
    : done > 0 ? describe(run.trace[done - 1]) : OPENING_LINES[tick % OPENING_LINES.length]
  // steps, not tool calls — the cap is on model turns
  const percent = reviewing
    ? Math.min(95, Math.round((reviewing.reviewed / Math.max(reviewing.total, 1)) * 100))
    : Math.min(95, Math.round(((run?.steps_used ?? 0) / (run?.max_steps ?? 8)) * 100))

  return (
    <div className="surface-card p-6">
      <div className="flex items-center gap-3">
        <Spinner />
        <p className="text-sm text-ink" role="status" aria-live="polite">{headline}</p>
      </div>

      <div className="mt-5 h-1 overflow-hidden rounded-full bg-border">
        <div
          className="h-full rounded-full bg-obsidian transition-[width] duration-700 ease-out"
          style={{ width: `${Math.max(6, percent)}%` }}
        />
      </div>
      <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
        {reviewing
          ? `bullet ${reviewing.reviewed} of ${reviewing.total} read${reviewing.unavailable ? ` · ${reviewing.unavailable} unavailable` : ''}`
          : `step ${run?.steps_used ?? 0} of ${run?.max_steps ?? 8} · ${done} tool ${done === 1 ? 'call' : 'calls'}`}
      </p>

      {recent.length > 0 && (
        <ul className="mt-5 space-y-1.5 border-t border-border pt-4">
          {recent.map((step, index) => (
            <li key={`${step.step}-${index}`} className={`text-xs ${index === 0 ? 'text-ink' : 'text-muted'}`}>
              {step.status === 'failed' ? `Rejected: ${step.error}` : describe(step)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EditCard({ edit, onDecide, busy, pending, error }) {
  const decided = edit.status !== 'proposed'
  const merged = edit.edit_type === 'merge'
  return (
    <article className={`surface-card p-5 ${decided ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">{edit.requirement}</p>
        {merged && (
          <span className="rounded-full border border-border bg-surface px-2 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
            Combines {edit.source_bullets.length} bullets
          </span>
        )}
      </div>

      {merged ? (
        <div className="mt-3">
          <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Now</p>
          <ul className="mt-1 space-y-1.5 text-sm leading-6 text-muted">
            {edit.source_bullets.map((bullet) => <li key={bullet.bullet_id}>• {bullet.text}</li>)}
          </ul>
        </div>
      ) : edit.original_text && (
        <div className="mt-3">
          <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Now</p>
          <p className="mt-1 text-sm leading-6 text-muted">{edit.original_text}</p>
        </div>
      )}

      <div className="mt-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Proposed</p>
        <p className="mt-1 text-sm leading-6 text-ink">{edit.proposed_text}</p>
      </div>

      {edit.validation_warnings?.length > 0 && (
        <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs leading-5 text-charcoal">
          <p className="font-medium text-ink">Check before accepting</p>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {edit.validation_warnings.map((warning) => (
              <li key={`${warning.code}:${warning.message}`}>
                {warning.message} {warning.evidence}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The one category of edit with no positive evidence behind it. The factual checks
          passed, so no technology or number was lost — but those are not every fact, and a
          rewrite can drop "Pitt's FSAE EV driverless program" and still arrive here. Saying so
          is more honest than letting the absence of a warning imply there is nothing to check. */}
      {edit.compression_only && (
        <p className="mt-4 border-t border-border pt-3 text-xs leading-5 text-muted">
          <span className="text-ink">Shorter than the original.</span>{' '}
          Nothing measurable was dropped — check that nothing important was.
        </p>
      )}

      {/* Why first: the question a reader has in front of a rewrite is "what does this buy me",
          and that has to be answerable before the citation trail is worth opening. Collapsed
          like the others so a screen of proposals stays skimmable. */}
      {edit.reason && (
        <details className="mt-4 border-t border-border pt-3">
          <summary className="cursor-pointer text-xs text-muted">Why this helps</summary>
          <p className="mt-2 text-xs leading-5 text-charcoal">{edit.reason}</p>
        </details>
      )}

      {edit.evidence.length > 0 && (
        <details className="mt-3 border-t border-border pt-3">
          <summary className="cursor-pointer text-xs text-muted">
            Cited {edit.evidence.length} {edit.evidence.length === 1 ? 'bullet' : 'bullets'} from your resume
          </summary>
          <ul className="mt-2 space-y-1.5">
            {edit.evidence.map((item) => (
              <li key={item.bullet_id} className="text-xs leading-5 text-muted">“{item.text}”</li>
            ))}
          </ul>
        </details>
      )}

      {edit.confirmed_details?.length > 0 && (
        <details className="mt-3 border-t border-border pt-3">
          <summary className="cursor-pointer text-xs text-muted">Includes a detail you confirmed</summary>
          <ul className="mt-2 space-y-2">
            {edit.confirmed_details.map((detail) => (
              <li key={detail.id} className="text-xs leading-5 text-muted">
                <span className="text-ink">{detail.question}</span> {detail.answer}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-4 flex items-center gap-2">
        {decided ? (
          <span className="text-xs text-muted">{edit.status === 'accepted' ? '✓ Accepted' : 'Rejected'}</span>
        ) : (
          <>
            <button className="primary-button" disabled={busy} onClick={() => onDecide(edit.id, 'accepted')}>
              <ButtonLabel pending={pending} pendingText="Saving…">Accept</ButtonLabel>
            </button>
            <button className="secondary-button" disabled={busy} onClick={() => onDecide(edit.id, 'rejected')}>
              Reject
            </button>
          </>
        )}
      </div>
      {error && <InlineAlert className="mt-3">{error.message}</InlineAlert>}
    </article>
  )
}

function DetailRequestCard({ request, onResolve, busy, error, showContext = true }) {
  const [answer, setAnswer] = useState('')
  const isThisQuestion = busy && request.pending
  // "Did you use X here?" is a different question from "how big was it", and answering no is
  // a real answer rather than a skipped one
  const asksAboutUse = request.intent === 'establish_use' 

  return (
    <article className={showContext ? 'surface-card p-5' : 'border-t border-border pt-4 first:border-t-0 first:pt-0'}>
      {showContext && (
        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">{request.requirement}</p>
      )}
      <h3 className={showContext ? 'mt-2 text-base font-medium text-ink' : 'text-base font-medium text-ink'}>
        {request.question}
      </h3>
      {showContext && request.bullet_text && (
        <div className="mt-3 border-l-2 border-border pl-3">
          <p className="text-xs leading-5 text-muted">“{request.bullet_text}”</p>
        </div>
      )}
      <label className="mt-4 block text-xs text-ink">
        Your answer
        <textarea
          className="control mt-2 min-h-24 resize-y p-3 text-sm leading-5"
          maxLength={500}
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          placeholder={asksAboutUse
            ? 'Example: Yes — I used it for the session cache on the login path.'
            : 'Example: Used by 12 staff members and reduced weekly reporting by 3 hours.'}
        />
      </label>
      <p className="mt-2 text-xs leading-5 text-muted">
        Give only facts you can stand behind. The app will never invent a number for you.
      </p>
      {asksAboutUse ? (
        // A yes/no has to be recorded, not read out of the prose: "I didn't use Redis" names
        // Redis, and a system that greps the answer cannot tell the two apart.
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            className="primary-button"
            disabled={busy || !answer.trim()}
            onClick={() => onResolve(request.id, {
              action: 'answer', answer: answer.trim(), used: true,
            })}
          >
            <ButtonLabel pending={isThisQuestion} pendingText="Continuing…">
              Yes — I used it
            </ButtonLabel>
          </button>
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => onResolve(request.id, {
              action: 'answer', answer: answer.trim() || "I didn't use this here.", used: false,
            })}
          >
            No, I didn&apos;t use this
          </button>
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => onResolve(request.id, { action: 'dismiss' })}
          >
            Skip question
          </button>
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            className="primary-button"
            disabled={busy || !answer.trim()}
            onClick={() => onResolve(request.id, { action: 'answer', answer: answer.trim() })}
          >
            <ButtonLabel pending={isThisQuestion} pendingText="Continuing…">Use this detail</ButtonLabel>
          </button>
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => onResolve(request.id, { action: 'dismiss' })}
          >
            Skip question
          </button>
        </div>
      )}
      {error && <InlineAlert className="mt-3">{error.message}</InlineAlert>}
    </article>
  )
}

function groupDetailRequests(requests) {
  const groups = new Map()
  for (const request of requests || []) {
    // An old unscoped row must stand alone. Grouping all NULL bullet ids would imply that
    // unrelated questions describe one piece of work.
    const key = request.bullet_id || `question:${request.id}`
    if (!groups.has(key)) groups.set(key, { key, context: request, requests: [] })
    groups.get(key).requests.push(request)
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      pending: group.requests.filter((request) => request.status === 'pending'),
      resolved: group.requests.filter((request) => request.status !== 'pending').length,
    }))
    .filter((group) => group.pending.length > 0)
}

function compositionDescription(composition) {
  if (!composition?.changed) return 'Your saved order already puts the strongest evidence first.'

  const changes = []
  if (composition.promoted_projects?.length > 0) {
    changes.push(`moves ${composition.promoted_projects.join(', ')} higher`)
  }
  if (composition.promoted_bullets?.length > 0) {
    const count = composition.promoted_bullets.length
    changes.push(`raises ${count} ${count === 1 ? 'bullet' : 'bullets'}`)
  }
  if (composition.promoted_skills?.length > 0) {
    const count = composition.promoted_skills.length
    changes.push(`raises ${count} ${count === 1 ? 'skill' : 'skills'}`)
  }
  return `For this role, it ${changes.join(', ')}. No claims are added or removed.`
}

// Evidence strength is ordinal, so it gets one hue in monotone steps — strongest darkest.
// The label always ships with the swatch; colour never carries the meaning by itself.
const STATES = {
  EXPLICIT: { label: 'Says it outright', swatch: 'bg-evidence-strong', weight: '1.0' },
  INFERRED: { label: 'Implied by', swatch: 'bg-evidence-medium', weight: '0.8' },
  PARTIAL: { label: 'Only hinted at', swatch: 'bg-evidence-weak', weight: '0.4' },
}

const IMPORTANCE_LABELS = { required: 'Required', preferred: 'Preferred', nice_to_have: 'Nice to have' }

function StateTag({ state }) {
  const meta = STATES[state]
  if (!meta) return <span className="text-muted">{state}</span>
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${meta.swatch}`} />
      {meta.label}
    </span>
  )
}

// One bar, one hue: this is magnitude, so length does the work and colour only reinforces it.
function ScoreBar({ score, max }) {
  const width = max > 0 ? Math.max((score / max) * 100, score > 0 ? 4 : 0) : 0
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-evidence-track" aria-hidden="true">
      {width > 0 && (
        <div
          className="h-full rounded-full bg-evidence-medium transition-[width] duration-200"
          style={{ width: `${width}%` }}
        />
      )}
    </div>
  )
}

function ScoredBullet({ row, max }) {
  return (
    <li className="border-t border-border py-4 first:border-t-0 first:pt-0">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm leading-6 text-ink">{row.text}</p>
        <span className="shrink-0 font-mono text-sm text-ink">{row.score}</span>
      </div>
      <div className="mt-2"><ScoreBar score={row.score} max={max} /></div>
      <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
        {row.entry}
        {row.from !== row.to && <span className="text-ink"> · moved {row.from} → {row.to}</span>}
      </p>

      {row.contributions.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-xs leading-5 text-muted">
          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-evidence-none" />
          Matches no requirement here, so it scored zero and stayed put.
        </p>
      ) : (
        <table className="mt-3 w-full text-left text-xs">
          <caption className="sr-only">Requirements this bullet evidences</caption>
          <thead>
            <tr className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
              <th scope="col" className="pb-1.5 font-normal">Requirement</th>
              <th scope="col" className="pb-1.5 font-normal">Match</th>
              <th scope="col" className="pb-1.5 font-normal">Posting wants</th>
              <th scope="col" className="pb-1.5 text-right font-normal">Points</th>
            </tr>
          </thead>
          <tbody>
            {row.contributions.map((item) => (
              <tr key={`${row.bullet_id}-${item.requirement}`} className="border-t border-border align-top">
                <td className="py-2 pr-3 text-ink">
                  {item.requirement}
                  {item.matched !== item.requirement && (
                    <span className="block text-muted">via &ldquo;{item.matched}&rdquo;</span>
                  )}
                </td>
                <td className="py-2 pr-3 text-charcoal">
                  <StateTag state={item.state} />
                  <span className="block font-mono text-[10px] text-muted">{item.state_weight}</span>
                </td>
                <td className="py-2 pr-3 text-charcoal">
                  {IMPORTANCE_LABELS[item.importance] || item.importance}
                  <span className="block font-mono text-[10px] text-muted">×{item.importance_weight}</span>
                </td>
                <td className="py-2 text-right font-mono text-ink">+{item.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </li>
  )
}

function WhatMoved({ composition }) {
  const projects = composition.promoted_projects || []
  const bullets = composition.promoted_bullets || []
  const skills = composition.promoted_skills || []
  if (projects.length + bullets.length + skills.length === 0) {
    return (
      <p className="text-sm leading-6 text-charcoal">
        Nothing moved — your saved order already leads with the strongest evidence for this posting.
      </p>
    )
  }

  return (
    <ul className="space-y-2">
      {projects.map((project) => (
        <li key={`project-${project}`} className="flex gap-3 text-sm leading-6 text-charcoal">
          <span className="mt-0.5 w-16 shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Project</span>
          <span className="text-ink">{project}</span>
          <span className="ml-auto shrink-0 text-muted">moved up</span>
        </li>
      ))}
      {bullets.map((bullet) => (
        <li key={`${bullet.entry}-${bullet.from}-${bullet.to}`} className="flex gap-3 text-sm leading-6 text-charcoal">
          <span className="mt-0.5 w-16 shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Bullet</span>
          <span className="min-w-0">
            <span className="text-ink">{bullet.entry}</span>
            <span className="block text-muted">{bullet.text}</span>
          </span>
          <span className="ml-auto shrink-0 whitespace-nowrap font-mono text-[10px] text-muted">
            {bullet.from} → {bullet.to}
          </span>
        </li>
      ))}
      {skills.map((skill) => (
        <li key={`skill-${skill}`} className="flex gap-3 text-sm leading-6 text-charcoal">
          <span className="mt-0.5 w-16 shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Skill</span>
          <span className="text-ink">{skill}</span>
          <span className="ml-auto shrink-0 text-muted">moved up</span>
        </li>
      ))}
    </ul>
  )
}

function Section({ title, children }) {
  return (
    <section className="mt-6 first:mt-0">
      <h3 className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function OrderingDialog({ open, onClose, composition }) {
  const rows = composition?.scored_bullets || []
  const max = rows.reduce((best, row) => Math.max(best, row.score), 0)
  const unscored = rows.filter((row) => row.score === 0).length

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width="max-w-3xl"
      title="What changed, and why"
      description="Reordering only — nothing is added, removed, or reworded."
    >
      <Section title="What moved">
        <WhatMoved composition={composition || {}} />
      </Section>

      <Section title="How a bullet is scored">
        <div className="rounded-md border border-border bg-surface px-4 py-3">
          <p className="text-sm leading-6 text-charcoal">
            For every requirement a bullet evidences:{' '}
            <span className="text-ink">how strongly it matches</span> ×{' '}
            <span className="text-ink">how much the posting wanted it</span>. Those add up, and
            higher sorts first.
          </p>
          <dl className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Match strength</dt>
              <dd className="mt-1.5 space-y-1">
                {Object.entries(STATES).map(([state, meta]) => (
                  <p key={state} className="flex items-center justify-between gap-3 text-xs text-charcoal">
                    <StateTag state={state} />
                    <span className="font-mono text-[10px] text-muted">{meta.weight}</span>
                  </p>
                ))}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Posting weight</dt>
              <dd className="mt-1.5 space-y-1">
                {[['Required', '3'], ['Preferred', '2'], ['Nice to have', '1']].map(([label, weight]) => (
                  <p key={label} className="flex items-center justify-between gap-3 text-xs text-charcoal">
                    {label}
                    <span className="font-mono text-[10px] text-muted">×{weight}</span>
                  </p>
                ))}
              </dd>
            </div>
          </dl>
        </div>
      </Section>

      <Section title={`Every bullet (${rows.length})`}>
        {unscored > 0 && (
          <p className="mb-3 text-xs leading-5 text-muted">
            {unscored} matched no requirement and scored zero.
          </p>
        )}
        <ul>
          {rows.map((row) => <ScoredBullet key={row.bullet_id} row={row} max={max} />)}
        </ul>
      </Section>
    </Dialog>
  )
}

function OrderingExplainer({ composition }) {
  const [open, setOpen] = useState(false)
  if (!composition) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex min-h-11 items-center text-xs leading-5 text-charcoal underline underline-offset-4 transition-colors duration-150 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-obsidian"
      >
        What changed, and why
      </button>
      <OrderingDialog open={open} onClose={() => setOpen(false)} composition={composition} />
    </>
  )
}

function OrderingChoice({ composition, ordering, onChange }) {
  if (!composition?.changed) {
    return (
      <div className="mt-4 rounded-md border border-border bg-surface px-3 py-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink">Order checked</p>
        <p className="mt-1 text-xs leading-5 text-muted">{compositionDescription(composition)}</p>
        <OrderingExplainer composition={composition} />
      </div>
    )
  }

  return (
    <fieldset className="mt-4">
      <legend className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Resume order</legend>
      <div className="mt-2 grid grid-cols-2 gap-1 rounded-md border border-border bg-surface p-1">
        {[
          ['tailored', 'Tailored order'],
          ['original', 'Original order'],
        ].map(([value, label]) => (
          <label key={value} className="cursor-pointer">
            <input
              className="peer sr-only"
              type="radio"
              name="resume-ordering"
              value={value}
              checked={ordering === value}
              onChange={() => onChange(value)}
            />
            <span className="flex min-h-11 items-center justify-center rounded-md px-3 text-center text-xs text-charcoal transition-colors duration-150 peer-checked:bg-obsidian peer-checked:text-white peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-obsidian">
              {label}
            </span>
          </label>
        ))}
      </div>
      <div className="mt-3 min-h-12">
        <div role="status" aria-live="polite">
          <p className="text-xs font-medium text-ink">
            {ordering === 'tailored' ? 'Tailored ordering applied' : 'Original ordering selected'}
          </p>
          <p className="mt-1 text-xs leading-5 text-muted">
            {ordering === 'tailored'
              ? compositionDescription(composition)
              : 'Keeps your saved entry, bullet, and skill order. Accepted wording edits still apply.'}
          </p>
        </div>
        <OrderingExplainer composition={composition} />
      </div>
    </fieldset>
  )
}

export default function TailoringRunPage() {
  const { id } = useParams()
  const queryClient = useQueryClient()
  const [ordering, setOrdering] = useState('tailored')

  const runQuery = useQuery({
    queryKey: ['tailoring-run', id],
    queryFn: () => apiFetch(`/tailoring/runs/${id}`),
    // No retry override: the app's QueryClient already retries, and pinning a number here
    // would quietly lower it and fight the test client, which turns retries off on purpose.
    // the run continues on the server after the request returns
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  })

  // a run whose worker was killed (deploy, restart) kept every step it finished — resuming
  // carries on from there instead of paying for those steps again
  const resumeMutation = useMutation({
    mutationFn: () => apiFetch(`/tailoring/runs/${id}/resume`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tailoring-run', id] }),
  })

  // the projects and jobs a skill can be attached to
  const evidenceQuery = useQuery({
    queryKey: ['resume-evidence'],
    queryFn: () => apiFetch('/resume/evidence'),
    retry: false,
  })
  const resumeEntries = (evidenceQuery.data?.entries || []).map((entry) => ({
    id: entry.id,
    name: entry.title || entry.organization || 'Untitled entry',
  }))
  const refreshRun = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['tailoring-run', id] })
    queryClient.invalidateQueries({ queryKey: ['job', id] })
  }, [queryClient, id])

  const decideMutation = useMutation({
    mutationFn: ({ editId, status }) => apiFetch(`/tailoring/edits/${editId}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tailoring-run', id] }),
  })

  const detailMutation = useMutation({
    mutationFn: ({ questionId, payload }) => apiFetch(`/tailoring/questions/${questionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tailoring-run', id] }),
  })

  if (runQuery.isLoading) return <PageLoader label="Loading tailoring run…" />

  // Only when there is nothing to show. A failed *background* poll used to replace a run the
  // user was in the middle of answering with a full-page error — tab away, come back, and the
  // work appeared to be gone until the next poll succeeded.
  if (runQuery.isError && !runQuery.data) {
    return (
      <div className="app-main min-h-screen bg-surface">
        <NavBar />
        <main className="page-container grid min-h-[70vh] place-items-center">
          <div className="max-w-md text-center animate-soft-in">
            <h1 className="page-heading">Run unavailable</h1>
            <p className="mt-3 text-sm text-muted">{runQuery.error.message}</p>
            <Link to="/dashboard" className="primary-button mt-5 inline-block">Back to dashboard</Link>
          </div>
        </main>
      </div>
    )
  }

  const run = runQuery.data
  const running = run.status === 'running'
  const waiting = run.status === 'waiting_for_user'
  const searches = run.trace.filter((step) => step.tool === 'search_resume')
  const rejected = run.trace.filter((step) => step.status === 'failed')
  const undecided = run.edits.filter((edit) => edit.status === 'proposed').length
  const accepted = run.edits.filter((edit) => edit.status === 'accepted').length
  const skillsToSurface = (run.outcomes || []).filter((item) => item.action === 'surface_skill')
  const keywordOnly = (run.outcomes || []).filter((item) => item.action === 'only_in_skills')
  const detailRequests = (run.detail_requests || []).filter((request) => request.status === 'pending')
  const detailGroups = groupDetailRequests(run.detail_requests || [])
  // matches RESUMABLE_ERRORS on the backend: the worker died, rather than the run being done
  const resumable = run.status === 'failed'
    && ['abandoned', 'tool_execution_failed', 'model_call_failed'].includes(run.error_code)
    && run.steps_used < run.max_steps

  const pageHeader = (
    <header className="mb-6">
      <p className="eyebrow">Tailoring</p>
      <h1 className="page-heading mt-2">
        {running ? 'Working through the posting' : waiting
          ? `${detailRequests.length === 1 ? 'One detail needs' : `${detailRequests.length} details need`} your input`
          : 'Proposed changes'}
      </h1>
      <p className="mt-2 max-w-2xl text-sm text-muted">
        Every proposal cites bullets from your own resume, and any technology or number it names is
        checked against them. Requirements with nothing behind them are listed as gaps instead.
      </p>
      {!running && !waiting && (
        <div className="mt-3 space-y-1 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
          <p>
            {run.status} · {run.steps_used} steps · {searches.length} {searches.length === 1 ? 'search' : 'searches'} ·{' '}
            {run.input_tokens + run.output_tokens} tokens
            {rejected.length > 0 && ` · ${rejected.length} rejected by grounding`}
          </p>
          {run.coverage && (
            <p>{run.coverage.accounted} of {run.coverage.total} requirements accounted for</p>
          )}
        </div>
      )}
    </header>
  )

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        {runQuery.isError && (
          <InlineAlert className="mb-4">
            Lost contact with the server for a moment — still showing the last update. Retrying.
          </InlineAlert>
        )}
        {/* no way back out mid-run: the user is being asked to finish something */}
        {!running && !waiting && (
          <Link to={`/jobs/${run.job_id}`} className="mb-6 inline-block text-sm text-muted hover:text-ink">← Back to job</Link>
        )}

        {running || waiting ? (
          <div className="mx-auto w-full max-w-2xl py-6 animate-soft-in sm:py-12">
            {/* only while paused — the working state has its own bar inside the run card */}
            {waiting && <StepRail run={run} waiting />}

            {running && (
              <>
                <h1 className="page-heading">Working through the posting</h1>
                <p className="mt-2 mb-6 text-sm leading-6 text-muted">
                  Searching your evidence and drafting only what it supports. This takes a few moments.
                </p>
                <RunProgress run={run} />
              </>
            )}

            {waiting && detailRequests.length > 0 && (
              <section aria-labelledby="detail-request-heading">
                <h1 id="detail-request-heading" className="page-heading">
                  {detailRequests.length === 1 ? 'One question before we continue' : `${detailRequests.length} questions before we continue`}
                </h1>
                <p className="mt-2 text-sm leading-6 text-muted">
                  Tailoring paused rather than guessing. Answer what you know or skip it — the
                  same run picks straight back up. If you skip every question for a bullet, that
                  bullet stays unchanged.
                </p>
                <div className="mt-6 space-y-4">
                  {detailGroups.map((group) => (
                    <section key={group.key} className="surface-card p-5">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
                          {group.context.requirement}
                        </p>
                        <p className="text-xs text-muted">
                          {group.resolved} of {group.requests.length} answered or skipped
                        </p>
                      </div>
                      {group.context.bullet_text && (
                        <div className="mt-3 border-l-2 border-border pl-3">
                          <p className="text-xs leading-5 text-muted">“{group.context.bullet_text}”</p>
                        </div>
                      )}
                      <div className="mt-5 space-y-5">
                        {group.pending.map((request) => (
                          <DetailRequestCard
                            key={request.id}
                            showContext={false}
                            request={{
                              ...request,
                              pending: detailMutation.variables?.questionId === request.id,
                            }}
                            busy={detailMutation.isPending}
                            error={detailMutation.variables?.questionId === request.id ? detailMutation.error : null}
                            onResolve={(questionId, payload) => detailMutation.mutate({ questionId, payload })}
                          />
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
              </section>
            )}
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]">
            <div className="min-w-0">
              {pageHeader}

              {run.status === 'failed' && (
                <InlineAlert className="mb-6">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <span>
                      {resumable
                        ? `This run stopped after ${run.steps_used} ${run.steps_used === 1 ? 'step' : 'steps'}. Resuming picks up where it left off — the finished steps aren't repeated.`
                        : 'This run failed before it could finish. Start another from the job page.'}
                    </span>
                    {resumable && (
                      <button
                        type="button"
                        onClick={() => resumeMutation.mutate()}
                        disabled={resumeMutation.isPending}
                        className="primary-button whitespace-nowrap"
                      >
                        <ButtonLabel pending={resumeMutation.isPending} pendingText="Resuming…">
                          Resume run
                        </ButtonLabel>
                      </button>
                    )}
                  </div>
                </InlineAlert>
              )}

              {resumeMutation.error && <InlineAlert className="mb-6">{resumeMutation.error.message}</InlineAlert>}

              <section aria-labelledby="proposals-heading">
              <div className="mb-3 flex items-baseline justify-between">
                <h2 id="proposals-heading" className="text-base font-medium text-ink">
                  Suggestions {run.edits.length > 0 && <span className="text-muted">({run.edits.length})</span>}
                </h2>
                {undecided > 0 && <p className="text-xs text-muted">{undecided} awaiting your call</p>}
              </div>

              {run.edits.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-6 py-10 text-center text-sm text-muted">
                  {waiting
                    ? 'The run will draft suggestions after you answer or skip the question above.'
                    : run.status === 'limit_reached'
                    ? 'This run hit its step limit before proposing anything. Start another from the job page.'
                    : 'No rewrites this run — nothing in your resume needed rewording for this posting.'}
                </div>
              ) : (
                <div className="space-y-4">
                  {run.edits.map((edit) => (
                    <EditCard
                      key={edit.id}
                      edit={edit}
                      busy={decideMutation.isPending}
                      pending={decideMutation.isPending && decideMutation.variables?.editId === edit.id}
                      error={decideMutation.variables?.editId === edit.id ? decideMutation.error : null}
                      onDecide={(editId, status) => decideMutation.mutate({ editId, status })}
                    />
                  ))}
                </div>
              )}
              </section>
            </div>

            <aside className="space-y-4">
              {!waiting && <section className="surface-card p-5" aria-labelledby="download-heading">
                <h2 id="download-heading" className="text-base font-medium text-ink">Tailored resume</h2>
                <p className="mt-1 text-xs leading-5 text-muted">
                  {accepted > 0
                    ? `${accepted} accepted ${accepted === 1 ? 'rewrite' : 'rewrites'} included.`
                    : 'No wording changes are accepted yet. You can still export the resume.'}
                </p>

                <OrderingChoice
                  composition={run.composition}
                  ordering={ordering}
                  onChange={setOrdering}
                />

                <DownloadActions runId={run.id} ordering={ordering} />
              </section>}

              {skillsToSurface.length > 0 && (
                <section className="surface-card p-5" aria-labelledby="surface-skills-heading">
                  <h2 id="surface-skills-heading" className="text-base font-medium text-ink">
                    Skills worth surfacing <span className="text-muted">({skillsToSurface.length})</span>
                  </h2>
                  <p className="mt-1 text-xs leading-5 text-muted">
                    Your evidence supports these, but they belong in Skills—not forced into a project bullet.
                  </p>
                  <ul className="mt-4 space-y-3">
                    {skillsToSurface.map((item) => (
                      <li key={`${item.position}-${item.requirement}`} className="border-t border-border pt-3 first:border-0 first:pt-0">
                        <p className="text-sm text-ink">{item.requirement}</p>
                        {item.inferred_from?.length > 0 && (
                          <p className="mt-1 text-xs leading-5 text-muted">
                            Supported by {item.inferred_from.join(', ')}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <KeywordOnlyPanel items={keywordOnly} entries={resumeEntries} runId={run.id} onSaved={refreshRun} />

              <WorkPanel work={run.work} candidates={run.candidates || []} />

              <GapsPanel gaps={run.gaps} outcomes={run.outcomes} entries={resumeEntries} runId={run.id} onSaved={refreshRun} />

              {(run.outcomes || []).length > 0 && (
                <details className="surface-card p-5">
                  <summary className="cursor-pointer text-sm text-ink">
                    Requirement decisions <span className="text-muted">({run.outcomes.length})</span>
                  </summary>
                  <ul className="mt-4 space-y-2">
                    {run.outcomes.map((item) => (
                      <li key={`${item.position}-${item.requirement}`} className="flex items-start justify-between gap-3 text-xs leading-5">
                        <span className="text-ink">{item.requirement}</span>
                        <span className="shrink-0 text-right text-muted">{outcomeLabels[item.action] || item.action}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              <details className="surface-card p-5">
                <summary className="cursor-pointer text-sm text-ink">
                  How it worked <span className="text-muted">({run.trace.length} calls)</span>
                </summary>
                <ol className="mt-4 space-y-2">
                  {run.trace.map((step, index) => (
                    <li key={index} className="text-xs leading-5 text-muted">
                      <span className="font-mono text-[10px] text-muted">{step.step}</span>{' '}
                      <span className="font-mono text-[11px] text-ink">{step.tool}</span>{' '}
                      {step.arguments?.query || step.arguments?.requirement || ''}
                      {step.status === 'failed' && <span className="text-ink"> — rejected: {step.error}</span>}
                    </li>
                  ))}
                </ol>
              </details>
            </aside>
          </div>
        )}
      </main>
    </div>
  )
}
