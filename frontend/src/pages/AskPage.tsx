import { lazy, Suspense, useRef, useState, type SyntheticEvent } from 'react'
import { askQuestion, MAX_QUESTION_CHARS } from '../api/ask'
import { ApiError } from '../api/client'
import type { AskResponse } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { AttemptsList } from '../components/AttemptsList'
import { ResultTable } from '../components/ResultTable'
import { SqlPanel } from '../components/SqlPanel'

// Recharts is most of the bundle: load it only when the first chart is shown.
const ResultChart = lazy(() => import('../components/ResultChart').then((m) => ({ default: m.ResultChart })))

interface RequestError {
  message: string
  correlationId: string | null
}

/** Ask a question, see the answer (R2.1, R5, U1, U2). */
export function AskPage() {
  const { user, logout } = useAuth()
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AskResponse | null>(null)
  const [error, setError] = useState<RequestError | null>(null)
  const inFlight = useRef<AbortController | null>(null)

  const trimmed = question.trim()
  const tooLong = trimmed.length > MAX_QUESTION_CHARS

  async function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault()
    if (trimmed.length === 0 || tooLong || busy) return
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    setBusy(true)
    setError(null)
    try {
      setResult(await askQuestion(trimmed, controller.signal))
    } catch (e) {
      if (controller.signal.aborted) return
      setResult(null)
      setError(
        e instanceof ApiError
          ? { message: e.message, correlationId: e.correlationId }
          : { message: 'Could not reach QueryMind. Check your connection and try again.', correlationId: null },
      )
    } finally {
      if (inFlight.current === controller) {
        setBusy(false)
      }
    }
  }

  return (
    <main>
      <header className="app-header">
        <strong>QueryMind</strong>
        <span>
          {user?.email} <button type="button" onClick={logout}>Sign out</button>
        </span>
      </header>

      <h1>Ask about your data</h1>
      <form onSubmit={(e) => void onSubmit(e)} aria-label="Ask a question" className="ask-form">
        <label htmlFor="question">Question</label>
        <textarea
          id="question"
          rows={3}
          placeholder="Which 5 customers spent the most last month?"
          value={question}
          onChange={(e) => { setQuestion(e.target.value) }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) e.currentTarget.form?.requestSubmit()
          }}
        />
        <div className="ask-actions">
          <span className={tooLong ? 'over-limit' : 'muted'}>
            {trimmed.length} / {MAX_QUESTION_CHARS}
          </span>
          <button type="submit" disabled={busy || trimmed.length === 0 || tooLong}>
            {busy ? 'Thinking…' : 'Ask'}
          </button>
        </div>
        {tooLong && <p role="alert">The question is longer than {MAX_QUESTION_CHARS} characters.</p>}
      </form>

      {busy && <p role="status">Generating and running the query…</p>}
      {error !== null && (
        <div role="alert" className="request-error">
          <p>{error.message}</p>
          {error.correlationId !== null && <p className="muted">Reference: {error.correlationId}</p>}
        </div>
      )}
      {result !== null && !busy && <AskResult key={result.historyId} result={result} />}
    </main>
  )
}

function AskResult({ result }: { result: AskResponse }) {
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
