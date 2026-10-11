import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { AskResponse, Cell } from '../api/types'
import { renderApp } from '../test/render'
import { askResponse, server, signInAs } from '../test/server'

function answerWith(response: Partial<AskResponse>) {
  server.use(http.post('*/api/ask', () => HttpResponse.json(askResponse(response))))
}

async function ask(question = 'Which customers spent the most?') {
  signInAs()
  const view = renderApp('/')
  await view.user.type(screen.getByLabelText('Question'), question)
  await view.user.click(screen.getByRole('button', { name: 'Ask' }))
  await screen.findByRole('article', { name: 'Answer' })
  return view
}

const SIXTY_ROWS: Cell[][] = Array.from({ length: 60 }, (_, i) => [`Customer ${String(i + 1)}`, (i + 1) * 10.5])

describe('success', () => {
  it('renders the summary and the table, 25 rows per page over 3 pages', async () => {
    answerWith({
      columns: [
        { name: 'company_name', type: 'text', dbType: 'varchar' },
        { name: 'total', type: 'numeric', dbType: 'float8' },
      ],
      rows: SIXTY_ROWS,
      rowCount: 60,
      summary: 'Customer 60 spent the most.',
    })
    const { user } = await ask()

    expect(screen.getByText('Customer 60 spent the most.')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['company_name', 'total'])
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 25)
    expect(screen.getByText('Page 1 of 3 · rows 1–25 of 60')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Customer 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Page 2 of 3 · rows 26–50 of 60')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Customer 26' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Page 3 of 3 · rows 51–60 of 60')).toBeInTheDocument()
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(1 + 10)
    expect(screen.getByRole('cell', { name: '630' })).toBeInTheDocument() // 60 × 10.5, number-formatted
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('copies the SQL to the clipboard', async () => {
    answerWith({ sql: 'SELECT count(*) FROM orders' })
    const { user } = await ask()

    await user.click(screen.getByRole('button', { name: 'Copy SQL' }))

    expect(await navigator.clipboard.readText()).toBe('SELECT count(*) FROM orders')
    expect(screen.getByText('Copied')).toBeInTheDocument()
  })

  it('shows a banner when the result was truncated', async () => {
    answerWith({ rows: SIXTY_ROWS.map((r) => [r[0] ?? null]), columns: [{ name: 'c', type: 'text', dbType: 'text' }], rowCount: 60, truncated: true })
    await ask()

    expect(screen.getByRole('note')).toHaveTextContent('Showing the first 60 rows. The result was cut off at the row limit')
  })

  it('shows no banner when the result is complete', async () => {
    answerWith({ truncated: false })
    await ask()

    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('says so when the query returned no rows', async () => {
    answerWith({ rows: [], rowCount: 0 })
    await ask()

    expect(screen.getByText('The query ran and returned no rows.')).toBeInTheDocument()
  })
})

describe('other statuses (R2.4, R3.2, R4)', () => {
  const nulls = { columns: null, rows: null, rowCount: null, truncated: null, chart: null, summary: null }

  it('cannot_answer shows the reason and no SQL', async () => {
    answerWith({
      ...nulls,
      status: 'cannot_answer',
      sql: null,
      message: "This can't be answered from this database: there is no salary data.",
    })
    await ask('What is the average salary?')

    expect(screen.getByRole('alert')).toHaveTextContent("This can't be answered from this database: there is no salary data.")
    expect(screen.queryByRole('region', { name: /SQL/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('failed shows the final error and the last attempted SQL', async () => {
    answerWith({
      ...nulls,
      status: 'failed',
      sql: 'SELECT nope FROM orders',
      message: 'column "nope" does not exist',
      attempts: [1, 2, 3].map((n) => ({ n, sql: 'SELECT nope FROM orders', errorCode: 'EXECUTION_ERROR', error: 'column "nope" does not exist', stage: 'execute' as const, latencyMs: 500 })),
    })
    await ask()

    expect(screen.getByRole('alert')).toHaveTextContent('The query could not be run after 3 attempts. column "nope" does not exist')
    expect(within(screen.getByRole('region', { name: 'Last attempted SQL' })).getByText('SELECT nope FROM orders')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('blocked shows the explanation and the rejected SQL', async () => {
    answerWith({
      ...nulls,
      status: 'blocked',
      sql: 'SELECT pg_sleep(10)',
      message: 'The generated query was blocked by the safety validator (FORBIDDEN_FUNCTION: pg_sleep).',
      attempts: [{ n: 1, sql: 'SELECT pg_sleep(10)', errorCode: 'FORBIDDEN_FUNCTION', error: 'pg_sleep', stage: 'validate', latencyMs: 400 }],
    })
    await ask()

    expect(screen.getByRole('alert')).toHaveTextContent('blocked by the safety validator (FORBIDDEN_FUNCTION: pg_sleep)')
    expect(within(screen.getByRole('region', { name: 'Rejected SQL' })).getByText('SELECT pg_sleep(10)')).toBeInTheDocument()
  })
})

describe('attempts (R3.3)', () => {
  it('"Show attempts" lists every attempt with its error and SQL', async () => {
    answerWith({
      attempts: [
        { n: 1, sql: null, errorCode: 'EMPTY_SQL', error: 'The reply contained no SQL query.', stage: 'extract', latencyMs: 300 },
        { n: 2, sql: 'SELECT countryy FROM customers', errorCode: 'EXECUTION_ERROR', error: 'column "countryy" does not exist', stage: 'execute', latencyMs: 450 },
        { n: 3, sql: 'SELECT country FROM customers', errorCode: null, error: null, stage: 'execute', latencyMs: 500 },
      ],
    })
    const { user } = await ask()
    expect(screen.queryByText('Attempt 1')).not.toBeInTheDocument() // hidden until asked for

    await user.click(screen.getByRole('button', { name: 'Show attempts (3)' }))

    const items = within(screen.getByRole('region', { name: 'Attempts' })).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Attempt 1 · ended at reading the model reply')
    expect(items[0]).toHaveTextContent('EMPTY_SQL: The reply contained no SQL query.')
    expect(items[0]).toHaveTextContent('No SQL in the reply.')
    expect(items[1]).toHaveTextContent('EXECUTION_ERROR: column "countryy" does not exist')
    expect(items[1]).toHaveTextContent('SELECT countryy FROM customers')
    expect(items[2]).toHaveTextContent('succeeded')
    expect(screen.getByRole('button', { name: 'Hide attempts' })).toHaveAttribute('aria-expanded', 'true')
  })
})

describe('request handling', () => {
  it('shows API errors with their reference', async () => {
    server.use(http.post('*/api/ask', () =>
      HttpResponse.json({ error: { code: 'RATE_LIMITED', message: 'Too many questions, wait a minute.', correlationId: 'ref-429' } }, { status: 429 })))
    signInAs()
    const { user } = renderApp('/')

    await user.type(screen.getByLabelText('Question'), 'q?')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many questions, wait a minute.')
    expect(alert).toHaveTextContent('Reference: ref-429')
  })

  it('sends the trimmed question and blocks empty or over-long ones', async () => {
    let sent: unknown = null
    server.use(http.post('*/api/ask', async ({ request }) => {
      sent = await request.json()
      return HttpResponse.json(askResponse())
    }))
    signInAs()
    const { user } = renderApp('/')
    const ask = screen.getByRole('button', { name: 'Ask' })
    expect(ask).toBeDisabled()

    const box = screen.getByLabelText('Question')
    await user.click(box)
    await user.paste('x'.repeat(1001))
    expect(ask).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('longer than 1000 characters')

    await user.clear(box)
    await user.type(box, '  How many orders?  ')
    await user.click(ask)
    await screen.findByRole('article', { name: 'Answer' })
    expect(sent).toEqual({ question: 'How many orders?' })
  })
})
