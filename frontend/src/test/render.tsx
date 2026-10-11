import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { App } from '../App'

/** Renders the whole app at <path>, as a signed-out (or pre-seeded sessionStorage) visitor. */
export function renderApp(path = '/') {
  const user = userEvent.setup()
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
  return { user, ...view }
}
