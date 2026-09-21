import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { NoBsTranslation } from '../../src/components/NoBsTranslation'
import { translationSections } from '../../src/lib/translation'

const LABELLED = [
  'What the role is: Backend CRUD in Go.',
  'What skills they expect: APIs and SQL.',
  'Day to day: Tickets and code review.',
].join('\n\n')

describe('NoBsTranslation', () => {
  it('shows each section under its own subtitle', () => {
    render(<NoBsTranslation text={LABELLED} />)

    expect(screen.getByText('What the role is')).toBeInTheDocument()
    expect(screen.getByText('Backend CRUD in Go.')).toBeInTheDocument()
    expect(screen.getByText('Day to day')).toBeInTheDocument()
    expect(screen.getByText('Tickets and code review.')).toBeInTheDocument()
  })

  it('keeps an older free-text translation as one paragraph', () => {
    const old = 'You would mostly write APIs.\n\nSome on-call.'
    expect(translationSections(old)).toBeNull()
    render(<NoBsTranslation text={old} />)
    expect(screen.getByText(/You would mostly write APIs/)).toBeInTheDocument()
  })

  it('says so when there is no translation', () => {
    render(<NoBsTranslation text={null} />)
    expect(screen.getByText('No translation was extracted.')).toBeInTheDocument()
  })
})
