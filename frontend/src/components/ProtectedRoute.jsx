import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export default function ProtectedRoute( {children} ) {
    
    const { isLoading, isError } = useQuery({
        queryKey: ['me'],
        queryFn: () => apiFetch('/auth/me'),
        retry: false,          
    })

    if (isLoading) return <p className="p-8">Loading…</p>
    if (isError) return <Navigate to="/login" replace />
    
    return children

}
