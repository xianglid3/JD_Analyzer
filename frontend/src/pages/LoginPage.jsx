import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import AuthShell from '../components/AuthShell'
import { ButtonLabel, InlineAlert } from '../components/Feedback'
import { apiFetch } from '../lib/api'

export default function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [searchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const loginMutation = useMutation({
    mutationFn: (credentials) => apiFetch('/auth/login', {
      method: 'POST',
      body: JSON.stringify(credentials),
    }),
    onSuccess: () => {
      queryClient.clear()
      navigate('/dashboard')
    },
  })

  function handleSubmit(event) {
    event.preventDefault()
    loginMutation.mutate({ username, password })
  }

  return (
    <AuthShell
      title="Welcome back"
      description="Log in to continue analyzing and tracking job descriptions."
      footer={<>New here? <Link to="/signup" className="text-ink underline underline-offset-4">Create an account</Link></>}
    >
      {searchParams.get('created') === '1' && !loginMutation.error && (
        <InlineAlert tone="success" className="mb-4">Account created. You can log in now.</InlineAlert>
      )}
      {loginMutation.error && <InlineAlert className="mb-4">{loginMutation.error.message}</InlineAlert>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block text-sm text-ink">
          Username
          <input
            required
            autoFocus
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="Enter your username"
            className="control mt-2 px-3 py-3 text-sm"
          />
        </label>

        <label className="block text-sm text-ink">
          Password
          <span className="relative mt-2 block">
            <input
              required
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Enter your password"
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
        </label>

        <button type="submit" disabled={loginMutation.isPending} className="primary-button mt-2 w-full">
          <ButtonLabel pending={loginMutation.isPending} pendingText="Logging in…">Log in</ButtonLabel>
        </button>
      </form>
    </AuthShell>
  )
}
