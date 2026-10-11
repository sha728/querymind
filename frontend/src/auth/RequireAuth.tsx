import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuth } from './AuthContext'

/**
 * Sends signed-out visitors to the login page, remembering where they were going and whether
 * their session was ended by the API (design §13.3: a 401 redirects to login).
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, sessionEnded } = useAuth()
  const location = useLocation()
  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname, expired: sessionEnded }} />
  }
  return children
}
