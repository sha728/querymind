import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

/**
 * A fake QueryMind API for tests (design §13.3). Keeps registered users in memory and records
 * the X-Correlation-ID of every request it sees.
 */
export const fakeApi = {
  users: new Map<string, { id: string; password: string }>(),
  correlationIds: [] as string[],
  validTokens: new Set<string>(),
  reset() {
    this.users.clear()
    this.correlationIds.length = 0
    this.validTokens.clear()
  },
}

function envelope(code: string, message: string, status: number) {
  return HttpResponse.json({ error: { code, message, correlationId: 'corr-from-server' } }, { status })
}

export const handlers = [
  http.all('*', ({ request }) => {
    fakeApi.correlationIds.push(request.headers.get('X-Correlation-ID') ?? '')
  }),

  http.post('*/api/auth/register', async ({ request }) => {
    const { email, password } = (await request.json()) as { email: string; password: string }
    const key = email.trim().toLowerCase()
    if (fakeApi.users.has(key)) {
      return envelope('EMAIL_TAKEN', 'An account with this email already exists.', 409)
    }
    const id = crypto.randomUUID()
    fakeApi.users.set(key, { id, password })
    return HttpResponse.json({ id, email: email.trim(), role: 'user' }, { status: 201 })
  }),

  http.post('*/api/auth/login', async ({ request }) => {
    const { email, password } = (await request.json()) as { email: string; password: string }
    const user = fakeApi.users.get(email.trim().toLowerCase())
    if (user?.password !== password) {
      return envelope('INVALID_CREDENTIALS', 'Invalid email or password.', 401)
    }
    const token = `token-${user.id}`
    fakeApi.validTokens.add(token)
    return HttpResponse.json({
      accessToken: token,
      expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      user: { id: user.id, email: email.trim(), role: 'user' },
    })
  }),

  http.get('*/api/schema', ({ request }) => {
    const token = request.headers.get('Authorization')?.replace('Bearer ', '') ?? ''
    if (!fakeApi.validTokens.has(token)) {
      return envelope('UNAUTHORIZED', 'Sign in to continue.', 401)
    }
    return HttpResponse.json({
      schemaHash: 'hash-1',
      introspectedAt: '2026-10-09T08:00:00Z',
      dialect: 'postgres',
      tables: [
        { name: 'orders', columns: [], foreignKeys: [] },
        { name: 'customers', columns: [], foreignKeys: [] },
      ],
    })
  }),
]

export const server = setupServer(...handlers)
