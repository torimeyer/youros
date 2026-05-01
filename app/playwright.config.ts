import { defineConfig, devices } from '@playwright/test'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'

const hasCerts = existsSync(join(homedir(), '.myos', 'localhost.key'))
const scheme = hasCerts ? 'https' : 'http'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || `${scheme}://localhost:3010`,
    ignoreHTTPSErrors: true,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'python -m uvicorn api.main:app --host 127.0.0.1 --port 8000',
      cwd: '..',
      port: 8000,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: 'npm run dev',
      port: 3010,
      reuseExistingServer: !process.env.CI,
      timeout: 15_000,
    },
  ],
})
