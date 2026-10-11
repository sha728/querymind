import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { fakeApi, server } from '../test/server'
import { apiFetch, ApiError, setUnauthorizedHandler } from './client'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

describe('apiFetch', () => {
  it('sends a fresh X-Correlation-ID with every request', async () => {
    await apiFetch('/api/auth/register', { method: 'POST', body: { email: 'a@example.com', password: 'long enough' } })
    await apiFetch('/api/auth/register', { method: 'POST', body: { email: 'b@example.com', password: 'long enough' } })

    expect(fakeApi.correlationIds).toHaveLength(2)
    const [first, second] = fakeApi.correlationIds
    expect(first).toMatch(UUID_V4)
    expect(second).toMatch(UUID_V4)
    expect(first).not.toBe(second)
  })

  it('sends the stored token as a bearer token', async () => {
    let auth: string | null = null
    server.use(http.get('*/api/history', ({ request }) => {
      auth = request.headers.get('Authorization')
      return HttpResponse.json({ items: [] })
    }))
    sessionStorage.setItem('qm.token', 'abc')

    await apiFetch('/api/history')

    expect(auth).toBe('Bearer abc')
  })

  it('turns the error envelope into an ApiError', async () => {
    server.use(http.post('*/api/ask', () =>
      HttpResponse.json({ error: { code: 'RATE_LIMITED', message: 'Too many questions, wait a minute.', correlationId: 'c-1' } }, { status: 429 })))

    const error = await apiFetch('/api/ask', { method: 'POST', body: { question: 'q' } }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 429, code: 'RATE_LIMITED', message: 'Too many questions, wait a minute.', correlationId: 'c-1' })
  })

  it('on 401 with a token: clears the session and calls the handler', async () => {
    const handler = vi.fn()
    const remove = setUnauthorizedHandler(handler)
    sessionStorage.setItem('qm.token', 'revoked')

    await expect(apiFetch('/api/schema')).rejects.toMatchObject({ status: 401 })

    expect(handler).toHaveBeenCalledOnce()
    expect(sessionStorage.getItem('qm.token')).toBeNull()
    remove()
  })

  it('on 401 without a token (wrong password): only throws', async () => {
    const handler = vi.fn()
    const remove = setUnauthorizedHandler(handler)

    await expect(apiFetch('/api/auth/login', { method: 'POST', body: { email: 'x@example.com', password: 'nope' } }))
      .rejects.toMatchObject({ status: 401, code: 'INVALID_CREDENTIALS' })

    expect(handler).not.toHaveBeenCalled()
    remove()
  })
})
