import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../helpers/render'
import TailoringRunPage from '../../src/pages/TailoringRunPage'
import { apiFetch } from '../../src/lib/api'

vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn() }))
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
  edits: [{
    id: 'edit-1',
    bullet_id: 'bullet-1',
    requirement: 'Kubernetes',
    proposed_text: 'Operated multi-region Kubernetes services',
    status: 'proposed',
    original_text: 'Deployed services to Kubernetes across three regions',
    evidence: [{ bullet_id: 'bullet-1', text: 'Deployed services to Kubernetes across three regions' }],
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

  it('separates gaps from suggestions', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByRole('heading', { name: /Suggestions/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Gaps/ })).toBeInTheDocument()
    expect(screen.getByText('Terraform')).toBeInTheDocument()
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

  it('explains an abandoned run instead of spinning forever', async () => {
    apiFetch.mockResolvedValueOnce({ ...finished, status: 'failed', error_code: 'abandoned', edits: [], gaps: [] })

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/This run stopped before it finished/)).toBeInTheDocument()
  })

  it('offers both download formats', async () => {
    apiFetch.mockResolvedValueOnce(finished)

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByRole('link', { name: 'Download HTML' }))
      .toHaveAttribute('href', '/api/tailoring/runs/run-1/resume.html')
    expect(screen.getByRole('link', { name: 'Download LaTeX' }))
      .toHaveAttribute('href', '/api/tailoring/runs/run-1/resume.tex')
  })

  it('counts accepted rewrites in the download panel', async () => {
    apiFetch.mockResolvedValueOnce({ ...finished, edits: [{ ...finished.edits[0], status: 'accepted' }] })

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText(/1 accepted rewrite applied/)).toBeInTheDocument()
  })

  it('surfaces a run that could not be loaded', async () => {
    apiFetch.mockRejectedValueOnce(new Error('run not found'))

    renderWithProviders(<TailoringRunPage />)

    expect(await screen.findByText('Run unavailable')).toBeInTheDocument()
  })
})
