import { useRef, useState, type SyntheticEvent } from 'react'
import { askQuestion, MAX_QUESTION_CHARS } from '../api/ask'
import type { AskResponse } from '../api/types'
import { AnswerView } from '../components/AnswerView'
import { ErrorNotice } from '../components/ErrorNotice'
import { toRequestError, type RequestError } from '../components/requestError'

// Starting points for the Northwind demo; clicking one fills the box (it is not sent).
const EXAMPLES = [
  'Which 5 customers spent the most last month?',
  'How many orders were placed each year?',
  'What is the revenue by product category?',
  'Which employee handled the most orders?',
]

/** Ask a question, see the answer (R2.1, R5, U1, U2). */
export function AskPage() {
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
      setError(toRequestError(e, 'Could not reach QueryMind. Check your connection and try again.'))
    } finally {
      if (inFlight.current === controller) {
        setBusy(false)
      }
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Ask about your data</h1>
        <p className="lede">
          Ask a question in plain English. QueryMind writes a read-only SQL query, runs it and shows the answer.
        </p>
      </div>

      <section className="card">
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
            <span className="hint muted">
              <span className={tooLong ? 'over-limit' : undefined}>{trimmed.length} / {MAX_QUESTION_CHARS}</span>
              {' · '}Ctrl+Enter to send
            </span>
            <button type="submit" disabled={busy || trimmed.length === 0 || tooLong}>
              {busy ? 'Thinking…' : 'Ask'}
            </button>
          </div>
          {tooLong && <p role="alert" className="form-error">The question is longer than {MAX_QUESTION_CHARS} characters.</p>}
          <div className="examples">
            <span className="examples-label">Try:</span>
            {EXAMPLES.map((example) => (
              <button key={example} type="button" className="chip" disabled={busy} onClick={() => { setQuestion(example) }}>
                {example}
              </button>
            ))}
          </div>
        </form>
      </section>

      {busy && (
        <div role="status" className="card loading">
          <span className="spinner" aria-hidden="true" />
          Generating and running the query…
        </div>
      )}
      {error !== null && <ErrorNotice error={error} />}
      {result !== null && !busy && <AnswerView key={result.historyId} result={result} />}
    </>
  )
}
