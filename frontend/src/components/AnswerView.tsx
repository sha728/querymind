import { lazy, Suspense } from 'react'
import type { AskResponse } from '../api/types'
import { AttemptsList } from './AttemptsList'
import { ResultTable } from './ResultTable'
import { SqlPanel } from './SqlPanel'

// Recharts is most of the bundle: load it only when the first chart is shown.
const ResultChart = lazy(() => import('./ResultChart').then((m) => ({ default: m.ResultChart })))

/** One answer from /api/ask or a re-run, laid out by status (design §4.2, R2.4, R3, R5, R6). */
export function AnswerView({ result }: { result: AskResponse }) {
  switch (result.status) {
    case 'success':
      return (
        <article aria-label="Answer">
          {result.summary !== null && <p className="summary">{result.summary}</p>}
          {result.truncated === true && (
            <p role="note" className="truncated-banner">
              Showing the first {result.rowCount} rows. The result was cut off at the row limit, so there are more rows
              in the database.
            </p>
          )}
          {result.chart !== null && result.columns !== null && result.rows !== null && result.rows.length > 0 && (
            <Suspense fallback={<p className="muted">Loading chart…</p>}>
              <ResultChart chart={result.chart} columns={result.columns} rows={result.rows} />
            </Suspense>
          )}
          {result.sql !== null && <SqlPanel sql={result.sql} />}
          <ResultTable columns={result.columns ?? []} rows={result.rows ?? []} />
          <Footer result={result} />
        </article>
      )
    case 'cannot_answer':
      return (
        <article aria-label="Answer">
          <p role="alert" className="outcome cannot-answer">{result.message}</p>
          <Footer result={result} />
        </article>
      )
    case 'failed':
      return (
        <article aria-label="Answer">
          <p role="alert" className="outcome failed">
            The query could not be run after {result.attempts.length} attempt{result.attempts.length === 1 ? '' : 's'}.{' '}
            {result.message}
          </p>
          {result.sql !== null && <SqlPanel sql={result.sql} label="Last attempted SQL" />}
          <Footer result={result} />
        </article>
      )
    case 'blocked':
      return (
        <article aria-label="Answer">
          <p role="alert" className="outcome blocked">{result.message}</p>
          {result.sql !== null && <SqlPanel sql={result.sql} label="Rejected SQL" />}
          <Footer result={result} />
        </article>
      )
  }
}

function Footer({ result }: { result: AskResponse }) {
  const tokens = result.usage?.promptTokens != null && result.usage.completionTokens != null
    ? `${String(result.usage.promptTokens + result.usage.completionTokens)} tokens`
    : null
  return (
    <footer className="answer-footer">
      <AttemptsList attempts={result.attempts} />
      <p className="muted">
        {(result.timings.totalMs / 1000).toFixed(1)} s{tokens === null ? '' : ` · ${tokens}`}
        {result.usage?.model != null ? ` · ${result.usage.model}` : ''} · ref {result.correlationId}
      </p>
    </footer>
  )
}
