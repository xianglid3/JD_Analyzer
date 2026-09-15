import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ButtonLabel, InlineAlert, PageLoader } from '../components/Feedback'
import Dialog from '../components/Dialog'
import NavBar from '../components/NavBar'
import ResumeStructureEditor from '../components/ResumeStructureEditor'
import { EMPTY, READING, READY, REVIEW, STALE, resumeState } from '../lib/resumeReady'
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

function UploadIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M10 13V3m0 0L6.5 6.5M10 3l3.5 3.5M4 12v3.5A1.5 1.5 0 0 0 5.5 17h9a1.5 1.5 0 0 0 1.5-1.5V12" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.25">
      <path d="m5 10.5 3.1 3.1L15.5 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function ResumePage() {
  const [structureOpen, setStructureOpen] = useState(false)
  const queryClient = useQueryClient()
  const [skills, setSkills] = useState([])
  const [skillsDirty, setSkillsDirty] = useState(false)
  const [newSkill, setNewSkill] = useState('')
  const [resumeText, setResumeText] = useState('')
  const [pendingFile, setPendingFile] = useState(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadPhase, setUploadPhase] = useState('idle')
  const [sourceMode, setSourceMode] = useState('upload')
  const [isDragging, setIsDragging] = useState(false)
  const [showDeleteResumeDialog, setShowDeleteResumeDialog] = useState(false)
  const [notice, setNotice] = useState(null)
  const [sourceNotice, setSourceNotice] = useState(null)
  const [skillsNotice, setSkillsNotice] = useState(null)
  const [evidenceNotice, setEvidenceNotice] = useState(null)
  const [extractOnOpen, setExtractOnOpen] = useState(false)
  const fileInputRef = useRef(null)

  const resumeQuery = useQuery({
    queryKey: ['resume'],
    queryFn: () => apiFetch('/resume'),
    retry: false,
  })

  useEffect(() => {
    if (!resumeQuery.data || skillsDirty) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSkills(resumeQuery.data.skills || [])
    setSkillsDirty(false)
  }, [resumeQuery.data, skillsDirty])

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
    onSuccess: (_data, variables) => {
      queryClient.setQueryData(['resume'], (current) => ({
        ...(current || {}),
        skills: variables.resume.skills,
        ...('resume_text' in variables.resume ? { resume_text: variables.resume.resume_text } : {}),
        ...(variables.file ? {
          source_file: { filename: variables.file.name, size_bytes: variables.file.size },
        } : {}),
      }))
      setSkillsDirty(false)
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['resume'] })
      queryClient.invalidateQueries({ queryKey: ['resume-evidence'] })

      if (variables.reason === 'skills') {
        setSkillsNotice({ tone: 'success', message: 'Skill changes saved and match scores refreshed.' })
        return
      }

      setPendingFile(null)
      setResumeText('')
      setUploadPhase('complete')
      setEvidenceNotice(null)
      setExtractOnOpen(true)
      setStructureOpen(true)
      setSourceNotice({
        tone: 'success',
        message: variables.reason === 'upload'
          ? `Resume saved. ${variables.resume.skills.length} skills extracted from ${variables.filename}.`
          : `Resume saved. ${variables.resume.skills.length} skills extracted from the pasted text.`,
      })
    },
    onError: (_error, variables) => {
      if (variables?.reason !== 'skills') setUploadPhase('save_failed')
    },
  })

  const parseMutation = useMutation({
    mutationFn: (text) => apiFetch('/resume/parse', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
    onSuccess: (data) => {
      // a fresh extraction replaces the list rather than merging into it: merging keeps
      // skills from a resume the user has just replaced, and those still score
      setSkills(mergeSkills([], data.skills))
      setSkillsDirty(false)
      setUploadPhase('saving')
      resumeMutation.mutate({
        resume: { skills: mergeSkills([], data.skills), resume_text: data.resume_text },
        file: null,
        reason: 'paste',
      })
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
      const extractedSkills = mergeSkills([], data.skills)
      setSkills(extractedSkills)
      setSkillsDirty(false)
      setResumeText('')
      setPendingFile(file)
      setUploadProgress(100)
      setUploadPhase('saving')
      resumeMutation.mutate({
        resume: { skills: extractedSkills, resume_text: data.resume_text.trim() },
        file,
        reason: 'upload',
        filename: file.name,
      })
    },
    onError: () => {
      setPendingFile(null)
      setUploadProgress(0)
      setUploadPhase('idle')
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

  const deleteResumeMutation = useMutation({
    mutationFn: () => apiFetch('/resume', { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.setQueryData(['resume'], null)
      queryClient.setQueryData(['resume-evidence'], {
        header: {}, entries: [], stale: false, source_hash: null,
      })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      setSkills([])
      setSkillsDirty(false)
      setResumeText('')
      setPendingFile(null)
      setUploadProgress(0)
      setUploadPhase('idle')
      setSourceMode('upload')
      setStructureOpen(false)
      setExtractOnOpen(false)
      setSourceNotice(null)
      setSkillsNotice(null)
      setEvidenceNotice(null)
      setShowDeleteResumeDialog(false)
      setNotice({ tone: 'success', message: 'Resume and all extracted data removed.' })
      if (fileInputRef.current) fileInputRef.current.value = ''
    },
  })

  function addSkill(event) {
    event?.preventDefault()
    if (!newSkill.trim()) return
    const nextSkills = mergeSkills(skills, [newSkill])
    if (nextSkills.length === skills.length) {
      setNewSkill('')
      return
    }
    setSkills(nextSkills)
    setSkillsDirty(true)
    setSkillsNotice(null)
    setNewSkill('')
    setNotice(null)
    resumeMutation.reset()
  }

  function removeSkill(skill) {
    setSkills((current) => current.filter((value) => value !== skill))
    setSkillsDirty(true)
    setSkillsNotice(null)
    setNotice(null)
    resumeMutation.reset()
  }

  function analyzeFile(file) {
    if (!file) return

    const extension = file.name.split('.').pop()?.toLocaleLowerCase()
    if (!SUPPORTED_EXTENSIONS.includes(extension)) {
        setSourceNotice({ tone: 'error', message: 'Choose a PDF, MD, TXT, or HTML file.' })
      return
    }
    if (file.size > MAX_FILE_SIZE) {
      setSourceNotice({ tone: 'error', message: 'File must be 5 MB or smaller.' })
      return
    }

    parseMutation.reset()
    resumeMutation.reset()
    uploadMutation.reset()
    setNotice(null)
    setSourceNotice(null)
    setPendingFile(file)
    setUploadProgress(0)
    setUploadPhase('uploading')
    uploadMutation.mutate(file)
  }

  function removePendingFile() {
    setPendingFile(null)
    setUploadProgress(0)
    setUploadPhase('idle')
    uploadMutation.reset()
    resumeMutation.reset()
    setNotice(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const openExtractionReview = useCallback(() => {
    setEvidenceNotice(null)
    setExtractOnOpen(true)
    setStructureOpen(true)
  }, [])

  const closeStructureReview = useCallback(() => {
    setStructureOpen(false)
    setExtractOnOpen(false)
  }, [])

  const evidenceQuery = useQuery({
    queryKey: ['resume-evidence'],
    queryFn: () => apiFetch('/resume/evidence'),
    retry: false,
  })

  // Safety net for resumes saved before extraction became part of upload. Open the same
  // confirmation flow, but never persist model-extracted evidence without the user's review.
  const evidenceReady = !evidenceQuery.isLoading && !evidenceQuery.isError
  const needsReview = Boolean(
    (resumeQuery.data?.resume_text || resumeQuery.data?.source_file)
    && evidenceReady
    && (evidenceQuery.data?.entries || []).length === 0
  )
  useEffect(() => {
    if (!needsReview) return undefined
    const timer = window.setTimeout(openExtractionReview, 0)
    return () => window.clearTimeout(timer)
  }, [needsReview, openExtractionReview])

  if (resumeQuery.isLoading) return <PageLoader label="Loading your resume…" />

  const resumeLoadError = resumeQuery.isError && resumeQuery.error?.status !== 404
  const entries = evidenceQuery.data?.entries || []
  const entryCount = entries.length
  const evidenceCount = entries.reduce((total, entry) => total + entry.bullets.length, 0)
  const evidenceStale = Boolean(evidenceQuery.data?.stale)
  const hasResume = Boolean(resumeQuery.data?.resume_text || resumeQuery.data?.source_file)
  const hasSavedResumeData = Boolean(resumeQuery.data || skills.length > 0 || evidenceCount > 0)
  const sourceBusy = parseMutation.isPending || uploadMutation.isPending || deleteResumeMutation.isPending || (resumeMutation.isPending && resumeMutation.variables?.reason !== 'skills')
  const sourceLocked = sourceBusy || uploadPhase === 'save_failed'
  const skillsReady = skills.length > 0 && !skillsDirty && !sourceLocked

  const readState = resumeState({
    hasResume,
    bulletCount: evidenceCount,
    stale: evidenceStale,
    reading: structureOpen && extractOnOpen,
  })

  const sourceError = parseMutation.error || uploadMutation.error || (resumeMutation.variables?.reason !== 'skills' ? resumeMutation.error : null)
  const activeError = downloadMutation.error || deleteResumeMutation.error

  return (
    <div className="app-main min-h-screen bg-surface">
      <NavBar />
      <main className="page-container animate-page-in">
        <header className="mb-8 border-b border-border pb-8">
          <p className="eyebrow">Profile</p>
          <h1 className="page-heading mt-2">Your resume</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Keep one trusted source, check the skills used for matching, then review the experience
            used for tailoring suggestions.
          </p>
        </header>

        {resumeLoadError && (
          <InlineAlert className="mb-4">
            Could not load your saved resume. <button className="ml-1 underline" onClick={() => resumeQuery.refetch()}>Try again</button>
          </InlineAlert>
        )}
        {activeError && <InlineAlert className="mb-4">{activeError.message}</InlineAlert>}
        {notice && !activeError && <InlineAlert tone={notice.tone} className="mb-4">{notice.message}</InlineAlert>}

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
          <div className="min-w-0 space-y-6">
        <section className="surface-card overflow-hidden" aria-labelledby="extract-heading">
          <div className="border-b border-border p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p className="eyebrow">Source</p>
                <h2 id="extract-heading" className="mt-2 text-base font-medium text-ink">Add or replace your resume</h2>
                <p className="mt-1 text-sm text-muted">Add a file or paste text. We save the source, extract the details, then open them for confirmation.</p>
              </div>
              {hasSavedResumeData && (
                <button
                  type="button"
                  className="shrink-0 text-sm text-muted underline-offset-4 hover:text-ink hover:underline"
                  disabled={sourceBusy}
                  onClick={() => {
                    deleteResumeMutation.reset()
                    setShowDeleteResumeDialog(true)
                  }}
                >
                  Remove resume
                </button>
              )}
            </div>

            <div className="mt-4 inline-flex rounded-md border border-border bg-surface p-1" role="group" aria-label="Resume input method">
              <button
                type="button"
                aria-pressed={sourceMode === 'upload'}
                disabled={sourceBusy}
                onClick={() => setSourceMode('upload')}
                className={`min-h-11 rounded-md px-4 text-sm ${sourceMode === 'upload' ? 'bg-obsidian text-white' : 'text-charcoal hover:text-ink'}`}
              >
                Upload file
              </button>
              <button
                type="button"
                aria-pressed={sourceMode === 'paste'}
                disabled={sourceBusy}
                onClick={() => setSourceMode('paste')}
                className={`min-h-11 rounded-md px-4 text-sm ${sourceMode === 'paste' ? 'bg-obsidian text-white' : 'text-charcoal hover:text-ink'}`}
              >
                Paste text
              </button>
            </div>
          </div>

          <div className={sourceMode === 'paste' ? 'p-5' : 'hidden'} aria-label="Paste resume text">
          <textarea
            id="resume-text"
            value={resumeText}
            disabled={sourceBusy}
            onChange={(event) => {
              setResumeText(event.target.value)
              if (pendingFile) removePendingFile()
              parseMutation.reset()
              resumeMutation.reset()
              setUploadPhase('idle')
              setNotice(null)
              setSourceNotice(null)
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
              disabled={sourceBusy || resumeText.trim().length < 100 || resumeText.trim().length > 20000}
              onClick={() => {
                uploadMutation.reset()
                resumeMutation.reset()
                setPendingFile(null)
                parseMutation.mutate(resumeText)
              }}
            >
              <ButtonLabel
                pending={sourceBusy}
                pendingText={parseMutation.isPending ? 'Extracting…' : 'Saving…'}
              >
                Analyze and save
              </ButtonLabel>
            </button>
          </div>
          </div>

          <div className={sourceMode === 'upload' ? 'p-5' : 'hidden'} aria-label="Upload resume file">
          <div
            role="button"
            tabIndex={sourceBusy ? -1 : 0}
            aria-label="Upload resume file"
            aria-disabled={sourceBusy}
            className={`rounded-md border border-dashed p-8 text-center transition-colors ${
              isDragging
                ? 'border-ink bg-surface'
                : 'border-border bg-soft-paper hover:border-ash hover:bg-surface'
            } ${sourceBusy ? 'cursor-wait opacity-70' : 'cursor-pointer'}`}
            onClick={() => !sourceBusy && fileInputRef.current?.click()}
            onKeyDown={(event) => {
              if (!sourceBusy && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault()
                fileInputRef.current?.click()
              }
            }}
            onDragEnter={(event) => {
              event.preventDefault()
              if (!sourceBusy) setIsDragging(true)
            }}
            onDragOver={(event) => {
              event.preventDefault()
              if (!sourceBusy) event.dataTransfer.dropEffect = 'copy'
            }}
            onDragLeave={(event) => {
              event.preventDefault()
              setIsDragging(false)
            }}
            onDrop={(event) => {
              event.preventDefault()
              setIsDragging(false)
              if (!sourceBusy) analyzeFile(event.dataTransfer.files?.[0])
            }}
          >
            <div className="mx-auto grid size-11 place-items-center rounded-md border border-border bg-surface text-ink"><UploadIcon /></div>
            <p className="mt-3 text-sm text-ink">Drop your resume here</p>
            <p className="mt-1 text-xs text-muted">or click to browse · PDF, MD, TXT, HTML · 5 MB max</p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.md,.txt,.html"
              className="sr-only"
              disabled={sourceBusy}
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
                      ? `${formatFileSize(pendingFile.size)} · ${uploadPhase === 'save_failed' ? 'save failed' : 'processing'}`
                      : `${formatFileSize(resumeQuery.data.source_file.size_bytes)} · saved source`}
                  </p>
                </div>
                {pendingFile ? (
                  <button
                    type="button"
                    className="text-sm text-muted hover:text-ink disabled:opacity-40"
                    disabled={sourceBusy}
                    onClick={removePendingFile}
                  >
                    Remove
                  </button>
                ) : (
                  <div className="flex shrink-0 items-center gap-3">
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={downloadMutation.isPending || deleteResumeMutation.isPending}
                      onClick={() => downloadMutation.mutate()}
                    >
                      <ButtonLabel pending={downloadMutation.isPending} pendingText="Preparing…">Download original</ButtonLabel>
                    </button>
                  </div>
                )}
              </div>

              {pendingFile && sourceBusy && (
                <div className="border-t border-border px-3 py-3" aria-live="polite">
                  <div className="flex items-center justify-between text-xs text-muted">
                    <span>{uploadPhase === 'analyzing' ? 'Analyzing resume…' : uploadPhase === 'saving' ? 'Saving resume…' : 'Uploading resume…'}</span>
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

          </div>
          {(sourceError || sourceNotice) && (
            <div className="border-t border-border p-5">
              {sourceError ? (
                <InlineAlert>
                  {sourceError.message}
                  {resumeMutation.isError && resumeMutation.variables?.reason !== 'skills' && (
                    <button
                      type="button"
                      className="ml-2 underline underline-offset-4"
                      onClick={() => resumeMutation.mutate(resumeMutation.variables)}
                    >
                      Retry save
                    </button>
                  )}
                </InlineAlert>
              ) : (
                <InlineAlert tone={sourceNotice.tone}>{sourceNotice.message}</InlineAlert>
              )}
            </div>
          )}
        </section>

        <section className="surface-card p-5" aria-labelledby="skills-heading">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="eyebrow">Matching input</p>
              <h2 id="skills-heading" className="mt-2 text-base font-medium text-ink">Review extracted skills</h2>
              <p className="mt-1 text-xs text-muted">Used for match scores. Up to 100, duplicates removed.</p>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="rounded-full border border-border px-2.5 py-1 text-xs text-muted">{skills.length} / 100</span>
              {skills.length > 0 && (
                <button
                  type="button"
                  className="text-xs text-muted underline-offset-4 hover:text-ink hover:underline disabled:opacity-40"
                  disabled={sourceLocked}
                  onClick={() => {
                    setSkills([])
                    setSkillsDirty(true)
                    setSkillsNotice(null)
                    setNotice(null)
                    resumeMutation.reset()
                  }}
                >
                  Clear all
                </button>
              )}
            </div>
          </div>

          <form onSubmit={addSkill} className="mt-4 flex gap-2">
            <label className="sr-only" htmlFor="new-skill">Add a skill</label>
            <input
              id="new-skill"
              value={newSkill}
              maxLength={100}
              disabled={sourceLocked}
              onChange={(event) => setNewSkill(event.target.value)}
              placeholder="Add a skill, e.g. Python"
              className="control min-w-0 flex-1 px-3 py-2.5 text-sm"
            />
            <button type="submit" className="secondary-button" disabled={sourceLocked || !newSkill.trim() || skills.length >= 100}>Add</button>
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
                    disabled={sourceLocked}
                    onClick={() => removeSkill(skill)}
                    aria-label={`Remove ${skill}`}
                    className="text-base leading-none text-muted hover:text-ink disabled:opacity-40"
                  >×</button>
                </span>
              ))}
            </div>
          )}

          {(skillsDirty || skillsNotice || (resumeMutation.isError && resumeMutation.variables?.reason === 'skills')) && (
            <div className="mt-4 flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 flex-1">
                {resumeMutation.isError && resumeMutation.variables?.reason === 'skills' ? (
                  <InlineAlert>{resumeMutation.error.message}</InlineAlert>
                ) : skillsNotice ? (
                  <InlineAlert tone={skillsNotice.tone}>{skillsNotice.message}</InlineAlert>
                ) : (
                  <p className="text-xs leading-5 text-muted">Save these edits to refresh every job’s match score.</p>
                )}
              </div>
              {skillsDirty && (
                <button
                  type="button"
                  className="primary-button compact-button shrink-0"
                  disabled={resumeMutation.isPending}
                  onClick={() => resumeMutation.mutate({ resume: { skills }, file: null, reason: 'skills' })}
                >
                  <ButtonLabel pending={resumeMutation.isPending} pendingText="Saving…">Save skill changes</ButtonLabel>
                </button>
              )}
            </div>
          )}
        </section>

        <section className="surface-card p-5" aria-labelledby="evidence-heading">
          <p className="eyebrow">Tailoring source</p>
          <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 id="evidence-heading" className="text-base font-medium text-ink">
                {{
                  [EMPTY]: 'No resume yet',
                  [READING]: 'Reading your experience…',
                  [REVIEW]: 'Confirm your experience',
                  [READY]: 'Resume ready',
                  [STALE]: 'Resume changed',
                }[readState]}
              </h2>
              <p className="mt-1 max-w-xl text-xs leading-5 text-muted">
                {{
                  [EMPTY]: 'Add a file or paste your resume above. Everything else happens on its own.',
                  [READING]: 'Pulling out your jobs, projects and education for you to check.',
                  [REVIEW]: 'Review the extracted draft, correct anything wrong, then confirm it before tailoring.',
                  [READY]: `${entryCount} ${entryCount === 1 ? 'entry' : 'entries'} · ${evidenceCount} ${evidenceCount === 1 ? 'bullet' : 'bullets'} · ${skills.length} skills. These are the claims tailoring is allowed to cite.`,
                  [STALE]: 'Your resume changed after this was confirmed. Re-read and confirm it so every tailored claim matches the current source.',
                }[readState]}
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              {readState === READY && (
                <button
                  className="secondary-button compact-button whitespace-nowrap"
                  disabled={sourceLocked}
                  onClick={() => {
                    setEvidenceNotice(null)
                    setExtractOnOpen(false)
                    setStructureOpen(true)
                  }}
                >
                  Review extracted info
                </button>
              )}
              {(readState === REVIEW || readState === STALE) && (
                <button
                  className="secondary-button compact-button whitespace-nowrap"
                  disabled={sourceLocked}
                  onClick={openExtractionReview}
                >
                  {readState === STALE ? 'Re-read and confirm' : 'Review and confirm'}
                </button>
              )}
            </div>
          </div>
          {evidenceNotice && (
            <InlineAlert tone={evidenceNotice.tone} className="mt-4">{evidenceNotice.message}</InlineAlert>
          )}
        </section>
          </div>

          <aside className="surface-card overflow-hidden lg:sticky lg:top-20" aria-labelledby="readiness-heading">
            <div className="border-b border-border p-5">
              <p className="eyebrow">Setup status</p>
              <h2 id="readiness-heading" className="mt-2 text-base font-medium text-ink">Resume readiness</h2>
              <p className="mt-1 text-xs leading-5 text-muted">Complete these once, then keep them current when your resume changes.</p>
            </div>

            <ol className="px-5">
              {[
                ['1', 'Add your resume', 'File or pasted text', hasResume],
                ['2', 'Check your skills', 'Used for match scores', skillsReady],
                ['3', 'Pull out your experience', 'Used as tailoring evidence', evidenceCount > 0 && !evidenceStale],
              ].map(([step, title, detail, done], index, steps) => {
                const nextDone = index < steps.length - 1 && done && steps[index + 1][3]
                return (
                  <li key={step} className="relative flex gap-3 py-4">
                    {index < steps.length - 1 && (
                      <span
                        aria-hidden="true"
                        data-testid={`readiness-connector-${index + 1}`}
                        data-complete={nextDone ? 'true' : 'false'}
                        className="absolute -bottom-4 left-[15px] top-12 w-px overflow-hidden bg-border"
                      >
                        <span
                          className={`block h-full origin-top bg-obsidian transition-transform duration-500 ease-out motion-reduce:transition-none ${
                            nextDone ? 'scale-y-100' : 'scale-y-0'
                          }`}
                        />
                      </span>
                    )}
                    <span className={`relative z-10 grid size-8 shrink-0 place-items-center rounded-full border font-mono text-xs transition-colors duration-300 motion-reduce:transition-none ${done ? 'border-obsidian bg-obsidian text-white' : 'border-border bg-surface text-muted'}`} aria-hidden="true">
                      {done ? <CheckIcon /> : step}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm text-ink">{title}</p>
                      <p className="mt-0.5 text-xs text-muted">{detail}</p>
                      <p className="sr-only">{done ? '✓ done' : `step ${step}`}</p>
                    </div>
                  </li>
                )
              })}
            </ol>

            <div className="border-t border-border p-5">
              <p className="text-xs leading-5 text-muted">Resume uploads save automatically. Any later skill edits are saved beside the skill list.</p>
            </div>
          </aside>
        </div>
      </main>

      <Dialog
        open={structureOpen}
        title={extractOnOpen ? 'Review extracted experience' : 'Review experience'}
        description={extractOnOpen
          ? 'We will extract a draft first. Nothing becomes tailoring evidence until you confirm it.'
          : 'Check each job, project, and bullet used as tailoring evidence.'}
        onClose={closeStructureReview}
        dismissible={!extractOnOpen}
        width="max-w-4xl"
      >
        <ResumeStructureEditor
          autoExtract={extractOnOpen}
          onClose={closeStructureReview}
          onSaved={() => {
            closeStructureReview()
            setEvidenceNotice({ tone: 'success', message: 'Experience confirmed. Tailoring can now cite these bullets.' })
            queryClient.invalidateQueries({ queryKey: ['resume-evidence'] })
            queryClient.invalidateQueries({ queryKey: ['jobs'] })
          }}
        />
      </Dialog>

      <Dialog
        open={showDeleteResumeDialog}
        title="Remove your resume?"
        description="This permanently removes the original file, extracted text, skills, experience evidence, and tailoring history. Your tracked jobs remain, but their match scores will reset."
        onClose={() => setShowDeleteResumeDialog(false)}
        dismissible={!deleteResumeMutation.isPending}
        width="max-w-md"
        footer={(
          <>
            <button className="secondary-button" onClick={() => setShowDeleteResumeDialog(false)} disabled={deleteResumeMutation.isPending}>Cancel</button>
            <button className="danger-button" onClick={() => deleteResumeMutation.mutate()} disabled={deleteResumeMutation.isPending}>
              <ButtonLabel pending={deleteResumeMutation.isPending} pendingText="Removing…">Remove resume</ButtonLabel>
            </button>
          </>
        )}
      >
        {deleteResumeMutation.error && <InlineAlert>{deleteResumeMutation.error.message}</InlineAlert>}
        <p className="text-sm leading-6 text-muted">To update your resume without clearing everything, upload its replacement instead.</p>
      </Dialog>
    </div>
  )
}
