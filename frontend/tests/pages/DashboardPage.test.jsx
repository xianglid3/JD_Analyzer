import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../../src/lib/api'
import { renderWithProviders } from '../helpers/render'
import DashboardPage from '../../src/pages/DashboardPage'

vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn() }))

const jobsPage = {
  jobs: [{
    id: 7,
    title: 'Frontend Engineer',
    company_name: 'Acme',
    location: 'Remote',
    work_type: 'remote',
    match_score: 80,
    status: 'saved',
    created_at: '2026-08-24T12:00:00Z',
  }],
  page: 1,
  per_page: 20,
  total: 1,
  total_pages: 1,
}

const stats = {
  total: 1,
  by_status: { saved: 1, applied: 0, interview: 0, offer: 0, rejected: 0, ghosted: 0, accepted: 0, decline: 0 },
}

describe('DashboardPage', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/jobs/stats') return Promise.resolve(stats)
      if (path.startsWith('/jobs?')) return Promise.resolve(jobsPage)
      if (path === '/jobs/drafts' && options?.method === 'POST') return Promise.resolve({ id: 'draft-8' })
      return Promise.resolve({})
    })
  })

  it('sends selected filters to the paginated jobs endpoint', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    expect(await screen.findByText('Frontend Engineer')).toBeInTheDocument()
    // the pipeline filter is a checkbox list now, not a row of buttons
    await user.click(screen.getByRole('checkbox', { name: /Applied/ }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(expect.stringContaining('status=applied')))
  })

  it('combines checked pipeline statuses as one filter', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    await screen.findByText('Frontend Engineer')
    await user.click(screen.getByRole('checkbox', { name: /Applied/ }))
    await user.click(screen.getByRole('checkbox', { name: /Interview/ }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(expect.stringMatching(/status=applied.*status=interview/)))
  })

  it('uses an idempotency key when analyzing a job description', async () => {
    const user = userEvent.setup()
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('123e4567-e89b-12d3-a456-426614174000')
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    await screen.findByText('Frontend Engineer')
    await user.click(screen.getByRole('button', { name: /Analyze new JD/ }))
    await user.type(screen.getByLabelText(/Job posting URL/), 'https://example.com/jobs/42')
    await user.type(screen.getByLabelText('Job description'), 'A'.repeat(60))
    await user.click(screen.getByRole('button', { name: 'Analyze' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/drafts', {
      method: 'POST',
      headers: { 'Idempotency-Key': '123e4567-e89b-12d3-a456-426614174000' },
      body: JSON.stringify({ description: 'A'.repeat(60), source_url: 'https://example.com/jobs/42' }),
    }))
    expect(screen.queryByText('Analysis saved to your dashboard.')).not.toBeInTheDocument()
  })
})
