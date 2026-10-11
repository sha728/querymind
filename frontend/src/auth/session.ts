import type { LoginResponse, User } from '../api/types'

// The JWT lives in sessionStorage (design §3.3): it survives a reload but not closing the tab,
// and is never written to localStorage or a cookie.
const TOKEN_KEY = 'qm.token'
const USER_KEY = 'qm.user'
const EXPIRES_KEY = 'qm.expiresAt'

export interface Session {
  token: string
  user: User
  expiresAt: string
}

export function saveSession(login: LoginResponse): Session {
  sessionStorage.setItem(TOKEN_KEY, login.accessToken)
  sessionStorage.setItem(USER_KEY, JSON.stringify(login.user))
  sessionStorage.setItem(EXPIRES_KEY, login.expiresAt)
  return { token: login.accessToken, user: login.user, expiresAt: login.expiresAt }
}

/** The stored session, or null if there is none, it cannot be read, or it has expired. */
export function loadSession(now: Date = new Date()): Session | null {
  const token = sessionStorage.getItem(TOKEN_KEY)
  const userJson = sessionStorage.getItem(USER_KEY)
  const expiresAt = sessionStorage.getItem(EXPIRES_KEY)
  if (token === null || userJson === null || expiresAt === null) {
    return null
  }
  if (new Date(expiresAt) <= now) {
    clearSession()
    return null
  }
  try {
    return { token, user: JSON.parse(userJson) as User, expiresAt }
  } catch {
    clearSession()
    return null
  }
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function clearSession(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USER_KEY)
  sessionStorage.removeItem(EXPIRES_KEY)
}
