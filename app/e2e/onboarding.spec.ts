import { test, expect } from '@playwright/test'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'

const hasCerts = existsSync(join(homedir(), '.youros', 'localhost.key'))
const API_BASE = hasCerts
  ? 'https://127.0.0.1:8000'
  : 'http://127.0.0.1:8000'

test.beforeEach(async ({ request }) => {
  await request.patch(`${API_BASE}/api/settings`, { data: { onboarded: false } })
})

test.afterEach(async ({ request }) => {
  await request.patch(`${API_BASE}/api/settings`, { data: { onboarded: true } })
})

// ---------------------------------------------------------------------------
// Personal onboarding: 3-step happy path (Connect -> Tracking -> Personalize)
// ---------------------------------------------------------------------------

test('personal onboarding happy path', async ({ page }) => {
  await page.goto('/')
  const wizard = page.getByTestId('onboarding-wizard')
  await expect(wizard).toBeVisible()

  // Step 1: Connect (Readiness & ostk trust)
  await expect(page.getByTestId('step-connect')).toBeVisible()
  await page.getByTestId('next-button').click()

  // Step 2: Tracking
  await expect(page.getByTestId('step-tracking')).toBeVisible()
  await page.getByTestId('skip-button').click()

  // Step 3: Personalize (Name your OS + Pick your theme)
  await expect(page.getByTestId('step-personalize')).toBeVisible()
  await page.getByTestId('os-name-input').fill('AlexOS')
  await page.getByTestId('theme-light').click()
  await expect(page.getByTestId('summary-os-name')).toHaveText('AlexOS')
  await expect(page.getByTestId('summary-theme')).toHaveText('Light')
  await page.getByTestId('finish-button').click()

  // Should land on the dashboard
  await expect(wizard).not.toBeVisible()
  await expect(page).toHaveURL('/')
})

// ---------------------------------------------------------------------------
// Skip-through: skippable steps advance cleanly to Personalize
// ---------------------------------------------------------------------------

test('skip-through flow completes without filling anything', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await expect(page.getByTestId('onboarding-wizard')).toBeVisible()

  const skippableSteps = ['step-connect', 'step-tracking']
  for (const stepId of skippableSteps) {
    await expect(page.getByTestId(stepId)).toBeVisible()
    await page.getByTestId('skip-button').click()
  }

  await expect(page.getByTestId('step-personalize')).toBeVisible()
  await page.getByTestId('finish-button').click()
  await expect(page.getByTestId('onboarding-wizard')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Progress dots track the 3 personal steps
// ---------------------------------------------------------------------------

test('progress dots advance with each step', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await expect(page.getByTestId('step-connect')).toBeVisible()

  const dots = page.getByTestId('progress-dots').locator('div')
  const dotCount = await dots.count()
  expect(dotCount).toBe(3)

  const firstDotClasses = await dots.nth(0).getAttribute('class')
  expect(firstDotClasses).toContain('bg-blue-500')

  await page.getByTestId('next-button').click()

  const secondDotClasses = await dots.nth(1).getAttribute('class')
  expect(secondDotClasses).toContain('bg-blue-500')
})

// ---------------------------------------------------------------------------
// Plain language: no jargon in onboarding steps
// ---------------------------------------------------------------------------

test('onboarding uses plain language throughout', async ({ page }) => {
  await page.goto('/')
  await page.waitForLoadState('networkidle')

  const jargonTerms = [
    'SWR', 'Monte Carlo', 'real return', 'API endpoint',
    'OAuth', 'SSO', 'token refresh', 'webhook',
  ]

  const steps = ['step-connect', 'step-tracking', 'step-personalize']
  for (const stepId of steps) {
    await expect(page.getByTestId(stepId)).toBeVisible()
    const text = await page.getByTestId(stepId).textContent()
    for (const term of jargonTerms) {
      expect(text?.toLowerCase()).not.toContain(term.toLowerCase())
    }
    if (stepId !== 'step-personalize') {
      await page.getByTestId('skip-button').click()
    }
  }
})

// ---------------------------------------------------------------------------
// Back button navigates to the previous step
// ---------------------------------------------------------------------------

test('back button returns to previous step', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByTestId('step-connect')).toBeVisible()
  await page.getByTestId('next-button').click()
  await expect(page.getByTestId('step-tracking')).toBeVisible()

  await page.getByTestId('back-button').click()
  await expect(page.getByTestId('step-connect')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Enter key finishes from OS name input on Personalize
// ---------------------------------------------------------------------------

test('enter key finishes onboarding from OS name input', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('next-button').click()
  await page.getByTestId('next-button').click()

  await expect(page.getByTestId('step-personalize')).toBeVisible()
  await page.getByTestId('os-name-input').fill('AlexOS')
  await page.getByTestId('os-name-input').press('Enter')

  await expect(page.getByTestId('onboarding-wizard')).not.toBeVisible()
  await expect(page).toHaveURL('/')
})

// ---------------------------------------------------------------------------
// Connect step: provider selection and optional cards
// ---------------------------------------------------------------------------

test('connect step allows provider selection', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('step-connect')).toBeVisible()

  const anthropicBtn = page.getByTestId('provider-Anthropic')
  await expect(anthropicBtn).toBeVisible()
  await expect(anthropicBtn.getByText('Selected')).toBeVisible()

  await page.getByTestId('provider-Google Gemini').click()
  await expect(page.getByTestId('provider-Google Gemini').getByText('Selected')).toBeVisible()
})

test('Atlassian setup card is visible in Connect step when not connected', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('step-connect')).toBeVisible()
  await expect(page.getByTestId('onboarding-atlassian-card')).toBeVisible()
})

test('GitHub setup card is visible in Connect step when not connected', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('step-connect')).toBeVisible()
  await expect(page.getByTestId('onboarding-github-card')).toBeVisible()
})
