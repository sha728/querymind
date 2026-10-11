import { apiFetch } from './client'
import type { AskResponse, HistoryPage, SchemaResponse } from './types'

export const HISTORY_PAGE_SIZE = 20

/** Own history, or every user's when <allUsers> (admin endpoint; the API enforces the role). */
export function listHistory(page: number, allUsers: boolean, signal?: AbortSignal): Promise<HistoryPage> {
  const query = new URLSearchParams({ page: String(page), pageSize: String(HISTORY_PAGE_SIZE) })
  const path = allUsers ? '/api/admin/history' : '/api/history'
  return apiFetch<HistoryPage>(`${path}?${query.toString()}`, signal === undefined ? {} : { signal })
}

/** Re-asks a stored question (design §4.2, assumption A2); returns the new answer. */
export function rerunHistory(id: string): Promise<AskResponse> {
  return apiFetch<AskResponse>(`/api/history/${encodeURIComponent(id)}/rerun`, { method: 'POST' })
}

export function getSchema(signal?: AbortSignal): Promise<SchemaResponse> {
  return apiFetch<SchemaResponse>('/api/schema', signal === undefined ? {} : { signal })
}
