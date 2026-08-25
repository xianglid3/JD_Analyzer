import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ButtonLabel, InlineAlert, PageLoader } from '../components/Feedback'
import NavBar from '../components/NavBar'
import { apiFetch } from '../lib/api'

function mergeSkills(current, incoming) {
  const merged = []
  const seen = new Set()
  for (const value of [...current, ...incoming]) {
    const skill = value.trim()
    const key = skill.toLocaleLowerCase()
    if (skill && !seen.has(key)) {
      seen.add(key)
      merged.push(skill)
    }
  }
  return merged
}

export default function ResumePage() {
  const queryClient = useQueryClient()
  const [skills, setSkills] = useState([])
  const [newSkill, setNewSkill] = useState('')
  const [resumeText, setResumeText] = useState('')
  const [notice, setNotice] = useState(null)

  const resumeQuery = useQuery({
    queryKey: ['resume'],
    queryFn: () => apiFetch('/resume'),
    retry: false,
  })

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (resumeQuery.data?.skills) setSkills(resumeQuery.data.skills)
  }, [resumeQuery.data])

  const parseMutation = useMutation({
    mutationFn: (text) => apiFetch('/resume/parse', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
    onSuccess: (data) => {
      setSkills((current) => mergeSkills(current, data.skills))
      setNotice({ tone: 'success', message: `${data.skills.length} skills extracted. Review them before saving.` })
    },
  })

  const uploadMutation = useMutation({
    mutationFn: (file) => {
      const form = new FormData()
      form.append('file', file)
      return apiFetch('/resume/upload', { method: 'POST', body: form })
    },
    onSuccess: (data) => {
      setSkills((current) => mergeSkills(current, data.skills))
      setNotice({ tone: 'success', message: `${data.skills.length} skills extracted from the file.` })
    },
  })

  const resumeMutation = useMutation({
    mutationFn: (resume) => apiFetch('/resume', {
      method: 'PUT',
      body: JSON.stringify(resume),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['resume'] })
      setNotice({ tone: 'success', message: 'Resume skills saved and match scores refreshed.' })
    },
  })

  function addSkill(event) {
    event?.preventDefault()
    if (!newSkill.trim()) return
    setSkills((current) => mergeSkills(current, [newSkill]))
    setNewSkill('')
    setNotice(null)
    resumeMutation.reset()
  }

  function removeSkill(skill) {
    setSkills((current) => current.filter((value) => value !== skill))
    setNotice(null)
    resumeMutation.reset()
  }

  if (resumeQuery.isLoading) return <PageLoader label="Loading your resume…" />

  const resumeLoadError = resumeQuery.isError && resumeQuery.error?.status !== 404
  const activeError = parseMutation.error || uploadMutation.error || resumeMutation.error

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <header className="mb-8">
          <p className="eyebrow">Profile</p>
          <h1 className="page-heading mt-2">Resume skills</h1>
          <p className="mt-2 text-sm text-muted">Your saved skills power every job match score.</p>
        </header>

        {resumeLoadError && (
          <InlineAlert className="mb-4">
            Could not load your saved resume. <button className="ml-1 underline" onClick={() => resumeQuery.refetch()}>Try again</button>
          </InlineAlert>
        )}
        {activeError && <InlineAlert className="mb-4">{activeError.message}</InlineAlert>}
        {notice && !activeError && <InlineAlert tone={notice.tone} className="mb-4">{notice.message}</InlineAlert>}

        <section className="surface-card mb-8 p-4" aria-labelledby="extract-heading">
          <div className="mb-4">
            <h2 id="extract-heading" className="text-base font-medium text-ink">Extract from a resume</h2>
            <p className="mt-1 text-sm text-muted">Paste text or upload a supported file. Nothing is saved until you confirm below.</p>
          </div>

          <label htmlFor="resume-text" className="text-sm text-ink">Resume text</label>
          <textarea
            id="resume-text"
            value={resumeText}
            disabled={parseMutation.isPending}
            onChange={(event) => {
              setResumeText(event.target.value)
              parseMutation.reset()
              setNotice(null)
            }}
            placeholder="Paste your resume text here…"
            className="control mt-2 h-44 resize-y p-4 text-sm leading-6"
          />
          <div className="mt-2 flex justify-between text-xs text-muted">
            <span>100–20,000 characters</span>
            <span>{resumeText.length.toLocaleString()} / 20,000</span>
          </div>

          <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
            <button
              className="primary-button"
              disabled={parseMutation.isPending || resumeText.trim().length < 100 || resumeText.trim().length > 20000}
              onClick={() => {
                uploadMutation.reset()
                resumeMutation.reset()
                parseMutation.mutate(resumeText)
              }}
            >
              <ButtonLabel pending={parseMutation.isPending} pendingText="Extracting…">Extract skills</ButtonLabel>
            </button>
            <span className="text-center text-xs text-muted">or</span>
            <label className={`secondary-button ${uploadMutation.isPending ? 'pointer-events-none opacity-50' : ''}`}>
              <ButtonLabel pending={uploadMutation.isPending} pendingText="Reading file…">Upload PDF, MD, TXT, or HTML</ButtonLabel>
              <input
                type="file"
                accept=".pdf,.md,.txt,.html"
                className="sr-only"
                disabled={uploadMutation.isPending}
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) {
                    parseMutation.reset()
                    resumeMutation.reset()
                    uploadMutation.mutate(file)
                  }
                  event.target.value = ''
                }}
              />
            </label>
          </div>
        </section>

        <section className="surface-card p-4" aria-labelledby="skills-heading">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 id="skills-heading" className="text-base font-medium text-ink">Skills</h2>
              <p className="mt-1 text-xs text-muted">Up to 100 skills. Duplicates are removed automatically.</p>
            </div>
            <span className="rounded-full border border-border px-2.5 py-1 text-xs text-muted">{skills.length} / 100</span>
          </div>

          <form onSubmit={addSkill} className="mt-4 flex gap-2">
            <label className="sr-only" htmlFor="new-skill">Add a skill</label>
            <input
              id="new-skill"
              value={newSkill}
              maxLength={100}
              onChange={(event) => setNewSkill(event.target.value)}
              placeholder="Add a skill, e.g. Python"
              className="control min-w-0 flex-1 px-3 py-2.5 text-sm"
            />
            <button type="submit" className="secondary-button" disabled={!newSkill.trim() || skills.length >= 100}>Add</button>
          </form>

          {skills.length === 0 ? (
            <div className="mt-4 rounded-md border border-dashed border-border p-8 text-center">
              <p className="text-sm text-ink">No skills saved yet</p>
              <p className="mt-1 text-xs text-muted">Extract a resume or add your first skill above.</p>
            </div>
          ) : (
            <div className="mt-4 flex flex-wrap gap-2" aria-live="polite">
              {skills.map((skill) => (
                <span key={skill} className="animate-soft-in inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-2 text-sm text-ink">
                  {skill}
                  <button
                    type="button"
                    onClick={() => removeSkill(skill)}
                    aria-label={`Remove ${skill}`}
                    className="text-base leading-none text-muted hover:text-ink"
                  >×</button>
                </span>
              ))}
            </div>
          )}
        </section>

        <footer className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-end">
          <span className="text-xs text-muted sm:mr-auto">Saving recalculates all existing match scores.</span>
          <button
            className="primary-button min-w-32"
            onClick={() => {
              parseMutation.reset()
              uploadMutation.reset()
              resumeMutation.mutate({ skills })
            }}
            disabled={resumeMutation.isPending}
          >
            <ButtonLabel pending={resumeMutation.isPending} pendingText="Saving…">Save resume</ButtonLabel>
          </button>
        </footer>
      </main>
    </div>
  )
}
