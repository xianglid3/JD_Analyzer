import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import Dialog from '../../src/components/Dialog'

function DialogHarness() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button onClick={() => setOpen(true)}>Open settings</button>
      <Dialog open={open} title="Settings" onClose={() => setOpen(false)}>
        <label htmlFor="display-name">Display name</label>
        <input id="display-name" data-dialog-autofocus />
      </Dialog>
    </>
  )
}

describe('Dialog focus management', () => {
  it('focuses the intended field and returns focus to the trigger after closing', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)

    const trigger = screen.getByRole('button', { name: 'Open settings' })
    await user.click(trigger)

    expect(screen.getByLabelText('Display name')).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Close dialog' }))

    expect(trigger).toHaveFocus()
  })
})
