import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, apiUpload } from '../../src/lib/api'
import { renderWithProviders } from '../helpers/render'
import ResumePage from '../../src/pages/ResumePage'

vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn(), apiUpload: vi.fn() }))

const extractedText = 'Backend engineer with Python, React, and PostgreSQL experience. '.repeat(2)

describe('ResumePage', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiUpload.mockReset()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume' && options?.method === 'PUT') return Promise.resolve({})
      if (path === '/resume/evidence' && options?.method === 'PUT') {
        return Promise.resolve({ header: {}, entries: [] })
      }
      if (path === '/resume/evidence') {
        return Promise.resolve({
          header: {},
          entries: [{ id: 'old-entry', kind: 'project', bullets: [{ id: 'old-bullet', text: 'Old evidence' }] }],
        })
      }
      if (path === '/resume/structure') {
        return Promise.resolve({
          header: { full_name: 'Shawn Li', links: [] },
          entries: [{ kind: 'project', title: 'Tracker', bullets: ['Built the tracker'] }],
          source_hash: 'new-source-hash',
        })
      }
      if (path === '/resume') return Promise.resolve({ skills: ['Python'], resume_text: 'Saved resume text', source_file: null })
      return Promise.resolve({ skills: [] })
    })
    apiUpload.mockImplementation((_path, _form, onProgress) => {
      onProgress(50)
      onProgress(100)
      return Promise.resolve({ skills: ['PostgreSQL'], resume_text: extractedText })
    })
  })

  it('deduplicates skills and saves the edited skill list', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByText('Python')).toBeInTheDocument()
    const skillInput = screen.getByLabelText('Add a skill')
    await user.type(skillInput, 'python')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getAllByText('Python')).toHaveLength(1)

    await user.type(skillInput, 'React')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.click(screen.getByRole('button', { name: 'Save skill changes' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume', {
      method: 'PUT',
      body: JSON.stringify({ skills: ['Python', 'React'] }),
    }))
  })

  it('clears all extracted skills only after the staged change is saved', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByText('Python')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear all' }))

    expect(screen.queryByText('Python')).not.toBeInTheDocument()
    expect(screen.getByText('No skills saved yet')).toBeInTheDocument()
    expect(apiFetch).not.toHaveBeenCalledWith('/resume', {
      method: 'PUT',
      body: JSON.stringify({ skills: [] }),
    })

    await user.click(screen.getByRole('button', { name: 'Save skill changes' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume', {
      method: 'PUT',
      body: JSON.stringify({ skills: [] }),
    }))
  })

  it('saves an analyzed file, opens extraction, and waits for confirmation', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ResumePage />, { route: '/resume' })

    await screen.findByText('Python')
    const file = new File([extractedText], 'backend-resume.txt', { type: 'text/plain' })
    fireEvent.drop(screen.getByRole('button', { name: 'Upload resume file' }), {
      dataTransfer: { files: [file] },
    })

    expect(await screen.findByText(/Resume saved.*backend-resume.txt/i)).toBeInTheDocument()
    const dialog = await screen.findByRole('dialog', { name: 'Review extracted experience' })
    expect(await within(dialog).findByDisplayValue('Built the tracker')).toBeInTheDocument()
    expect(apiFetch.mock.calls.filter(([path, options]) => (
      path === '/resume/evidence' && options?.method === 'PUT'
    ))).toHaveLength(0)

    await user.clear(within(dialog).getByDisplayValue('Built the tracker'))
    await user.type(within(dialog).getByLabelText('Entry 1 bullet 1'), 'Built and shipped the tracker')
    await user.click(within(dialog).getByRole('button', { name: 'Confirm experience' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Review extracted experience' })).not.toBeInTheDocument())
    const evidenceSave = apiFetch.mock.calls.find(([path, options]) => (
      path === '/resume/evidence' && options?.method === 'PUT'
    ))
    expect(JSON.parse(evidenceSave[1].body).entries[0].bullets).toEqual([
      { id: null, text: 'Built and shipped the tracker' },
    ])
    expect(screen.getByPlaceholderText('Paste your resume text here…')).toHaveValue('')
    expect(apiUpload).toHaveBeenCalledWith('/resume/upload', expect.any(FormData), expect.any(Function))
    await waitFor(() => {
      const saveCall = apiFetch.mock.calls.find(([path, options]) => (
        path === '/resume' && options?.method === 'PUT' && options.body instanceof FormData
      ))
      expect(saveCall).toBeTruthy()
      const form = saveCall[1].body
      expect(form.get('file').name).toBe('backend-resume.txt')
      expect(JSON.parse(form.get('resume'))).toEqual({
        skills: ['PostgreSQL'],
        resume_text: extractedText.trim(),
      })
    })
  })

  it('requires confirmation before removing the resume and all extracted data', async () => {
    const user = userEvent.setup()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume' && options?.method === 'DELETE') return Promise.resolve({ ok: true })
      if (path === '/resume/evidence') {
        return Promise.resolve({
          header: {},
          entries: [{ id: 'entry-1', kind: 'project', bullets: [{ id: 'bullet-1', text: 'Built it' }] }],
        })
      }
      if (path === '/resume') {
        return Promise.resolve({
          skills: ['Python'],
          resume_text: 'Saved resume text',
          source_file: { filename: 'backend-resume.pdf', size_bytes: 2048 },
        })
      }
      return Promise.resolve({})
    })
    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByText('backend-resume.pdf')).toBeInTheDocument()
    expect(screen.getAllByText('✓ done')).toHaveLength(3)
    await user.click(screen.getByRole('button', { name: 'Remove resume' }))

    const dialog = screen.getByRole('dialog', { name: 'Remove your resume?' })
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText(/original file, extracted text, skills, experience evidence, and tailoring history/i)).toBeInTheDocument()
    expect(apiFetch).not.toHaveBeenCalledWith('/resume', { method: 'DELETE' })

    await user.click(within(dialog).getByRole('button', { name: 'Remove resume' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume', { method: 'DELETE' }))
    expect(await screen.findByText(/resume and all extracted data removed/i)).toBeInTheDocument()
    expect(screen.queryByText('backend-resume.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('Python')).not.toBeInTheDocument()
    expect(screen.queryAllByText('✓ done')).toHaveLength(0)
  })
})

describe('ResumePage extraction', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiUpload.mockReset()
  })

  it('replaces the saved skills rather than merging a new resume into the old one', async () => {
    const user = userEvent.setup()
    let savedResume = { skills: ['COBOL'], resume_text: null, source_file: null }
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/evidence') return Promise.resolve({ entries: [] })
      if (path === '/resume' && options?.method === 'PUT') {
        savedResume = { ...savedResume, ...JSON.parse(options.body) }
        return Promise.resolve({})
      }
      if (path === '/resume') return Promise.resolve(savedResume)
      if (path === '/resume/parse') return Promise.resolve({ skills: ['Python'], resume_text: extractedText })
      return Promise.resolve({})
    })

    renderWithProviders(<ResumePage />, { route: '/resume' })

    await screen.findByText('COBOL')
    await user.type(screen.getByPlaceholderText(/Paste your resume text/i), extractedText)
    await user.click(screen.getByRole('button', { name: /Analyze and save/ }))

    expect(await screen.findByText('Python')).toBeInTheDocument()
    expect(screen.queryByText('COBOL')).not.toBeInTheDocument()
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume', {
      method: 'PUT',
      body: JSON.stringify({ skills: ['Python'], resume_text: extractedText }),
    }))
  })
})

describe('ResumePage guidance', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiUpload.mockReset()
  })

  function mockResume({ resume = {}, evidence = { entries: [] } } = {}) {
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/evidence' && !options) return Promise.resolve(evidence)
      if (path === '/resume' && options?.method === 'PUT') return Promise.resolve({})
      if (path === '/resume') return Promise.resolve({ skills: [], resume_text: null, source_file: null, ...resume })
      return Promise.resolve({})
    })
  }

  it('tells a new user what order to do things in', async () => {
    mockResume()

    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByText('Add your resume')).toBeInTheDocument()
    expect(screen.getByText('Check your skills')).toBeInTheDocument()
    expect(screen.getByText('Pull out your experience')).toBeInTheDocument()
    expect(screen.getAllByText(/step \d/)).toHaveLength(3)
    expect(screen.getAllByTestId(/readiness-connector/)).toHaveLength(2)
    expect(screen.getAllByTestId(/readiness-connector/).every((line) => (
      line.dataset.complete === 'false'
    ))).toBe(true)
  })

  it('ticks off the steps that are done', async () => {
    mockResume({
      resume: { skills: ['Python'], resume_text: 'Saved resume text' },
      evidence: { entries: [{ id: 'e1', kind: 'project', bullets: [{ id: 'b1', text: 'Shipped it' }] }] },
    })

    renderWithProviders(<ResumePage />, { route: '/resume' })

    await waitFor(() => expect(screen.getAllByText('✓ done')).toHaveLength(3))
    expect(screen.getAllByTestId(/readiness-connector/).every((line) => (
      line.dataset.complete === 'true'
    ))).toBe(true)
  })

  it('asks for a resume and nothing else when there is none', async () => {
    mockResume()

    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByRole('heading', { name: 'No resume yet' })).toBeInTheDocument()
    // reading is a system step, so there is no button here for the user to miss
    expect(screen.queryByRole('button', { name: /Extract experience/ })).not.toBeInTheDocument()
  })

  it('keeps file upload primary and reveals pasted-text controls on request', async () => {
    const user = userEvent.setup()
    mockResume()

    renderWithProviders(<ResumePage />, { route: '/resume' })

    const uploadChoice = await screen.findByRole('button', { name: 'Upload file' })
    const pasteChoice = screen.getByRole('button', { name: 'Paste text' })
    expect(uploadChoice).toHaveAttribute('aria-pressed', 'true')

    await user.click(pasteChoice)

    expect(pasteChoice).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByPlaceholderText('Paste your resume text here…')).toBeVisible()
  })

  it('counts the evidence once it exists', async () => {
    mockResume({
      resume: { skills: ['Python'], resume_text: 'saved resume text' },
      evidence: {
        entries: [
          { id: 'e1', kind: 'project', bullets: [{ id: 'b1', text: 'a' }, { id: 'b2', text: 'b' }] },
          { id: 'e2', kind: 'experience', bullets: [{ id: 'b3', text: 'c' }] },
        ],
      },
    })

    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByRole('heading', { name: 'Resume ready' })).toBeInTheDocument()
    expect(screen.getByText(/2 entries · 3 bullets/)).toBeInTheDocument()
    // reviewing is offered, not required
    expect(screen.getByRole('button', { name: 'Review extracted info' })).toBeInTheDocument()
  })

  it('opens a confirmation draft for a resume saved before automatic extraction', async () => {
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/evidence') return Promise.resolve({ header: {}, entries: [] })
      if (path === '/resume/structure' && options?.method === 'POST') {
        return Promise.resolve({
          header: { full_name: 'Shawn Li', links: [] },
          entries: [{ kind: 'project', title: 'Tracker', bullets: ['Built the tracker'] }],
          skills: ['Python'],
          source_hash: 'hash-1',
        })
      }
      if (path === '/resume') return Promise.resolve({ skills: ['Python'], resume_text: extractedText, source_file: null })
      return Promise.resolve({})
    })

    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByRole('dialog', { name: 'Review extracted experience' })).toBeInTheDocument()
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume/structure', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({}),
      timeoutMs: 50000,
    })))

    expect(await screen.findByDisplayValue('Built the tracker')).toBeInTheDocument()
    expect(apiFetch.mock.calls.filter(([path, options]) => (
      path === '/resume/evidence' && options?.method === 'PUT'
    ))).toHaveLength(0)
  })

  it('takes upload straight through to the confirm step, with no button to find', async () => {
    const user = userEvent.setup()
    let evidence = { header: {}, entries: [] }
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/evidence' && !options) return Promise.resolve(evidence)
      if (path === '/resume/evidence' && options?.method === 'PUT') {
        evidence = { header: {}, entries: [{ id: 'e1', kind: 'project', bullets: [{ id: 'b1', text: 'Built the tracker' }] }] }
        return Promise.resolve(evidence)
      }
      if (path === '/resume/structure' && options?.method === 'POST') {
        return Promise.resolve({
          header: { full_name: 'Shawn Li', links: [] },
          entries: [{ kind: 'project', title: 'Tracker', bullets: ['Built the tracker'] }],
          skills: ['Python'],
          source_hash: 'hash-1',
        })
      }
      if (path === '/resume/parse') {
        return Promise.resolve({ skills: ['Python'], resume_text: extractedText })
      }
      if (path === '/resume' && options?.method === 'PUT') return Promise.resolve({})
      if (path === '/resume') return Promise.resolve({ skills: [], resume_text: null, source_file: null })
      return Promise.resolve({})
    })

    renderWithProviders(<ResumePage />, { route: '/resume' })

    await user.click(await screen.findByRole('button', { name: 'Paste text' }))
    await user.type(screen.getByPlaceholderText('Paste your resume text here…'), extractedText)
    await user.click(screen.getByRole('button', { name: /Analyze and save/i }))

    // the review opens by itself, already extracting — the old flow needed a second button
    // nobody knew existed, and tailoring then claimed there was no resume at all
    const dialog = await screen.findByRole('dialog', { name: /Review extracted experience/ })
    expect(await within(dialog).findByDisplayValue('Built the tracker')).toBeInTheDocument()

    // and nothing is evidence until it is confirmed
    await user.click(within(dialog).getByRole('button', { name: /Confirm/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})
