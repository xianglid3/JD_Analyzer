import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import NavBar from '../components/NavBar'


export default function DashboardPage() {

  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [text, setText] = useState('')  //JD text input 
  const [showModal, setShowModal] = useState(false) // pop up window

  const createJob = useMutation({
    mutationFn: (description) =>
      apiFetch('/jobs', { method: 'POST', body: JSON.stringify({description})}),
    onSuccess: () => {
      queryClient.invalidateQueries( {queryKey: ['jobs']} )
      queryClient.invalidateQueries({queryKey : ['jobs-stats']})
      setText('')
      setShowModal(false)

    }
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }) =>
      apiFetch(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({queryKey : ['jobs-stats']})

    }
  })

  const { data, isLoading, isError } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => apiFetch('/jobs'),
  })

  const {data: stats} = useQuery({
    queryKey: ['jobs-stats'], 
    queryFn: () => apiFetch('/jobs/stats')
  })

  if (isLoading) return <p className='p-8'> Loading... </p>
  if (isError) return <p className='p-8'> Failed to load jobs </p>

  return(
      <div className='p-8'>
        <NavBar/>
          <h1 className='text-2xl font-semibold text-ink mb-4'>Your Job Descriptions</h1>
          
          <button 
            onClick={ () => setShowModal(true)}
            className="bg-primary text-white rounded-md px-4 py-2">
            + Analyze New JD
          </button>
          
          {showModal && (
            <div className="fixed inset-0 bg-black/40 flex items-center justify-center"
              onClick={() => setShowModal(false)}>
              
              <div className="bg-white rounded-lg p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
                
                <h2 className="text-lg font-semibold mb-3">Analyze a job description</h2>
                <textarea value={text} onChange={(e) => setText(e.target.value)}
                  placeholder="Paste a job description…"
                  className="w-full h-40 border border-border rounded-md p-2" />
                
                <div className="flex justify-end gap-2 mt-3">
                  
                  <button onClick={() => setShowModal(false)}
                    className="border border-border rounded-md px-4 py-2">Cancel</button>
                  
                  <button onClick={() => createJob.mutate(text)} disabled={createJob.isPending}
                    className="bg-primary text-white rounded-md px-4 py-2">
                    {createJob.isPending ? 'Analyzing…' : 'Analyze'}
                  </button>
                
                </div>
              
              </div>
            
            </div>
          )}

          {stats && (  //stat on jobs
            <div className="flex gap-3 mb-6">
              {[
                ['Total', stats.total],
                ['Saved', stats.by_status.saved],
                ['Applied', stats.by_status.applied],
                ['Interview', stats.by_status.interview],
                ['Offer', stats.by_status.offer],
                ['Rejected', stats.by_status.rejected],
              ].map(([label, value]) => (
                <div key={label} className="border border-border rounded-md px-4 py-2 text-center">
                  <div className="text-xl font-bold text-ink">{value}</div>
                  <div className="text-xs text-muted uppercase">{label}</div>
                </div>
              ))}
            </div>
          )}   
          
          
          {/* textarea for job description*
          
          <textarea value={text} onChange={(event) => setText(event.target.value)}
            placeholder="Paste a job description…" className="w-full border border-border rounded-md p-2" />
          <button onClick={() => createJob.mutate(text)} disabled={createJob.isPending}
            className="bg-primary text-white rounded-md px-4 py-2 mt-2">
            {createJob.isPending ? 'Analyzing…' : 'Analyze'}
          </button>
          
          */}

          <table className='w-full text-left'>
              <thead>
                  <tr className='text-sm text-muted border-b border-border'>
                      <th>Title</th><th>Company</th><th>Match</th><th>Location</th><th>Date Added</th><th>Status</th>
                  </tr>
              </thead>
              <tbody>
                  {data.jobs.map((job) => (
                      <tr key={job.id} onClick={() => navigate(`/jobs/${job.id}`)} className='border-b border-border'>
                          <td>{job.title}</td>
                          <td>{job.company_name}</td>
                          <td>{job.match_score}%</td>
                          <td>{job.location}</td>
                          <td>{new Date(job.created_at).toLocaleDateString()}</td>                              
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
