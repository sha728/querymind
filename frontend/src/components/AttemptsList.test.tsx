import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { AttemptsList } from './AttemptsList'

describe('AttemptsList', () => {
  it('renders nothing without attempts', () => {
    const { container } = render(<AttemptsList attempts={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('toggles open and closed', async () => {
    const user = userEvent.setup()
    render(<AttemptsList attempts={[{ n: 1, sql: 'SELECT 1', errorCode: null, error: null, stage: 'execute', latencyMs: 12 }]} />)

    await user.click(screen.getByRole('button', { name: 'Show attempts (1)' }))
    expect(screen.getByText('SELECT 1')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Hide attempts' }))
    expect(screen.queryByText('SELECT 1')).not.toBeInTheDocument()
  })
})
