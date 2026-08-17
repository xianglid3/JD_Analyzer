import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import NavBar from '../components/NavBar'

export default function JobDetailPage() {
    const { id } = useParams()
    
    const { data: job, isLoading, isError } = useQuery({
        queryKey: ['job', id],
        queryFn: () => apiFetch(`/jobs/${id}`),
    })

    if (isLoading) return <p className="p-8">Loading…</p>
    if (isError) return <p className="p-8">Job not found.</p>

    return (
        <div className="p-8">
            <NavBar />
            <h1 className="text-2xl font-semibold text-ink">{job.title}</h1>
                <p className="text-muted">{job.company_name} • {job.location} • {job.work_type}</p>
                <p className="mt-2 text-primary font-semibold">Match: {job.match_score}%</p>

            <h2 className="mt-4 font-semibold">Summary</h2>
                <p>{job.summary}</p>

            <h2 className="mt-4 font-semibold">No-BS Translation</h2>
                <p className="bg-surface rounded-md p-3">{job.no_bs_translation}</p>

            <h2 className="mt-4 font-semibold">Skills</h2>
            <div>
                {job.skills.map((s) => (
                <span key={s} className="inline-block bg-surface rounded-full px-3 py-1 mr-2">{s}</span>
                ))}
            </div>
        </div>
    )
}
