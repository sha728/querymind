import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Dev server proxies the API like nginx does in compose (design §14.1). The .NET API's
// launch profile listens on http://localhost:5147.
const apiTarget = process.env.QM_API_URL ?? 'http://localhost:5147'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': apiTarget,
      '/health': apiTarget,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
})
