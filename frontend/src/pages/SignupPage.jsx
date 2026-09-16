import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import AuthShell from '../components/AuthShell'
import { ButtonLabel, InlineAlert } from '../components/Feedback'
import { apiFetch } from '../lib/api'

export default function SignupPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [inviteCode, setInviteCode] = useState('')
  const navigate = useNavigate()

  // The field only exists when the server asks for one, so an open instance never shows a
  // box nobody can fill in. The answer says whether a code is needed, never what it is.
  const { data: config } = useQuery({
    queryKey: ['auth', 'config'],
    queryFn: () => apiFetch('/auth/config'),
    staleTime: Infinity,
  })
  const inviteRequired = config?.invite_required === true

  const signupMutation = useMutation({
    mutationFn: (credentials) => apiFetch('/auth/signup', {
      method: 'POST',
      body: JSON.stringify(credentials),
    }),
    onSuccess: () => navigate('/login?created=1'),
  })

  function handleSubmit(event) {
    event.preventDefault()
    signupMutation.mutate(inviteRequired ? { username, password, invite_code: inviteCode } : { username, password })
  }

  return (
    <AuthShell
      title="Create your account"
      description="Build a private workspace for clearer job descriptions and application tracking."
      footer={<>Already have an account? <Link to="/login" className="text-ink underline underline-offset-4">Log in</Link></>}
    >
      {signupMutation.error && <InlineAlert className="mb-4">{signupMutation.error.message}</InlineAlert>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block text-sm text-ink">
          Username
          <input
            required
            autoFocus
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="Letters and numbers only"
            className="control mt-2 px-3 py-3 text-sm"
          />
          <span className="mt-1 block text-xs text-muted">Letters and numbers, 3–50 characters</span>
        </label>

        <label className="block text-sm text-ink">
          Password
          <span className="relative mt-2 block">
            <input
              required
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Create a password"
              className="control px-3 py-3 pr-16 text-sm"
            />
            <button
              type="button"
              onClick={() => setShowPassword((current) => !current)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted hover:text-ink"
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </span>
          <span className="mt-1 block text-xs text-muted">At least 8 characters, no spaces</span>
        </label>

        {inviteRequired && (
          <label className="block text-sm text-ink">
            Invite code
            <input
              required
              value={inviteCode}
              onChange={(event) => setInviteCode(event.target.value)}
              placeholder="The code you were given"
              className="control mt-2 px-3 py-3 text-sm"
            />
            <span className="mt-1 block text-xs text-muted">This service is invite-only right now</span>
          </label>
        )}

        <button type="submit" disabled={signupMutation.isPending} className="primary-button mt-2 w-full">
          <ButtonLabel pending={signupMutation.isPending} pendingText="Creating account…">Create account</ButtonLabel>
        </button>
      </form>
    </AuthShell>
  )
}
