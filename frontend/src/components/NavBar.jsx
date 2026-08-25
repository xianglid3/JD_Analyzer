import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import { Spinner } from './Feedback'
import TranslateMark from './TranslateMark'

export default function NavBar() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const currentUser = queryClient.getQueryData(['me'])

  const logout = useMutation({
    mutationFn: () => apiFetch('/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      queryClient.clear()
      navigate('/login')
    },
  })

  const navItems = [
    ['/dashboard', 'Dashboard'],
    ['/resume', 'Resume'],
  ]

  return (
    <header className="sticky top-0 z-40 h-16 bg-surface/90 backdrop-blur-xl">
      <div className="mx-auto flex h-full max-w-7xl items-center gap-4 px-4 sm:px-6 lg:px-12">
        <Link to="/dashboard" className="flex shrink-0 items-center gap-2 text-sm font-medium text-ink">
          <TranslateMark className="h-[18px] w-[18px] text-carbon" />
          <span className="hidden sm:inline">JD Translator</span>
          <span className="sm:hidden">JD</span>
        </Link>

        <nav className="ml-2 flex items-center gap-1" aria-label="Main navigation">
          {navItems.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `rounded-md px-3 py-2 text-sm ${
                isActive ? 'bg-white text-ink shadow-[0_0_0_1px_#ebebeb]' : 'text-charcoal hover:text-ink'
              }`}
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          {currentUser?.username && <span className="hidden font-mono text-[11px] uppercase tracking-wider text-muted md:inline">{currentUser.username}</span>}
          <button
            type="button"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-white px-3 py-2 text-sm font-normal leading-5 text-charcoal hover:text-ink"
          >
            {logout.isPending && <Spinner size="sm" />}
            <span className="hidden sm:inline">{logout.isPending ? 'Logging out…' : 'Log out'}</span>
            <span className="sm:hidden">Out</span>
          </button>
        </div>
      </div>
      {logout.error && <p role="alert" className="absolute right-4 top-14 text-xs text-ink">Could not log out.</p>}
    </header>
  )
}
