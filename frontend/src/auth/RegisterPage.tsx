import { useState, type SyntheticEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError } from '../api/client'
import { useAuth } from './AuthContext'

const MIN_PASSWORD_LENGTH = 8 // design §4.2

export function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault()
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${String(MIN_PASSWORD_LENGTH)} characters.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const user = await register(email, password)
      await navigate('/login', { state: { registered: user.email } })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not create the account. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <h1>Create an account</h1>
      <form onSubmit={(e) => void onSubmit(e)} aria-label="Create account">
        <label>
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => { setEmail(e.target.value) }} />
        </label>
        <label>
          Password
          <input type="password" autoComplete="new-password" required value={password} onChange={(e) => { setPassword(e.target.value) }} />
        </label>
        {error !== null && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create account'}</button>
      </form>
      <p>
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </main>
  )
}
