import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { fakeApi, server } from './server'

beforeAll(() => { server.listen({ onUnhandledRequest: 'error' }) })
afterEach(() => {
  cleanup()
  server.resetHandlers()
  fakeApi.reset()
  sessionStorage.clear()
})
afterAll(() => { server.close() })
