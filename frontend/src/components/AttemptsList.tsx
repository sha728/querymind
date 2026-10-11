import { useState } from 'react'
import type { AskAttempt } from '../api/types'

const STAGE_LABELS: Record<AskAttempt['stage'], string> = {
  extract: 'reading the model reply',
  validate: 'safety check',
  execute: 'running the query',
}

/** Every generation attempt, hidden until asked for (R3.3, U2). */
export function AttemptsList({ attempts }: { attempts: AskAttempt[] }) {
  const [open, setOpen] = useState(false)
  if (attempts.length === 0) {
    return null
  }

  return (
    <section className="attempts" aria-label="Attempts">
      <button type="button" aria-expanded={open} onClick={() => { setOpen(!open) }}>
        {open ? 'Hide attempts' : `Show attempts (${String(attempts.length)})`}
      </button>
      {open && (
        <ol>
          {attempts.map((a) => (
            <li key={a.n}>
              <p>
                <strong>Attempt {a.n}</strong> · ended at {STAGE_LABELS[a.stage]} · {a.latencyMs} ms
                {a.errorCode === null ? ' · succeeded' : ''}
              </p>
              {a.errorCode !== null && (
                <p className="attempt-error">
                  {a.errorCode}{a.error === null ? '' : `: ${a.error}`}
                </p>
              )}
              {a.sql === null ? <p className="muted">No SQL in the reply.</p> : <pre><code>{a.sql}</code></pre>}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
