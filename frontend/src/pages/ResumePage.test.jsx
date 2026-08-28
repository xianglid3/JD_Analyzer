import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, apiUpload } from '../lib/api'
import { renderWithProviders } from '../test/render'
import ResumePage from './ResumePage'

vi.mock('../lib/api', () => ({ apiFetch: vi.fn(), apiUpload: vi.fn() }))

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
    await user.click(screen.getByRole('button', { name: 'Save resume' }))

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith('/resume', {
      method: 'PUT',
      body: JSON.stringify({ skills: ['Python', 'React'], resume_text: 'Saved resume text' }),
    }))
  })

  it('keeps an analyzed file pending until Save sends the multipart commit', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ResumePage />, { route: '/resume' })

    await screen.findByText('Python')
    const file = new File([extractedText], 'backend-resume.txt', { type: 'text/plain' })
    fireEvent.drop(screen.getByRole('button', { name: 'Upload resume file' }), {
      dataTransfer: { files: [file] },
    })

    expect(await screen.findByText(/pending save/i)).toBeInTheDocument()
    expect(screen.getByText('backend-resume.txt')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Paste your resume text here…')).toHaveValue('')
    expect(apiUpload).toHaveBeenCalledWith('/resume/upload', expect.any(FormData), expect.any(Function))
    await user.click(screen.getByRole('button', { name: 'Save resume' }))

    await waitFor(() => {
      const saveCall = apiFetch.mock.calls.find(([path, options]) => (
        path === '/resume' && options?.method === 'PUT' && options.body instanceof FormData
      ))
      expect(saveCall).toBeTruthy()
      const form = saveCall[1].body
      expect(form.get('file').name).toBe('backend-resume.txt')
      expect(JSON.parse(form.get('resume'))).toEqual({
        skills: ['Python', 'PostgreSQL'],
        resume_text: extractedText.trim(),
      })
    })
  })
})
