import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { SchemaResponse } from '../api/types'
import { renderApp } from '../test/render'
import { server, signInAs } from '../test/server'

const SCHEMA: SchemaResponse = {
  schemaHash: 'hash-1',
  introspectedAt: '2026-10-09T08:00:00Z',
  dialect: 'postgres',
  tables: [
    {
      name: 'orders',
      columns: [
        { name: 'order_id', type: 'integer', nullable: false, primaryKey: true, samples: ['10248', '10249'] },
        { name: 'customer_id', type: 'text', nullable: true, primaryKey: false, samples: ['VINET', 'TOMSP'] },
        { name: 'order_date', type: 'date', nullable: true, primaryKey: false, samples: ['2026-07-04'] },
      ],
      foreignKeys: [{ columns: ['customer_id'], refTable: 'customers', refColumns: ['customer_id'] }],
    },
    {
      name: 'customers',
      columns: [{ name: 'customer_id', type: 'text', nullable: false, primaryKey: true, samples: ['ALFKI'] }],
      foreignKeys: [],
    },
  ],
}

function cellsOf(table: string, column: string) {
  const section = screen.getByRole('region', { name: table })
  const row = within(section).getAllByRole('row').find((r) => within(r).queryByText(column) !== null)
  if (row === undefined) throw new Error(`no row for ${table}.${column}`)
  return within(row).getAllByRole('cell').map((c) => c.textContent)
}

describe('schema browser (R1.3)', () => {
  it('lists tables, columns, types, keys and samples from /api/schema', async () => {
    server.use(http.get('*/api/schema', () => HttpResponse.json(SCHEMA)))
    signInAs()
    renderApp('/schema')

    expect(await screen.findByText(/^2 tables · read /)).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['orders', 'customers'])
    expect(cellsOf('orders', 'order_id')).toEqual(['order_id', 'integer', 'PK', 'no', '10248, 10249'])
    expect(cellsOf('orders', 'customer_id')).toEqual(['customer_id', 'text', 'FK → customers.customer_id', 'yes', 'VINET, TOMSP'])
    expect(cellsOf('orders', 'order_date')).toEqual(['order_date', 'date', '', 'yes', '2026-07-04'])
    expect(cellsOf('customers', 'customer_id')).toEqual(['customer_id', 'text', 'PK', 'no', 'ALFKI'])
  })

  it('filters by table or column name', async () => {
    server.use(http.get('*/api/schema', () => HttpResponse.json(SCHEMA)))
    signInAs()
    const { user } = renderApp('/schema')
    await screen.findByText(/^2 tables/)

    await user.type(screen.getByRole('searchbox', { name: 'Find a table or column' }), 'order_date')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['orders'])

    await user.clear(screen.getByRole('searchbox'))
    await user.type(screen.getByRole('searchbox'), 'nothing-like-this')
    expect(screen.getByText('No table or column matches “nothing-like-this”.')).toBeInTheDocument()
  })

  it('shows the error when the engine is not ready', async () => {
    server.use(http.get('*/api/schema', () =>
      HttpResponse.json({ error: { code: 'TARGET_DB_UNAVAILABLE', message: 'Database unavailable.', correlationId: 'ref-s' } }, { status: 503 })))
    signInAs()
    renderApp('/schema')

    expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable.')
  })
})
