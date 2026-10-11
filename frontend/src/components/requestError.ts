import { ApiError } from '../api/client'

export interface RequestError {
  message: string
  correlationId: string | null
}

/** A failed request as shown to the user: the API's message plus its reference (design §12). */
export function toRequestError(e: unknown, fallback: string): RequestError {
  return e instanceof ApiError
    ? { message: e.message, correlationId: e.correlationId }
    : { message: fallback, correlationId: null }
}
