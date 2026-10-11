import { clearSession, getToken } from '../auth/session'
import type { ErrorEnvelope } from './types'

/** A non-2xx response, carrying the API's error envelope (design §4.1). */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly correlationId: string | null

  constructor(status: number, code: string, message: string, correlationId: string | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.correlationId = correlationId
  }
}

type UnauthorizedHandler = () => void

let onUnauthorized: UnauthorizedHandler = () => undefined

/**
 * Called when a signed-in request gets 401 (expired or revoked token). The app registers a
 * handler that sends the user to the login page. Returns a function that removes it.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): () => void {
  onUnauthorized = handler
  return () => {
    if (onUnauthorized === handler) {
      onUnauthorized = () => undefined
    }
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
}

/**
 * Calls the QueryMind API. Every request gets a fresh X-Correlation-ID (design §11.1) and the
 * session's bearer token, if any.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers({
    Accept: 'application/json',
    'X-Correlation-ID': crypto.randomUUID(),
  })
  const token = getToken()
  if (token !== null) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  const init: RequestInit = { method: options.method ?? 'GET', headers }
  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    init.body = JSON.stringify(options.body)
  }
  if (options.signal !== undefined) {
    init.signal = options.signal
  }

  const response = await fetch(new URL(path, window.location.origin), init)
  if (response.ok) {
    return (await response.json()) as T
  }

  const envelope = await readEnvelope(response)
  // A 401 on a request that carried a token means the session is over. A 401 without one
  // (wrong password on the login form) is just an error for the form to show.
  if (response.status === 401 && token !== null) {
    clearSession()
    onUnauthorized()
  }
  throw new ApiError(
    response.status,
    envelope?.error.code ?? `HTTP_${String(response.status)}`,
    envelope?.error.message ?? 'Something went wrong. Please try again.',
    envelope?.error.correlationId ?? response.headers.get('X-Correlation-ID'),
  )
}

async function readEnvelope(response: Response): Promise<ErrorEnvelope | null> {
  try {
    const body = (await response.json()) as Partial<ErrorEnvelope>
    return typeof body.error?.code === 'string' ? (body as ErrorEnvelope) : null
  } catch {
    return null
  }
}
