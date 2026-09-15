import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../../src/lib/api'
import { renderWithProviders } from '../helpers/render'
import JobDetailPage from '../../src/pages/JobDetailPage'

vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn() }))

const job = {
  id: 42,
  title: 'Platform Engineer',
  company_name: 'Acme',
  location: 'Remote',
  work_type: 'remote',
  match_score: 75,
  summary: 'Build reliable systems.',
  no_bs_translation: 'Own production infrastructure.',
  skills: ['Python', 'PostgreSQL'],
  match_detail: { matched: ['Python'], missing: ['Kubernetes'] },
  raw_description: 'Original posting',
  notes: '',
  status: 'saved',
  deadline: null,
  source_url: 'https://example.com/jobs/42',
}

describe('JobDetailPage', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/jobs/42' && !options) return Promise.resolve(job)
      return Promise.resolve({})
    })
  })

  function renderPage() {
    return renderWithProviders(
      <Routes>
        <Route path="/jobs/:id" element={<JobDetailPage />} />
        <Route path="/dashboard" element={<p>Dashboard destination</p>} />
      </Routes>,
      { route: '/jobs/42' },
    )
  }

  it('saves edited tracking details', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('Platform Engineer')).toBeInTheDocument()
    expect(screen.getByText('No-BS translation').closest('section')).toHaveClass('inverted-card')
    expect(screen.getByRole('link', { name: 'Open posting ↗' })).toHaveAttribute('href', job.source_url)
    await user.click(screen.getByRole('combobox', { name: 'Status' }))
    await user.click(screen.getByRole('option', { name: 'Interview' }))
    await user.type(screen.getByLabelText(/^Notes/), 'Recruiter call Friday')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/42', {
      method: 'PATCH',
      body: JSON.stringify({
        notes: 'Recruiter call Friday',
        status: 'interview',
        deadline: null,
        source_url: job.source_url,
      }),
    }))
    const tracking = screen.getByRole('heading', { name: 'Tracking' }).closest('section')
    expect(await within(tracking).findByText('Tracking details saved.')).toBeInTheDocument()
  })

  it('requires confirmation before deleting a job', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Platform Engineer')
    await user.click(screen.getByRole('button', { name: 'Delete job' }))
    expect(screen.getByRole('dialog', { name: 'Delete this job?' })).toBeInTheDocument()
    expect(apiFetch).not.toHaveBeenCalledWith('/jobs/42', { method: 'DELETE' })

    await user.click(screen.getByRole('button', { name: 'Delete permanently' }))
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/42', { method: 'DELETE' }))
    expect(await screen.findByText('Dashboard destination')).toBeInTheDocument()
  })

  it('sends unconfirmed resume evidence to review instead of silently saving it', async () => {
    const user = userEvent.setup()
    const error = new Error('review and confirm your resume experience before tailoring')
    error.reason = 'needs_confirmation'
    apiFetch.mockImplementation((path, options) => {
      if (path === '/jobs/42' && !options) return Promise.resolve(job)
      if (path === '/jobs/42/tailor' && options?.method === 'POST') return Promise.reject(error)
      return Promise.resolve({})
    })

    renderPage()
    await screen.findByText('Platform Engineer')
    await user.click(screen.getAllByRole('button', { name: 'Tailor resume' })[0])

    const reviewLinks = await screen.findAllByRole('link', { name: 'Review resume' })
    expect(reviewLinks.every((link) => link.getAttribute('href') === '/resume')).toBe(true)
    expect(apiFetch).not.toHaveBeenCalledWith('/resume/structure', expect.anything())
    expect(apiFetch).not.toHaveBeenCalledWith('/resume/evidence', expect.anything())
  })
})

describe('JobDetailPage match breakdown', () => {
  it('shows per-requirement states with the evidence behind them', async () => {
    apiFetch.mockResolvedValue({
      id: 'job-1',
      title: 'Frontend Engineer',
      skills: ['CSS'],
      match_score: 72,
      match_detail: {
        matched: ['CSS', 'JavaScript'],
        missing: ['Kubernetes'],
        fit_score: 86,
        visibility_score: 40,
        hidden: ['JavaScript'],
        eligibility: ['US citizenship required'],
        requirements: [
          {
            requirement: 'CSS', state: 'INFERRED', importance: 'required', inferred_from: ['tailwind'],
            evidence: [{ bullet_id: 'b1', text: 'Built responsive interfaces using Tailwind CSS' }],
          },
          { requirement: 'Kubernetes', state: 'NONE', importance: 'preferred', inferred_from: [], evidence: [] },
        ],
      },
    })

    renderWithProviders(<JobDetailPage />)

    expect(await screen.findByText('inferred')).toBeInTheDocument()
    expect(screen.getByText('via tailwind')).toBeInTheDocument()
    expect(screen.getByText(/Built responsive interfaces using Tailwind CSS/)).toBeInTheDocument()
    expect(screen.getByText(/fit 86%/)).toBeInTheDocument()
    expect(screen.getByText(/visible 40%/)).toBeInTheDocument()
    // fit and visibility are never blended into one number
    expect(screen.queryByText(/keywords/i)).not.toBeInTheDocument()
    expect(screen.getByText(/never says so outright/)).toBeInTheDocument()
    // eligibility is shown but explicitly excluded from both numbers
    expect(screen.getByText('US citizenship required')).toBeInTheDocument()
    expect(screen.getByText(/gates you either meet or you don't/)).toBeInTheDocument()
  })

  it('still renders jobs scored before the fit/visibility rename', async () => {
    // match_detail is stored JSONB, so rows written before the rename keep the old keys
    // until the resume is next saved. Reading both is what stops the score going blank.
    apiFetch.mockResolvedValue({
      id: 'job-1',
      title: 'Frontend Engineer',
      skills: ['CSS'],
      match_score: 72,
      match_detail: {
        matched: ['CSS'],
        missing: [],
        capability_score: 86,
        communication_score: 40,
        requirements: [
          { requirement: 'CSS', state: 'EXPLICIT', importance: 'required', inferred_from: [], evidence: [] },
        ],
      },
    })

    renderWithProviders(<JobDetailPage />)

    expect(await screen.findByText(/fit 86%/)).toBeInTheDocument()
    expect(screen.getByText(/visible 40%/)).toBeInTheDocument()
  })

  it('falls back to chips for jobs scored before the states existed', async () => {
    apiFetch.mockResolvedValue({
      id: 'job-1',
      title: 'Frontend Engineer',
      skills: [],
      match_score: 50,
      match_detail: { matched: ['React'], missing: ['Kubernetes'] },
    })

    renderWithProviders(<JobDetailPage />)

    expect(await screen.findByText('Matched')).toBeInTheDocument()
    expect(screen.getByText(/○ Kubernetes/)).toBeInTheDocument()
  })
})

describe('JobDetailPage tailoring history', () => {
  it('links to earlier runs for this job', async () => {
    apiFetch.mockImplementation((path) => {
      if (path.endsWith('/tailoring')) {
        return Promise.resolve({
          runs: [{
            id: 'run-9', status: 'completed', steps_used: 4,
            started_at: '2026-08-31T12:00:00+00:00', completed_at: null,
            edit_count: 2, gap_count: 1,
          }],
        })
      }
      return Promise.resolve({ id: 42, title: 'Platform Engineer', skills: [], match_detail: null })
    })

    renderWithProviders(<JobDetailPage />, { route: '/jobs/42' })

    const link = await screen.findByRole('link', { name: /2 suggestions, 1 gap/ })
    expect(link).toHaveAttribute('href', '/tailoring/run-9')
  })

  it('says nothing when the job has never been tailored', async () => {
    apiFetch.mockImplementation((path) => {
      if (path.endsWith('/tailoring')) return Promise.resolve({ runs: [] })
      return Promise.resolve({ id: 42, title: 'Platform Engineer', skills: [], match_detail: null })
    })

    renderWithProviders(<JobDetailPage />, { route: '/jobs/42' })

    await screen.findByText('Platform Engineer')
    expect(screen.queryByText('Earlier runs')).not.toBeInTheDocument()
  })
})
