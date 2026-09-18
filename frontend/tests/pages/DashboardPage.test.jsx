import { screen, waitFor, within } from '@testing-library/react'
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
    source_url: 'https://example.com/careers/frontend-engineer',
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
    // the pipeline filter is a menu now: checkboxes, but behind a trigger
    await user.click(screen.getByRole('button', { name: 'Filter by pipeline status' }))
    await user.click(screen.getByRole('checkbox', { name: /Applied/ }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(expect.stringContaining('status=applied')))
  })

  it('combines checked pipeline statuses as one filter', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    await screen.findByText('Frontend Engineer')
    await user.click(screen.getByRole('button', { name: 'Filter by pipeline status' }))
    await user.click(screen.getByRole('checkbox', { name: /Applied/ }))
    // the menu stays open: picking two statuses is the point of checkboxes
    await user.click(screen.getByRole('checkbox', { name: /Interview/ }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(expect.stringMatching(/status=applied.*status=interview/)))
  })

  it('uses an idempotency key when analyzing a job description', async () => {
    const user = userEvent.setup()
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('123e4567-e89b-12d3-a456-426614174000')
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    await screen.findByText('Frontend Engineer')
    // two buttons read "Analyze": the one in the header and the one inside the dialog it opens
    await user.click(screen.getAllByRole('button', { name: /^Analyze$/ })[0])
    await user.type(screen.getByLabelText(/Job posting URL/), 'https://example.com/jobs/42')
    await user.type(screen.getByLabelText('Job description'), 'A'.repeat(60))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Analyze' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/drafts', {
      method: 'POST',
      headers: { 'Idempotency-Key': '123e4567-e89b-12d3-a456-426614174000' },
      body: JSON.stringify({ description: 'A'.repeat(60), source_url: 'https://example.com/jobs/42' }),
    }))
    expect(screen.queryByText('Analysis saved to your dashboard.')).not.toBeInTheDocument()
  })

  it('shows status-save feedback beside the status that changed', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    const title = await screen.findByText('Frontend Engineer')
    const row = title.closest('li')
    await user.click(within(row).getByRole('combobox', { name: /Status for/ }))
    await user.click(screen.getByRole('option', { name: 'Applied' }))

    expect(await within(row).findByText('Status updated.')).toBeInTheDocument()
  })

  it('opens the posting link, and keeps edit and copy out of the way until wanted', async () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    const title = await screen.findByText('Frontend Engineer')
    const row = title.closest('li')

    // the link itself is a link: clicking it goes to the posting, not into an editor
    const link = within(row).getByRole('link', { name: /example\.com\/careers/ })
    expect(link).toHaveAttribute('href', 'https://example.com/careers/frontend-engineer')
    expect(link).toHaveAttribute('target', '_blank')

    // the actions exist for keyboard and hover, rather than being permanently on show
    expect(within(row).getByRole('button', { name: /Edit link for Frontend Engineer/ })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: /Copy link for/ })).toBeInTheDocument()
  })

  it('edits the posting link in place', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    const row = (await screen.findByText('Frontend Engineer')).closest('li')
    await user.click(within(row).getByRole('button', { name: /Edit link for Frontend Engineer/ }))

    const field = within(row).getByRole('textbox', { name: /link for Frontend Engineer/ })
    await user.clear(field)
    await user.type(field, 'https://example.com/careers/moved')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/7', {
      method: 'PATCH',
      body: JSON.stringify({ source_url: 'https://example.com/careers/moved' }),
    }))
  })

  it('asks before deleting a job, and says what goes with it', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    const row = (await screen.findByText('Frontend Engineer')).closest('li')
    await user.click(within(row).getByRole('button', { name: /Delete Frontend Engineer/ }))

    // a job takes its tailoring runs with it, so the confirmation has to say so
    expect(await screen.findByText(/every tailoring run for it go too/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/jobs/7', { method: 'DELETE' }))
  })

  it('abandons a link edit when the click lands elsewhere', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    const row = (await screen.findByText('Frontend Engineer')).closest('li')
    await user.click(within(row).getByRole('button', { name: /Edit link for Frontend Engineer/ }))
    await user.type(within(row).getByRole('textbox', { name: /link for Frontend Engineer/ }), '/typo')

    await user.click(document.body)

    // a half-typed URL should not follow you around the page
    expect(within(row).queryByRole('textbox', { name: /link for Frontend Engineer/ })).not.toBeInTheDocument()
    expect(apiFetch).not.toHaveBeenCalledWith('/jobs/7', expect.objectContaining({ method: 'PATCH' }))
  })

  it('shows when each job was added, in its own column', async () => {
    renderWithProviders(<DashboardPage />, { route: '/dashboard' })

    const row = (await screen.findByText('Frontend Engineer')).closest('li')
    expect(within(row).getByText(new Date('2026-08-24T12:00:00Z').toLocaleDateString())).toBeInTheDocument()
  })
})
