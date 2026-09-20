import { describe, expect, it } from 'vitest'
import config from '../../vercel.json'

/**
 * Deployment config is code: deleting `vercel.json` breaks every reload of a deep link, and only
 * in production, where nobody runs the test suite.
 *
 * The file holds nothing but the rewrite, because Vercel validates it against a strict schema
 * and refuses the whole deployment over an unknown key — a `"//"` comment included. So the
 * explanation lives here: React Router's routes exist in the running app, not on the server, so
 * reloading /tailoring/<id> asks Vercel for a file that was never built. Static assets match
 * before rewrites, so hashed bundles still resolve normally.
 */
describe('vercel.json', () => {
  it('sends unmatched paths to the SPA', () => {
    // without it, /tailoring/<id> 404s on reload — Vercel is asked for a file that was never
    // built, and refreshing asks the same question again
    expect(config.rewrites).toContainEqual({ source: '/(.*)', destination: '/index.html' })
  })
})
