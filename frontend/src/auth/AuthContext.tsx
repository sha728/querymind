import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import * as authApi from '../api/auth'
import type { User } from '../api/types'
import { clearSession, loadSession, saveSession } from './session'

interface AuthState {
  user: User | null
  /** True after the API rejected the session (401), until the next login. */
  sessionEnded: boolean
  login: (email: string, password: string) => Promise<User>
  register: (email: string, password: string) => Promise<User>
  logout: () => void
  /** Ends the session because the API no longer accepts it. */
  expire: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => loadSession()?.user ?? null)
  const [sessionEnded, setSessionEnded] = useState(false)

  const login = useCallback(async (email: string, password: string) => {
    const session = saveSession(await authApi.login(email, password))
    setSessionEnded(false)
    setUser(session.user)
    return session.user
  }, [])

  const register = useCallback((email: string, password: string) => authApi.register(email, password), [])

  const logout = useCallback(() => {
    clearSession()
    setSessionEnded(false)
    setUser(null)
  }, [])

  const expire = useCallback(() => {
    clearSession()
    setSessionEnded(true)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, sessionEnded, login, register, logout, expire }),
    [user, sessionEnded, login, register, logout, expire],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}

// eslint-disable-next-line react-refresh/only-export-components -- hook belongs with its provider
export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (auth === null) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return auth
}
