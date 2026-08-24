import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import AuthShell from '../components/AuthShell'
import { ButtonLabel, InlineAlert } from '../components/Feedback'
import { apiFetch } from '../lib/api'

export default function SignupPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const navigate = useNavigate()

  const signupMutation = useMutation({
    mutationFn: (credentials) => apiFetch('/auth/signup', {
      method: 'POST',
      body: JSON.stringify(credentials),
    }),
    onSuccess: () => navigate('/login?created=1'),
  })

  function handleSubmit(event) {
    event.preventDefault()
    signupMutation.mutate({ username, password })
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
          <span className="mt-1 block text-xs text-muted">3–50 characters</span>
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
          <span className="mt-1 block text-xs text-muted">8–72 bytes, with no spaces</span>
        </label>

        <button type="submit" disabled={signupMutation.isPending} className="primary-button mt-2 w-full">
          <ButtonLabel pending={signupMutation.isPending} pendingText="Creating account…">Create account</ButtonLabel>
        </button>
      </form>
    </AuthShell>
  )
}
