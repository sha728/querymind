import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { AskResponse } from '../api/types'

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

function signedIn(request: Request): boolean {
  const token = request.headers.get('Authorization')?.replace('Bearer ', '') ?? ''
  return fakeApi.validTokens.has(token)
}

/** A successful /api/ask response (design §4.2); override any field. */
export function askResponse(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    historyId: crypto.randomUUID(),
    correlationId: 'corr-ask-1',
    status: 'success',
    question: 'How many customers are there?',
    sql: 'SELECT count(*) AS customers FROM customers',
    columns: [{ name: 'customers', type: 'integer', dbType: 'int8' }],
    rows: [[91]],
    rowCount: 1,
    truncated: false,
    chart: { recommended: 'table', allowed: ['table'], x: null, y: [] },
    summary: 'There are 91 customers.',
    message: null,
    attempts: [{ n: 1, sql: 'SELECT count(*) AS customers FROM customers', errorCode: null, error: null, stage: 'execute', latencyMs: 609 }],
    timings: { linkingMs: 0, generationMs: 609, validationMs: 10, executionMs: 40, summaryMs: 685, engineTotalMs: 16097, totalMs: 16140 },
    usage: { model: 'gpt-oss-120b', promptTokens: 3027, completionTokens: 77 },
    ...overrides,
  }
}

/** Puts a valid signed-in session in sessionStorage, as if the user had logged in. */
export function signInAs(email = 'ana@example.com'): void {
  const token = 'token-signed-in'
  fakeApi.validTokens.add(token)
  sessionStorage.setItem('qm.token', token)
  sessionStorage.setItem('qm.user', JSON.stringify({ id: 'u-1', email, role: 'user' }))
  sessionStorage.setItem('qm.expiresAt', new Date(Date.now() + 60 * 60 * 1000).toISOString())
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

  http.post('*/api/ask', async ({ request }) => {
    if (!signedIn(request)) {
      return envelope('UNAUTHORIZED', 'Sign in to continue.', 401)
    }
    const { question } = (await request.json()) as { question: string }
    return HttpResponse.json(askResponse({ question }))
  }),

  http.get('*/api/schema', ({ request }) => {
    if (!signedIn(request)) {
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
