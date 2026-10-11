import { useState, type SyntheticEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { ApiError } from '../api/client'
import { useAuth } from './AuthContext'

interface LocationState {
  from?: string
  registered?: string
  expired?: boolean
}

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const state = (useLocation().state ?? {}) as LocationState
  const [email, setEmail] = useState(state.registered ?? '')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
      await navigate(state.from ?? '/', { replace: true })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not sign in. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <h1>Sign in to QueryMind</h1>
      {state.registered !== undefined && <p role="status">Account created. Sign in to continue.</p>}
      {state.expired === true && <p role="status">Your session has ended. Please sign in again.</p>}
      <form onSubmit={(e) => void onSubmit(e)} aria-label="Sign in">
        <label>
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => { setEmail(e.target.value) }} />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => { setPassword(e.target.value) }} />
        </label>
        {error !== null && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
      <p>
        No account? <Link to="/register">Create one</Link>
      </p>
    </main>
  )
}
