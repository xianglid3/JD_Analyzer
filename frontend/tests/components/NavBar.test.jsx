import { QueryClient } from '@tanstack/react-query'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import NavBar from '../../src/components/NavBar'
import { apiFetch } from '../../src/lib/api'
import { renderWithProviders } from '../helpers/render'

vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn() }))

function renderNav() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  client.setQueryData(['me'], { username: 'shawn' })
  return renderWithProviders(
    <Routes>
      <Route path="/dashboard" element={<NavBar />} />
      <Route path="/login" element={<p>Login destination</p>} />
    </Routes>,
    { route: '/dashboard', client },
  )
}

describe('NavBar user menu', () => {
  beforeEach(() => apiFetch.mockReset())

  it('keeps logout inside the user menu and signs out from there', async () => {
    const user = userEvent.setup()
    apiFetch.mockResolvedValue({})
    renderNav()

    expect(screen.queryByRole('menuitem', { name: 'Log out' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open user menu for shawn' }))

    const logout = screen.getByRole('menuitem', { name: 'Log out' })
    expect(logout).toHaveClass('text-red-700')
    await user.click(logout)

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/auth/logout', { method: 'POST' }))
    expect(await screen.findByText('Login destination')).toBeInTheDocument()
  })

  it('closes the menu with Escape and restores trigger focus', async () => {
    const user = userEvent.setup()
    renderNav()
    const trigger = screen.getByRole('button', { name: 'Open user menu for shawn' })

    await user.click(trigger)
    expect(screen.getByRole('menu', { name: 'User menu' })).toBeInTheDocument()
    await user.keyboard('{Escape}')

    expect(screen.queryByRole('menu', { name: 'User menu' })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
