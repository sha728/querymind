import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { AskColumn, Cell, ChartType } from '../api/types'
import { ResultChart } from './ResultChart'

// Fixtures shaped like the engine's chart recommender output (design §9).
const BAR = {
  columns: [
    { name: 'product', type: 'text', dbType: 'varchar' },
    { name: 'units', type: 'integer', dbType: 'int8' },
    { name: 'revenue', type: 'numeric', dbType: 'float8' },
  ] satisfies AskColumn[],
  rows: [['Chai', 5, 90.5], ['Chang', 7, 133], ['Tofu', 3, 69.75]] satisfies Cell[][],
  chart: { recommended: 'bar' as ChartType, allowed: ['bar', 'table'] as ChartType[], x: 'product', y: ['units', 'revenue'] },
}

const LINE = {
  columns: [
    { name: 'month', type: 'date', dbType: 'date' },
    { name: 'orders', type: 'integer', dbType: 'int8' },
  ] satisfies AskColumn[],
  rows: [['2026-06-01', 61], ['2026-07-01', 70], ['2026-08-01', 74], ['2026-09-01', 14]] satisfies Cell[][],
  chart: { recommended: 'line' as ChartType, allowed: ['line', 'bar', 'table'] as ChartType[], x: 'month', y: ['orders'] },
}

const PIE = {
  columns: [
    { name: 'country', type: 'text', dbType: 'varchar' },
    { name: 'customers', type: 'integer', dbType: 'int8' },
  ] satisfies AskColumn[],
  rows: [['USA', 13], ['France', 11], ['Germany', 11], ['Brazil', 9]] satisfies Cell[][],
  chart: { recommended: 'pie' as ChartType, allowed: ['pie', 'bar', 'table'] as ChartType[], x: 'country', y: ['customers'] },
}

function figure() {
  return screen.getByRole('img')
}

function switcherOptions() {
  return within(screen.getByRole('group', { name: 'Chart type' })).getAllByRole('button').map((b) => b.textContent)
}

describe('ResultChart renders the recommended chart (Recharts)', () => {
  it('bar: one bar per row and series, with a legend for two series', () => {
    const { container } = render(<ResultChart {...BAR} />)

    expect(figure()).toHaveAttribute('data-chart-type', 'bar')
    expect(figure()).toHaveAccessibleName('Bar chart of units, revenue by product')
    expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(3 * 2)
    expect(container.querySelector('.recharts-legend-wrapper')).toHaveTextContent('units')
    expect(container.querySelector('.recharts-legend-wrapper')).toHaveTextContent('revenue')
  })

  it('line: one line, no legend for a single series', () => {
    const { container } = render(<ResultChart {...LINE} />)

    expect(figure()).toHaveAttribute('data-chart-type', 'line')
    expect(container.querySelectorAll('.recharts-line-curve')).toHaveLength(1)
    expect(container.querySelectorAll('.recharts-line-dot')).toHaveLength(4)
    expect(container.querySelector('.recharts-legend-wrapper')).toBeNull()
    expect(container).toHaveTextContent('2026-07-01') // x-axis tick
  })

  it('pie: one sector per row, labelled by category', async () => {
    const { container } = render(<ResultChart {...PIE} />)

    expect(figure()).toHaveAttribute('data-chart-type', 'pie')
    expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(4)
    // Recharts fills the pie legend after its first render.
    await waitFor(() => { expect(container.querySelector('.recharts-legend-wrapper')).toHaveTextContent('Brazil') })
  })
})

describe('chart-type switcher (R6.2)', () => {
  it('offers only the allowed types, with the recommended one selected', () => {
    render(<ResultChart {...LINE} />)

    expect(switcherOptions()).toEqual(['Line', 'Bar', 'Table'])
    expect(screen.getByRole('button', { name: 'Line' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: 'Pie' })).not.toBeInTheDocument()
  })

  it('switching changes the rendered chart', async () => {
    const user = userEvent.setup()
    const { container } = render(<ResultChart {...PIE} />)

    await user.click(screen.getByRole('button', { name: 'Bar' }))

    expect(figure()).toHaveAttribute('data-chart-type', 'bar')
    expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(0)
    expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(4)
    expect(screen.getByRole('button', { name: 'Bar' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Pie' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('"Table" hides the chart, and choosing a chart again brings it back', async () => {
    const user = userEvent.setup()
    const { container } = render(<ResultChart {...BAR} />)

    await user.click(screen.getByRole('button', { name: 'Table' }))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(container.querySelector('svg.recharts-surface')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Bar' }))
    expect(figure()).toHaveAttribute('data-chart-type', 'bar')
  })

  it('renders nothing when the only allowed type is table', () => {
    const { container } = render(
      <ResultChart columns={BAR.columns} rows={BAR.rows} chart={{ recommended: 'table', allowed: ['table'], x: null, y: [] }} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
