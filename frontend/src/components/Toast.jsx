import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

import { subscribe } from '../lib/toast'

/**
 * Transient feedback, in the corner, out of the way.
 *
 * Saving a status or a link used to print a line underneath the control that changed, which
 * meant the page moved every time something succeeded, and the message was easy to miss if
 * you had already looked away. A toast says the same thing without displacing anything.
 */
const TONES = {
  success: 'border-terminal-green/30 bg-terminal-green/10 text-ink',
  error: 'border-red-700/30 bg-red-50 text-ink',
}

export default function ToastHost() {
  const [items, setItems] = useState([])

  useEffect(() => {
    function receive(entry) {
      setItems((current) => [...current, entry])
      // long enough to read a sentence, short enough not to linger over the page
      setTimeout(() => setItems((current) => current.filter((item) => item.id !== entry.id)), 4000)
    }
    return subscribe(receive)
  }, [])

  if (items.length === 0) return null

  return createPortal((
    <div
      className="pointer-events-none fixed right-4 top-4 z-[60] flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2"
      aria-live="polite"
    >
      {items.map((item) => (
        <div
          key={item.id}
          // `alert` for failures so a screen reader interrupts; `status` for successes, which
          // are worth hearing but not worth cutting someone off for
          role={item.tone === 'error' ? 'alert' : 'status'}
          className={`animate-soft-in pointer-events-auto flex items-start gap-2 rounded-[0.625rem] border px-3 py-2.5 text-sm shadow-sm ${TONES[item.tone]}`}
        >
          <span aria-hidden="true" className={item.tone === 'error' ? 'text-red-700' : 'text-terminal-green'}>
            {item.tone === 'error' ? '!' : '✓'}
          </span>
          <p className="min-w-0 flex-1 leading-5">{item.message}</p>
          <button
            type="button"
            aria-label="Dismiss"
            className="shrink-0 px-1 text-muted hover:text-ink"
            onClick={() => setItems((current) => current.filter((entry) => entry.id !== item.id))}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  ), document.body)
}
