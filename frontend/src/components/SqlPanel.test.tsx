import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { SqlPanel } from './SqlPanel'

describe('SqlPanel', () => {
  it('copies the SQL and confirms', async () => {
    const user = userEvent.setup()
    render(<SqlPanel sql="SELECT 1" />)

    await user.click(screen.getByRole('button', { name: 'Copy SQL' }))

    expect(await navigator.clipboard.readText()).toBe('SELECT 1')
    expect(screen.getByRole('status')).toHaveTextContent('Copied')
  })

  it('says so when the clipboard is unavailable', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    render(<SqlPanel sql="SELECT 1" label="Rejected SQL" />)

    await user.click(screen.getByRole('button', { name: 'Copy SQL' }))

    expect(screen.getByRole('heading', { name: 'Rejected SQL' })).toBeInTheDocument()
    expect(await screen.findByText('Copy failed: select the text instead')).toBeInTheDocument()
  })
})
