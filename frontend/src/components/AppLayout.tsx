import { Link, NavLink, Outlet } from 'react-router'
import { useAuth } from '../auth/AuthContext'
import { Logo } from './Logo'

/** Top bar with navigation for signed-in pages. */
export function AppLayout() {
  const { user, logout } = useAuth()
  const initial = (user?.email.charAt(0) ?? '?').toUpperCase()
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand" aria-label="QueryMind home">
            <Logo />
            QueryMind
          </Link>
          <nav aria-label="Main">
            <NavLink to="/" end>Ask</NavLink>
            <NavLink to="/history">History</NavLink>
            <NavLink to="/schema">Schema</NavLink>
          </nav>
          <div className="user-menu">
            <span className="avatar" aria-hidden="true">{initial}</span>
            <span className="email">{user?.email}</span>
            {user?.role === 'admin' && <span className="badge">admin</span>}
            <button type="button" className="ghost small" onClick={logout}>Sign out</button>
          </div>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </>
  )
}
