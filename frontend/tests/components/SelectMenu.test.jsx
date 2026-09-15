import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import SelectMenu from '../../src/components/SelectMenu'

const options = [
  { label: 'Saved', value: 'saved' },
  { label: 'Applied', value: 'applied' },
  { label: 'Interview', value: 'interview' },
]

describe('SelectMenu', () => {
  it('opens its listbox and selects an option', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<SelectMenu ariaLabel="Application status" value="saved" options={options} onChange={onChange} />)

    const trigger = screen.getByRole('combobox', { name: 'Application status' })
    await user.click(trigger)

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    await user.click(screen.getByRole('option', { name: 'Applied' }))

    expect(onChange).toHaveBeenCalledWith('applied')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('supports arrow-key navigation and returns focus after selection', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<SelectMenu ariaLabel="Application status" value="saved" options={options} onChange={onChange} />)

    const trigger = screen.getByRole('combobox', { name: 'Application status' })
    trigger.focus()
    await user.keyboard('{ArrowDown}{Enter}')

    expect(onChange).toHaveBeenCalledWith('applied')
    expect(trigger).toHaveFocus()
  })
})
