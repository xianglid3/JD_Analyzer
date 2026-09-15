import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../../src/lib/api'
import { renderWithProviders } from '../helpers/render'
import JobReviewPage from '../../src/pages/JobReviewPage'

vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn() }))

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

  function renderPage(overrides = {}) {
    if (Object.keys(overrides).length) {
      apiFetch.mockImplementation((path, options) => {
        if (path === '/jobs/drafts/draft-8' && !options) return Promise.resolve({ ...draft, ...overrides })
        if (path.endsWith('/confirm')) return Promise.resolve({ id: 'job-42' })
        return Promise.resolve({})
      })
    }
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
    await user.click(screen.getByRole('combobox', { name: 'Initial status' }))
    await user.click(screen.getByRole('option', { name: 'Applied' }))
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
        requirements: [],
      }),
    }))
    expect(await screen.findByText('Saved job destination')).toBeInTheDocument()
  })

  it('lets the user correct a requirement and its importance', async () => {
    const user = userEvent.setup()
    renderPage({ requirements: [{ skill: 'Kubernets', importance: 'required' }] })

    const field = await screen.findByLabelText('Requirement 1')
    await user.clear(field)
    await user.type(field, 'Kubernetes')
    await user.click(screen.getByRole('combobox', { name: /Importance of/ }))
    await user.click(screen.getByRole('option', { name: 'Preferred' }))
    await user.click(screen.getByRole('button', { name: 'Confirm and save' }))

    await waitFor(() => {
      const confirm = apiFetch.mock.calls.find(([path]) => path.endsWith('/confirm'))
      expect(JSON.parse(confirm[1].body).requirements).toEqual([
        { skill: 'Kubernetes', importance: 'preferred' },
      ])
    })
  })

  it('drops a requirement the analysis invented', async () => {
    const user = userEvent.setup()
    renderPage({
      requirements: [
        { skill: 'Kubernetes', importance: 'required' },
        { skill: 'Jira', importance: 'required' },
      ],
    })

    await user.click(await screen.findByRole('button', { name: 'Remove requirement 2' }))
    await user.click(screen.getByRole('button', { name: 'Confirm and save' }))

    await waitFor(() => {
      const confirm = apiFetch.mock.calls.find(([path]) => path.endsWith('/confirm'))
      expect(JSON.parse(confirm[1].body).requirements).toEqual([
        { skill: 'Kubernetes', importance: 'required' },
      ])
    })
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

  it('shows confirmation errors beside the confirmation button', async () => {
    const user = userEvent.setup()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/jobs/drafts/draft-8' && !options) return Promise.resolve(draft)
      if (path.endsWith('/confirm')) return Promise.reject(new Error('Could not save this job.'))
      return Promise.resolve({})
    })
    renderPage()

    const saveButton = await screen.findByRole('button', { name: 'Confirm and save' })
    await user.click(saveButton)

    const footer = saveButton.closest('footer')
    expect(await within(footer).findByText('Could not save this job.')).toBeInTheDocument()
  })

  it('edits a backfilled single-alternative requirement like a plain skill', async () => {
    apiFetch.mockResolvedValueOnce({
      ...draft,
      requirements: [{
        id: 'R1', type: 'skill', importance: 'preferred', source_text: null,
        condition: { operator: 'any_of', minimum: 1, items: ['ai'] },
      }],
    })

    renderWithProviders(<JobReviewPage />)

    const input = await screen.findByLabelText('Requirement 1')
    expect(input).toHaveValue('ai')                     // not blank, and confirm does not throw

    apiFetch.mockResolvedValueOnce({ id: 'job-9' })
    await userEvent.clear(input)
    await userEvent.type(input, 'machine learning')
    await userEvent.click(screen.getByRole('button', { name: /Confirm and save/i }))

    await waitFor(() => {
      const body = JSON.parse(apiFetch.mock.calls.at(-1)[1].body)
      expect(body.requirements).toEqual([
        { skill: 'machine learning', importance: 'preferred' },
      ])
    })
  })

  it('sends a multi-alternative condition back untouched when it is not edited', async () => {
    const condition = { operator: 'any_of', minimum: 1, items: ['Java', 'Python', 'C++'] }
    apiFetch.mockResolvedValueOnce({
      ...draft,
      requirements: [{
        type: 'skill', importance: 'required',
        source_text: 'experience in one of Java, Python or C++',
        condition,
      }],
    })

    renderWithProviders(<JobReviewPage />)

    // each alternative is its own removable chip, and the posting's phrasing is quoted below
    expect(await screen.findByText('Java')).toBeInTheDocument()
    expect(screen.getByText('C++')).toBeInTheDocument()
    expect(screen.getByLabelText('How many of requirement 1 are needed')).toHaveValue(1)

    apiFetch.mockResolvedValueOnce({ id: 'job-9' })
    await userEvent.click(screen.getByRole('button', { name: /Confirm and save/i }))

    await waitFor(() => {
      const body = JSON.parse(apiFetch.mock.calls.at(-1)[1].body)
      // the alternatives survive the round trip — collapsing them would lose two of three
      expect(body.requirements).toEqual([{
        importance: 'required',
        source_text: 'experience in one of Java, Python or C++',
        condition,
      }])
    })
  })

  it('combines ticked requirements into one choice', async () => {
    apiFetch.mockResolvedValueOnce({
      ...draft,
      requirements: [
        { skill: 'java', importance: 'required' },
        { skill: 'python', importance: 'preferred' },
        { skill: 'git', importance: 'required' },
      ],
    })

    renderWithProviders(<JobReviewPage />)

    await userEvent.click(await screen.findByLabelText('Select requirement 1 to combine'))
    await userEvent.click(screen.getByLabelText('Select requirement 2 to combine'))
    await userEvent.click(screen.getByRole('button', { name: /Combine 2 into one choice/i }))

    apiFetch.mockResolvedValueOnce({ id: 'job-9' })
    await userEvent.click(screen.getByRole('button', { name: /Confirm and save/i }))

    await waitFor(() => {
      const body = JSON.parse(apiFetch.mock.calls.at(-1)[1].body)
      expect(body.requirements).toEqual([
        {
          // required beats preferred: the posting still demands one of the two
          importance: 'required',
          source_text: null,
          condition: { operator: 'any_of', minimum: 1, items: ['java', 'python'] },
        },
        { skill: 'git', importance: 'required' },
      ])
    })
  })

  it('splits a choice back into separate requirements', async () => {
    apiFetch.mockResolvedValueOnce({
      ...draft,
      requirements: [{
        type: 'skill', importance: 'required',
        condition: { operator: 'any_of', minimum: 1, items: ['Java', 'Python'] },
      }],
    })

    renderWithProviders(<JobReviewPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Split apart/i }))

    apiFetch.mockResolvedValueOnce({ id: 'job-9' })
    await userEvent.click(screen.getByRole('button', { name: /Confirm and save/i }))

    await waitFor(() => {
      const body = JSON.parse(apiFetch.mock.calls.at(-1)[1].body)
      expect(body.requirements).toEqual([
        { skill: 'Java', importance: 'required' },
        { skill: 'Python', importance: 'required' },
      ])
    })
  })

  it('degrades a choice to a plain requirement when only one alternative is left', async () => {
    apiFetch.mockResolvedValueOnce({
      ...draft,
      requirements: [{
        type: 'skill', importance: 'required',
        source_text: 'Java or Python',
        condition: { operator: 'any_of', minimum: 1, items: ['Java', 'Python'] },
      }],
    })

    renderWithProviders(<JobReviewPage />)
    await userEvent.click(
      await screen.findByLabelText('Remove Java from requirement 1'),
    )

    apiFetch.mockResolvedValueOnce({ id: 'job-9' })
    await userEvent.click(screen.getByRole('button', { name: /Confirm and save/i }))

    await waitFor(() => {
      const body = JSON.parse(apiFetch.mock.calls.at(-1)[1].body)
      // "one of Python" is not a choice, so it must not be sent as one
      expect(body.requirements).toEqual([{ skill: 'Python', importance: 'required' }])
    })
  })

  it('keeps eligibility gates out of the skills list', async () => {
    apiFetch.mockResolvedValueOnce({
      ...draft,
      requirements: [
        { skill: 'python', importance: 'required' },
        {
          skill: "Individuals who are completing or have recently completed a Bachelor's degree",
          importance: 'required', type: 'eligibility',
        },
      ],
    })

    renderWithProviders(<JobReviewPage />)

    // the skill is editable; the gate is listed separately and is never scored
    expect(await screen.findByLabelText('Requirement 1')).toHaveValue('python')
    expect(screen.queryByLabelText('Requirement 2')).not.toBeInTheDocument()
    expect(screen.getByText(/Eligibility/)).toBeInTheDocument()
    expect(screen.getByText(/completing or have recently completed/)).toBeInTheDocument()

    apiFetch.mockResolvedValueOnce({ id: 'job-9' })
    await userEvent.click(screen.getByRole('button', { name: /Confirm and save/i }))

    await waitFor(() => {
      const body = JSON.parse(apiFetch.mock.calls.at(-1)[1].body)
      // carried through, still typed as a gate
      expect(body.requirements).toHaveLength(2)
      expect(body.requirements[1].type).toBe('eligibility')
    })
  })
})
