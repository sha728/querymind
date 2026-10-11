import { apiFetch } from './client'
import type { AskResponse } from './types'

export const MAX_QUESTION_CHARS = 1000 // design §4.2

export function askQuestion(question: string, signal?: AbortSignal): Promise<AskResponse> {
  return apiFetch<AskResponse>('/api/ask', {
    method: 'POST',
    body: { question },
    ...(signal === undefined ? {} : { signal }),
  })
}
