import { screen, waitFor } from '@testing-library/react'
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
    await user.selectOptions(screen.getByLabelText('Status'), 'interview')
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
        capability_score: 86,
        keyword_score: 40,
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
    expect(screen.getByText(/capability 86% · keywords 40%/)).toBeInTheDocument()
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
