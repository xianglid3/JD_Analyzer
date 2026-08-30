import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ButtonLabel, InlineAlert, PageLoader } from '../components/Feedback'
import Dialog from '../components/Dialog'
import NavBar from '../components/NavBar'
import { apiFetch, apiUpload } from '../lib/api'

const MAX_FILE_SIZE = 5 * 1024 * 1024
const SUPPORTED_EXTENSIONS = ['pdf', 'md', 'txt', 'html']

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

function formatFileSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export default function ResumePage() {
  const queryClient = useQueryClient()
  const [skills, setSkills] = useState([])
  const [newSkill, setNewSkill] = useState('')
  const [resumeText, setResumeText] = useState('')
  const [pendingFile, setPendingFile] = useState(null)
  const [pendingFileText, setPendingFileText] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadPhase, setUploadPhase] = useState('idle')
  const [isDragging, setIsDragging] = useState(false)
  const [showDeleteFileDialog, setShowDeleteFileDialog] = useState(false)
  const [notice, setNotice] = useState(null)
  const fileInputRef = useRef(null)

  const resumeQuery = useQuery({
    queryKey: ['resume'],
    queryFn: () => apiFetch('/resume'),
    retry: false,
  })

  useEffect(() => {
    if (!resumeQuery.data) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSkills(resumeQuery.data.skills || [])
  }, [resumeQuery.data])

  const parseMutation = useMutation({
    mutationFn: (text) => apiFetch('/resume/parse', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
    onSuccess: (data) => {
      setSkills((current) => mergeSkills(current, data.skills))
      setResumeText(data.resume_text)
      setNotice({ tone: 'success', message: `${data.skills.length} skills extracted. Review them before saving.` })
    },
  })

  const uploadMutation = useMutation({
    mutationFn: (file) => {
      const form = new FormData()
      form.append('file', file)
      return apiUpload('/resume/upload', form, (progress) => {
        setUploadProgress(progress)
        if (progress >= 100) setUploadPhase('analyzing')
      })
    },
    onSuccess: (data, file) => {
      setSkills((current) => mergeSkills(current, data.skills))
      setResumeText('')
      setPendingFileText(data.resume_text.trim())
      setPendingFile(file)
      setUploadProgress(100)
      setUploadPhase('complete')
      setNotice({ tone: 'success', message: `${data.skills.length} skills extracted. Save to store ${file.name}.` })
    },
    onError: () => {
      setPendingFile(null)
      setUploadProgress(0)
      setUploadPhase('idle')
    },
  })

  const resumeMutation = useMutation({
    mutationFn: ({ resume, file }) => {
      if (file) {
        const form = new FormData()
        form.append('resume', JSON.stringify(resume))
        form.append('file', file)
        return apiFetch('/resume', { method: 'PUT', body: form })
      }
      return apiFetch('/resume', {
        method: 'PUT',
        body: JSON.stringify(resume),
      })
    },
    onSuccess: () => {
      setPendingFile(null)
      setPendingFileText('')
      setResumeText('')
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['resume'] })
      setNotice({ tone: 'success', message: 'Resume saved and match scores refreshed.' })
    },
  })

  const downloadMutation = useMutation({
    mutationFn: () => apiFetch('/resume/file'),
    onSuccess: (data) => {
      const link = document.createElement('a')
      link.href = data.url
      link.download = data.filename || 'resume'
      link.rel = 'noopener noreferrer'
      document.body.appendChild(link)
      link.click()
      link.remove()
    },
  })

  const deleteFileMutation = useMutation({
    mutationFn: () => apiFetch('/resume/file', { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.setQueryData(['resume'], (current) => (
        current ? { ...current, source_file: null } : current
      ))
      setShowDeleteFileDialog(false)
      setNotice({ tone: 'success', message: 'Stored resume file removed. Your extracted skills and text were kept.' })
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

  function analyzeFile(file) {
    if (!file) return

    const extension = file.name.split('.').pop()?.toLocaleLowerCase()
    if (!SUPPORTED_EXTENSIONS.includes(extension)) {
      setNotice({ tone: 'error', message: 'Choose a PDF, MD, TXT, or HTML file.' })
      return
    }
    if (file.size > MAX_FILE_SIZE) {
      setNotice({ tone: 'error', message: 'File must be 5 MB or smaller.' })
      return
    }

    parseMutation.reset()
    resumeMutation.reset()
    uploadMutation.reset()
    setNotice(null)
    setPendingFile(file)
    setPendingFileText('')
    setUploadProgress(0)
    setUploadPhase('uploading')
    uploadMutation.mutate(file)
  }

  function removePendingFile() {
    setPendingFile(null)
    setPendingFileText('')
    setUploadProgress(0)
    setUploadPhase('idle')
    uploadMutation.reset()
    setNotice(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  if (resumeQuery.isLoading) return <PageLoader label="Loading your resume…" />

  const resumeLoadError = resumeQuery.isError && resumeQuery.error?.status !== 404
  const activeError = parseMutation.error || uploadMutation.error || resumeMutation.error || downloadMutation.error || deleteFileMutation.error

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

          <textarea
            id="resume-text"
            value={resumeText}
            disabled={parseMutation.isPending}
            onChange={(event) => {
              setResumeText(event.target.value)
              if (pendingFile) removePendingFile()
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

          <div className="mt-4">
            <button
              className="primary-button"
              disabled={parseMutation.isPending || resumeText.trim().length < 100 || resumeText.trim().length > 20000}
              onClick={() => {
                uploadMutation.reset()
                resumeMutation.reset()
                setPendingFile(null)
                setPendingFileText('')
                parseMutation.mutate(resumeText)
              }}
            >
              <ButtonLabel pending={parseMutation.isPending} pendingText="Extracting…">Extract skills</ButtonLabel>
            </button>
          </div>

          <div className="my-5 flex items-center gap-3" aria-hidden="true">
            <span className="h-px flex-1 bg-border" />
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">or upload a file</span>
            <span className="h-px flex-1 bg-border" />
          </div>

          <div
            role="button"
            tabIndex={uploadMutation.isPending ? -1 : 0}
            aria-label="Upload resume file"
            aria-disabled={uploadMutation.isPending}
            className={`rounded-md border border-dashed p-7 text-center transition-colors ${
              isDragging
                ? 'border-ink bg-surface'
                : 'border-border bg-soft-paper hover:border-ash hover:bg-surface'
            } ${uploadMutation.isPending ? 'cursor-wait opacity-70' : 'cursor-pointer'}`}
            onClick={() => !uploadMutation.isPending && fileInputRef.current?.click()}
            onKeyDown={(event) => {
              if (!uploadMutation.isPending && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault()
                fileInputRef.current?.click()
              }
            }}
            onDragEnter={(event) => {
              event.preventDefault()
              if (!uploadMutation.isPending) setIsDragging(true)
            }}
            onDragOver={(event) => {
              event.preventDefault()
              if (!uploadMutation.isPending) event.dataTransfer.dropEffect = 'copy'
            }}
            onDragLeave={(event) => {
              event.preventDefault()
              setIsDragging(false)
            }}
            onDrop={(event) => {
              event.preventDefault()
              setIsDragging(false)
              if (!uploadMutation.isPending) analyzeFile(event.dataTransfer.files?.[0])
            }}
          >
            <div className="mx-auto grid size-9 place-items-center rounded-md border border-border bg-surface font-mono text-lg text-ink" aria-hidden="true">↑</div>
            <p className="mt-3 text-sm text-ink">Drop your resume here</p>
            <p className="mt-1 text-xs text-muted">or click to browse · PDF, MD, TXT, HTML · 5 MB max</p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.md,.txt,.html"
              className="sr-only"
              disabled={uploadMutation.isPending}
              aria-label="Choose resume file"
              onChange={(event) => {
                analyzeFile(event.target.files?.[0])
                event.target.value = ''
              }}
            />
          </div>

          {(pendingFile || resumeQuery.data?.source_file) && (
            <div className="mt-4 overflow-hidden rounded-md border border-border bg-surface">
              <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink">
                    {pendingFile?.name || resumeQuery.data.source_file.filename}
                  </p>
                  <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.06em] text-muted">
                    {pendingFile
                      ? `${formatFileSize(pendingFile.size)} · ${uploadMutation.isPending ? 'processing' : 'pending save'}`
                      : `${formatFileSize(resumeQuery.data.source_file.size_bytes)} · saved source`}
                  </p>
                </div>
                {pendingFile ? (
                  <button
                    type="button"
                    className="text-sm text-muted hover:text-ink disabled:opacity-40"
                    disabled={uploadMutation.isPending}
                    onClick={removePendingFile}
                  >
                    Remove
                  </button>
                ) : (
                  <div className="flex shrink-0 items-center gap-3">
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={downloadMutation.isPending || deleteFileMutation.isPending}
                      onClick={() => downloadMutation.mutate()}
                    >
                      <ButtonLabel pending={downloadMutation.isPending} pendingText="Preparing…">Download original</ButtonLabel>
                    </button>
                    <button
                      type="button"
                      className="text-sm text-muted underline-offset-4 hover:text-ink hover:underline"
                      disabled={deleteFileMutation.isPending}
                      onClick={() => {
                        deleteFileMutation.reset()
                        setShowDeleteFileDialog(true)
                      }}
                    >
                      Remove file
                    </button>
                  </div>
                )}
              </div>

              {uploadMutation.isPending && (
                <div className="border-t border-border px-3 py-3" aria-live="polite">
                  <div className="flex items-center justify-between text-xs text-muted">
                    <span>{uploadPhase === 'analyzing' ? 'Analyzing resume…' : 'Uploading resume…'}</span>
                    <span className="font-mono">{uploadProgress}%</span>
                  </div>
                  <div
                    className="mt-2 h-1.5 overflow-hidden rounded-full bg-border"
                    role="progressbar"
                    aria-label="Resume upload progress"
                    aria-valuemin="0"
                    aria-valuemax="100"
                    aria-valuenow={uploadProgress}
                  >
                    <div
                      className="h-full rounded-full bg-ink transition-[width] duration-200"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                </div>
              )}

            </div>
          )}
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
              resumeMutation.mutate({
                resume: {
                  skills,
                  resume_text: pendingFile
                    ? pendingFileText
                    : (resumeText.trim() || resumeQuery.data?.resume_text || null),
                },
                file: pendingFile,
              })
            }}
            disabled={resumeMutation.isPending || uploadMutation.isPending}
          >
            <ButtonLabel pending={resumeMutation.isPending} pendingText="Saving…">Save resume</ButtonLabel>
          </button>
        </footer>
      </main>

      <Dialog
        open={showDeleteFileDialog}
        title="Remove stored resume file?"
        description="The original uploaded file will be permanently removed. Your extracted skills and resume text will stay saved."
        onClose={() => setShowDeleteFileDialog(false)}
        dismissible={!deleteFileMutation.isPending}
        width="max-w-md"
        footer={(
          <>
            <button className="secondary-button" onClick={() => setShowDeleteFileDialog(false)} disabled={deleteFileMutation.isPending}>Cancel</button>
            <button className="danger-button" onClick={() => deleteFileMutation.mutate()} disabled={deleteFileMutation.isPending}>
              <ButtonLabel pending={deleteFileMutation.isPending} pendingText="Removing…">Remove stored file</ButtonLabel>
            </button>
          </>
        )}
      >
        {deleteFileMutation.error && <InlineAlert>{deleteFileMutation.error.message}</InlineAlert>}
        <p className="text-sm leading-6 text-muted">You can upload and save another original file later.</p>
      </Dialog>
    </div>
  )
}
