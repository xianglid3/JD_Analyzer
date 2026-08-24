import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import NavBar from '../components/NavBar'

export default function ResumePage() {

  const queryClient = useQueryClient()

  const [skills, setSkills] = useState([])
  const [newSkill, setNewSkill] = useState('')
  const [resumeText, setResumeText] = useState('')

  //mutation for pasting resume text
  //call '/parse' to grab skills out of text
  const parseMutation = useMutation({
    mutationFn: (text) =>
      apiFetch('/resume/parse', { method: 'POST', body: JSON.stringify({text}) }),
    onSuccess: (data) => setSkills([...new Set([...skills, ...data.skills])]),
  })

  //mutation for uploading a resume file (PDF/MD/TXT) → extract skills
  const uploadMutation = useMutation({
    mutationFn: (file) => {
      const form = new FormData()
      form.append('file', file)
      return apiFetch('/resume/upload', { method: 'POST', body: form })
    },
    onSuccess: (data) => setSkills([...new Set([...skills, ...data.skills])]),
  })

  //mutation for resume
  const resumeMutation = useMutation({
    mutationFn: (resume) =>
      apiFetch('/resume', { method: 'PUT', body: JSON.stringify(resume) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })

  const { data } = useQuery({
    queryKey: ['resume'],
    queryFn: () => apiFetch('/resume'),
    retry: false,
  })

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (data?.skills) setSkills(data.skills)
  }, [data])

  function addSkill() {
    if (!newSkill.trim()) return
    setSkills([...skills, newSkill.trim()])
    setNewSkill('')
  }

  function removeSkill(skill) {
    setSkills(skills.filter((s) => s !== skill))
  }

  return (
    <div className="min-h-screen bg-surface">
      <NavBar />

      <div className="max-w-3xl mx-auto px-8 py-8">
        <h1 className="text-2xl font-semibold text-ink mb-1">Resume</h1>
        <p className="text-sm text-muted mb-6">
          Saved and used to score how well each job matches your skills.
        </p>

        <textarea
          value={resumeText}
          onChange={(e) => setResumeText(e.target.value)}
          placeholder='Paste your resume text here'
          className="w-full h-40 border border-border rounded-md p-3 text-sm mb-3"
        />

        <button
          onClick={() => parseMutation.mutate(resumeText)}
          disabled = {parseMutation.isPending}
          className="bg-primary text-white rounded-md px-4 py-2 disabled:opacity-50"
        >
          {parseMutation.isPending? 'Extracting...' : 'Extract'}

        </button>

        <div className="flex items-center gap-3 my-3">
          <span className="text-xs text-muted">or</span>
          <label className="border border-border rounded-md px-4 py-2 text-sm cursor-pointer hover:bg-surface">
            {uploadMutation.isPending ? 'Reading file…' : 'Upload a file (PDF, MD, TXT)'}
            <input
              type="file"
              accept=".pdf,.md,.txt,.html"
              className="hidden"
              onChange={(e) => e.target.files[0] && uploadMutation.mutate(e.target.files[0])}
            />
          </label>
          {uploadMutation.error && (
            <span className="text-red-600 text-sm">{uploadMutation.error.message}</span>
          )}
        </div>

        <div className="bg-white border border-border rounded-lg p-6">
          <h2 className="font-semibold text-ink mb-3">Skills</h2>

          <div className="flex gap-2 mb-4">
            <input
              value={newSkill}
              onChange={(e) => setNewSkill(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addSkill())}
              placeholder="Add a skill (e.g. Python)"
              className="flex-1 border border-border rounded-md px-3 py-2"
            />
            <button onClick={addSkill} className="bg-primary text-white rounded-md px-4 py-2">
              Add
            </button>
          </div>

          {skills.length === 0 ? (
            <p className="text-sm text-muted">No skills yet — add some above.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {skills.map((skill) => (
                <span
                  key={skill}
                  className="inline-flex items-center gap-1 bg-surface rounded-full px-3 py-1 text-sm"
                >
                  {skill}
                  <button
                    onClick={() => removeSkill(skill)}
                    className="text-muted hover:text-red-600"
                  >
                    x
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-3 mt-6">
          <button
            onClick={() => resumeMutation.mutate({ skills })}
            disabled={resumeMutation.isPending}
            className="bg-primary text-white rounded-md px-6 py-2 font-medium disabled:opacity-50"
          >
            {resumeMutation.isPending ? 'Saving…' : 'Save resume'}
          </button>
          {resumeMutation.isSuccess && <span className="text-green-600 text-sm">Saved ✓</span>}
        </div>
      </div>
    </div>
  )
}
