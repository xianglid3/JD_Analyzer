import { Link, NavLink, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { Spinner } from './Feedback'

export default function NavBar() {
    const navigate = useNavigate()
    const queryClient = useQueryClient()
    const currentUser = queryClient.getQueryData(['me'])

    const logout = useMutation({
        mutationFn: () => apiFetch('/auth/logout', { method: 'POST' }),
        onSuccess: () => {
        queryClient.clear() // wipe cached jobs/resume/me
        navigate('/login')
        },
    })

    const navItems = [
        ['/dashboard', 'Dashboard'],
        ['/resume', 'Resume'],
    ]

    const navLinkClass = ({ isActive }) => `flex items-center rounded-xl px-3 py-3 text-base ${
        isActive ? 'bg-deep-teal text-white' : 'text-muted hover:bg-soft-paper hover:text-ink'
    }`

    return (
      <>
        <header className="sticky top-0 z-40 flex items-center justify-between border-b border-border bg-surface px-3 py-3 lg:hidden">
          <Link to="/dashboard" className="text-base font-medium text-ink">
            <span className="sm:hidden">JD</span><span className="hidden sm:inline">JD Translator</span>
          </Link>
          <nav className="flex items-center gap-1" aria-label="Mobile navigation">
            {navItems.map(([to, label]) => (
              <NavLink key={to} to={to} className={({ isActive }) => `rounded-md px-2.5 py-2 text-sm ${
                isActive ? 'bg-deep-teal text-white' : 'text-muted'
              }`}>
                {label}
              </NavLink>
            ))}
            <button
              type="button"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              className="rounded-md px-2.5 py-2 text-sm text-muted"
              aria-label="Log out"
            >
              {logout.isPending ? <Spinner size="sm" /> : 'Out'}
            </button>
          </nav>
        </header>

        <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col border-r border-border bg-surface p-4 lg:flex">
          <Link to="/dashboard" className="mb-8 flex items-center gap-2 px-3 py-2 text-base font-medium text-ink">
            <span className="grid h-7 w-7 place-items-center rounded-md border border-border" aria-hidden="true">JD</span>
            JD Translator
          </Link>

          <p className="mb-2 px-3 text-xs text-muted">Workspace</p>
          <nav className="space-y-1" aria-label="Main navigation">
            {navItems.map(([to, label]) => (
              <NavLink key={to} to={to} className={navLinkClass}>{label}</NavLink>
            ))}
          </nav>

          <div className="mt-auto border-t border-border pt-4">
            {currentUser?.username && (
              <p className="mb-2 truncate px-3 text-xs text-muted">Signed in as {currentUser.username}</p>
            )}
            <button
              type="button"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-muted hover:bg-soft-paper hover:text-ink"
            >
              {logout.isPending && <Spinner size="sm" />}
              {logout.isPending ? 'Logging out…' : 'Log out'}
            </button>
            {logout.error && <p role="alert" className="mt-2 px-3 text-xs text-ink">Could not log out.</p>}
          </div>
        </aside>
      </>
    )
}
