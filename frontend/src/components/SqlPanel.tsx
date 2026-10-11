import { useState } from 'react'

interface Props {
  sql: string
  label?: string
}

/** The SQL that ran (or was rejected), with a copy button (R5.2). */
export function SqlPanel({ sql, label = 'SQL' }: Props) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')

  async function copy() {
    try {
      await navigator.clipboard.writeText(sql)
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }

  return (
    <section className="sql-panel" aria-label={label}>
      <div className="sql-header">
        <h2>{label}</h2>
        <button type="button" onClick={() => void copy()}>Copy SQL</button>
        <span role="status">
          {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed: select the text instead' : ''}
        </span>
      </div>
      <pre><code>{sql}</code></pre>
    </section>
  )
}
