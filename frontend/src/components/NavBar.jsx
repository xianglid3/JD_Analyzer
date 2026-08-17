import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export default function NavBar() {
    const navigate = useNavigate()
    const queryClient = useQueryClient()

    const logout = useMutation({
        mutationFn: () => apiFetch('/auth/logout', { method: 'POST' }),
        onSuccess: () => {
        queryClient.clear()      // wipe cached jobs/resume/me
        navigate('/login')
        },
    })

    return (
        <nav className="flex items-center justify-between border-b border-border px-8 py-3">
        <span className="font-semibold text-primary">JD Translator</span>
        <div className="flex items-center gap-4">
            <Link to="/dashboard" className="text-ink">Dashboard</Link>
            <Link to="/resume" className="text-ink">Resume</Link>
            <button onClick={() => logout.mutate()} className="text-ink">Log out</button>
        </div>
        </nav>
    )
}
