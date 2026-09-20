import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../helpers/render'
import TailoringRunPage from '../../src/pages/TailoringRunPage'
import { apiFetch, apiUrl } from '../../src/lib/api'

// apiUrl is real, not a stub: the download link is the one place that builds a URL itself,
// and a mock that returns undefined would hide exactly the bug this covers
vi.mock('../../src/lib/api', async (importOriginal) => ({
  ...(await importOriginal()),
  apiFetch: vi.fn(),
}))
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual('react-router-dom')),
  useParams: () => ({ id: 'run-1' }),
}))

const finished = {
  id: 'run-1',
  job_id: 'job-1',
  status: 'completed',
  steps_used: 4,
  max_steps: 8,
  input_tokens: 900,
  output_tokens: 100,
  error_code: null,
  coverage: {
    total: 4, accounted: 4, rewrite_candidates: 1, skills_to_surface: 1, gaps: 1, confirmations: 1,
  },
  work: { assigned: 2, handled: 1, skipped: 0, needs_review: 1, unfinished: 0 },
  candidates: [
    {
      id: 'cand-1', position: 0, requirement: 'Kubernetes', action: 'rewrite',
      status: 'handled', outcome: 'propose_edit', attempts: 0,
    },
    {
      id: 'cand-2', position: 1, requirement: 'AWS', action: 'confirm',
      status: 'needs_review', outcome: 'the model stopped before handling this', attempts: 0,
    },
  ],
  composition: {
    changed: true,
    moved_bullets: 2,
    promoted_projects: ['Calendar map'],
    promoted_bullets: [{
      entry: 'Calendar map', text: 'Designed a FastAPI backend', from: 2, to: 1,
    }],
    promoted_skills: ['Python'],
    reordered_skills: 3,
    scored_bullets: [
      {
        bullet_id: 'b1', entry: 'Calendar map', kind: 'project',
        text: 'Designed a FastAPI backend', score: 3.4, from: 2, to: 1,
        contributions: [
          {
            requirement: 'Python', matched: 'fastapi', state: 'INFERRED',
            state_weight: 0.8, importance: 'required', importance_weight: 3, points: 2.4,
          },
          {
            requirement: 'api', matched: 'api', state: 'EXPLICIT',
            state_weight: 1, importance: 'nice_to_have', importance_weight: 1, points: 1,
          },
        ],
      },
      {
        bullet_id: 'b2', entry: 'Calendar map', kind: 'project',
        text: 'Led a team of four', score: 0, from: 1, to: 2, contributions: [],
      },
    ],
  },
  outcomes: [
    {
      position: 0,
      requirement: 'Kubernetes',
      state: 'EXPLICIT',
      importance: 'required',
      action: 'rewrite',
      reason: 'The requirement is explicit in a bullet.',
    },
    {
      position: 1,
      requirement: 'Terraform',
      state: 'NONE',
      importance: 'required',
      action: 'gap',
      reason: 'No supporting evidence was found in the saved resume.',
    },
    {
      position: 2,
      requirement: 'AWS',
      state: 'PARTIAL',
      importance: 'preferred',
      action: 'confirm',
      reason: 'Related evidence exists, but it does not establish this specific requirement.',
    },
    {
      position: 3,
      requirement: 'CSS',
      state: 'INFERRED',
      importance: 'nice_to_have',
      action: 'surface_skill',
      reason: 'A strict one-hop authorship rule allows this evidence to be stated more plainly.',
      inferred_from: ['tailwind'],
    },
  ],
  needs_review: [],
  detail_requests: [],
  edits: [{
    id: 'edit-1',
    bullet_id: 'bullet-1',
    requirement: 'Kubernetes',
    proposed_text: 'Operated multi-region Kubernetes services',
    status: 'proposed',
    edit_type: 'rewrite',
    original_text: 'Deployed services to Kubernetes across three regions',
    source_bullets: [{ bullet_id: 'bullet-1', text: 'Deployed services to Kubernetes across three regions' }],
    evidence: [{ bullet_id: 'bullet-1', text: 'Deployed services to Kubernetes across three regions' }],
    confirmed_details: [],
    reason: 'The bullet already names Kubernetes; leading with the action makes it scannable.',
  }],
  gaps: [{ id: 'gap-1', requirement: 'Terraform', note: 'no IaC work found', searched: ['terraform'] }],
  trace: [
    { step: 1, tool: 'search_resume', arguments: { query: 'kubernetes' }, status: 'completed', error: null },
    { step: 2, tool: 'propose_edit', arguments: { requirement: 'Kubernetes' }, status: 'completed', error: null },
  ],
}

const running = {
  ...finished,
  status: 'running',
  steps_used: 2,
  edits: [],
  gaps: [],
  trace: [{ step: 1, tool: 'search_resume', arguments: { query: 'kubernetes' }, status: 'completed', error: null }],
}

beforeEach(() => apiFetch.mockReset())

describe('TailoringRunPage', () => {
  it('shows the proposal against the current wording', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText('Operated multi-region Kubernetes services')).toBeInTheDocument()
    expect(screen.getByText('Deployed services to Kubernetes across three regions')).toBeInTheDocument()
    expect(screen.getByText(/Cited 1 bullet from your resume/)).toBeInTheDocument()
  })

  it('shows every source bullet when a proposal combines them', async () => {
    apiFetch.mockResolvedValueOnce({
      ...finished,
      edits: [{
        ...finished.edits[0],
        edit_type: 'merge',
        proposed_text: 'Built and deployed Kubernetes services with Helm',
        source_bullets: [
          { bullet_id: 'bullet-1', text: 'Built Kubernetes services' },
          { bullet_id: 'bullet-2', text: 'Managed Helm releases' },
        ],
      }],
    })

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText('Combines 2 bullets')).toBeInTheDocument()
    expect(screen.getByText(/Built Kubernetes services/)).toBeInTheDocument()
    expect(screen.getByText(/Managed Helm releases/)).toBeInTheDocument()
  })

  it('asks for a missing fact at the decision point and resumes after an answer', async () => {
    const waiting = {
      ...finished,
      status: 'waiting_for_user',
      composition: null,
      edits: [],
      detail_requests: [{
        id: 'question-1', bullet_id: 'bullet-1', requirement: 'Kubernetes',
        question: 'How much time did this save?', answer: null, status: 'pending',
        bullet_text: 'Worked on Kubernetes deployments',
      }],
    }
    apiFetch.mockResolvedValueOnce(waiting)
    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByRole('heading', { name: 'One question before we continue' })).toBeInTheDocument()
    expect(screen.getByText(/Tailoring paused rather than guessing/)).toBeInTheDocument()
    // mid-run the page is a step, not a place to browse: nothing offers a way out
    expect(screen.getByRole('progressbar', { name: 'Tailoring progress' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Back to job/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Tailored resume/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Gaps/ })).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Your answer'), 'Reduced deployment time by 40%.')

    apiFetch.mockResolvedValueOnce({ run_id: 'run-1', status: 'running' })
    apiFetch.mockResolvedValueOnce({ ...waiting, status: 'running', detail_requests: [] })
    await userEvent.click(screen.getByRole('button', { name: 'Use this detail' }))

    await waitFor(() => {
      const patch = apiFetch.mock.calls.find(([path, options]) => (
        path === '/tailoring/questions/question-1' && options?.method === 'PATCH'
      ))
      expect(JSON.parse(patch[1].body)).toEqual({
        action: 'answer', answer: 'Reduced deployment time by 40%.',
      })
    })
  })

  it('separates gaps from suggestions', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByRole('heading', { name: /Suggestions/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Gaps/ })).toBeInTheDocument()
    expect(screen.getAllByText('Terraform').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: /What this run worked on/ })).toBeInTheDocument()
    expect(screen.getAllByText('AWS').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: /Skills worth surfacing/ })).toBeInTheDocument()
    expect(screen.getAllByText('CSS').length).toBeGreaterThan(0)
    expect(screen.getByText(/4 of 4 requirements accounted for/)).toBeInTheDocument()

    await userEvent.click(screen.getByText(/Requirement decisions/))
    expect(screen.getByText('evidence gap')).toBeInTheDocument()
    expect(screen.getByText('surface in Skills')).toBeInTheDocument()
  })

  it('does not claim no gaps when the fit engine has one', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    expect((await screen.findAllByText('Terraform')).length).toBeGreaterThan(0)
    expect(screen.queryByText('No evidence gaps in the fit assessment.')).not.toBeInTheDocument()
  })

  it('reports what the run cost', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/4 steps · 1 search · 1000 tokens/)).toBeInTheDocument()
  })

  it('narrates live progress from the real trace while running', async () => {
    apiFetch.mockResolvedValue(running)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/Searching your resume for “kubernetes”/)).toBeInTheDocument()
    expect(screen.getByText(/step 2 of 8 · 1 tool call/)).toBeInTheDocument()
    // proposals stay hidden until there is something to decide on
    expect(screen.queryByRole('heading', { name: /Suggestions/ })).not.toBeInTheDocument()
  })

  it('accepts a proposal', async () => {
    apiFetch.mockResolvedValueOnce(finished)
    renderWithProviders(<TailoringRunPage />)

    apiFetch.mockResolvedValueOnce({ id: 'edit-1', status: 'accepted' })
    apiFetch.mockResolvedValueOnce({ ...finished, edits: [{ ...finished.edits[0], status: 'accepted' }] })
    await userEvent.click(await screen.findByRole('button', { name: 'Accept' }))

    await waitFor(() => {
      const patch = apiFetch.mock.calls.find(([, options]) => options?.method === 'PATCH')
      expect(patch[0]).toBe('/tailoring/edits/edit-1')
      expect(JSON.parse(patch[1].body)).toEqual({ status: 'accepted' })
    })
    expect(await screen.findByText('✓ Accepted')).toBeInTheDocument()
  })

  it('shows decision errors inside the proposal that triggered them', async () => {
    apiFetch.mockResolvedValueOnce(finished)
    renderWithProviders(<TailoringRunPage />)

    const accept = await screen.findByRole('button', { name: 'Accept' })
    apiFetch.mockRejectedValueOnce(new Error('Could not save that decision.'))
    await userEvent.click(accept)

    const proposal = accept.closest('article')
    expect(await within(proposal).findByText('Could not save that decision.')).toBeInTheDocument()
  })

  it('shows grounding rejections in the trace', async () => {
    apiFetch.mockResolvedValueOnce({
      ...finished,
      trace: [...finished.trace, {
        step: 3, tool: 'propose_edit', arguments: { requirement: 'Go' },
        status: 'failed', error: 'bullet was not returned by a search in this run',
      }],
    })

    renderWithProviders(<TailoringRunPage />)

    await userEvent.click(await screen.findByText(/How it worked/))
    expect(screen.getByText(/rejected: bullet was not returned by a search in this run/)).toBeInTheDocument()
    expect(screen.getByText(/1 rejected by grounding/)).toBeInTheDocument()
  })

  it('explains an abandoned run instead of spinning forever, and offers to resume it', async () => {
    const abandoned = { ...finished, status: 'failed', error_code: 'abandoned', steps_used: 2, edits: [], gaps: [] }
    apiFetch.mockResolvedValueOnce(abandoned)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/This run stopped after 2 steps/)).toBeInTheDocument()
    expect(screen.getByText(/aren't repeated/)).toBeInTheDocument()

    apiFetch.mockResolvedValueOnce({ id: 'run-1', status: 'running', resumed_from: 2 })
    await userEvent.click(screen.getByRole('button', { name: /Resume run/ }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/tailoring/runs/run-1/resume', { method: 'POST' }))
  })

  it('does not offer to resume a run that failed for a reason that would recur', async () => {
    apiFetch.mockResolvedValueOnce({
      ...finished, status: 'failed', error_code: 'quota_exceeded', edits: [], gaps: [],
    })

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/failed before it could finish/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Resume run/ })).not.toBeInTheDocument()
  })

  it('offers both download formats', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    // both formats are visible at once — two options never justified a dropdown
    expect(await screen.findByRole('link', { name: 'Download HTML' }))
      .toHaveAttribute('href', '/api/tailoring/runs/run-1/resume.html?ordering=tailored')
    expect(screen.getByRole('link', { name: 'Download LaTeX' }))
      .toHaveAttribute('href', '/api/tailoring/runs/run-1/resume.tex?ordering=tailored')
  })

  it('explains tailored ordering next to the downloads', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText('Tailored ordering applied')).toBeInTheDocument()
    expect(screen.getByText(/moves Calendar map higher, raises 1 bullet, raises 1 skill/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'What changed, and why' }))
    const dialog = await screen.findByRole('dialog', { name: 'What changed, and why' })
    // the bullet appears twice on purpose: once under what moved, once in the full list
    expect(within(dialog).getAllByText(/Designed a FastAPI backend/)).toHaveLength(2)
    expect(within(dialog).getByText('2 → 1')).toBeInTheDocument()
    expect(within(dialog).getAllByText('Python').length).toBeGreaterThan(0)
  })

  it('lets the user export the original order without changing accepted wording', async () => {
    apiFetch.mockResolvedValueOnce(finished)
    renderWithProviders(<TailoringRunPage />)

    await userEvent.click(await screen.findByRole('radio', { name: 'Original order' }))

    expect(screen.getByText('Original ordering selected')).toBeInTheDocument()
    expect(screen.getByText(/Accepted wording edits still apply/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download HTML' }))
      .toHaveAttribute('href', '/api/tailoring/runs/run-1/resume.html?ordering=original')
    expect(screen.getByRole('link', { name: 'Download LaTeX' }))
      .toHaveAttribute('href', '/api/tailoring/runs/run-1/resume.tex?ordering=original')
  })

  it('does not show a meaningless order choice when nothing would move', async () => {
    apiFetch.mockResolvedValueOnce({
      ...finished,
      composition: {
        changed: false,
        moved_bullets: 0,
        promoted_projects: [],
        promoted_bullets: [],
        promoted_skills: [],
        reordered_skills: 0,
      },
    })

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText('Order checked')).toBeInTheDocument()
    expect(screen.getByText(/saved order already puts the strongest evidence first/)).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: 'Original order' })).not.toBeInTheDocument()
  })

  it('counts accepted rewrites in the download panel', async () => {
    apiFetch.mockResolvedValueOnce({ ...finished, edits: [{ ...finished.edits[0], status: 'accepted' }] })

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/1 accepted rewrite included/)).toBeInTheDocument()
  })

  it('reports what the run actually handled rather than asking for a decision it discards', async () => {
    apiFetch.mockResolvedValueOnce(finished)
    renderWithProviders(<TailoringRunPage />)

    const panel = await screen.findByRole('region', { name: /What this run worked on/ })
    expect(within(panel).getByText('Handled')).toBeInTheDocument()
    expect(within(panel).getByText('Needs you')).toBeInTheDocument()
    expect(within(panel).getByText(/1 of 2 handled/)).toBeInTheDocument()

    // the old Keep/Ignore pair saved nothing and is gone
    expect(screen.queryByRole('button', { name: 'Keep' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ignore' })).not.toBeInTheDocument()
  })

  it('groups evidence gaps into collapsible importance categories', async () => {
    apiFetch.mockResolvedValueOnce({
      ...finished,
      coverage: { ...finished.coverage, total: 5, accounted: 5, gaps: 2 },
      outcomes: [
        ...finished.outcomes,
        {
          position: 4, requirement: 'GraphQL', state: 'NONE', action: 'gap',
          importance: 'preferred', reason: 'No supporting evidence was found.',
        },
      ],
      gaps: [
        ...finished.gaps,
        { id: 'gap-2', requirement: 'GraphQL', note: 'no GraphQL work found', searched: [] },
      ],
    })
    renderWithProviders(<TailoringRunPage />)

    const required = await screen.findByRole('button', { name: 'Required gaps (1)' })
    const preferred = screen.getByRole('button', { name: 'Preferred gaps (1)' })
    expect(required).toHaveAttribute('aria-expanded', 'true')
    expect(preferred).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(preferred)
    expect(preferred).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByText('GraphQL').length).toBeGreaterThan(0)
  })

  it('surfaces a run that could not be loaded', async () => {
    apiFetch.mockRejectedValueOnce(new Error('run not found'))

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText('Run unavailable')).toBeInTheDocument()
  })

  it('explains the ranking arithmetic in a dialog', async () => {
    apiFetch.mockResolvedValue(finished)
    renderWithProviders(<TailoringRunPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'What changed, and why' }))
    const dialog = await screen.findByRole('dialog', { name: 'What changed, and why' })

    // both halves of the multiplication, not just the total — that is the explanation
    expect(within(dialog).getAllByText('0.8').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('×3').length).toBeGreaterThan(0)
    expect(within(dialog).getByText('+2.4')).toBeInTheDocument()
    expect(within(dialog).getAllByText('Implied by').length).toBeGreaterThan(0)
    expect(within(dialog).getByText(/via “fastapi”/)).toBeInTheDocument()

    // a bullet naming no technology scores zero and sinks — the dialog exists to make that
    // visible rather than looking arbitrary
    expect(within(dialog).getByText(/Matches no requirement here/)).toBeInTheDocument()
    expect(within(dialog).getByText(/1 matched no requirement/)).toBeInTheDocument()
  })

  it('closes the ordering dialog with Escape', async () => {
    apiFetch.mockResolvedValue(finished)
    renderWithProviders(<TailoringRunPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'What changed, and why' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('turns "I used this here" into a proposed edit in two steps', async () => {
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/evidence' && !options) {
        return Promise.resolve({
          entries: [
            { id: 'entry-1', kind: 'project', title: 'Calendar Map', bullets: [] },
            { id: 'entry-2', kind: 'project', title: 'Study Buddy', bullets: [] },
          ],
        })
      }
      if (path?.endsWith('/surface')) return Promise.resolve({ edit_id: 'edit-2' })
      return Promise.resolve(finished)
    })

    renderWithProviders(<TailoringRunPage />)

    // a gap is us reading a resume, not a verdict on the person — it has to be correctable
    await userEvent.click((await screen.findAllByRole('button', { name: 'I used this — where?' }))[0])

    // step one: which project
    await userEvent.click(screen.getByRole('radio', { name: 'Calendar Map' }))
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))

    // step two: what they actually did with it, which is the evidence for the claim
    await userEvent.type(
      screen.getByLabelText(/What you actually did with it/),
      'Tracked releases and rolled back a bad deploy',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Add to my resume' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/tailoring/runs/run-1/surface', {
      method: 'POST',
      body: JSON.stringify({
        skill: 'Terraform',
        entry_id: 'entry-1',
        detail: 'Tracked releases and rolled back a bad deploy',
      }),
    }))
    expect(await screen.findByText(/waiting for your approval/)).toBeInTheDocument()
  })

  it('cannot write a claim without a project or without the fact behind it', async () => {
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/evidence' && !options) {
        return Promise.resolve({ entries: [{ id: 'entry-1', kind: 'project', title: 'Calendar Map', bullets: [] }] })
      }
      return Promise.resolve(finished)
    })

    renderWithProviders(<TailoringRunPage />)
    await userEvent.click((await screen.findAllByRole('button', { name: 'I used this — where?' }))[0])

    // "I have this skill" with no project behind it is the keyword list again
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()

    await userEvent.click(screen.getByRole('radio', { name: 'Calendar Map' }))
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))

    // and a bare confirmation carries no fact the rewrite could use
    expect(screen.getByRole('button', { name: 'Add to my resume' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/What you actually did with it/), 'yes')
    expect(screen.getByRole('button', { name: 'Add to my resume' })).toBeDisabled()
  })

  it('points the download at the API host, not the page it is served from', async () => {
    // a bare "/api/..." href resolves against the frontend's own domain, 404s there, and never
    // reaches the backend — so it does not even appear in the API logs
    apiFetch.mockResolvedValue(finished)
    renderWithProviders(<TailoringRunPage />)

    const link = await screen.findByRole('link', { name: 'Download HTML' })
    expect(link).toHaveAttribute('href', apiUrl('/tailoring/runs/run-1/resume.html?ordering=tailored'))
  })

  it('keeps the run on screen when a background poll fails', async () => {
    // Tab away during a question, come back, and the token refresh plus reconnect can fail one
    // poll. That used to replace the whole run with "Run unavailable" until the next poll
    // succeeded — the work looked lost at the exact moment the user was answering.
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/evidence' && !options) return Promise.resolve({ entries: [] })
      return Promise.resolve(finished)
    })

    const { queryClient } = renderWithProviders(<TailoringRunPage />)
    expect(await screen.findByText('Proposed changes')).toBeInTheDocument()

    apiFetch.mockRejectedValueOnce(new Error('Failed to fetch'))
    await queryClient.refetchQueries({ queryKey: ['tailoring-run', 'run-1'] })

    await waitFor(() => expect(screen.getByText(/Lost contact with the server/)).toBeInTheDocument())
    expect(screen.getByText('Proposed changes')).toBeInTheDocument()
    expect(screen.queryByText('Run unavailable')).not.toBeInTheDocument()
  })

  it('says why each rewrite is an improvement, under the evidence it cites', async () => {
    apiFetch.mockResolvedValue(finished)
    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/leading with the action makes it scannable/)).toBeInTheDocument()
  })
})
