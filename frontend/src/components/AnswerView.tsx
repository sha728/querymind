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
        <article aria-label="Answer" className="answer">
          {result.summary !== null && (
            <div className="card">
              <p className="summary">
                <span className="summary-icon" aria-hidden="true">
                  <SparkIcon />
                </span>
                <span>{result.summary}</span>
              </p>
            </div>
          )}
          {result.truncated === true && (
            <p role="note" className="truncated-banner">
              Showing the first {result.rowCount} rows. The result was cut off at the row limit, so there are more rows
              in the database.
            </p>
          )}
          {result.chart !== null && result.columns !== null && result.rows !== null && result.rows.length > 0 && (
            <ChartCard result={result} />
          )}
          {result.sql !== null && <SqlPanel sql={result.sql} />}
          <div className="card">
            <ResultTable columns={result.columns ?? []} rows={result.rows ?? []} />
          </div>
          <Footer result={result} />
        </article>
      )
    case 'cannot_answer':
      return (
        <article aria-label="Answer" className="answer">
          <p role="alert" className="outcome cannot-answer">{result.message}</p>
          <Footer result={result} />
        </article>
      )
    case 'failed':
      return (
        <article aria-label="Answer" className="answer">
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
        <article aria-label="Answer" className="answer">
          <p role="alert" className="outcome blocked">{result.message}</p>
          {result.sql !== null && <SqlPanel sql={result.sql} label="Rejected SQL" />}
          <Footer result={result} />
        </article>
      )
  }
}

function ChartCard({ result }: { result: AskResponse }) {
  if (result.chart === null || result.columns === null || result.rows === null) return null
  // A "table only" recommendation renders nothing inside; the card then stays out of the way.
  if (result.chart.allowed.length === 1 && result.chart.allowed[0] === 'table') return null
  return (
    <div className="card">
      <Suspense fallback={<p className="muted">Loading chart…</p>}>
        <ResultChart chart={result.chart} columns={result.columns} rows={result.rows} />
      </Suspense>
    </div>
  )
}

function Footer({ result }: { result: AskResponse }) {
  const usage = result.usage
  const tokens = usage?.promptTokens != null && usage.completionTokens != null
    ? `${String(usage.promptTokens + usage.completionTokens)} tokens`
    : null
  return (
    <footer className="answer-footer">
      <AttemptsList attempts={result.attempts} />
      <p className="meta">
        <span>{(result.timings.totalMs / 1000).toFixed(1)} s</span>
        {tokens !== null && <span>{tokens}</span>}
        {usage?.model != null && <span>{usage.model}</span>}
        <span title="Reference for support">ref {result.correlationId}</span>
      </p>
    </footer>
  )
}

function SparkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    </svg>
  )
}
