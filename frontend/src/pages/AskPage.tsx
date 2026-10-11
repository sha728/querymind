import { useRef, useState, type SyntheticEvent } from 'react'
import { askQuestion, MAX_QUESTION_CHARS } from '../api/ask'
import type { AskResponse } from '../api/types'
import { AnswerView } from '../components/AnswerView'
import { ErrorNotice } from '../components/ErrorNotice'
import { toRequestError, type RequestError } from '../components/requestError'

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
      {error !== null && <ErrorNotice error={error} />}
      {result !== null && !busy && <AnswerView key={result.historyId} result={result} />}
    </>
  )
}
