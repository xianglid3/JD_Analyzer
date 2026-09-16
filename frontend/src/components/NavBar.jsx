import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import { Spinner } from './Feedback'
import MatchaMark from './MatchaMark'

export default function NavBar() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const currentUser = queryClient.getQueryData(['me'])
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)
  const triggerRef = useRef(null)
  const username = currentUser?.username || 'Account'

  useEffect(() => {
    if (!menuOpen) return undefined

    function handlePointerDown(event) {
      if (!menuRef.current?.contains(event.target)) setMenuOpen(false)
    }

    function handleKeyDown(event) {
      if (event.key !== 'Escape') return
      setMenuOpen(false)
      triggerRef.current?.focus()
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [menuOpen])

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
        <Link to="/dashboard" className="flex min-h-11 shrink-0 items-center gap-2 text-sm font-medium text-ink">
          <MatchaMark className="h-[18px] w-[18px] text-carbon" />
          <span className="hidden sm:inline">JobMatcha</span>
          <span className="sm:hidden">Matcha</span>
        </Link>

        <nav className="ml-2 flex items-center gap-1" aria-label="Main navigation">
          {navItems.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `inline-flex min-h-11 items-center rounded-md px-3 py-2 text-sm ${
                isActive ? 'bg-white text-ink shadow-[0_0_0_1px_#ebebeb]' : 'text-charcoal hover:text-ink'
              }`}
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <div ref={menuRef} className="relative ml-auto">
          <button
            ref={triggerRef}
            type="button"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={`Open user menu for ${username}`}
            onClick={() => setMenuOpen((open) => !open)}
            className="inline-flex min-h-11 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-charcoal hover:bg-white hover:text-ink"
          >
            <span className="grid size-7 place-items-center rounded-full bg-obsidian text-xs font-medium uppercase text-white" aria-hidden="true">
              {username.slice(0, 1)}
            </span>
            <span className="hidden max-w-32 truncate sm:inline">{username}</span>
            <svg aria-hidden="true" viewBox="0 0 16 16" className={`h-3.5 w-3.5 text-muted transition-transform ${menuOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="m4 6 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>

          {menuOpen && (
            <div
              role="menu"
              aria-label="User menu"
              className="surface-card absolute right-0 top-full z-50 mt-2 w-52 overflow-hidden animate-soft-in"
            >
              <div className="border-b border-border px-4 py-3">
                <p className="text-xs text-muted">Signed in as</p>
                <p className="mt-0.5 truncate text-sm text-ink">{username}</p>
              </div>
              <div className="p-1.5">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => logout.mutate()}
                  disabled={logout.isPending}
                  className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 text-left text-sm text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  {logout.isPending && <Spinner size="sm" />}
                  {logout.isPending ? 'Logging out…' : 'Log out'}
                </button>
                {logout.error && <p role="alert" className="px-3 pb-2 text-xs text-red-700">Could not log out. Try again.</p>}
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
