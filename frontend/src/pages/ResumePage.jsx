import { useNavigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import NavBar from '../components/NavBar'


export default function ResumePage(){

    const [skills, setSkills] = useState([])
    const [newSkill, setNewSkill] = useState('')

    const queryClient = useQueryClient()

    const resumeMutation = useMutation({
        mutationFn: (resume) =>
        apiFetch('/resume', { method: 'PUT', body: JSON.stringify(resume) }),
        onSuccess: () => 
            queryClient.invalidateQueries( {queryKey: ['jobs']} )
    }) 

    const { data } = useQuery({
        queryKey: ['resume'],
        queryFn: () => apiFetch('/resume'),
        retry: false,
    })
    
    useEffect(() => {
        if (data?.skills) {
            setSkills(data.skills)
        }
    }, [data])
    
    function addSkill() {
        if (!newSkill.trim()) return
        setSkills([...skills, newSkill.trim()])
        setNewSkill('')
    }

    function removeSkill(skill) {
        setSkills(skills.filter((s) => s !== skill))
    }

    return(
       
        <div className='p-8'>
            <NavBar />
            <h1 className="text-2xl font-semibold text-ink mb-4">Resume</h1>

            <div className="mb-4">
                <input value={newSkill} onChange={(e) => setNewSkill(e.target.value)}
                    placeholder="Add a skill"
                    className="border border-border rounded-md px-3 py-2" />
                <button onClick={addSkill} className="ml-2 bg-primary text-white rounded-md px-4 py-2">Add</button>
            </div>

            <div>
                {skills.map((skill) => (
                    <span key={skill} className="inline-flex items-center gap-1 bg-surface rounded-full px-3 py-1 mr-2">
                        {skill}
                        <button onClick={() => removeSkill(skill)}>×</button>

                    </span>
                ))}
            </div>

            <button 
                onClick= {() => resumeMutation.mutate({ skills })}
                disabled = {resumeMutation.isPending}
                className="mt-4 block bg-primary text-white rounded-md px-4 py-2"
            >{resumeMutation.isPending? 'Saving': 'Save'} </button>
        
            </div>
    )

}
