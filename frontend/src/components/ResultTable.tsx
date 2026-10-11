import { useState } from 'react'
import type { AskColumn, Cell } from '../api/types'
import { formatCell } from './format'

const ROWS_PER_PAGE = 25

const NUMERIC_TYPES = new Set<AskColumn['type']>(['integer', 'numeric'])

interface Props {
  columns: AskColumn[]
  rows: Cell[][]
  rowsPerPage?: number
}

/**
 * The result grid with client-side pagination (design §3.3, R5.1). Remount it (key) for a new
 * result so it starts again on page 1.
 */
export function ResultTable({ columns, rows, rowsPerPage = ROWS_PER_PAGE }: Props) {
  const [page, setPage] = useState(1)
  const pages = Math.max(1, Math.ceil(rows.length / rowsPerPage))
  const start = (page - 1) * rowsPerPage
  const visible = rows.slice(start, start + rowsPerPage)

  if (rows.length === 0) {
    return <p className="empty-result">The query ran and returned no rows.</p>
  }

  return (
    <section aria-label="Result">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.name} scope="col" title={c.dbType}>{c.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr key={start + i}>
                {columns.map((c, j) => {
                  const value = row[j] ?? null
                  return (
                    <td key={c.name} className={[NUMERIC_TYPES.has(c.type) ? 'num' : '', value === null ? 'null' : ''].join(' ').trim() || undefined}>
                      {formatCell(value)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <nav className="pager" aria-label="Pages">
        <button type="button" onClick={() => { setPage(page - 1) }} disabled={page === 1}>Previous</button>
        <span>
          Page {page} of {pages} · rows {start + 1}–{start + visible.length} of {rows.length}
        </span>
        <button type="button" onClick={() => { setPage(page + 1) }} disabled={page === pages}>Next</button>
      </nav>
    </section>
  )
}
