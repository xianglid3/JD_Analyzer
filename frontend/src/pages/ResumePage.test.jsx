import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../lib/api'
import { renderWithProviders } from '../test/render'
import ResumePage from './ResumePage'

vi.mock('../lib/api', () => ({ apiFetch: vi.fn() }))

describe('ResumePage', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume' && options?.method === 'PUT') return Promise.resolve({})
      if (path === '/resume') return Promise.resolve({ skills: ['Python'] })
      return Promise.resolve({ skills: [] })
    })
  })

  it('deduplicates skills and saves the edited skill list', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByText('Python')).toBeInTheDocument()
    const skillInput = screen.getByLabelText('Add a skill')
    await user.type(skillInput, 'python')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getAllByText('Python')).toHaveLength(1)

    await user.type(skillInput, 'React')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.click(screen.getByRole('button', { name: 'Save resume' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume', {
      method: 'PUT',
      body: JSON.stringify({ skills: ['Python', 'React'] }),
    }))
  })
})
