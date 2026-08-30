import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../lib/api'
import { renderWithProviders } from '../test/render'
import JobReviewPage from './JobReviewPage'

vi.mock('../lib/api', () => ({ apiFetch: vi.fn() }))

const draft = {
  id: 'draft-8',
  title: 'Backend Engineer',
  company_name: 'Acme',
  location: 'New York',
  work_type: 'hybrid',
  source_url: 'https://example.com/jobs/42',
  summary: 'Build reliable backend services.',
  no_bs_translation: 'Own APIs and production systems.',
  skills: ['Python', 'PostgreSQL'],
  expires_at: '2026-08-28T19:00:00Z',
  confirmed_job_id: null,
}

describe('JobReviewPage', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/jobs/drafts/draft-8' && !options) return Promise.resolve(draft)
      if (path === '/jobs/drafts/draft-8/confirm' && options?.method === 'POST') return Promise.resolve({ id: 'job-42' })
      if (path === '/jobs/drafts/draft-8' && options?.method === 'DELETE') return Promise.resolve({ deleted: 'draft-8' })
      return Promise.resolve({})
    })
  })

  function renderPage() {
    return renderWithProviders(
      <Routes>
        <Route path="/jobs/review/:id" element={<JobReviewPage />} />
        <Route path="/jobs/:id" element={<p>Saved job destination</p>} />
        <Route path="/dashboard" element={<p>Dashboard destination</p>} />
      </Routes>,
      { route: '/jobs/review/draft-8' },
    )
  }

  it('lets the user review fields before confirming the permanent job', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Review job details' })).toBeInTheDocument()
    const title = screen.getByLabelText('Job title')
    await user.clear(title)
    await user.type(title, 'Senior Backend Engineer')
    await user.selectOptions(screen.getByLabelText('Initial status'), 'applied')
    await user.click(screen.getByRole('button', { name: 'Confirm and save' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/drafts/draft-8/confirm', {
      method: 'POST',
      body: JSON.stringify({
        title: 'Senior Backend Engineer',
        company_name: 'Acme',
        location: 'New York',
        work_type: 'hybrid',
        source_url: 'https://example.com/jobs/42',
        status: 'applied',
        deadline: null,
      }),
    }))
    expect(await screen.findByText('Saved job destination')).toBeInTheDocument()
  })

  it('can discard a draft without saving a job', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('heading', { name: 'Review job details' })
    await user.click(screen.getByRole('button', { name: 'Discard draft' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/drafts/draft-8', { method: 'DELETE' }))
    expect(await screen.findByText('Dashboard destination')).toBeInTheDocument()
    expect(apiFetch).not.toHaveBeenCalledWith('/jobs/drafts/draft-8/confirm', expect.anything())
  })
})
