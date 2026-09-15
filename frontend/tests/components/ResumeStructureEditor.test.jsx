import { StrictMode } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../helpers/render'
import ResumeStructureEditor from '../../src/components/ResumeStructureEditor'
import { apiFetch } from '../../src/lib/api'

vi.mock('../../src/lib/api', () => ({ apiFetch: vi.fn() }))

const saved = {
  header: { full_name: 'Shawn Li', email: 'shl362@pitt.edu', phone: null, location: 'Pittsburgh, PA', links: ['github.com/shawn'] },
  entries: [{
    id: 'entry-1',
    kind: 'experience',
    organization: 'Acme',
    title: 'Backend Intern',
    location: null,
    start_date: 'Jun 2024',
    end_date: 'Aug 2024',
    bullets: [{ id: 'bullet-1', text: 'Shipped the ingestion pipeline' }],
  }],
}

beforeEach(() => apiFetch.mockReset())

describe('ResumeStructureEditor', () => {
  it('shows saved evidence as editable fields', async () => {
    apiFetch.mockResolvedValueOnce(saved)

    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    expect(await screen.findByDisplayValue('Shipped the ingestion pipeline')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Acme')).toBeInTheDocument()
    expect(screen.getByText(/1 entry · 1 bullet/)).toBeInTheDocument()
  })

  it('sends an edited bullet back with its id, so it keeps its identity', async () => {
    apiFetch.mockResolvedValueOnce(saved)
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    const bullet = await screen.findByDisplayValue('Shipped the ingestion pipeline')
    apiFetch.mockResolvedValueOnce(saved)
    await userEvent.type(bullet, ' in Python')
    await userEvent.click(screen.getByRole('button', { name: 'Save experience' }))

    await waitFor(() => {
      const put = apiFetch.mock.calls.find(([, options]) => options?.method === 'PUT')
      expect(put[0]).toBe('/resume/evidence')
      expect(JSON.parse(put[1].body).entries[0].bullets).toEqual([
        { id: 'bullet-1', text: 'Shipped the ingestion pipeline in Python' },
      ])
    })
  })

  it('cannot save until something changes', async () => {
    apiFetch.mockResolvedValueOnce(saved)
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    expect(await screen.findByRole('button', { name: 'Save experience' })).toBeDisabled()
  })

  it('warns when the resume changed after this was extracted', async () => {
    apiFetch.mockResolvedValueOnce({ ...saved, stale: true })

    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    expect(await screen.findByText(/Your resume changed after this was extracted/)).toBeInTheDocument()
  })

  it('sends back the hash of the extraction it came from', async () => {
    apiFetch.mockResolvedValueOnce({ header: {}, entries: [] })
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    apiFetch.mockResolvedValueOnce({ header: {}, entries: [], skills: [], source_hash: 'abc123' })
    await userEvent.click(await screen.findByRole('button', { name: 'Extract from resume' }))
    apiFetch.mockResolvedValueOnce({ header: {}, entries: [] })
    await userEvent.click(screen.getByRole('button', { name: 'Save experience' }))

    await waitFor(() => {
      const put = apiFetch.mock.calls.find(([, options]) => options?.method === 'PUT')
      expect(JSON.parse(put[1].body).source_hash).toBe('abc123')
    })
  })

  it('extraction fills the editor without saving', async () => {
    apiFetch.mockResolvedValueOnce({ header: {}, entries: [] })
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    apiFetch.mockResolvedValueOnce({
      header: { full_name: 'Shawn Li', links: [] },
      entries: [{ kind: 'project', organization: null, title: 'Tailor', bullets: ['Wrote the loop'] }],
      skills: ['python'],
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Extract from resume' }))

    expect(await screen.findByDisplayValue('Wrote the loop')).toBeInTheDocument()
    expect(apiFetch.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(0)
  })

  it('clears the loading state when a delayed extraction response arrives', async () => {
    apiFetch.mockResolvedValueOnce({ header: {}, entries: [] })
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    let finishExtraction
    apiFetch.mockImplementationOnce(() => new Promise((resolve) => {
      finishExtraction = resolve
    }))
    await userEvent.click(await screen.findByRole('button', { name: 'Extract from resume' }))
    expect(screen.getByText('Reading your resume…')).toBeInTheDocument()

    finishExtraction({
      header: {},
      entries: [{ kind: 'project', title: 'Delayed result', bullets: ['Response reached the browser'] }],
      source_hash: 'delayed-hash',
    })

    expect(await screen.findByDisplayValue('Response reached the browser')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Reading your resume…')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Re-extract from resume' })).toBeEnabled()
  })

  it('surfaces the error when there is no resume to extract from', async () => {
    apiFetch.mockResolvedValueOnce({ header: {}, entries: [] })
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    apiFetch.mockRejectedValueOnce(new Error('no saved resume text — paste or upload a resume first'))
    await userEvent.click(await screen.findByRole('button', { name: 'Extract from resume' }))

    expect(await screen.findByText(/no saved resume text/)).toBeInTheDocument()
  })

  it('lets the user cancel a slow extraction', async () => {
    apiFetch.mockResolvedValueOnce({ header: {}, entries: [] })
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    apiFetch.mockImplementationOnce((_path, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('Extraction canceled.')
        error.status = 499
        reject(error)
      })
    }))
    await userEvent.click(await screen.findByRole('button', { name: 'Extract from resume' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByText('Extraction canceled.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Extract from resume' })).toBeEnabled()
  })

  it('removes a bullet', async () => {
    apiFetch.mockResolvedValueOnce(saved)
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    await userEvent.click(await screen.findByRole('button', { name: /Remove bullet 1 from entry 1/ }))

    expect(screen.queryByDisplayValue('Shipped the ingestion pipeline')).not.toBeInTheDocument()
  })

  it('shows contact details and sends them on save', async () => {
    apiFetch.mockResolvedValueOnce(saved)
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    expect(await screen.findByDisplayValue('Shawn Li')).toBeInTheDocument()
    expect(screen.getByDisplayValue('github.com/shawn')).toBeInTheDocument()

    apiFetch.mockResolvedValueOnce(saved)
    await userEvent.type(screen.getByLabelText(/Phone/), '412-400-6088')
    await userEvent.click(screen.getByRole('button', { name: 'Save experience' }))

    await waitFor(() => {
      const put = apiFetch.mock.calls.find(([, options]) => options?.method === 'PUT')
      expect(JSON.parse(put[1].body).header).toEqual({
        full_name: 'Shawn Li',
        email: 'shl362@pitt.edu',
        phone: '412-400-6088',
        location: 'Pittsburgh, PA',
        links: ['github.com/shawn'],
      })
    })
  })

  it('extraction fills in the header too', async () => {
    apiFetch.mockResolvedValueOnce({ header: {}, entries: [] })
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)

    apiFetch.mockResolvedValueOnce({ header: { full_name: 'Shawn Li', links: [] }, entries: [], skills: [] })
    await userEvent.click(await screen.findByRole('button', { name: 'Extract from resume' }))

    expect(await screen.findByDisplayValue('Shawn Li')).toBeInTheDocument()
  })

  it('closes without saving', async () => {
    const onClose = vi.fn()
    apiFetch.mockResolvedValueOnce(saved)
    renderWithProviders(<ResumeStructureEditor onClose={onClose} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Close' }))

    expect(onClose).toHaveBeenCalled()
  })

  it('cancel aborts the extraction that is actually running', async () => {
    apiFetch.mockResolvedValueOnce(saved)                       // the evidence load
    renderWithProviders(<ResumeStructureEditor onClose={() => {}} />)
    await screen.findByDisplayValue('Acme')

    // hold the extraction open so the pending UI stays up, and keep its signal
    let signal
    apiFetch.mockImplementationOnce((_path, options) => {
      signal = options.signal
      return new Promise(() => {})
    })
    await userEvent.click(screen.getByRole('button', { name: /extract from resume/i }))

    expect(await screen.findByText('Reading your resume…')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(signal.aborted).toBe(true)
    // and the UI leaves the pending state even though the promise never settles
    await waitFor(() => expect(screen.queryByText('Reading your resume…')).not.toBeInTheDocument())
  })

  it('auto-extract survives the StrictMode mount/unmount/remount cycle', async () => {
    // React runs effects twice in StrictMode. This used to abort the request auto-extract
    // had just started, then refuse to re-fire it, leaving the spinner up forever.
    apiFetch.mockResolvedValueOnce(saved)                         // evidence load
    const extracted = {
      header: saved.header,
      source_hash: 'abc123',
      entries: [{ ...saved.entries[0], bullets: [{ id: 'bullet-9', text: 'Extracted bullet' }] }],
    }
    const calls = []
    apiFetch.mockImplementation((path) => {
      calls.push(path)
      return path === '/resume/structure' ? Promise.resolve(extracted) : Promise.resolve(saved)
    })

    renderWithProviders(
      <StrictMode><ResumeStructureEditor autoExtract onClose={() => {}} /></StrictMode>,
    )

    // it finishes: the extracted bullet lands and the pending label goes away
    expect(await screen.findByDisplayValue('Extracted bullet')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Reading your resume…')).not.toBeInTheDocument())

    // and it is paid for exactly once
    expect(calls.filter((path) => path === '/resume/structure')).toHaveLength(1)
  })
})
