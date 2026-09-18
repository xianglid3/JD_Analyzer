/**
 * Transient feedback, in the corner, out of the way.
 *
 * Saving a status or a link used to print a line underneath the control that changed, which
 * meant the page moved every time something succeeded, and the message was easy to miss if you
 * had already looked away. A toast says the same thing without displacing anything.
 *
 * A module-level subscription rather than context: every page and half the components need to
 * raise one, and threading a provider through all of them buys nothing when there is exactly
 * one host on screen.
 */
let nextId = 1
const listeners = new Set()

function emit(tone, message) {
  const entry = { id: nextId++, tone, message: String(message ?? '') }
  listeners.forEach((listener) => listener(entry))
  return entry.id
}

export const toast = {
  success: (message) => emit('success', message),
  error: (message) => emit('error', message),
}

export function subscribe(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
