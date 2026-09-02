import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ButtonLabel, InlineAlert, PageLoader, Spinner } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { apiFetch } from '../lib/api'

// only the first line is generic; after that each one describes a tool call that ran
const OPENING_LINES = ['Reading the job posting…', 'Reading your resume evidence…', 'Planning what to look for…']

function describe(step) {
  if (step.tool === 'search_resume') return `Searching your resume for “${step.arguments?.query ?? ''}”…`
  if (step.tool === 'propose_edit') return `Drafting a rewrite for ${step.arguments?.requirement ?? 'a requirement'}…`
  if (step.tool === 'flag_gap') return `No evidence for ${step.arguments?.requirement ?? 'a requirement'} — flagging it…`
  return 'Thinking…'
}

function RunProgress({ run }) {
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setTick((value) => value + 1), 2200)
    return () => clearInterval(timer)
  }, [])

  const done = run?.trace?.length ?? 0
  // history only, since the newest call is already the headline
  const recent = run?.trace?.slice(0, -1).slice(-3).reverse() ?? []
  const headline = done > 0 ? describe(run.trace[done - 1]) : OPENING_LINES[tick % OPENING_LINES.length]
  // steps, not tool calls — the cap is on model turns
  const percent = Math.min(95, Math.round(((run?.steps_used ?? 0) / (run?.max_steps ?? 8)) * 100))

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
        step {run?.steps_used ?? 0} of {run?.max_steps ?? 8} · {done} tool {done === 1 ? 'call' : 'calls'}
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

function EditCard({ edit, onDecide, pending }) {
  const decided = edit.status !== 'proposed'
  return (
    <article className={`surface-card p-5 ${decided ? 'opacity-60' : ''}`}>
      <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">{edit.requirement}</p>

      {edit.original_text && (
        <div className="mt-3">
          <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Now</p>
          <p className="mt-1 text-sm leading-6 text-muted">{edit.original_text}</p>
        </div>
      )}

      <div className="mt-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Proposed</p>
        <p className="mt-1 text-sm leading-6 text-ink">{edit.proposed_text}</p>
      </div>

      {edit.evidence.length > 0 && (
        <details className="mt-4 border-t border-border pt-3">
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

      <div className="mt-4 flex items-center gap-2">
        {decided ? (
          <span className="text-xs text-muted">{edit.status === 'accepted' ? '✓ Accepted' : 'Rejected'}</span>
        ) : (
          <>
            <button className="primary-button" disabled={pending} onClick={() => onDecide(edit.id, 'accepted')}>
              <ButtonLabel pending={pending} pendingText="Saving…">Accept</ButtonLabel>
            </button>
            <button className="secondary-button" disabled={pending} onClick={() => onDecide(edit.id, 'rejected')}>
              Reject
            </button>
          </>
        )}
      </div>
    </article>
  )
}

export default function TailoringRunPage() {
  const { id } = useParams()
  const queryClient = useQueryClient()

  const runQuery = useQuery({
    queryKey: ['tailoring-run', id],
    queryFn: () => apiFetch(`/tailoring/runs/${id}`),
    retry: false,
    // the run continues on the server after the request returns
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  })

  const decideMutation = useMutation({
    mutationFn: ({ editId, status }) => apiFetch(`/tailoring/edits/${editId}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tailoring-run', id] }),
  })

  if (runQuery.isLoading) return <PageLoader label="Loading tailoring run…" />

  if (runQuery.isError) {
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
  const searches = run.trace.filter((step) => step.tool === 'search_resume')
  const rejected = run.trace.filter((step) => step.status === 'failed')
  const undecided = run.edits.filter((edit) => edit.status === 'proposed').length
  const accepted = run.edits.filter((edit) => edit.status === 'accepted').length

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <Link to={`/jobs/${run.job_id}`} className="mb-6 inline-block text-sm text-muted hover:text-ink">← Back to job</Link>

        <header className="mb-6">
          <p className="eyebrow">Tailoring</p>
          <h1 className="page-heading mt-2">{running ? 'Working through the posting' : 'Proposed changes'}</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Every proposal cites bullets from your own resume, and any technology or number it names is
            checked against them. Requirements with nothing behind them are listed as gaps instead.
          </p>
          {!running && (
            <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
              {run.status} · {run.steps_used} steps · {searches.length} {searches.length === 1 ? 'search' : 'searches'} ·{' '}
              {run.input_tokens + run.output_tokens} tokens
              {rejected.length > 0 && ` · ${rejected.length} rejected by grounding`}
            </p>
          )}
        </header>

        {decideMutation.error && <InlineAlert className="mb-4">{decideMutation.error.message}</InlineAlert>}

        {running && <RunProgress run={run} />}

        {run.status === 'failed' && (
          <InlineAlert className="mb-6">
            {run.error_code === 'abandoned'
              ? 'This run stopped before it finished. Start another from the job page.'
              : 'This run failed before it could finish. Start another from the job page.'}
          </InlineAlert>
        )}

        {!running && (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]">
            <section aria-labelledby="proposals-heading">
              <div className="mb-3 flex items-baseline justify-between">
                <h2 id="proposals-heading" className="text-base font-medium text-ink">
                  Suggestions {run.edits.length > 0 && <span className="text-muted">({run.edits.length})</span>}
                </h2>
                {undecided > 0 && <p className="text-xs text-muted">{undecided} awaiting your call</p>}
              </div>

              {run.edits.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-6 py-10 text-center text-sm text-muted">
                  {run.status === 'limit_reached'
                    ? 'This run hit its step limit before proposing anything. Start another from the job page.'
                    : 'No rewrites this run — nothing in your resume needed rewording for this posting.'}
                </div>
              ) : (
                <div className="space-y-4">
                  {run.edits.map((edit) => (
                    <EditCard
                      key={edit.id}
                      edit={edit}
                      pending={decideMutation.isPending}
                      onDecide={(editId, status) => decideMutation.mutate({ editId, status })}
                    />
                  ))}
                </div>
              )}
            </section>

            <aside className="space-y-4">
              <section className="surface-card p-5" aria-labelledby="download-heading">
                <h2 id="download-heading" className="text-base font-medium text-ink">Tailored resume</h2>
                <p className="mt-1 text-xs leading-5 text-muted">
                  {accepted > 0
                    ? `Your resume with ${accepted} accepted ${accepted === 1 ? 'rewrite' : 'rewrites'} applied. Everything else is unchanged.`
                    : 'Accept a suggestion above and it will appear here. You can still download your resume as it stands.'}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {/* plain links so the cookie goes along and the browser does the saving */}
                  <a className="secondary-button" href={`/api/tailoring/runs/${run.id}/resume.html`}>
                    Download HTML
                  </a>
                  <a className="secondary-button" href={`/api/tailoring/runs/${run.id}/resume.tex`}>
                    Download LaTeX
                  </a>
                </div>
                <p className="mt-3 text-xs leading-5 text-muted">
                  Open the HTML in a browser and print to PDF, or compile the LaTeX in Overleaf.
                </p>
              </section>

              <section className="surface-card p-5" aria-labelledby="gaps-heading">
                <h2 id="gaps-heading" className="text-base font-medium text-ink">
                  Gaps {run.gaps.length > 0 && <span className="text-muted">({run.gaps.length})</span>}
                </h2>
                <p className="mt-1 text-xs leading-5 text-muted">
                  Requirements with no support in your resume. Worth building, not worth claiming.
                </p>
                {run.gaps.length === 0 ? (
                  <p className="mt-4 text-xs text-muted">None found.</p>
                ) : (
                  <ul className="mt-4 space-y-3">
                    {run.gaps.map((gap) => (
                      <li key={gap.id} className="border-t border-border pt-3 first:border-0 first:pt-0">
                        <p className="text-sm text-ink">{gap.requirement}</p>
                        {gap.note && <p className="mt-1 text-xs leading-5 text-muted">{gap.note}</p>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

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
