import { useEffect, useState } from 'react'
import { apiFetch, ApiError } from './api/client'
import type { SchemaResponse } from './api/types'
import { useAuth } from './auth/AuthContext'

/** Signed-in landing page. Replaced by the Ask page in T41; for now it shows the connected schema. */
export function HomePage() {
  const { user, logout } = useAuth()
  const [schema, setSchema] = useState<SchemaResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    apiFetch<SchemaResponse>('/api/schema', { signal: controller.signal })
      .then(setSchema)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status !== 401) {
          setError(e.message)
        }
      })
    return () => { controller.abort() }
  }, [])

  return (
    <main>
      <header>
        <span>Signed in as {user?.email}</span> <button type="button" onClick={logout}>Sign out</button>
      </header>
      <h1>QueryMind</h1>
      {schema !== null && <p>Connected to a database with {schema.tables.length} tables.</p>}
      {error !== null && <p role="alert">{error}</p>}
    </main>
  )
}
