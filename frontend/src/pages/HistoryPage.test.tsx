import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { HistoryItem } from '../api/types'
import { renderApp } from '../test/render'
import { askResponse, server, signInAs } from '../test/server'

function item(n: number, extra: Partial<HistoryItem> = {}): HistoryItem {
  return {
    id: `h-${String(n)}`,
    question: `Question ${String(n)}?`,
    status: 'success',
    finalSql: 'SELECT 1',
    rowCount: n,
    totalMs: 1500,
    createdAt: '2026-10-09T08:00:00Z',
    ...extra,
  }
}

/** A fake history of <total> entries, newest first; records each list request's URL. */
function historyOf(total: number, requests: URL[] = [], extra: (n: number) => Partial<HistoryItem> = () => ({})) {
  const all = Array.from({ length: total }, (_, i) => item(total - i, extra(total - i)))
  const respond = ({ request }: { request: Request }) => {
    const url = new URL(request.url)
    requests.push(url)
    const page = Number(url.searchParams.get('page') ?? '1')
    const size = Number(url.searchParams.get('pageSize') ?? '20')
    return HttpResponse.json({ page, pageSize: size, total, items: all.slice((page - 1) * size, page * size) })
  }
  server.use(http.get('*/api/history', respond), http.get('*/api/admin/history', respond))
  return requests
}

function historyRows() {
  return within(screen.getByRole('table', { name: 'Question history' })).getAllByRole('row').slice(1)
}

describe('history list (R7.1)', () => {
  it('is paged, 20 per page', async () => {
    const requests = historyOf(45)
    signInAs()
    const { user } = renderApp('/history')

    expect(await screen.findByText('Page 1 of 3 · 45 questions')).toBeInTheDocument()
    expect(historyRows()).toHaveLength(20)
    expect(historyRows()[0]).toHaveTextContent('Question 45?')

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Page 2 of 3 · 45 questions')).toBeInTheDocument()
    expect(historyRows()[0]).toHaveTextContent('Question 25?')

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Page 3 of 3 · 45 questions')).toBeInTheDocument()
    expect(historyRows()).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()

    expect(requests.map((u) => `${u.pathname}?${u.searchParams.toString()}`)).toEqual([
      '/api/history?page=1&pageSize=20',
      '/api/history?page=2&pageSize=20',
      '/api/history?page=3&pageSize=20',
    ])
  })

  it('shows each outcome in words', async () => {
    historyOf(3, [], (n) => ({ status: (['success', 'blocked', 'error'] as const)[n - 1] ?? 'success' }))
    signInAs()
    renderApp('/history')

    await screen.findByText('Page 1 of 1 · 3 questions')
    expect(historyRows().map((r) => within(r).getAllByRole('cell')[2]?.textContent)).toEqual(['Service error', 'Blocked', 'Answered'])
  })

  it('says so when there is no history', async () => {
    historyOf(0)
    signInAs()
    renderApp('/history')

    expect(await screen.findByText('No questions yet. Ask one on the Ask page.')).toBeInTheDocument()
  })
})

describe('re-run (R7.2)', () => {
  it('calls /rerun, shows the new result and refreshes the list', async () => {
    const requests = historyOf(2)
    const reruns: string[] = []
    server.use(http.post('*/api/history/:id/rerun', ({ params }) => {
      reruns.push(String(params.id))
      return HttpResponse.json(askResponse({ question: 'Question 2?', summary: 'Fresh answer: 42.' }))
    }))
    signInAs()
    const { user } = renderApp('/history')
    await screen.findByText('Page 1 of 1 · 2 questions')

    await user.click(screen.getByRole('button', { name: 'Re-run: Question 2?' }))

    const result = await screen.findByRole('region', { name: 'Re-run result' })
    expect(reruns).toEqual(['h-2'])
    expect(within(result).getByText('Re-run: Question 2?')).toBeInTheDocument()
    expect(within(result).getByText('Fresh answer: 42.')).toBeInTheDocument()
    expect(within(result).getByRole('table')).toBeInTheDocument()
    expect(requests).toHaveLength(2) // the list was loaded again
  })

  it('shows the error when a re-run fails', async () => {
    historyOf(1)
    server.use(http.post('*/api/history/:id/rerun', () =>
      HttpResponse.json({ error: { code: 'LLM_RATE_LIMITED', message: 'The language model is busy. Try again shortly.', correlationId: 'ref-9' } }, { status: 503 })))
    signInAs()
    const { user } = renderApp('/history')
    await screen.findByText('Page 1 of 1 · 1 questions')

    await user.click(screen.getByRole('button', { name: 'Re-run: Question 1?' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The language model is busy. Try again shortly.')
    expect(screen.getByRole('alert')).toHaveTextContent('Reference: ref-9')
  })
})

describe('admin view (R8.2)', () => {
  it('admins get an "All users" toggle that lists everyone with emails', async () => {
    const requests = historyOf(2, [], (n) => ({ userEmail: n === 2 ? 'bob@example.com' : 'alice@example.com' }))
    signInAs('admin@example.com', 'admin')
    const { user } = renderApp('/history')
    await screen.findByText('Page 1 of 1 · 2 questions')
    expect(screen.queryByRole('columnheader', { name: 'User' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'All users' }))

    expect(await screen.findByRole('columnheader', { name: 'User' })).toBeInTheDocument()
    expect(historyRows().map((r) => within(r).getAllByRole('cell')[1]?.textContent)).toEqual(['bob@example.com', 'alice@example.com'])
    expect(requests.at(-1)?.pathname).toBe('/api/admin/history')
  })

  it('users get no toggle', async () => {
    historyOf(1)
    signInAs()
    renderApp('/history')

    await screen.findByText('Page 1 of 1 · 1 questions')
    expect(screen.queryByRole('checkbox', { name: 'All users' })).not.toBeInTheDocument()
  })
})

describe('navigation', () => {
  it('links Ask, History and Schema', async () => {
    historyOf(0)
    signInAs()
    const { user } = renderApp('/')

    await user.click(screen.getByRole('link', { name: 'History' }))

    expect(await screen.findByRole('heading', { name: 'History' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Schema' })).toHaveAttribute('href', '/schema')
  })
})
