import { NavLink, Outlet } from 'react-router'
import { useAuth } from '../auth/AuthContext'

/** Header with navigation for signed-in pages. */
export function AppLayout() {
  const { user, logout } = useAuth()
  return (
    <main>
      <header className="app-header">
        <nav aria-label="Main">
          <strong>QueryMind</strong>
          <NavLink to="/" end>Ask</NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/schema">Schema</NavLink>
        </nav>
        <span>
          {user?.email}
          {user?.role === 'admin' && <span className="badge">admin</span>}{' '}
          <button type="button" onClick={logout}>Sign out</button>
        </span>
      </header>
      <Outlet />
    </main>
  )
}
