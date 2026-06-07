import { test, expect } from '@playwright/test'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'

const hasCerts = existsSync(join(homedir(), '.youros', 'localhost.key'))
const API_BASE = hasCerts
  ? 'https://127.0.0.1:8000'
  : 'http://127.0.0.1:8000'

test.beforeEach(async ({ request }) => {
  await request.patch(`${API_BASE}/api/settings`, { data: { onboarded: true } })
})

// ---------------------------------------------------------------------------
// Global footer: present on every authenticated route
// ---------------------------------------------------------------------------

test('footer is visible on the dashboard', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('footer')).toBeVisible()
  await expect(page.getByTestId('footer-link-about')).toBeVisible()
  await expect(page.getByTestId('footer-link-privacy')).toBeVisible()
})

test('footer About link points to /about', async ({ page }) => {
  await page.goto('/')
  const aboutLink = page.getByTestId('footer-link-about')
  await expect(aboutLink).toHaveAttribute('href', '/about')
})

test('footer Privacy link points to /privacy', async ({ page }) => {
  await page.goto('/')
  const privacyLink = page.getByTestId('footer-link-privacy')
  await expect(privacyLink).toHaveAttribute('href', '/privacy')
})
