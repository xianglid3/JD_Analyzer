import { useNavigate, Link } from 'react-router-dom'
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export default function SignupPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const navigate = useNavigate() // let you navigate('/login')

  const signupMutation = useMutation({
    mutationFn: (credentials) =>
      apiFetch('/auth/signup', { method: 'POST', body: JSON.stringify(credentials) }),
    onSuccess: () => navigate('/login'),
  })

  function handleSubmit(e) {
    e.preventDefault() // cancel browser page reload when form submitted
    signupMutation.mutate({ username, password })
  }

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center px-4">
      <form onSubmit={handleSubmit} className="bg-white border border-border rounded-xl p-8 w-full max-w-sm">
        <h2 className="text-center text-primary font-bold text-lg">JD Translator</h2>
        <h1 className="text-center text-ink font-semibold text-2xl mt-1 mb-6">Create your account</h1>

        {signupMutation.error && (
          <p className="text-red-600 text-sm mb-3">{signupMutation.error.message}</p>
        )}

        <label className="block text-sm text-ink mb-1">Username</label>
        <input
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Enter your username"
          className="w-full border border-border rounded-md px-3 py-2 mb-4"
        />

        <label className="block text-sm text-ink mb-1">Password</label>
        <input
          required
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Create a password"
          className="w-full border border-border rounded-md px-3 py-2 mb-6"
        />

        <button
          type="submit"
          disabled={signupMutation.isPending}
          className="w-full bg-primary text-white rounded-md py-2 font-medium disabled:opacity-50"
        >
          {signupMutation.isPending ? 'Signing up…' : 'Sign up'}
        </button>

        <p className="text-center text-sm text-ink mt-4">
          Already have an account? <Link to="/login" className="text-primary">Log in</Link>
        </p>
      </form>
    </div>
  )
}
