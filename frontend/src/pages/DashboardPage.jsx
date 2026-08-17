import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import NavBar from '../components/NavBar'


export default function DashboardPage() {

  const navigate = useNavigate()

  //for JD text input 
  const [text, setText] = useState('')
  const queryClient = useQueryClient()
  
  //mutations
  const createJob = useMutation({
    mutationFn: (description) =>
      apiFetch('/jobs', { method: 'POST', body: JSON.stringify({description})}),
    onSuccess: () => {
      queryClient.invalidateQueries( {queryKey: ['jobs']} )
      setText('')
    }
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) =>
      apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    }
  })

  //lets get it
  const { data, isLoading, isError } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => apiFetch('/jobs'),
  })

  if (isLoading) return <p className='p-8'> Loading... </p>
  if (isError) return <p className='p-8'> Failed to load jobs </p>

  return(
      <div className='p-8'>
        <NavBar/>
          <h1 className='text-2xl font-semibold text-ink mb-4'>Your Job Descruiptions</h1>
          
          {/* textarea for job description*/}
          <textarea value={text} onChange={(event) => setText(event.target.value)}
            placeholder="Paste a job description…" className="w-full border border-border rounded-md p-2" />
          <button onClick={() => createJob.mutate(text)} disabled={createJob.isPending}
            className="bg-primary text-white rounded-md px-4 py-2 mt-2">
            {createJob.isPending ? 'Analyzing…' : 'Analyze'}
          </button>

          <table className='w-full text-left'>
              <thead>
                  <tr className='text-sm text-muted border-b border-border'>
                      <th>Title</th><th>Company</th><th>Match</th><th>Location</th><th>Status</th>
                  </tr>
              </thead>
              <tbody>
                  {data.jobs.map((job) => (
                      <tr key={job.id} onClick={() => navigate(`/jobs/${job.id}`)} className='border-b border-border'>
                          <td>{job.title}</td>
                          <td>{job.company_name}</td>
                          <td>{job.match_score}%</td>
                          <td>{job.location}</td>                           
                          <td>
                              <select
                                value={job.status}
                                onChange={(e) => updateStatus.mutate({ id: job.id, status: e.target.value })}
                                onClick={(e) => e.stopPropagation()}
                                className="border border-border rounded-md px-2 py-1"
                              >
                                <option value="saved">Saved</option>
                                <option value="applied">Applied</option>
                                <option value="interview">Interview</option>
                                <option value="offer">Offer</option>
                                <option value="rejected">Rejected</option>
                              </select>
                          </td>                           
                      </tr>
                  ))}
              </tbody>
          </table>
      </div>
    
  )

}
