import { afterEach, describe, expect, it, vi } from 'vitest'
import { requestKey } from '../../src/lib/requestKey'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

describe('requestKey', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses crypto.randomUUID when the page is in a secure context', () => {
    const randomUUID = vi.fn(() => '11111111-1111-4111-8111-111111111111')
    vi.stubGlobal('crypto', { randomUUID, getRandomValues: vi.fn() })

    expect(requestKey()).toBe('11111111-1111-4111-8111-111111111111')
    expect(randomUUID).toHaveBeenCalled()
  })

  it('still returns a valid v4 uuid when randomUUID is missing', () => {
    // BUG-058: randomUUID is undefined outside HTTPS and localhost, and calling it threw
    // before any request was made — Analyze looked like it did nothing
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes) => {
        for (let index = 0; index < bytes.length; index += 1) bytes[index] = index * 7
        return bytes
      },
    })

    expect(requestKey()).toMatch(UUID_V4)
  })

  it('survives crypto being absent entirely', () => {
    vi.stubGlobal('crypto', undefined)

    expect(requestKey()).toMatch(UUID_V4)
  })

  it('does not repeat itself', () => {
    // the real implementation, captured before the stub replaces the global it lives on
    const fill = globalThis.crypto.getRandomValues.bind(globalThis.crypto)
    vi.stubGlobal('crypto', { getRandomValues: fill })

    const keys = new Set(Array.from({ length: 200 }, () => requestKey()))

    expect(keys.size).toBe(200)
  })
})
