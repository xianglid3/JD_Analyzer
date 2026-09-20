import { describe, expect, it } from 'vitest'
import config from '../../vercel.json'

/**
 * Deployment config is code: deleting this file breaks every reload of a deep link, and only in
 * production, where nobody is running the test suite.
 */
describe('vercel.json', () => {
  it('sends unmatched paths to the SPA', () => {
    // without it, /tailoring/<id> 404s on reload — Vercel is asked for a file that was never
    // built, and refreshing asks the same question again
    expect(config.rewrites).toContainEqual({ source: '/(.*)', destination: '/index.html' })
  })
})
