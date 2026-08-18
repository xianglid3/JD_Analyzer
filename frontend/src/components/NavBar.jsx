import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export default function NavBar() {
    const navigate = useNavigate()
    const queryClient = useQueryClient()

    const logout = useMutation({
        mutationFn: () => apiFetch('/auth/logout', { method: 'POST' }),
        onSuccess: () => {
        queryClient.clear() // wipe cached jobs/resume/me
        navigate('/login')
        },
    })

    return (
        <nav className="bg-white border-b border-border">
        <div className="max-w-6xl mx-auto flex items-center justify-between px-8 py-3">
            <Link to="/dashboard" className="font-semibold text-primary text-lg">JD Translator</Link>
            <div className="flex items-center gap-6 text-sm">
            <Link to="/dashboard" className="text-ink hover:text-primary">Dashboard</Link>
            <Link to="/resume" className="text-ink hover:text-primary">Resume</Link>
            <button onClick={() => logout.mutate()} className="text-ink hover:text-primary">Logout</button>
            </div>
        </div>
        </nav>
    )
}
