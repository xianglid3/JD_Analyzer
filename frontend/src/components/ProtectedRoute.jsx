import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { PageLoader } from './Feedback'

export default function ProtectedRoute( {children} ) {
    
    const { isLoading, isError } = useQuery({
        queryKey: ['me'],
        queryFn: () => apiFetch('/auth/me'),
        retry: false,          
    })

    if (isLoading) return <PageLoader label="Loading your workspace…" />
    if (isError) return <Navigate to="/login" replace />
    
    return children

}
