import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../lib/api'
import { renderWithProviders } from '../test/render'
import JobDetailPage from './JobDetailPage'

vi.mock('../lib/api', () => ({ apiFetch: vi.fn() }))

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
