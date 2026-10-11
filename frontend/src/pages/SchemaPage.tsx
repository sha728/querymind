import { useEffect, useState } from 'react'
import { getSchema } from '../api/history'
import type { SchemaResponse } from '../api/types'
import { ErrorNotice } from '../components/ErrorNotice'
import { toRequestError, type RequestError } from '../components/requestError'

const dateFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })

/** The connected database's tables, columns, keys and sample values (R1.3), to help phrase questions. */
export function SchemaPage() {
  const [schema, setSchema] = useState<SchemaResponse | null>(null)
  const [error, setError] = useState<RequestError | null>(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    getSchema(controller.signal)
      .then(setSchema)
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(toRequestError(e, 'Could not load the schema.'))
      })
    return () => { controller.abort() }
  }, [])

  const needle = filter.trim().toLowerCase()
  const tables = (schema?.tables ?? []).filter(
    (t) => needle === '' || t.name.toLowerCase().includes(needle) || t.columns.some((c) => c.name.toLowerCase().includes(needle)),
  )

  return (
    <>
      <div className="page-header toolbar">
        <div>
          <h1>Schema</h1>
          <p className="lede">The tables QueryMind can read. Use their names and sample values to phrase questions.</p>
        </div>
      </div>
      {error !== null && <ErrorNotice error={error} />}
      {schema !== null && (
        <>
          <div className="toolbar">
            <label className="filter">
              Find a table or column
              <input type="search" value={filter} onChange={(e) => { setFilter(e.target.value) }} />
            </label>
            <p className="muted">
              {schema.tables.length} tables · read {dateFormat.format(new Date(schema.introspectedAt))}
            </p>
          </div>
          {tables.length === 0 && <div className="card empty-state">No table or column matches “{filter}”.</div>}
          {tables.map((table) => {
            const references = new Map(
              table.foreignKeys.flatMap((fk) => fk.columns.map((col, i) => [col, `${fk.refTable}.${fk.refColumns[i] ?? ''}`] as const)),
            )
            return (
              <section key={table.name} aria-label={table.name} className="card schema-table">
                <div className="schema-header">
                  <h2>{table.name}</h2>
                  <span className="muted">{table.columns.length} columns</span>
                </div>
                <table>
                  <colgroup>
                    <col className="c-name" />
                    <col className="c-type" />
                    <col className="c-key" />
                    <col className="c-null" />
                    <col />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">Column</th>
                      <th scope="col">Type</th>
                      <th scope="col">Key</th>
                      <th scope="col">Nullable</th>
                      <th scope="col">Sample values</th>
                    </tr>
                  </thead>
                  <tbody>
                    {table.columns.map((column) => {
                      const ref = references.get(column.name)
                      return (
                        <tr key={column.name}>
                          <td><code>{column.name}</code></td>
                          <td>{column.type}</td>
                          <td>
                            {column.primaryKey && <span className="key key-pk">PK</span>}
                            {column.primaryKey && ref !== undefined && <span className="visually-hidden">, </span>}
                            {ref !== undefined && <span className="key key-fk">FK → {ref}</span>}
                          </td>
                          <td>{column.nullable ? 'yes' : 'no'}</td>
                          <td className="muted">{column.samples.join(', ')}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </section>
            )
          })}
        </>
      )}
    </>
  )
}
