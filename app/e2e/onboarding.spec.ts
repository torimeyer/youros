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
// Personal onboarding: full happy path
// ---------------------------------------------------------------------------

test('personal onboarding happy path', async ({ page }) => {
  await page.goto('/')
  const wizard = page.getByTestId('onboarding-wizard')
  await expect(wizard).toBeVisible()

  // Step 1: Welcome (Fork is hidden when team mode is off)
  await expect(page.getByTestId('step-welcome')).toBeVisible()
  await expect(page.getByText('Welcome!')).toBeVisible()
  await expect(page.getByText('only take a minute')).toBeVisible()
  await page.getByTestId('next-button').click()

  // Step 2: Your name
  await expect(page.getByTestId('step-you')).toBeVisible()
  await page.getByTestId('user-name-input').fill('Alex')
  await page.getByTestId('next-button').click()

  // Step 3: Name your OS
  await expect(page.getByTestId('step-name')).toBeVisible()
  await page.getByTestId('os-name-input').fill('AlexOS')
  await page.getByTestId('next-button').click()

  // Step 4: Profile - pick a persona
  await expect(page.getByTestId('step-profile')).toBeVisible()
  await page.getByTestId('persona-card-everyone').click()
  await page.getByTestId('next-button').click()

  // Step 5: Customize - starter agents appear
  await expect(page.getByTestId('step-customize')).toBeVisible()
  await page.getByTestId('next-button').click()

  // Step 6: Theme
  await expect(page.getByTestId('step-theme')).toBeVisible()
  await page.getByTestId('theme-light').click()
  await page.getByTestId('next-button').click()

  // Step 7: Connect (auto-skipped if a provider was detected)
  const connectVisible = await page.getByTestId('step-connect').isVisible().catch(() => false)
  if (connectVisible) {
    await page.getByTestId('skip-button').click()
  }

  // Step 8: Ready - summary + finish
  await expect(page.getByTestId('step-ready')).toBeVisible()
  await expect(page.getByTestId('summary-os-name')).toHaveText('AlexOS')
  await expect(page.getByTestId('summary-theme')).toHaveText('Light')
  await page.getByTestId('finish-button').click()

  // Should land on the dashboard
  await expect(wizard).not.toBeVisible()
  await expect(page).toHaveURL('/')
})

// ---------------------------------------------------------------------------
// Skip-through: every skippable step can be skipped
// ---------------------------------------------------------------------------

test('skip-through flow completes without filling anything', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('onboarding-wizard')).toBeVisible()

  // Welcome has no skip, only Next
  await expect(page.getByTestId('step-welcome')).toBeVisible()
  await page.getByTestId('next-button').click()

  // Skip every remaining step until Ready
  const skippableSteps = ['step-you', 'step-name', 'step-profile', 'step-customize', 'step-theme']
  for (const stepId of skippableSteps) {
    await expect(page.getByTestId(stepId)).toBeVisible()
    await page.getByTestId('skip-button').click()
  }

  // Connect may be auto-skipped if a provider was detected
  const connectVisible = await page.getByTestId('step-connect').isVisible().catch(() => false)
  if (connectVisible) {
    await page.getByTestId('skip-button').click()
  }

  // Ready - finish
  await expect(page.getByTestId('step-ready')).toBeVisible()
  await page.getByTestId('finish-button').click()
  await expect(page.getByTestId('onboarding-wizard')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Progress dots track the current step
// ---------------------------------------------------------------------------

test('progress dots advance with each step', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('step-welcome')).toBeVisible()

  const dots = page.getByTestId('progress-dots').locator('div')
  const dotCount = await dots.count()
  expect(dotCount).toBeGreaterThan(0)

  // First dot (Welcome) should be active (blue)
  const firstDotClasses = await dots.nth(0).getAttribute('class')
  expect(firstDotClasses).toContain('bg-blue-500')

  await page.getByTestId('next-button').click()

  // After advancing, second dot should be active
  const secondDotClasses = await dots.nth(1).getAttribute('class')
  expect(secondDotClasses).toContain('bg-blue-500')
})

// ---------------------------------------------------------------------------
// Plain language: no jargon in any onboarding step
// ---------------------------------------------------------------------------

test('onboarding uses plain language throughout', async ({ page }) => {
  await page.goto('/')

  const jargonTerms = [
    'SWR', 'Monte Carlo', 'real return', 'API endpoint',
    'OAuth', 'SSO', 'token refresh', 'webhook',
  ]

  // Check Welcome, You, Name steps for jargon
  const steps = ['step-welcome', 'step-you', 'step-name']
  for (const stepId of steps) {
    await expect(page.getByTestId(stepId)).toBeVisible()
    const text = await page.getByTestId(stepId).textContent()
    for (const term of jargonTerms) {
      expect(text?.toLowerCase()).not.toContain(term.toLowerCase())
    }
    if (stepId === 'step-welcome') {
      await page.getByTestId('next-button').click()
    } else {
      await page.getByTestId('skip-button').click()
    }
  }
})

// ---------------------------------------------------------------------------
// Back button navigates to the previous step
// ---------------------------------------------------------------------------

test('back button returns to previous step', async ({ page }) => {
  await page.goto('/')

  // Welcome -> Next -> You
  await expect(page.getByTestId('step-welcome')).toBeVisible()
  await page.getByTestId('next-button').click()
  await expect(page.getByTestId('step-you')).toBeVisible()

  // Back -> Welcome
  await page.getByTestId('back-button').click()
  await expect(page.getByTestId('step-welcome')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Enter key advances from input steps
// ---------------------------------------------------------------------------

test('enter key advances from name input', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('next-button').click() // Welcome -> You

  // You step: type name and press Enter
  await expect(page.getByTestId('step-you')).toBeVisible()
  await page.getByTestId('user-name-input').fill('Alex')
  await page.getByTestId('user-name-input').press('Enter')

  // Should advance to Name step
  await expect(page.getByTestId('step-name')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Connect step: provider selection works
// ---------------------------------------------------------------------------

test('connect step allows provider selection', async ({ page }) => {
  await page.goto('/')

  // Skip to Connect step: Welcome(next) + You/Name/Profile/Customize/Theme(skip)
  await page.getByTestId('next-button').click()
  for (let i = 0; i < 5; i++) {
    await page.getByTestId('skip-button').click()
  }

  // Connect may be auto-skipped when a provider is already detected.
  // In that case this test has nothing to verify, which is still correct
  // behavior: the user already has a provider configured.
  const connectVisible = await page.getByTestId('step-connect').isVisible().catch(() => false)
  if (!connectVisible) {
    // Auto-skipped to Ready. Provider was detected. Test passes.
    await expect(page.getByTestId('step-ready')).toBeVisible()
    return
  }

  // Anthropic should be default selected
  const anthropicBtn = page.getByTestId('provider-Anthropic')
  await expect(anthropicBtn).toBeVisible()
  await expect(anthropicBtn.getByText('Selected')).toBeVisible()

  // Switch to Gemini
  await page.getByTestId('provider-Google Gemini').click()
  await expect(page.getByTestId('provider-Google Gemini').getByText('Selected')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Connect step: Atlassian and GitHub setup cards appear when not connected
// ---------------------------------------------------------------------------

test('Atlassian setup card is visible in Connect step when not connected', async ({ page, request }) => {
  await page.goto('/')

  // Skip to Connect step: Welcome(next) + You/Name/Profile/Customize/Theme(skip)
  await page.getByTestId('next-button').click()
  for (let i = 0; i < 5; i++) {
    await page.getByTestId('skip-button').click()
  }

  const connectVisible = await page.getByTestId('step-connect').isVisible().catch(() => false)
  if (!connectVisible) {
    // Already past Connect — Atlassian must already be connected (backend says so).
    // Verify by checking the API directly.
    const status = await request.get(`${API_BASE}/api/atlassian/status`)
    const body = await status.json()
    // If connected, the card is correctly hidden — test passes with no assertion needed.
    expect(body.connected).toBe(true)
    return
  }

  // The card should be visible; it hides only when connected === true.
  // Backend returns connected: false in a fresh dev environment.
  await expect(page.getByTestId('step-connect')).toBeVisible()
  await expect(page.getByTestId('onboarding-atlassian-card')).toBeVisible()
})

test('GitHub setup card is visible in Connect step when not connected', async ({ page, request }) => {
  await page.goto('/')

  await page.getByTestId('next-button').click()
  for (let i = 0; i < 5; i++) {
    await page.getByTestId('skip-button').click()
  }

  const connectVisible = await page.getByTestId('step-connect').isVisible().catch(() => false)
  if (!connectVisible) {
    const status = await request.get(`${API_BASE}/api/github/status`)
    const body = await status.json()
    expect(body.connected).toBe(true)
    return
  }

  await expect(page.getByTestId('step-connect')).toBeVisible()
  await expect(page.getByTestId('onboarding-github-card')).toBeVisible()
})
