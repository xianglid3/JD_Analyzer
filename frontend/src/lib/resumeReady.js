export const EMPTY = 'empty'
export const READING = 'reading'
export const REVIEW = 'review'
export const READY = 'ready'
export const STALE = 'stale'

/** One state for the whole resume, rather than two the user has to reconcile. */
export function resumeState({ hasResume, bulletCount, stale, reading }) {
  if (reading) return READING
  if (!hasResume) return EMPTY
  if (stale) return STALE
  return bulletCount > 0 ? READY : REVIEW
}
