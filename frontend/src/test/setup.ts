import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { fakeApi, server } from './server'

// jsdom has no ResizeObserver; Recharts' ResponsiveContainer needs one (it then keeps its
// initialDimension, so charts render at a fixed size in tests).
class ResizeObserverStub {
  observe(): void { /* no layout in jsdom */ }
  unobserve(): void { /* no layout in jsdom */ }
  disconnect(): void { /* no layout in jsdom */ }
}
if (!('ResizeObserver' in globalThis)) {
  Object.assign(globalThis, { ResizeObserver: ResizeObserverStub })
}

beforeAll(() => { server.listen({ onUnhandledRequest: 'error' }) })
afterEach(() => {
  cleanup()
  server.resetHandlers()
  fakeApi.reset()
  sessionStorage.clear()
})
afterAll(() => { server.close() })
