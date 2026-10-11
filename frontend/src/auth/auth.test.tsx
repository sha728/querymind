import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fakeApi } from '../test/server'
import { renderApp } from '../test/render'

const PASSWORD = 'correct horse battery'

function seedUser(email: string) {
  fakeApi.users.set(email, { id: 'u-1', password: PASSWORD })
}

describe('login', () => {
  it('stores the token in sessionStorage and opens the app', async () => {
    seedUser('ana@example.com')
    const { user } = renderApp('/login')

    await user.type(screen.getByLabelText('Email'), 'ana@example.com')
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('heading', { name: 'Ask about your data' })).toBeInTheDocument()
    expect(sessionStorage.getItem('qm.token')).toBe('token-u-1')
    expect(localStorage.length).toBe(0) // never localStorage
    expect(screen.getByText('ana@example.com')).toBeInTheDocument()
  })

  it('shows the API message on a wrong password and stores nothing', async () => {
    seedUser('ana@example.com')
    const { user } = renderApp('/login')

    await user.type(screen.getByLabelText('Email'), 'ana@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    expect(sessionStorage.getItem('qm.token')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Sign in to QueryMind' })).toBeInTheDocument()
  })

  it('sends signed-out visitors to login', () => {
    renderApp('/')

    expect(screen.getByRole('heading', { name: 'Sign in to QueryMind' })).toBeInTheDocument()
  })
})

describe('register', () => {
  it('registers, then logs in with the new account', async () => {
    const { user } = renderApp('/register')

    await user.type(screen.getByLabelText('Email'), 'ben@example.com')
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Account created. Sign in to continue.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveValue('ben@example.com')
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('heading', { name: 'Ask about your data' })).toBeInTheDocument()
    expect(sessionStorage.getItem('qm.token')).toMatch(/^token-/)
  })

  it('shows the API message when the email is taken', async () => {
    seedUser('ben@example.com')
    const { user } = renderApp('/register')

    await user.type(screen.getByLabelText('Email'), 'ben@example.com')
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('An account with this email already exists.')
  })

  it('checks the password length before calling the API', async () => {
    const { user } = renderApp('/register')

    await user.type(screen.getByLabelText('Email'), 'ben@example.com')
    await user.type(screen.getByLabelText('Password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Password must be at least 8 characters.')
    expect(fakeApi.users.size).toBe(0)
  })
})

describe('session expiry', () => {
  it('redirects to login on any 401 and clears the session', async () => {
    // A stored session whose token the API no longer accepts (expired or revoked).
    sessionStorage.setItem('qm.token', 'revoked-token')
    sessionStorage.setItem('qm.user', JSON.stringify({ id: 'u-9', email: 'old@example.com', role: 'user' }))
    sessionStorage.setItem('qm.expiresAt', new Date(Date.now() + 60_000).toISOString())

    const { user } = renderApp('/')
    await user.type(screen.getByLabelText('Question'), 'How many orders?')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    expect(await screen.findByText('Your session has ended. Please sign in again.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sign in to QueryMind' })).toBeInTheDocument()
    expect(sessionStorage.getItem('qm.token')).toBeNull()
  })

  it('treats a stored session past its expiry as signed out', () => {
    sessionStorage.setItem('qm.token', 'old-token')
    sessionStorage.setItem('qm.user', JSON.stringify({ id: 'u-9', email: 'old@example.com', role: 'user' }))
    sessionStorage.setItem('qm.expiresAt', new Date(Date.now() - 1000).toISOString())

    renderApp('/')

    expect(screen.getByRole('heading', { name: 'Sign in to QueryMind' })).toBeInTheDocument()
    expect(sessionStorage.getItem('qm.token')).toBeNull()
  })
})
