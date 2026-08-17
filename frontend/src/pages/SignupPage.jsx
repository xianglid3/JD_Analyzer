import { useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export default function SignupPage(){

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
        <div className="p-8">
        <h1 className="text-2xl font-semibold text-ink">Signup page</h1>
        <form onSubmit={handleSubmit}>
            <input required value = {username}  placeholder = 'username' onChange={ (event) => setUsername(event.target.value)}/>
            <input required value = {password}  type = 'password' placeholder = 'password' onChange={ (event) => setPassword(event.target.value)}/>
            <button type='submit' disabled = {signupMutation.isPending}> {signupMutation.isPending? 'Signing Up...' : 'SignUp'} </button>
        </form>
        </div>
    )
}