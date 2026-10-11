import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { setUnauthorizedHandler } from './api/client'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { LoginPage } from './auth/LoginPage'
import { RegisterPage } from './auth/RegisterPage'
import { RequireAuth } from './auth/RequireAuth'
import { AppLayout } from './components/AppLayout'
import { AskPage } from './pages/AskPage'
import { HistoryPage } from './pages/HistoryPage'
import { SchemaPage } from './pages/SchemaPage'

/**
 * Any 401 on a signed-in request ends the session (design §13.3). RequireAuth then redirects to
 * login with a "session has ended" note: one redirect path, no race with route transitions.
 */
function SessionExpiry() {
  const { expire } = useAuth()
  useEffect(() => setUnauthorizedHandler(expire), [expire])
  return null
}

export function App() {
  return (
    <AuthProvider>
      <SessionExpiry />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
          <Route index element={<AskPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="schema" element={<SchemaPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}
