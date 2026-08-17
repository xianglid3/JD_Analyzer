import { useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export default function LoginPage() {

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const navigate = useNavigate()
  
  const loginMutation = useMutation({
    mutationFn: (credentials) =>
      apiFetch('/auth/login', { method: 'POST', body: JSON.stringify(credentials) }),
    onSuccess: () => navigate('/dashboard'),
  })

  function handleSubmit(e) {
    e.preventDefault() //?
    loginMutation.mutate({ username, password })  
  }

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold text-ink">Login page</h1>
      <form onSubmit={handleSubmit}>
        <input required value = {username}  placeholder = 'username' onChange={ (event) => setUsername(event.target.value)}/>
        <input required value = {password}  type = 'password' placeholder = 'password' onChange={ (event) => setPassword(event.target.value)}/>
        <button type='submit' disabled = {loginMutation.isPending}> {loginMutation.isPending? 'Logging in...' : 'Login'} </button>
      </form>
    </div>
  )
}
