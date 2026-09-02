import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, apiUpload } from '../../src/lib/api'

function response(status, data = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: vi.fn().mockResolvedValue(data),
  }
}

describe('apiFetch', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('refreshes an expired session and retries the original request once', async () => {
    fetch
      .mockResolvedValueOnce(response(401, { error: 'expired' }))
      .mockResolvedValueOnce(response(200))
      .mockResolvedValueOnce(response(200, { username: 'alex' }))

    await expect(apiFetch('/auth/me')).resolves.toEqual({ username: 'alex' })

    expect(fetch).toHaveBeenNthCalledWith(1, '/api/auth/me', expect.any(Object))
    expect(fetch).toHaveBeenNthCalledWith(2, '/api/auth/refresh', {
      method: 'POST',
      credentials: 'include',
    })
    expect(fetch).toHaveBeenNthCalledWith(3, '/api/auth/me', expect.any(Object))
  })

  it('preserves custom headers while adding JSON content type', async () => {
    fetch.mockResolvedValueOnce(response(200, { id: 1 }))

    await apiFetch('/jobs', {
      method: 'POST',
      headers: { 'Idempotency-Key': 'request-key' },
      body: JSON.stringify({ description: 'A valid job description' }),
    })

    expect(fetch).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 'request-key',
      },
    }))
  })

  it('aborts a bounded request and returns an actionable timeout error', async () => {
    vi.useFakeTimers()
    fetch.mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('aborted')
        error.name = 'AbortError'
        reject(error)
      })
    }))

    try {
      const assertion = expect(apiFetch('/resume/structure', { timeoutMs: 1000 })).rejects.toMatchObject({
        message: 'Request timed out. Please try again.',
        status: 408,
      })
      await vi.advanceTimersByTimeAsync(1000)
      await assertion
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('apiUpload', () => {
  it('reports upload progress and returns the parsed response', async () => {
    let request

    class MockXMLHttpRequest {
      constructor() {
        request = this
        this.listeners = {}
        this.uploadListeners = {}
        this.upload = {
          addEventListener: (name, callback) => {
            this.uploadListeners[name] = callback
          },
        }
        this.open = vi.fn()
        this.send = vi.fn()
      }

      addEventListener(name, callback) {
        this.listeners[name] = callback
      }
    }

    vi.stubGlobal('XMLHttpRequest', MockXMLHttpRequest)
    const onProgress = vi.fn()
    const form = new FormData()
    form.append('file', new File(['resume'], 'resume.txt', { type: 'text/plain' }))

    const result = apiUpload('/resume/upload', form, onProgress)
    request.uploadListeners.progress({ lengthComputable: true, loaded: 3, total: 4 })
    request.status = 200
    request.responseText = JSON.stringify({ skills: ['Python'] })
    request.listeners.load()

    await expect(result).resolves.toEqual({ skills: ['Python'] })
    expect(request.open).toHaveBeenCalledWith('POST', '/api/resume/upload')
    expect(request.withCredentials).toBe(true)
    expect(request.send).toHaveBeenCalledWith(form)
    expect(onProgress).toHaveBeenCalledWith(75)
  })
})
