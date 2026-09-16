import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProtectedRoute from '../../src/components/ProtectedRoute'
import { apiFetch } from '../../src/lib/api'
import LoginPage from '../../src/pages/LoginPage'
import SignupPage from '../../src/pages/SignupPage'

vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useQuery: vi.fn() }
})
vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn() }))

function renderRoutes(initialEntry, routes) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, throwOnError: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>{routes}</Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('authentication screens', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    useQuery.mockReset()
    // SignupPage asks the server whether an invite code is required; an open instance is the
    // default, and every test that cares overrides it
    useQuery.mockReturnValue({ data: { invite_required: false } })
  })

  it('submits login credentials and navigates to the dashboard', async () => {
    apiFetch.mockResolvedValue({})
    const user = userEvent.setup()
    renderRoutes('/login', (
      <>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/dashboard" element={<p>Dashboard reached</p>} />
      </>
    ))

    await user.type(screen.getByLabelText('Username'), 'alex123')
    await user.type(screen.getByLabelText('Password'), 'Secure!123')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(apiFetch).toHaveBeenCalledWith('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: 'alex123', password: 'Secure!123' }),
    })
    expect(await screen.findByText('Dashboard reached')).toBeInTheDocument()
  })

  it('shows confirmation after successful signup', async () => {
    apiFetch.mockResolvedValue({})
    const user = userEvent.setup()
    renderRoutes('/signup', (
      <>
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/login" element={<LoginPage />} />
      </>
    ))

    await user.type(screen.getByLabelText(/^Username/), 'newuser')
    await user.type(screen.getByLabelText(/^Password/), 'Symbols!123')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Account created. You can log in now.')).toBeInTheDocument()
  })

  it('asks for no invite code on an open instance', async () => {
    apiFetch.mockResolvedValue({})
    renderRoutes('/signup', <Route path="/signup" element={<SignupPage />} />)

    expect(screen.queryByLabelText(/Invite code/)).not.toBeInTheDocument()
  })

  it('collects the invite code and sends it when the server requires one', async () => {
    // without this the form posts username and password to an invite-only server and is
    // refused, which is what setting SIGNUP_INVITE_CODE did to signup before the field existed
    useQuery.mockReturnValue({ data: { invite_required: true } })
    apiFetch.mockResolvedValue({})
    const user = userEvent.setup()
    renderRoutes('/signup', (
      <>
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/login" element={<LoginPage />} />
      </>
    ))

    await user.type(screen.getByLabelText(/^Username/), 'invited')
    await user.type(screen.getByLabelText(/^Password/), 'Symbols!123')
    await user.type(screen.getByLabelText(/Invite code/), 'let-me-in')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(apiFetch).toHaveBeenCalledWith('/auth/signup', {
      method: 'POST',
      body: JSON.stringify({ username: 'invited', password: 'Symbols!123', invite_code: 'let-me-in' }),
    })
  })

  it('redirects unauthenticated users away from protected content', async () => {
    useQuery.mockReturnValue({ isLoading: false, isError: true })

    renderRoutes('/private', (
      <>
        <Route path="/login" element={<p>Login destination</p>} />
        <Route path="/private" element={<ProtectedRoute><p>Private content</p></ProtectedRoute>} />
      </>
    ))

    expect(await screen.findByText('Login destination')).toBeInTheDocument()
    expect(screen.queryByText('Private content')).not.toBeInTheDocument()
  })
})
