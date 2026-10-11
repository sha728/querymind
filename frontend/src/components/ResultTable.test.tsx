import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { formatCell } from './format'
import { ResultTable } from './ResultTable'

const COLUMNS = [
  { name: 'n', type: 'integer' as const, dbType: 'int4' },
  { name: 'label', type: 'text' as const, dbType: 'text' },
]

describe('ResultTable', () => {
  it('has one page when rows fit exactly', () => {
    render(<ResultTable columns={COLUMNS} rows={Array.from({ length: 25 }, (_, i) => [i, 'x'])} />)

    expect(screen.getByText('Page 1 of 1 · rows 1–25 of 25')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('goes back with Previous', async () => {
    const user = userEvent.setup()
    render(<ResultTable columns={COLUMNS} rows={Array.from({ length: 26 }, (_, i) => [i + 1, 'x'])} />)

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Page 2 of 2 · rows 26–26 of 26')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Previous' }))
    expect(screen.getByText('Page 1 of 2 · rows 1–25 of 26')).toBeInTheDocument()
  })

  it('right-aligns numbers and marks nulls', () => {
    render(<ResultTable columns={COLUMNS} rows={[[1234567, null]]} />)

    const cells = within(screen.getByRole('table')).getAllByRole('cell')
    expect(cells[0]).toHaveTextContent('1,234,567')
    expect(cells[0]).toHaveClass('num')
    expect(cells[1]).toHaveTextContent('NULL')
    expect(cells[1]).toHaveClass('null')
  })

  it('formats cells', () => {
    expect(formatCell(null)).toBe('NULL')
    expect(formatCell(12345.678)).toBe('12,345.678')
    expect(formatCell(true)).toBe('true')
    expect(formatCell('2026-09-06')).toBe('2026-09-06')
  })
})
