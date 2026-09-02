import { fireEvent, screen, waitFor } from '@testing-library/react'
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

  it('saves an analyzed file automatically as the multipart commit', async () => {
    renderWithProviders(<ResumePage />, { route: '/resume' })

    await screen.findByText('Python')
    const file = new File([extractedText], 'backend-resume.txt', { type: 'text/plain' })
    fireEvent.drop(screen.getByRole('button', { name: 'Upload resume file' }), {
      dataTransfer: { files: [file] },
    })

    expect(await screen.findByText(/Resume saved.*backend-resume.txt/i)).toBeInTheDocument()
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

  it('requires confirmation before removing only the saved source file', async () => {
    const user = userEvent.setup()
    apiFetch.mockImplementation((path, options) => {
      if (path === '/resume/file' && options?.method === 'DELETE') return Promise.resolve({ ok: true })
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
    await user.click(screen.getByRole('button', { name: 'Remove file' }))

    expect(screen.getByRole('dialog', { name: 'Remove stored resume file?' })).toBeInTheDocument()
    expect(screen.getByText(/extracted skills and resume text will stay saved/i)).toBeInTheDocument()
    expect(apiFetch).not.toHaveBeenCalledWith('/resume/file', { method: 'DELETE' })

    await user.click(screen.getByRole('button', { name: 'Remove stored file' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume/file', { method: 'DELETE' }))
    expect(await screen.findByText(/stored resume file removed/i)).toBeInTheDocument()
    expect(screen.queryByText('backend-resume.pdf')).not.toBeInTheDocument()
    expect(screen.getByText('Python')).toBeInTheDocument()
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
  })

  it('ticks off the steps that are done', async () => {
    mockResume({
      resume: { skills: ['Python'], resume_text: 'Saved resume text' },
      evidence: { entries: [{ id: 'e1', kind: 'project', bullets: [{ id: 'b1', text: 'Shipped it' }] }] },
    })

    renderWithProviders(<ResumePage />, { route: '/resume' })

    await waitFor(() => expect(screen.getAllByText('✓ done')).toHaveLength(3))
  })

  it('explains why experience matters when there is none', async () => {
    mockResume()

    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByText(/Add a resume first.*extract experience here/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Extract experience' })).toBeDisabled()
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
      evidence: {
        entries: [
          { id: 'e1', kind: 'project', bullets: [{ id: 'b1', text: 'a' }, { id: 'b2', text: 'b' }] },
          { id: 'e2', kind: 'experience', bullets: [{ id: 'b3', text: 'c' }] },
        ],
      },
    })

    renderWithProviders(<ResumePage />, { route: '/resume' })

    expect(await screen.findByText(/3 bullets across 2 entries/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review experience' })).toBeInTheDocument()
  })

  it('extracts experience from the card without a second button click', async () => {
    const user = userEvent.setup()
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

    await user.click(await screen.findByRole('button', { name: 'Extract experience' }))

    expect(await screen.findByDisplayValue('Built the tracker')).toBeInTheDocument()
    expect(apiFetch).toHaveBeenCalledWith('/resume/structure', {
      method: 'POST',
      body: JSON.stringify({}),
      timeoutMs: 70000,
    })
  })
})
