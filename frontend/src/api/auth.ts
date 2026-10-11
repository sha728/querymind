import { apiFetch } from './client'
import type { LoginResponse, User } from './types'

export function register(email: string, password: string): Promise<User> {
  return apiFetch<User>('/api/auth/register', { method: 'POST', body: { email, password } })
}

export function login(email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>('/api/auth/login', { method: 'POST', body: { email, password } })
}
