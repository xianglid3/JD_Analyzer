import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ButtonLabel, InlineAlert, Spinner } from './Feedback'
import SelectMenu from './SelectMenu'
import { apiFetch } from '../lib/api'

const kindOptions = [
  ['Experience', 'experience'],
  ['Project', 'project'],
  ['Education', 'education'],
  ['Certificate', 'certificate'],
]

const emptyHeader = () => ({ full_name: '', email: '', phone: '', location: '', links: '' })

const toEditableHeader = (header) => ({
  full_name: header?.full_name || '',
  email: header?.email || '',
  phone: header?.phone || '',
  location: header?.location || '',
  links: (header?.links || []).join(', '),
})

const emptyEntry = () => ({
  kind: 'experience',
  organization: '',
  title: '',
  location: '',
  start_date: '',
  end_date: '',
  bullets: [{ id: null, text: '' }],
})

// saved bullets arrive as {id, text}, freshly extracted ones as strings. The id rides along
// so an edited bullet keeps its identity; the server checks it belongs to this user.
const toEditable = (entries = []) => entries.map((entry) => ({
  kind: entry.kind || 'experience',
  organization: entry.organization || '',
  title: entry.title || '',
  location: entry.location || '',
  start_date: entry.start_date || '',
  end_date: entry.end_date || '',
  bullets: (entry.bullets || []).map((bullet) => (
    typeof bullet === 'string' ? { id: null, text: bullet } : { id: bullet.id, text: bullet.text }
  )),
}))

export default function ResumeStructureEditor({ autoExtract = false, onClose, onSaved }) {
  const queryClient = useQueryClient()
  const [header, setHeader] = useState(emptyHeader())
  const [entries, setEntries] = useState([])
  const [dirty, setDirty] = useState(false)
  const [saved, setSaved] = useState(false)
  // stamped by the extraction this evidence came from, so saving can't bless stale text
  const [sourceHash, setSourceHash] = useState(null)
  const autoExtractStarted = useRef(false)
  const extractController = useRef(null)
  const extractRequestId = useRef(0)
  // Automatic extraction owns the first view of the modal, so there is no flash of the old
  // evidence form between the evidence query resolving and the extraction effect starting.
  const [extracting, setExtracting] = useState(autoExtract)
  const [extractError, setExtractError] = useState(null)
  // Cancel invalidates the active request and clears its error, so its confirmation is
  // held separately from the request state.
  const [canceled, setCanceled] = useState(false)

  const evidenceQuery = useQuery({
    queryKey: ['resume-evidence'],
    queryFn: () => apiFetch('/resume/evidence'),
    retry: false,
  })

  useEffect(() => {
    if (!evidenceQuery.data || dirty) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEntries(toEditable(evidenceQuery.data.entries))
    setHeader(toEditableHeader(evidenceQuery.data.header))
    // carry the stamp forward: editing evidence is not re-extracting it
    setSourceHash(evidenceQuery.data.source_hash || null)
  }, [evidenceQuery.data, dirty])

  const runExtraction = useCallback(async () => {
    const requestId = extractRequestId.current + 1
    extractRequestId.current = requestId
    extractController.current?.abort()

    const controller = new AbortController()
    extractController.current = controller
    setCanceled(false)
    setExtractError(null)
    setExtracting(true)

    try {
      const data = await apiFetch('/resume/structure', {
        method: 'POST',
        body: JSON.stringify({}),
        timeoutMs: 50000,
        signal: controller.signal,
      })

      // A canceled or superseded request is no longer allowed to write into the editor,
      // even if its promise finishes afterward.
      if (extractRequestId.current !== requestId) return
      setEntries(toEditable(data.entries))
      setHeader(toEditableHeader(data.header))
      setSourceHash(data.source_hash || null)
      setDirty(true)
      setSaved(false)
    } catch (error) {
      if (extractRequestId.current === requestId) setExtractError(error)
    } finally {
      // The same request that turned the spinner on is responsible for turning it off.
      // This no longer depends on React Query's mutation observer surviving a live HMR,
      // StrictMode replay, or component lifecycle transition.
      if (extractRequestId.current === requestId) {
        extractController.current = null
        setExtracting(false)
      }
    }
  }, [])

  const cancelExtraction = useCallback(() => {
    // Invalidate the request before aborting so a late response cannot write stale data or
    // turn a newer request's spinner off.
    extractRequestId.current += 1
    extractController.current?.abort()
    extractController.current = null
    setExtracting(false)
    setExtractError(null)
    setCanceled(true)
  }, [])

  // Deliberately no abort-on-unmount. The model call is already paid for by the time the
  // component goes away, so cancelling only throws away a result we bought — and in dev
  // StrictMode, where React mounts, unmounts and remounts, it aborted the very request
  // auto-extract had just started, leaving the UI waiting on a request nobody was running.
  // Cancelling stays an explicit user action.

  useEffect(() => {
    if (!autoExtract || evidenceQuery.isLoading || autoExtractStarted.current) return
    autoExtractStarted.current = true
    runExtraction()
  }, [autoExtract, evidenceQuery.isLoading, runExtraction])

  const saveMutation = useMutation({
    mutationFn: () => apiFetch('/resume/evidence', {
      method: 'PUT',
      body: JSON.stringify({
        source_hash: sourceHash,
        header: {
          full_name: header.full_name.trim() || null,
          email: header.email.trim() || null,
          phone: header.phone.trim() || null,
          location: header.location.trim() || null,
          links: header.links.split(',').map((link) => link.trim()).filter(Boolean),
        },
        entries: entries.map((entry) => ({
          kind: entry.kind,
          organization: entry.organization.trim() || null,
          title: entry.title.trim() || null,
          location: entry.location.trim() || null,
          start_date: entry.start_date.trim() || null,
          end_date: entry.end_date.trim() || null,
          bullets: entry.bullets
            .map((bullet) => ({ id: bullet.id, text: bullet.text.trim() }))
            .filter((bullet) => bullet.text),
        })),
      }),
    }),
    onSuccess: (data) => {
      setEntries(toEditable(data.entries))
      setHeader(toEditableHeader(data.header))
      setDirty(false)
      setSaved(true)
      queryClient.invalidateQueries({ queryKey: ['resume-evidence'] })
      onSaved?.(data)
    },
  })

  const updateHeader = (patch) => {
    setHeader((current) => ({ ...current, ...patch }))
    setDirty(true)
    setSaved(false)
  }

  const updateEntry = (index, patch) => {
    setEntries((current) => current.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)))
    setDirty(true)
    setSaved(false)
  }

  const updateBullet = (entryIndex, bulletIndex, value) => {
    updateEntry(entryIndex, {
      bullets: entries[entryIndex].bullets.map((bullet, i) => (
        i === bulletIndex ? { ...bullet, text: value } : bullet
      )),
    })
  }

  const addBullet = (entryIndex) => {
    updateEntry(entryIndex, { bullets: [...entries[entryIndex].bullets, { id: null, text: '' }] })
  }

  const removeBullet = (entryIndex, bulletIndex) => {
    updateEntry(entryIndex, { bullets: entries[entryIndex].bullets.filter((_, i) => i !== bulletIndex) })
  }

  const addEntry = () => {
    setEntries((current) => [...current, emptyEntry()])
    setDirty(true)
    setSaved(false)
  }

  const removeEntry = (index) => {
    setEntries((current) => current.filter((_, i) => i !== index))
    setDirty(true)
    setSaved(false)
  }

  if (evidenceQuery.isLoading) {
    return (
      <div className="grid min-h-40 place-items-center text-sm text-muted">
        <span className="inline-flex items-center gap-3"><Spinner /> Loading resume evidence…</span>
      </div>
    )
  }

  const busy = extracting || saveMutation.isPending
  const extractionError = extractError || evidenceQuery.error
  const bulletCount = entries.reduce((total, entry) => total + entry.bullets.filter((b) => b.text.trim()).length, 0)

  if (extracting) {
    return (
      <div className="grid min-h-64 place-items-center text-center">
        <div>
          <span className="inline-flex items-center gap-3 text-sm text-ink" role="status">
            <Spinner /> Reading your resume…
          </span>
          <p className="mt-3 text-xs text-muted">Pulling out your jobs, projects, education, and bullets.</p>
          {extracting && (
            <button
              type="button"
              className="mt-4 min-h-11 px-3 text-xs text-muted underline-offset-4 hover:text-ink hover:underline"
              onClick={cancelExtraction}
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="resume-structure-editor space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted">
          {entries.length
            ? `${entries.length} ${entries.length === 1 ? 'entry' : 'entries'} · ${bulletCount} ${bulletCount === 1 ? 'bullet' : 'bullets'}`
            : 'Nothing structured yet.'}
        </p>
        <div className="flex items-center gap-2">
          <button className="secondary-button whitespace-nowrap" disabled={busy} onClick={runExtraction}>
            <ButtonLabel pending={extracting} pendingText="Reading your resume…">
              {entries.length ? 'Re-extract from resume' : 'Extract from resume'}
            </ButtonLabel>
          </button>
          {extracting && (
            <button
              type="button"
              className="min-h-11 px-2 text-xs text-muted underline-offset-4 hover:text-ink hover:underline"
              onClick={cancelExtraction}
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      {evidenceQuery.data?.stale && !dirty && (
        <InlineAlert>
          Your resume changed after this was extracted. Re-extract so tailoring quotes what you actually
          have — until then it will refuse to run.
        </InlineAlert>
      )}
      {canceled && <InlineAlert>Extraction canceled.</InlineAlert>}
      {extractionError && <InlineAlert>{extractionError.message}</InlineAlert>}
      {extracting && (
        <p className="text-xs text-muted" role="status">This can take up to 50 seconds. You can cancel and retry safely.</p>
      )}

      <section className="rounded-md border border-border bg-pure-white p-4">
        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">Contact details</p>
        <p className="mt-1 text-xs text-muted">Used as the header when a tailored resume is generated.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="block text-xs text-muted">
            Full name
            <input
              value={header.full_name}
              maxLength={200}
              onChange={(event) => updateHeader({ full_name: event.target.value })}
              className="control mt-1.5 px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="block text-xs text-muted">
            Email
            <input
              value={header.email}
              maxLength={200}
              onChange={(event) => updateHeader({ email: event.target.value })}
              className="control mt-1.5 px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="block text-xs text-muted">
            Phone
            <input
              value={header.phone}
              maxLength={200}
              onChange={(event) => updateHeader({ phone: event.target.value })}
              className="control mt-1.5 px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="block text-xs text-muted">
            Location
            <input
              value={header.location}
              maxLength={200}
              onChange={(event) => updateHeader({ location: event.target.value })}
              className="control mt-1.5 px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="block text-xs text-muted sm:col-span-2">
            Links <span className="text-muted">(comma separated)</span>
            <input
              value={header.links}
              onChange={(event) => updateHeader({ links: event.target.value })}
              placeholder="github.com/you, linkedin.com/in/you"
              className="control mt-1.5 px-3 py-2 text-sm text-ink"
            />
          </label>
        </div>
      </section>

      {entries.length === 0 && (
        <div className="rounded-md border border-dashed border-border px-6 py-10 text-center">
          <p className="text-sm text-ink">Pull your experience out of the resume you saved</p>
          <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-muted">
            Each bullet becomes something the tailoring agent can cite. Extract them, fix anything wrong,
            and save.
          </p>
          <button className="secondary-button mt-4" onClick={addEntry}>Add one by hand</button>
        </div>
      )}

      <div className="space-y-4">
        {entries.map((entry, entryIndex) => (
          <section key={entryIndex} className="rounded-md border border-border bg-pure-white p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="grid flex-1 gap-3 sm:grid-cols-2">
                <div className="text-xs text-muted">
                  <p>Type</p>
                  <SelectMenu
                    className="mt-1.5"
                    ariaLabel={`Type for entry ${entryIndex + 1}`}
                    value={entry.kind}
                    onChange={(nextKind) => updateEntry(entryIndex, { kind: nextKind })}
                    options={kindOptions.map(([label, optionValue]) => ({ label, value: optionValue }))}
                  />
                </div>
                <label className="block text-xs text-muted">
                  Organization
                  <input
                    value={entry.organization}
                    maxLength={200}
                    placeholder="Company, school, or issuer"
                    onChange={(event) => updateEntry(entryIndex, { organization: event.target.value })}
                    className="control mt-1.5 px-3 py-2 text-sm text-ink"
                  />
                </label>
                <label className="block text-xs text-muted">
                  Title
                  <input
                    value={entry.title}
                    maxLength={200}
                    placeholder="Role, degree, or project name"
                    onChange={(event) => updateEntry(entryIndex, { title: event.target.value })}
                    className="control mt-1.5 px-3 py-2 text-sm text-ink"
                  />
                </label>
                <label className="block text-xs text-muted">
                  Location
                  <input
                    value={entry.location}
                    maxLength={200}
                    onChange={(event) => updateEntry(entryIndex, { location: event.target.value })}
                    className="control mt-1.5 px-3 py-2 text-sm text-ink"
                  />
                </label>
                <label className="block text-xs text-muted">
                  Start
                  <input
                    value={entry.start_date}
                    maxLength={200}
                    placeholder="Jun 2024"
                    onChange={(event) => updateEntry(entryIndex, { start_date: event.target.value })}
                    className="control mt-1.5 px-3 py-2 text-sm text-ink"
                  />
                </label>
                <label className="block text-xs text-muted">
                  End
                  <input
                    value={entry.end_date}
                    maxLength={200}
                    placeholder="Present"
                    onChange={(event) => updateEntry(entryIndex, { end_date: event.target.value })}
                    className="control mt-1.5 px-3 py-2 text-sm text-ink"
                  />
                </label>
              </div>
              <button
                className="text-xs text-muted hover:text-ink"
                onClick={() => removeEntry(entryIndex)}
                aria-label={`Remove entry ${entryIndex + 1}`}
              >
                Remove
              </button>
            </div>

            <div className="mt-4 border-t border-border pt-3">
              <div className="space-y-2">
                {entry.bullets.map((bullet, bulletIndex) => (
                  <div key={bulletIndex} className="flex items-start gap-2">
                    <textarea
                      value={bullet.text}
                      rows={2}
                      maxLength={500}
                      placeholder="One accomplishment, in your own words"
                      onChange={(event) => updateBullet(entryIndex, bulletIndex, event.target.value)}
                      className="control flex-1 px-3 py-2 text-sm text-ink"
                      aria-label={`Entry ${entryIndex + 1} bullet ${bulletIndex + 1}`}
                    />
                    <button
                      className="mt-1 text-sm text-muted hover:text-ink"
                      onClick={() => removeBullet(entryIndex, bulletIndex)}
                      aria-label={`Remove bullet ${bulletIndex + 1} from entry ${entryIndex + 1}`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              <button className="mt-2 text-xs text-muted hover:text-ink" onClick={() => addBullet(entryIndex)}>
                + Add bullet
              </button>
            </div>
          </section>
        ))}
      </div>

      {saveMutation.error && <InlineAlert>{saveMutation.error.message}</InlineAlert>}
      {saved && !dirty && <InlineAlert tone="success">Saved. Tailoring can now cite these bullets.</InlineAlert>}

      <div className="sticky -bottom-5 z-10 -mx-5 flex flex-col-reverse gap-2 border-t border-border bg-soft-paper px-5 pb-1 pt-4 sm:flex-row sm:justify-between">
        <button className="secondary-button" disabled={busy} onClick={addEntry}>Add entry</button>
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <button className="secondary-button" disabled={busy} onClick={onClose}>{autoExtract ? 'Cancel' : 'Close'}</button>
          <button className="primary-button min-w-36" disabled={busy || !dirty} onClick={() => saveMutation.mutate()}>
            <ButtonLabel pending={saveMutation.isPending} pendingText="Saving…">
              {autoExtract ? 'Confirm experience' : 'Save experience'}
            </ButtonLabel>
          </button>
        </div>
      </div>
    </div>
  )
}
