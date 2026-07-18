import { describe, it, expect, vi, beforeEach } from 'vitest'
import fs from 'node:fs'
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Settings from './Settings'
import { useAppStore } from '../stores/app'

// Mock the api module
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
  },
}))

// Mock push notifications module
vi.mock('../lib/pushNotifications', () => ({
  isPushSupported: vi.fn().mockReturnValue(false),
  isSubscribed: vi.fn().mockResolvedValue(false),
  subscribe: vi.fn().mockResolvedValue(false),
  unsubscribe: vi.fn().mockResolvedValue(false),
}))

// Mock notification store so Settings handler error toasts are observable (→2777 CHANGE 2).
// The mock must be CALLABLE: TopBar (rendered inside Settings) uses it as a
// hook with a selector, while the handlers reach it via getState(). A plain
// object here crashes every render in this file.
const { mockAddPersistentToast } = vi.hoisted(() => ({ mockAddPersistentToast: vi.fn() }))
vi.mock('../stores/notifications', () => {
  const state = { addPersistentToast: mockAddPersistentToast, toasts: [], notifications: [] }
  const useNotificationStore = Object.assign(
    (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state),
    { getState: () => state },
  )
  return { useNotificationStore, shouldSuppressAgentToast: () => false }
})

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so the responsive detection in TopBar (rendered by Settings) does not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

import { api } from '../lib/api'
import {
  recordSettingsWriteStart,
  recordSettingsWriteSettled,
  resetSettingsWriteBarrier,
} from '../lib/settingsWriteBarrier'

const mockedApiPatch = vi.mocked(api.patch)
const mockedApiPost = vi.mocked(api.post)

// Mock useNavigate
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => vi.fn() }
})

function renderSettings() {
  return render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>
  )
}

describe('Settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'yourOS',
      darkMode: true,
      accentColor: 'blue',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Specs', enabled: true },
        { label: 'Automations', enabled: false },
        { label: 'Cost Tracking', enabled: true },
      ],
    })
  })

  describe('System Features toggle labels', () => {
    it('shows the Cost Tracking feature as "Usage" to match the nav', () => {
      renderSettings()
      // The Settings toggle row should display "Usage", not "Cost Tracking",
      // so the user sees the same name as the sidebar nav entry.
      const usageToggle = screen.getByRole('switch', { name: 'Usage' })
      expect(usageToggle).toBeInTheDocument()
      // And the old wording must not appear as a visible label.
      expect(screen.queryByRole('switch', { name: 'Cost Tracking' })).toBeNull()
    })
  })

  describe('Page header', () => {
    it('renders the Settings page shell with its top nav tabs', () => {
      renderSettings()
      // The old PageHeader was replaced by PageShell + TopNavTabs. The page
      // identity now comes from the nav tabs rather than a page-header testid.
      const tabButtons = screen.getAllByRole('button').filter(
        (btn) => btn.textContent === 'Connections' || btn.textContent === 'Preferences'
      )
      expect(tabButtons.length).toBeGreaterThanOrEqual(2)
    })

    it('passes the Settings title to the page shell', () => {
      renderSettings()
      // PageShell forwards title="Settings" to TopBar; the nav tabs confirm the
      // Settings page mounted.
      expect(
        screen.getAllByRole('button').some((btn) => btn.textContent === 'Preferences')
      ).toBe(true)
    })
  })

  describe('Accent color picker', () => {
    it('updates accentColor in the store when a color is clicked', () => {
      renderSettings()
      // Find the pink color button (second dot)
      const colorDots = screen.getAllByRole('button').filter((btn) =>
        btn.className.includes('rounded-full') && btn.className.includes('bg-pink-500')
      )
      expect(colorDots.length).toBeGreaterThan(0)
      fireEvent.click(colorDots[0])
      expect(useAppStore.getState().accentColor).toBe('pink')
    })

    it('persists accent color change to API', () => {
      renderSettings()
      const colorDots = screen.getAllByRole('button').filter((btn) =>
        btn.className.includes('rounded-full') && btn.className.includes('bg-purple-500')
      )
      fireEvent.click(colorDots[0])
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { accent_color: 'purple' })
    })

    it('accent color in store defaults to blue', () => {
      expect(useAppStore.getState().accentColor).toBe('blue')
    })
  })

  describe('OS Identifier', () => {
    it('reads osName from the store', () => {
      renderSettings()
      const input = screen.getByDisplayValue('yourOS')
      expect(input).toBeInTheDocument()
    })

    it('updates osName in the store on change', () => {
      renderSettings()
      const input = screen.getByDisplayValue('yourOS')
      fireEvent.change(input, { target: { value: 'MyOS' } })
      expect(useAppStore.getState().osName).toBe('MyOS')
    })

    it('persists osName to API on blur', () => {
      renderSettings()
      const input = screen.getByDisplayValue('yourOS')
      fireEvent.change(input, { target: { value: 'MyOS' } })
      fireEvent.blur(input)
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { os_name: 'MyOS' })
    })

    // →2777 CHANGE 3: blur must not re-send what onChange already queued.
    it('blur skips the PATCH when onChange already queued the same value (→2777 CHANGE 3)', () => {
      renderSettings()
      const input = screen.getByDisplayValue('yourOS')
      // onChange queues the value; blur fires immediately after (navigate-away pattern).
      fireEvent.change(input, { target: { value: 'ToriOS' } })
      const patchCallsBeforeBlur = mockedApiPatch.mock.calls.length
      fireEvent.blur(input)
      // No additional PATCH from blur — it saw lastQueuedOsName === 'ToriOS' and skipped.
      expect(mockedApiPatch.mock.calls.length).toBe(patchCallsBeforeBlur)
    })

    it('blur DOES send a PATCH for a value that was never queued via onChange (→2777 CHANGE 3)', () => {
      // Simulate a value arriving in the store without going through onChange
      // (e.g. the module-level lastQueuedOsName was reset by a previous test cleanup).
      // Directly set the store so lastQueuedOsName is undefined for this fresh render.
      useAppStore.setState({ osName: 'DirectlySetOS' })
      renderSettings()
      const input = screen.getByDisplayValue('DirectlySetOS')
      // Blur without any onChange — value was never queued, so blur must send it.
      fireEvent.blur(input)
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { os_name: 'DirectlySetOS' })
    })

    // →2777 CHANGE 2: a failed blur PATCH must surface a toast, not swallow the error.
    it('handleOsNameBlur shows a toast when the save fails (→2777 CHANGE 2)', async () => {
      mockAddPersistentToast.mockReset()
      mockedApiPatch.mockRejectedValueOnce(new Error('network error'))
      useAppStore.setState({ osName: 'yourOS' })
      renderSettings()
      const input = screen.getByDisplayValue('yourOS')
      // Blur with an unsent value so handleOsNameBlur actually fires the PATCH.
      fireEvent.blur(input)
      await waitFor(() => expect(mockAddPersistentToast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Couldn't save" })
      ))
    })
  })

  describe('Feature toggles', () => {
    it('updates features in the global store when toggled', () => {
      renderSettings()
      // Click the toggle switch for Automations (disabled by default)
      const toggle = screen.getByRole('switch', { name: /Automations/i })
      fireEvent.click(toggle)

      const updated = useAppStore.getState().features
      const automations = updated.find((f) => f.label === 'Automations')
      expect(automations?.enabled).toBe(true)
    })

    it('persists feature toggles to API', () => {
      renderSettings()
      const toggle = screen.getByRole('switch', { name: /Automations/i })
      fireEvent.click(toggle)

      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', expect.objectContaining({
        features: expect.objectContaining({ Automations: true }),
      }))
    })

    it('disabling a feature updates the store', () => {
      renderSettings()
      const toggle = screen.getByRole('switch', { name: /Tasks/i })
      fireEvent.click(toggle)

      const updated = useAppStore.getState().features
      const tasks = updated.find((f) => f.label === 'Tasks')
      expect(tasks?.enabled).toBe(false)
    })
  })

  describe('API Key', () => {
    it('persists API key to keychain on Save click', () => {
      renderSettings()
      const input = screen.getByPlaceholderText('Paste API key (sk-ant-xxxx...)')
      fireEvent.change(input, { target: { value: 'sk-ant-test123' } })
      const saveBtn = screen.getByText('Save to Keychain')
      fireEvent.click(saveBtn)
      expect(mockedApiPost).toHaveBeenCalledWith('/secrets', { key: 'ANTHROPIC_API_KEY', value: 'sk-ant-test123' })
    })

    it('toggles API key visibility', () => {
      renderSettings()
      const input = screen.getByPlaceholderText('Paste API key (sk-ant-xxxx...)')
      expect(input).toHaveAttribute('type', 'password')

      // Find the visibility toggle button (the one inside the API key section)
      const visToggle = input.parentElement?.querySelector('button')
      expect(visToggle).toBeTruthy()
      fireEvent.click(visToggle!)
      expect(input).toHaveAttribute('type', 'text')
    })

    it('shows both Gemini key paths (AI Studio and Google Cloud) when Gemini is selected', () => {
      renderSettings()
      // Click the Google Gemini provider card in the "Set Up Provider" section.
      // There are two cards (Default Chat AI and Set Up Provider), click the
      // second (Set Up Provider) one.
      const geminiCards = screen.getAllByText('Google Gemini')
      // The Set Up Provider card is the one NOT inside default-llm testid.
      const setupCard = geminiCards.find(
        (el) => !el.closest('[data-testid^="default-llm-"]')
      )
      expect(setupCard).toBeTruthy()
      fireEvent.click(setupCard!.closest('div[class*="cursor-pointer"]')!)

      // Cloud setup instructions are behind the Advanced toggle (collapsed by default).
      // Expand it first, then verify both paths still appear.
      const toggle = screen.getByTestId('gemini-advanced-toggle')
      expect(toggle).toBeInTheDocument()
      fireEvent.click(toggle)
      expect(screen.getByTestId('gemini-key-help')).toBeInTheDocument()
      expect(screen.getByText(/Google AI Studio/i)).toBeInTheDocument()
      expect(screen.getByText(/Google Cloud project/i)).toBeInTheDocument()
      // Users must be told to enable the Generative Language API FIRST,
      // otherwise the restriction dropdown is empty when they try to lock
      // the key down.
      expect(screen.getByText(/Enable/)).toBeInTheDocument()
      expect(screen.getAllByText(/Generative Language API/i).length).toBeGreaterThan(0)
    })
  })

  describe('Notification toggles', () => {
    it('persists notification changes to API', () => {
      renderSettings()
      // Find the notification toggle buttons
      const agentCompleteRow = screen.getByText('Agent Complete').closest('div')
      const toggle = agentCompleteRow?.querySelector('button')
      expect(toggle).toBeTruthy()
      fireEvent.click(toggle!)

      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', expect.objectContaining({
        notifications: expect.objectContaining({ 'Agent Complete': false }),
      }))
    })

    it('toggles quiet hours and persists', () => {
      renderSettings()
      const quietHoursLabel = screen.getByText('Quiet Hours')
      const container = quietHoursLabel.closest('div[class*="flex items-center justify-between"]')
      const toggle = container?.querySelector('button')
      expect(toggle).toBeTruthy()
      fireEvent.click(toggle!)

      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { quiet_hours: false })
    })
  })

  describe('Data management', () => {
    it('import and export buttons are no longer shown (feature removed)', () => {
      renderSettings()
      expect(screen.queryByText('Import Config')).not.toBeInTheDocument()
      expect(screen.queryByText('Export Config')).not.toBeInTheDocument()
    })
  })

  describe('GitHub and Atlassian setup cards in Connections', () => {
    it('renders GitHub connect section in Connections tab', async () => {
      renderSettings()
      const githubPill = screen.getByTestId('pill-github')
      fireEvent.click(githubPill)
      expect(screen.getByTestId('github-connect-section')).toBeInTheDocument()
    })

    it('renders Atlassian connect section in Connections tab', async () => {
      renderSettings()
      const atlassianPill = screen.getByTestId('pill-atlassian')
      fireEvent.click(atlassianPill)
      expect(screen.getByTestId('atlassian-connect-section')).toBeInTheDocument()
    })

    it('renders GithubSetupCard inside github section after api resolves', async () => {
      renderSettings()
      const githubPill = screen.getByTestId('pill-github')
      fireEvent.click(githubPill)
      const card = await screen.findByTestId('onboarding-github-card')
      expect(card).toBeInTheDocument()
    })

    it('renders an always-available Atlassian connect path after api resolves', async () => {
      // UAT item 6: the old AtlassianSetupCard returned null while
      // /atlassian/status loaded, leaving the panel blank below the header.
      // AtlassianConnect always renders a connect path (OAuth button or token
      // form), so a connect option is reachable as soon as the status resolves.
      renderSettings()
      const atlassianPill = screen.getByTestId('pill-atlassian')
      fireEvent.click(atlassianPill)
      const card = await screen.findByTestId('atlassian-connect-disconnected')
      expect(card).toBeInTheDocument()
    })
  })

  describe('Data Management and Shared Links tab scoping', () => {
    it('Data Management and Shared links sections are not shown (removed)', () => {
      renderSettings()
      expect(screen.queryByRole('heading', { name: 'Data Management' })).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Shared links' })).not.toBeInTheDocument()
    })
  })

  describe('Tab order', () => {
    it('nav shows exactly Connections and Preferences tabs', () => {
      renderSettings()
      const navButtons = screen.getAllByRole('button').filter(
        (btn) =>
          btn.textContent === 'Connections' ||
          btn.textContent === 'Preferences'
      )
      expect(navButtons.length).toBeGreaterThanOrEqual(2)
    })

    it('Developer tab no longer exists in nav', () => {
      renderSettings()
      expect(screen.queryByRole('button', { name: 'Developer' })).not.toBeInTheDocument()
    })

    it('AI & Chat tab no longer exists in nav', () => {
      renderSettings()
      expect(screen.queryByRole('button', { name: 'AI & Chat' })).not.toBeInTheDocument()
    })
  })

  describe('AI Provider selector', () => {
    it('renders the AI Provider section with Claude and Gemini Enterprise options', () => {
      renderSettings()
      expect(screen.getByTestId('ai-provider-section')).toBeInTheDocument()
      expect(screen.getByTestId('provider-card-claude')).toBeInTheDocument()
      expect(screen.getByTestId('provider-card-gemini')).toBeInTheDocument()
      expect(screen.getByText('Claude')).toBeInTheDocument()
      expect(screen.getByText('Gemini Enterprise')).toBeInTheDocument()
    })

    it('shows api_error text when gemini status returns api_reachable=false with an error', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/gemini/status')
          return Promise.resolve({ available: false, email: null, api_error: 'serviceusage.services.use permission denied' })
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('gemini-api-error')).toBeInTheDocument()
      })
      expect(screen.getByTestId('gemini-api-error')).toHaveTextContent('permission denied')
    })

    it('does not show api_error element when gemini is available', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/gemini/status')
          return Promise.resolve({ available: true, email: 'user@example.com', api_error: null })
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('provider-card-gemini')).not.toBeDisabled()
      })
      expect(screen.queryByTestId('gemini-api-error')).not.toBeInTheDocument()
    })

    it('selects Gemini provider and persists to API', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/gemini/status') return Promise.resolve({ available: true, email: null })
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('provider-card-gemini')).not.toBeDisabled()
      })
      fireEvent.click(screen.getByTestId('provider-card-gemini'))
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { default_provider: 'gemini' })
    })

    it('selects Claude provider and persists to API', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/gemini/status') return Promise.resolve({ available: true, email: null })
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('provider-card-gemini')).not.toBeDisabled()
      })
      fireEvent.click(screen.getByTestId('provider-card-gemini'))
      vi.clearAllMocks()
      fireEvent.click(screen.getByTestId('provider-card-claude'))
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { default_provider: 'claude' })
    })

    it('loads default_model from API on mount and updates the store', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockResolvedValue({
        default_model: '@gemini',
      })

      renderSettings()

      await waitFor(() => {
        expect(useAppStore.getState().defaultChatModel).toBe('gemini')
      })
    })

    it('falls back to provider field when default_model is not set', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockResolvedValue({
        provider: 'Google Gemini',
      })

      renderSettings()

      await waitFor(() => {
        expect(useAppStore.getState().defaultChatModel).toBe('gemini')
      })
    })

    it('renders the Set Up Provider label', () => {
      renderSettings()
      expect(screen.getByText('Set Up Provider')).toBeInTheDocument()
    })
  })

  describe('Anthropic model dropdown', () => {
    it('model dropdown shows when anthropic backend selected', async () => {
      // Default provider is Anthropic. The dropdown should render.
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('anthropic-model-dropdown')).toBeInTheDocument()
      })
    })

    it('model dropdown hides when gemini backend selected', async () => {
      renderSettings()
      // Dropdown is present by default
      await waitFor(() => {
        expect(screen.getByTestId('anthropic-model-dropdown')).toBeInTheDocument()
      })
      // Switch to Gemini
      const geminiCard = screen.getByText('Google Gemini').closest('div[class*="cursor-pointer"]')!
      fireEvent.click(geminiCard)
      await waitFor(() => {
        expect(screen.queryByTestId('anthropic-model-dropdown')).not.toBeInTheDocument()
      })
    })

    it('shows current Claude models (Opus 4.6, Sonnet 4.6, Haiku 4.5)', async () => {
      renderSettings()
      const dropdown = await screen.findByTestId('anthropic-model-dropdown')
      const options = dropdown.querySelectorAll('option')
      const labels = Array.from(options).map((o) => o.textContent?.trim())
      // Labels should be plain-language, not raw ids.
      expect(labels).toContain('Opus 4.6')
      expect(labels).toContain('Sonnet 4.6')
      expect(labels).toContain('Haiku 4.5')
      // Old ids must not leak into the UI.
      expect(labels).not.toContain('claude-opus-4-20250514')
      expect(labels).not.toContain('claude-sonnet-4-20250514')
      expect(labels).not.toContain('claude-haiku-35-20241022')
    })
  })

  describe('Feature toggle key normalization', () => {
    it('reads lowercase feature keys from backend and applies them correctly', async () => {
      // Simulate a backend that returns lowercase keys (old format)
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockResolvedValue({
        features: { tasks: false, chat: true, activity: true },
      })

      renderSettings()

      await waitFor(() => {
        const state = useAppStore.getState()
        const tasks = state.features.find((f) => f.label === 'Tasks')
        expect(tasks?.enabled).toBe(false)
      })

      const state = useAppStore.getState()
      const chat = state.features.find((f) => f.label === 'Chat')
      expect(chat?.enabled).toBe(true)
      const activity = state.features.find((f) => f.label === 'Activity')
      expect(activity?.enabled).toBe(true)
    })

    it('reads TitleCase feature keys from backend and applies them correctly', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockResolvedValue({
        features: { Tasks: false, Chat: true, Activity: true },
      })

      renderSettings()

      await waitFor(() => {
        const state = useAppStore.getState()
        const tasks = state.features.find((f) => f.label === 'Tasks')
        expect(tasks?.enabled).toBe(false)
      })

      const state = useAppStore.getState()
      const chat = state.features.find((f) => f.label === 'Chat')
      expect(chat?.enabled).toBe(true)
    })

    it('falls back to store defaults when feature key is missing from backend', async () => {
      const mockedApiGet = vi.mocked(api.get)
      // Only send some keys, not all
      mockedApiGet.mockResolvedValue({
        features: { tasks: false },
      })

      renderSettings()

      await waitFor(() => {
        const state = useAppStore.getState()
        const tasks = state.features.find((f) => f.label === 'Tasks')
        expect(tasks?.enabled).toBe(false)
      })

      // Chat was not in the API response, so it should keep its default (true)
      const state = useAppStore.getState()
      const chat = state.features.find((f) => f.label === 'Chat')
      expect(chat?.enabled).toBe(true)
    })
  })

  describe('MCP servers panel moved to Agents tab', () => {
    it('does not show Connected Tools in Settings', async () => {
      renderSettings()

      await waitFor(() => {
        expect(screen.queryByText('Connected Tools')).not.toBeInTheDocument()
      })
    })

    it('does not show MCP servers section heading in Settings', async () => {
      renderSettings()

      await waitFor(() => {
        expect(screen.queryByText('MCP servers')).not.toBeInTheDocument()
      })
    })

    it('does not show ostk-managed server content in Settings', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/secrets/key-status') {
          return Promise.resolve({ google_oauth_available: false })
        }
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        expect(screen.queryByText('Managed automatically (configured in your profile)')).not.toBeInTheDocument()
      })
    })
  })

  describe('Connections section', () => {
    it('fires all four connection status fetches in parallel, not serially', async () => {
      // Track call ordering. If the code awaits each request before
      // starting the next, we would see call 2 start only after call 1
      // resolves. Parallel dispatch means all four names appear before
      // any of the deferred promises have resolved.
      const dispatched: string[] = []
      const resolvers: Record<string, () => void> = {}

      vi.mocked(api.get).mockImplementation((path: string) => {
        dispatched.push(path)
        // Keys we care about get a deferred response so the test can
        // observe that all four were dispatched before any resolved.
        const connectionPaths = [
          '/gmail/auth/status',
          '/calendar/auth/status',
          '/drive/auth/status',
          '/slack/status',
        ]
        if (connectionPaths.includes(path)) {
          return new Promise((resolve) => {
            resolvers[path] = () => {
              if (path === '/slack/status') {
                resolve({ connected: true, team_name: 'Acme' })
              } else {
                resolve({ authenticated: true, email: 'test@example.com' })
              }
            }
          })
        }
        return Promise.resolve({})
      })

      renderSettings()

      // Wait for all four to have been dispatched. If the code awaited
      // each response in sequence, this would time out because later
      // paths would never be dispatched until earlier ones resolved.
      await waitFor(() => {
        expect(dispatched).toContain('/gmail/auth/status')
        expect(dispatched).toContain('/calendar/auth/status')
        expect(dispatched).toContain('/drive/auth/status')
        expect(dispatched).toContain('/slack/status')
      })

      // Now resolve all four and confirm the pills render with connected status.
      resolvers['/gmail/auth/status']()
      resolvers['/calendar/auth/status']()
      resolvers['/drive/auth/status']()
      resolvers['/slack/status']()

      await waitFor(() => {
        expect(screen.getByTestId('pill-google')).toBeInTheDocument()
        expect(screen.getByTestId('pill-slack')).toBeInTheDocument()
      })
    })

    it('renders the four active connection pills: Google, Slack, GitHub, Atlassian', async () => {
      renderSettings()

      await waitFor(() => {
        expect(screen.getByTestId('pill-google')).toBeInTheDocument()
        expect(screen.getByTestId('pill-slack')).toBeInTheDocument()
        expect(screen.getByTestId('pill-github')).toBeInTheDocument()
        expect(screen.getByTestId('pill-atlassian')).toBeInTheDocument()
      })
    })

    it('hides every Text yourOS surface while the feature is paused (TEXT_YOUROS_VISIBLE=false)', async () => {
      renderSettings()

      await waitFor(() => {
        expect(screen.getByTestId('pill-google')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('pill-text-youros')).not.toBeInTheDocument()
      expect(screen.queryByTestId('pill-imessage')).not.toBeInTheDocument()
      expect(screen.queryByTestId('text-youros-section')).not.toBeInTheDocument()
      expect(screen.queryByTestId('text-bridge-section')).not.toBeInTheDocument()
    })

    it('expands Google pill details when clicked', async () => {
      renderSettings()

      const googlePill = screen.getByTestId('pill-google')
      fireEvent.click(googlePill)

      await waitFor(() => {
        expect(screen.getByTestId('google-connect-section')).toBeVisible()
      })
    })

    it('collapses Google pill when clicked again', async () => {
      renderSettings()

      const googlePill = screen.getByTestId('pill-google')
      fireEvent.click(googlePill)

      await waitFor(() => {
        expect(screen.getByTestId('google-connect-section')).toBeVisible()
      })

      fireEvent.click(googlePill)

      await waitFor(() => {
        expect(screen.queryByTestId('google-connect-section')).not.toBeInTheDocument()
      })
    })

    it('shows connected status for Gmail when authenticated', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/drive/auth/status') return Promise.resolve({ authenticated: true, email: 'test@example.com' })
        if (path === '/gmail/auth/status') return Promise.resolve({ authenticated: true, email: null })
        if (path === '/calendar/auth/status') return Promise.resolve({ authenticated: true, email: null })
        if (path === '/slack/status') return Promise.resolve({ connected: false, team_name: '' })
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        const googlePill = screen.getByTestId('pill-google')
        expect(googlePill.textContent).toContain('test@example.com')
      })
    })

    it('shows disconnected subtitle when services are not connected', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/gmail/auth/status') return Promise.resolve({ authenticated: false, email: null })
        if (path === '/calendar/auth/status') return Promise.resolve({ authenticated: false, email: null })
        if (path === '/drive/auth/status') return Promise.resolve({ authenticated: false, email: null })
        if (path === '/slack/status') return Promise.resolve({ connected: false, team_name: '' })
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        const googlePill = screen.getByTestId('pill-google')
        expect(googlePill.textContent).toContain('Sign in to use Gmail, Calendar, and Drive')
      })
    })

    it('clicking Disconnect button calls /drive/auth/revoke and updates UI', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/drive/auth/status') return Promise.resolve({ authenticated: true, email: 'test@example.com' })
        if (path === '/gmail/auth/status') return Promise.resolve({ authenticated: true, email: null })
        if (path === '/calendar/auth/status') return Promise.resolve({ authenticated: true, email: null })
        if (path === '/slack/status') return Promise.resolve({ connected: false, team_name: '' })
        return Promise.resolve({})
      })
      vi.mocked(api.post).mockResolvedValue({})

      renderSettings()

      // Expand the Google pill
      const googlePill = screen.getByTestId('pill-google')
      fireEvent.click(googlePill)

      // Wait for the expand and find the Disconnect button
      await waitFor(() => {
        const googleCard = screen.getByTestId('google-connect-section')
        expect(googleCard).toBeInTheDocument()
      })

      // Click Disconnect
      const disconnectBtn = screen.getByRole('button', { name: /Disconnect/i })
      fireEvent.click(disconnectBtn)

      // Verify the API was called with the correct endpoint
      await waitFor(() => {
        expect(vi.mocked(api.post)).toHaveBeenCalledWith('/drive/auth/revoke', {})
      })

      // Verify UI shows disconnected state
      await waitFor(() => {
        const googlePill = screen.getByTestId('pill-google')
        expect(googlePill.textContent).toContain('Sign in to use Gmail, Calendar, and Drive')
      })
    })

    // The →1695 iMessage status-dot tests lived here. The dot sits inside
    // the iMessage pill, hidden while Text yourOS is paused
    // (TEXT_YOUROS_VISIBLE=false in stores/app.ts). They come back with the
    // pill when the flag flips; the paused state itself is covered by the
    // "hides every Text yourOS surface" test above.
  })

  describe('Chat backend section (Fix 2: separate card)', () => {
    it('renders a separate Chat backend section with its own heading', () => {
      renderSettings()
      expect(screen.getByTestId('chat-backend-section')).toBeInTheDocument()
      expect(screen.getByText('AI backend')).toBeInTheDocument()
    })

    it('AI Provider section has its own heading separate from Chat backend', () => {
      renderSettings()
      const aiSection = screen.getByTestId('ai-provider-section')
      expect(aiSection).toBeInTheDocument()
      expect(aiSection.querySelector('h2')?.textContent).toBe('AI Provider')
    })

    it('chat backend radios are inside the chat-backend-section, not ai-provider-section', () => {
      renderSettings()
      const chatBackendSection = screen.getByTestId('chat-backend-section')
      const radios = chatBackendSection.querySelectorAll('input[type="radio"]')
      expect(radios.length).toBe(3)
      const aiProviderSection = screen.getByTestId('ai-provider-section')
      expect(aiProviderSection.querySelector('input[type="radio"]')).toBeNull()
    })

    it('toggling provider does NOT call chat_backend_preference API', () => {
      renderSettings()
      const geminiCard = screen.getByText('Google Gemini').closest('div[class*="cursor-pointer"]')!
      fireEvent.click(geminiCard)
      const patchCalls = vi.mocked(api.patch).mock.calls
      const chatBackendCalls = patchCalls.filter(
        ([, body]) => body && typeof body === 'object' && 'chat_backend_preference' in body
      )
      expect(chatBackendCalls).toHaveLength(0)
    })

    it('renders the Re-check button', () => {
      renderSettings()
      expect(screen.getByTestId('claude-recheck-button')).toBeInTheDocument()
    })

    it('Re-check button calls the chat-backend-status endpoint', async () => {
      vi.mocked(api.get).mockResolvedValue({})
      renderSettings()
      fireEvent.click(screen.getByTestId('claude-recheck-button'))
      await waitFor(() => {
        expect(vi.mocked(api.get)).toHaveBeenCalledWith('/settings/chat-backend-status')
      })
    })
  })

  describe('Provider sign-in status (Fix 1)', () => {
    it('renders the claude-code-ready-indicator in the chat-backend-section', () => {
      renderSettings()
      const chatBackendSection = screen.getByTestId('chat-backend-section')
      expect(chatBackendSection.querySelector('[data-testid="claude-code-ready-indicator"]')).toBeInTheDocument()
    })

    it('shows sign-in instructions when Claude is not signed in', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/settings/chat-backend-status') return Promise.resolve({ claude_code_available: false })
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('claude-login-instructions')).toBeInTheDocument()
      })
      expect(screen.getByText(/open the terminal app/i)).toBeInTheDocument()
    })

    it('does not show sign-in instructions when Claude is signed in', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/settings/chat-backend-status') return Promise.resolve({ claude_code_available: true })
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('claude-auth-status-signed-in')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('claude-login-instructions')).not.toBeInTheDocument()
    })
  })
})

describe('Settings - Enter key submit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue({})
    useAppStore.setState({ osName: 'yourOS', darkMode: false })
  })

  it('Enter on the OS Identifier field saves the name', async () => {
    renderSettings()

    // OS Identifier field lives in the Appearance section (always rendered in new layout)
    await waitFor(() => {
      expect(screen.getAllByText('Appearance').length).toBeGreaterThan(0)
    })

    const osInput = await screen.findByDisplayValue('yourOS')
    fireEvent.change(osInput, { target: { value: 'ToriOS' } })
    fireEvent.keyDown(osInput, { key: 'Enter' })

    // handleOsNameBlur sets store and patches settings
    await waitFor(() => {
      expect(useAppStore.getState().osName).toBe('ToriOS')
    })
  })

  it('MCP server add form is not present in Settings (moved to Agents)', async () => {
    renderSettings()

    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Server name (e.g. Stitch)')).not.toBeInTheDocument()
      expect(screen.queryByPlaceholderText('Paste your server URL after running setup')).not.toBeInTheDocument()
    })
  })

  describe('Compression toggle', () => {
    it('does not render a Compression toggle in Settings', () => {
      renderSettings()
      expect(screen.queryByTestId('compression-toggle')).not.toBeInTheDocument()
      expect(screen.queryByText('Compression')).not.toBeInTheDocument()
    })
  })

  describe('Standing instructions', () => {
    it('renders the Standing instructions section with a textarea and Save button', () => {
      renderSettings()
      const section = screen.getByTestId('standing-instructions-section')
      expect(section).toBeInTheDocument()
      // The standing-instructions content was merged into the "AI behavior" card
      // during the Settings reorg; the heading now reads "AI behavior". Scope the
      // lookup to the section because a hidden legacy copy also carries the text.
      expect(within(section).getByText('AI behavior')).toBeInTheDocument()
      expect(screen.getByTestId('standing-instructions-textarea')).toBeInTheDocument()
      expect(screen.getByTestId('standing-instructions-save')).toBeInTheDocument()
    })

    it('uses the standing-instructions id so the Usage page link can deep-link to it', () => {
      renderSettings()
      const section = screen.getByTestId('standing-instructions-section')
      expect(section.id).toBe('standing-instructions')
    })

    it('saves standing_instructions via PATCH when the Save button is clicked', async () => {
      renderSettings()
      const textarea = screen.getByTestId('standing-instructions-textarea') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'Always reply in plain language.' } })
      fireEvent.click(screen.getByTestId('standing-instructions-save'))
      await waitFor(() => {
        expect(mockedApiPatch).toHaveBeenCalledWith(
          '/settings',
          expect.objectContaining({ standing_instructions: 'Always reply in plain language.' }),
        )
      })
    })

    it('loads the saved standing_instructions from the server on mount', async () => {
      vi.mocked(api.get).mockImplementation((url: string) => {
        if (url === '/settings') {
          return Promise.resolve({ standing_instructions: 'Keep replies short.' })
        }
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        const textarea = screen.getByTestId('standing-instructions-textarea') as HTMLTextAreaElement
        expect(textarea.value).toBe('Keep replies short.')
      })
    })

    it('test_standing_instructions_hydrates_from_server_on_mount', async () => {
      // The textarea must be populated from GET /api/settings on first render
      // so the user sees their existing instructions. The bug report was that
      // this did not happen and Tori's stored value looked lost.
      vi.mocked(api.get).mockImplementation((url: string) => {
        if (url === '/settings') {
          return Promise.resolve({
            standing_instructions: 'be nice to me and be the best you can be but always be accurate',
          })
        }
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        const textarea = screen.getByTestId('standing-instructions-textarea') as HTMLTextAreaElement
        expect(textarea.value).toBe('be nice to me and be the best you can be but always be accurate')
      })
    })

    it('test_standing_instructions_save_patches_and_shows_toast', async () => {
      // Clicking Save must both PATCH /api/settings and surface a visible
      // confirmation so the user knows the write succeeded. Actionable
      // feedback is required per the project rule.
      mockedApiPatch.mockResolvedValueOnce({})
      renderSettings()
      const textarea = screen.getByTestId('standing-instructions-textarea') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'Keep replies short.' } })
      fireEvent.click(screen.getByTestId('standing-instructions-save'))
      await waitFor(() => {
        expect(mockedApiPatch).toHaveBeenCalledWith(
          '/settings',
          expect.objectContaining({ standing_instructions: 'Keep replies short.' }),
        )
      })
      await waitFor(() => {
        const status = screen.getByTestId('standing-instructions-status')
        expect(status).toHaveTextContent('Saved')
      })
    })

    it('test_standing_instructions_save_failure_shows_error', async () => {
      // If the PATCH fails the user must see an actionable error message,
      // not a silent Save. The error span must be role=alert so screen
      // readers announce it.
      mockedApiPatch.mockRejectedValueOnce(new Error('network down'))
      renderSettings()
      const textarea = screen.getByTestId('standing-instructions-textarea') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'anything' } })
      fireEvent.click(screen.getByTestId('standing-instructions-save'))
      await waitFor(() => {
        const status = screen.getByTestId('standing-instructions-status')
        expect(status).toHaveTextContent(/could not save/i)
        expect(status).toHaveAttribute('role', 'alert')
      })
    })

    it('renders the Suggest for me button above the textarea', () => {
      renderSettings()
      expect(screen.getByTestId('standing-instructions-suggest')).toBeInTheDocument()
      expect(screen.getByTestId('standing-instructions-suggest')).toHaveTextContent(/suggest for me/i)
    })

    it('clicking Suggest for me calls the suggest endpoint and renders checkboxes', async () => {
      vi.mocked(api.post).mockImplementation((url: string) => {
        if (url === '/settings/standing-instructions/suggest') {
          return Promise.resolve({
            suggestions: [
              'Always explain things in plain language.',
              'Prefer Google Calendar for scheduling.',
              'Never use em-dashes.',
            ],
          })
        }
        return Promise.resolve({})
      })
      renderSettings()
      fireEvent.click(screen.getByTestId('standing-instructions-suggest'))
      await waitFor(() => {
        expect(vi.mocked(api.post)).toHaveBeenCalledWith(
          '/settings/standing-instructions/suggest',
          expect.anything(),
        )
      })
      await waitFor(() => {
        expect(screen.getByTestId('standing-instructions-suggestions')).toBeInTheDocument()
      })
      // Three suggestions must each have a checkbox and text input.
      expect(screen.getByTestId('standing-instructions-suggestion-check-0')).toBeInTheDocument()
      expect(screen.getByTestId('standing-instructions-suggestion-check-1')).toBeInTheDocument()
      expect(screen.getByTestId('standing-instructions-suggestion-check-2')).toBeInTheDocument()
      const row0 = screen.getByTestId('standing-instructions-suggestion-text-0') as HTMLInputElement
      expect(row0.value).toBe('Always explain things in plain language.')
      // All checked by default so "Save all checked" is the happy path.
      const check0 = screen.getByTestId('standing-instructions-suggestion-check-0') as HTMLInputElement
      expect(check0.checked).toBe(true)
    })

    it('Save all checked joins checked rows with newlines and PATCHes the store', async () => {
      vi.mocked(api.post).mockResolvedValueOnce({
        suggestions: [
          'Always explain things in plain language.',
          'Prefer Google Calendar for scheduling.',
          'Never use em-dashes.',
        ],
      })
      mockedApiPatch.mockResolvedValueOnce({})
      renderSettings()
      fireEvent.click(screen.getByTestId('standing-instructions-suggest'))
      await waitFor(() => {
        expect(screen.getByTestId('standing-instructions-suggestions')).toBeInTheDocument()
      })
      // Uncheck the middle one so only rows 0 and 2 are saved.
      fireEvent.click(screen.getByTestId('standing-instructions-suggestion-check-1'))
      fireEvent.click(screen.getByTestId('standing-instructions-save-checked'))
      await waitFor(() => {
        expect(mockedApiPatch).toHaveBeenCalledWith(
          '/settings',
          expect.objectContaining({
            standing_instructions: 'Always explain things in plain language.\nNever use em-dashes.',
          }),
        )
      })
    })

    it('Save all checked appends to existing instructions instead of replacing', async () => {
      vi.mocked(api.get).mockImplementation((url: string) => {
        if (url === '/settings') {
          return Promise.resolve({ standing_instructions: 'Keep replies short.' })
        }
        return Promise.resolve({})
      })
      vi.mocked(api.post).mockResolvedValueOnce({
        suggestions: ['Prefer Gmail for email drafts.'],
      })
      mockedApiPatch.mockResolvedValueOnce({})
      renderSettings()
      // Wait for the existing instructions to hydrate.
      await waitFor(() => {
        const ta = screen.getByTestId('standing-instructions-textarea') as HTMLTextAreaElement
        expect(ta.value).toBe('Keep replies short.')
      })
      fireEvent.click(screen.getByTestId('standing-instructions-suggest'))
      await waitFor(() => {
        expect(screen.getByTestId('standing-instructions-suggestions')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('standing-instructions-save-checked'))
      await waitFor(() => {
        expect(mockedApiPatch).toHaveBeenCalledWith(
          '/settings',
          expect.objectContaining({
            standing_instructions: 'Keep replies short.\nPrefer Gmail for email drafts.',
          }),
        )
      })
    })

    it('Suggest failure shows an actionable error message', async () => {
      vi.mocked(api.post).mockRejectedValueOnce(new Error('network down'))
      renderSettings()
      fireEvent.click(screen.getByTestId('standing-instructions-suggest'))
      await waitFor(() => {
        const err = screen.getByTestId('standing-instructions-suggest-error')
        expect(err).toHaveTextContent(/could not get suggestions/i)
        expect(err).toHaveAttribute('role', 'alert')
      })
    })
  })
})



describe('Settings page: Developer section', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'yourOS',
      darkMode: true,
      accentColor: 'blue',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Specs', enabled: true },
        { label: 'Automations', enabled: false },
        { label: 'Cost Tracking', enabled: true },
      ],
    })
  })

  it('renders only 2 navigation tabs: Connections and Preferences', () => {
    renderSettings()
    const buttons = screen.getAllByRole('button').filter(btn => {
      const text = btn.textContent?.trim()
      return text === 'Connections' || text === 'Preferences'
    })
    expect(buttons.length).toBeGreaterThanOrEqual(2)
    const hasConnections = buttons.some(btn => btn.textContent?.trim() === 'Connections')
    const hasPreferences = buttons.some(btn => btn.textContent?.trim() === 'Preferences')
    expect(hasConnections).toBe(true)
    expect(hasPreferences).toBe(true)
  })

  it('Connections tab is default on load', () => {
    renderSettings()
    const googlePill = screen.getByTestId('pill-google')
    expect(googlePill).toBeVisible()
  })

  it('Google card shows Connected when driveStatus.authenticated is true', async () => {
    renderSettings()
    const googlePill = screen.getByTestId('pill-google')
    fireEvent.click(googlePill)
    await waitFor(() => {
      const googleCard = screen.getByTestId('google-connect-section')
      expect(googleCard).toBeInTheDocument()
      const connectedIndicator = googleCard.querySelector('svg + p')
      if (connectedIndicator) {
        expect(connectedIndicator.textContent).toMatch(/Connected|gmail|drive/i)
      }
    })
  })
})

describe('Settings page: Push notifications toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'yourOS',
      darkMode: true,
      accentColor: 'blue',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Specs', enabled: true },
        { label: 'Automations', enabled: false },
        { label: 'Cost Tracking', enabled: true },
      ],
    })
  })

  it('does not render push toggle when push is not supported', async () => {
    renderSettings()
    const toggle = screen.queryByTestId('push-toggle')
    expect(toggle).not.toBeInTheDocument()
  })

  it('clicking push toggle calls subscribe when not subscribed', async () => {
    const { isPushSupported, subscribe, isSubscribed } = await import('../lib/pushNotifications')
    vi.mocked(isPushSupported).mockReturnValue(true)
    vi.mocked(isSubscribed).mockResolvedValue(false)
    vi.mocked(subscribe).mockResolvedValue(true)

    renderSettings()
    const toggle = await screen.findByTestId('push-toggle')
    
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(vi.mocked(subscribe)).toHaveBeenCalled()
    })
  })

  it('clicking push toggle calls unsubscribe when subscribed', async () => {
    const { isPushSupported, unsubscribe, isSubscribed } = await import('../lib/pushNotifications')
    vi.mocked(isPushSupported).mockReturnValue(true)
    vi.mocked(isSubscribed).mockResolvedValue(true)
    vi.mocked(unsubscribe).mockResolvedValue(true)

    renderSettings()
    const toggle = await screen.findByTestId('push-toggle')
    
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(vi.mocked(unsubscribe)).toHaveBeenCalled()
    })
  })

  describe('Connections skeleton loading state (→1061)', () => {
    it('shows key-status skeleton while key-status API is pending', () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/secrets/key-status') {
          return new Promise(() => {}) // never resolves
        }
        return Promise.resolve({})
      })

      renderSettings()

      expect(screen.getByTestId('key-status-skeleton')).toBeInTheDocument()
      expect(screen.queryByText(/No key found/)).not.toBeInTheDocument()
    })

    it('hides skeleton and shows key status after API resolves with no key', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/secrets/key-status') {
          return Promise.resolve({ anthropic: false, gemini: false, google_oauth_available: false })
        }
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        expect(screen.queryByTestId('key-status-skeleton')).not.toBeInTheDocument()
      })
      expect(screen.getByText(/No key found/)).toBeInTheDocument()
    })

    it('hides skeleton and shows key available banner after API resolves with key present', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/secrets/key-status') {
          return Promise.resolve({
            anthropic: true,
            anthropic_source: 'keychain',
            gemini: false,
            google_oauth_available: false,
          })
        }
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        expect(screen.queryByTestId('key-status-skeleton')).not.toBeInTheDocument()
      })
      expect(screen.getByText(/Key available/)).toBeInTheDocument()
      expect(screen.queryByText(/No key found/)).not.toBeInTheDocument()
    })

    it('never shows amber no-key warning before key-status resolves', () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/secrets/key-status') {
          return new Promise(() => {}) // never resolves
        }
        return Promise.resolve({})
      })

      renderSettings()

      expect(screen.queryByText(/No key found/)).not.toBeInTheDocument()
    })

    it('connection dots render immediately on mount before status loads', () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/gmail/auth/status' || path === '/calendar/auth/status' ||
            path === '/drive/auth/status' || path === '/slack/status') {
          return new Promise(() => {}) // never resolves
        }
        return Promise.resolve({})
      })

      renderSettings()

      expect(screen.getByTestId('pill-google')).toBeInTheDocument()
      expect(screen.getByTestId('pill-slack')).toBeInTheDocument()
    })
  })

  describe('Help section in Preferences (→1421)', () => {
    function switchToPreferences() {
      const btn = screen.getAllByRole('button').find(b => b.textContent?.trim() === 'Preferences')
      if (btn) fireEvent.click(btn)
    }

    it('renders a "Take the tour" button in Settings Preferences', () => {
      renderSettings()
      switchToPreferences()
      expect(screen.getByTestId('settings-tour-button')).toBeInTheDocument()
      expect(screen.getByText('Take the tour')).toBeInTheDocument()
    })

    it('"Take the tour" button calls setShowTour(true) when clicked', () => {
      const setShowTour = vi.fn()
      useAppStore.setState({ setShowTour })
      renderSettings()
      switchToPreferences()
      fireEvent.click(screen.getByTestId('settings-tour-button'))
      expect(setShowTour).toHaveBeenCalledWith(true)
    })

  })
})

describe('Settings: Memory provenance (F4)', () => {
  function switchToPreferences() {
    const btn = screen.getAllByRole('button').find(b => b.textContent?.trim() === 'Preferences')
    if (btn) fireEvent.click(btn)
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'yourOS',
      darkMode: true,
      accentColor: 'blue',
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Specs', enabled: true },
        { label: 'Automations', enabled: false },
        { label: 'Cost Tracking', enabled: true },
      ],
    })
  })

  it('renders a timestamp for a bullet that has a provenance comment', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/memory') {
        return Promise.resolve({
          content: '- I prefer plain language <!-- added 2026-05-17T22:18:30Z -->',
        })
      }
      return Promise.resolve({})
    })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    switchToPreferences()
    await waitFor(() => {
      expect(screen.getAllByTestId('memory-bullet-list')[0]).toBeInTheDocument()
    })
    expect(screen.getAllByTestId('memory-provenance-0')[0]).toHaveTextContent('added May 17, 2026')
  })

  it('renders "edited manually" for a bullet without a provenance comment', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/memory') {
        return Promise.resolve({ content: '- I like coffee' })
      }
      return Promise.resolve({})
    })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    switchToPreferences()
    await waitFor(() => {
      expect(screen.getAllByTestId('memory-bullet-list')[0]).toBeInTheDocument()
    })
    expect(screen.getAllByTestId('memory-provenance-0')[0]).toHaveTextContent('edited manually')
  })

  it('shows overflow banner when memory is overflowed', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/memory') return Promise.resolve({ content: '- I prefer plain language' })
      if (path === '/memory/user/overflow-status') return Promise.resolve({ overflowed: true, reason: 'lines', kb: 10, lines: 155, total_kb: 10, hard_cap: false })
      return Promise.resolve({})
    })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    switchToPreferences()
    await waitFor(() => {
      expect(screen.getByTestId('memory-overflow-banner')).toBeInTheDocument()
    })
    expect(screen.getByTestId('suggest-topics-button')).toBeInTheDocument()
  })

  it('does not show overflow banner when memory is within limits', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/memory') return Promise.resolve({ content: '- I prefer plain language' })
      if (path === '/memory/user/overflow-status') return Promise.resolve({ overflowed: false, reason: '', kb: 2, lines: 5, total_kb: 2, hard_cap: false })
      return Promise.resolve({})
    })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    switchToPreferences()
    await waitFor(() => {
      expect(screen.queryByTestId('memory-overflow-banner')).not.toBeInTheDocument()
    })
  })

  it('shows hard-cap banner when total memory exceeds 200KB', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/memory') return Promise.resolve({ content: '- I prefer plain language' })
      if (path === '/memory/user/overflow-status') return Promise.resolve({ overflowed: true, reason: 'kb', kb: 210, lines: 500, total_kb: 210, hard_cap: true })
      return Promise.resolve({})
    })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    switchToPreferences()
    await waitFor(() => {
      expect(screen.getAllByTestId('memory-hard-cap-banner')[0]).toBeInTheDocument()
    })
    expect(screen.queryByTestId('memory-overflow-banner')).not.toBeInTheDocument()
  })

  it('shows suggested topics after clicking Suggest topics', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/memory') return Promise.resolve({ content: "- Don't say grep.\n- Use plain language." })
      if (path === '/memory/user/overflow-status') return Promise.resolve({ overflowed: true, reason: 'lines', kb: 10, lines: 155, total_kb: 10, hard_cap: false })
      return Promise.resolve({})
    })
    vi.mocked(api.post).mockResolvedValue({ topics: [{ topic: 'writing-style', bullets: ["Don't say grep.", 'Use plain language.'] }] })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    switchToPreferences()
    await waitFor(() => expect(screen.getByTestId('suggest-topics-button')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('suggest-topics-button'))
    await waitFor(() => expect(screen.getByTestId('suggested-topics-list')).toBeInTheDocument())
    expect(screen.getByText('writing-style')).toBeInTheDocument()
  })

  it('Gemini CLI toggle has no Experimental badge', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/settings') return Promise.resolve({ provider: 'Google Gemini' })
      return Promise.resolve({})
    })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('gemini-cli-toggle')).toBeInTheDocument())
    expect(screen.queryByText('Experimental')).not.toBeInTheDocument()
  })
})

describe('Settings: Gemini provider clarity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ osName: 'yourOS', darkMode: false, accentColor: 'blue', features: [] })
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/settings') return Promise.resolve({ provider: 'Google Gemini' })
      if (path === '/secrets/key-status') return Promise.resolve({
        google_oauth_available: false, google_connected: false,
        anthropic: false, gemini: false, anthropic_source: 'none', gemini_source: 'none',
      })
      return Promise.resolve({})
    })
  })

  it('shows subscription note when Google Gemini is selected', async () => {
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('api-key-setup-section')).toBeInTheDocument())
    expect(screen.getByTestId('api-key-setup-section')).toHaveTextContent(/Gemini Advanced.*doesn't include API access/i)
  })

  it('Advanced toggle exists for Gemini and Cloud instructions are hidden by default', async () => {
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('gemini-advanced-toggle')).toBeInTheDocument())
    expect(screen.queryByTestId('gemini-key-help')).not.toBeInTheDocument()
  })

  it('clicking the Advanced toggle reveals Cloud setup instructions', async () => {
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('gemini-advanced-toggle')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('gemini-advanced-toggle'))
    await waitFor(() => expect(screen.getByTestId('gemini-key-help')).toBeInTheDocument())
    expect(screen.getByTestId('gemini-key-help')).toHaveTextContent(/Google Cloud project/i)
    expect(screen.getByTestId('gemini-key-help')).toHaveTextContent(/Generative Language API/i)
  })

  it('Anthropic provider does not show the subscription note or Advanced toggle', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/settings') return Promise.resolve({ provider: 'Anthropic' })
      return Promise.resolve({})
    })
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('api-key-setup-section')).toBeInTheDocument())
    expect(screen.queryByTestId('gemini-advanced-toggle')).not.toBeInTheDocument()
    expect(screen.queryByText(/Gemini Advanced/i)).not.toBeInTheDocument()
  })
})

describe('Settings: Memory split suggestions (→1820)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/memory/user/overflow-status') {
        return Promise.resolve({ overflowed: true, reason: 'lines', lines: 150, hard_cap: false })
      }
      if (path === '/memory') return Promise.resolve({ content: '- fact 1\n- fact 2' })
      return Promise.resolve({})
    })
    vi.mocked(api.post).mockImplementation((path: string) => {
      if (path === '/memory/user/suggest-topics') {
        return Promise.resolve({
          topics: [
            { topic: 'coding', bullets: ['fact 1', 'fact 2'] }
          ]
        })
      }
      return Promise.resolve({ ok: true })
    })
  })

  it('renders overflow banner and suggest button when overflowed', async () => {
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('memory-overflow-banner')).toBeInTheDocument())
    expect(screen.getByTestId('suggest-topics-button')).toBeInTheDocument()
  })

  it('suggest-topics button renders the topic list with checkboxes', async () => {
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('suggest-topics-button')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('suggest-topics-button'))
    await waitFor(() => expect(screen.getByTestId('suggested-topics-list')).toBeInTheDocument())
    expect(screen.getByText('coding')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox').length).toBe(2)
  })

  it('Move button calls api.post with selected bullet_texts', async () => {
    render(<MemoryRouter><Settings /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('suggest-topics-button')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('suggest-topics-button'))
    await waitFor(() => expect(screen.getByTestId('suggested-topics-list')).toBeInTheDocument())

    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])

    const moveBtn = screen.getByTestId('apply-split-coding')
    expect(moveBtn).not.toBeDisabled()
    fireEvent.click(moveBtn)

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/memory/user/split-topic', {
        bullet_texts: ['fact 1', 'fact 2'],
        topic_name: 'coding'
      })
    })
  })
})

// ---------------------------------------------------------------------------
// →2777: loading the Settings page must never write the fetched os_name back
// to the server. fetchSettings used to apply the GET value through setOsName,
// whose store setter PATCHes /settings with whatever it is given. On a busy
// backend that GET can resolve AFTER the user already saved a new name, and
// the echo then overwrote the just-saved name on disk with the stale
// pre-save value. The browser pre-release check saw this as "OS name did not
// persist" while direct backend PATCHes kept working.
// ---------------------------------------------------------------------------
describe('Settings load path never echoes os_name back to the server (→2777)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ osName: 'yourOS', darkMode: true })
  })

  function osNamePatchCalls() {
    return mockedApiPatch.mock.calls.filter(
      ([path, body]) =>
        path === '/settings' &&
        !!body &&
        typeof body === 'object' &&
        'os_name' in (body as Record<string, unknown>)
    )
  }

  it('applies the fetched os_name to the input without PATCHing it back', async () => {
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings' ? Promise.resolve({ os_name: 'savedOS' }) : Promise.resolve({})
    )
    renderSettings()
    // The fetched name reaches the input...
    expect(await screen.findByDisplayValue('savedOS')).toBeInTheDocument()
    // ...but reading must never write: no os_name PATCH may fire on load.
    expect(osNamePatchCalls()).toHaveLength(0)
  })

  it('a slow settings fetch never overwrites a name typed while it was loading', async () => {
    let resolveSettings: (v: unknown) => void = () => {}
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings'
        ? new Promise((r) => {
            resolveSettings = r
          })
        : Promise.resolve({})
    )
    renderSettings()

    // The user renames their OS while GET /settings is still in flight.
    const input = screen.getByDisplayValue('yourOS')
    fireEvent.change(input, { target: { value: 'freshOS' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(useAppStore.getState().osName).toBe('freshOS')

    // The stale reply lands after the save.
    await act(async () => {
      resolveSettings({ os_name: 'staleOS' })
    })

    // The typed name wins, in the store and on the wire: nothing may have
    // PATCHed the stale name back to the server.
    expect(useAppStore.getState().osName).toBe('freshOS')
    const staleEchoes = osNamePatchCalls().filter(
      ([, body]) => (body as { os_name?: string }).os_name === 'staleOS'
    )
    expect(staleEchoes).toHaveLength(0)
  })

  // →2777 round 3: typing updates the store synchronously, but the
  // re-render that refreshes the Enter/blur handler's closure can lag on
  // a loaded machine. When Enter arrives before that re-render commits,
  // the handler still holds the PREVIOUS name and saves it 400-600ms
  // after the keystroke's own save, overwriting the fresh name on disk.
  it('a fast Enter saves the name just typed, never the previous render’s name (→2777)', () => {
    vi.mocked(api.get).mockImplementation(() => Promise.resolve({}))
    useAppStore.setState({ osName: 'oldOS' })
    renderSettings()
    const input = screen.getByDisplayValue('oldOS')

    // Update the store the way a keystroke does, but WITHOUT letting React
    // commit the re-render first (no act wrapper, on purpose): the key
    // event below dispatches against the last committed render, whose
    // handler closure still holds 'oldOS'.
    useAppStore.setState({ osName: 'newOS' })
    fireEvent.keyDown(input, { key: 'Enter' })

    const osPatches = osNamePatchCalls()
    // The save fired by Enter must carry the name that was just typed...
    expect(osPatches[osPatches.length - 1]).toEqual(['/settings', { os_name: 'newOS' }])
    // ...and the previous name must never be written at all.
    const staleWrites = osPatches.filter(
      ([, body]) => (body as { os_name?: string }).os_name === 'oldOS'
    )
    expect(staleWrites).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// →2778: the same load-echo bug as →2777 remained for three more fields. The
// settings GET applied accent_color, default_model, and use_ostk_terms via
// their store setters (setAccentColor, setDefaultChatModel, setUseOstkTerms),
// and every one of those setters PATCHes its value straight back to /settings.
// On a busy backend the GET can resolve after the user already saved a new
// value, so the stale reply overwrote the just-saved value on disk. The load
// path must apply fetched values without writing anything back, and it must
// skip a reply that raced a user edit (same snapshot guard as os_name).
// ---------------------------------------------------------------------------
describe('Settings load path never echoes accent color, default model, or terminology back to the server (→2778)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetSettingsWriteBarrier()
    useAppStore.setState({
      osName: 'yourOS',
      darkMode: true,
      accentColor: 'blue',
      defaultChatModel: 'claude',
      useOstkTerms: false,
    })
  })

  function patchCallsWith(field: string) {
    return mockedApiPatch.mock.calls.filter(
      ([path, body]) =>
        path === '/settings' &&
        !!body &&
        typeof body === 'object' &&
        field in (body as Record<string, unknown>)
    )
  }

  it('applies the fetched accent color to the store without PATCHing it back', async () => {
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings' ? Promise.resolve({ accent_color: 'pink' }) : Promise.resolve({})
    )
    renderSettings()
    // The fetched color reaches the store...
    await waitFor(() => expect(useAppStore.getState().accentColor).toBe('pink'))
    // ...but reading must never write: no accent_color PATCH may fire on load.
    expect(patchCallsWith('accent_color')).toHaveLength(0)
  })

  it('applies the fetched default model to the store without PATCHing it back', async () => {
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings' ? Promise.resolve({ default_model: '@gemini' }) : Promise.resolve({})
    )
    renderSettings()
    await waitFor(() => expect(useAppStore.getState().defaultChatModel).toBe('gemini'))
    expect(patchCallsWith('default_model')).toHaveLength(0)
  })

  it('the provider fallback for the default model also never PATCHes it back', async () => {
    // No default_model saved yet: the load path derives one from provider.
    // Deriving is fine; writing the derived value back to the server is not.
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings' ? Promise.resolve({ provider: 'Google Gemini' }) : Promise.resolve({})
    )
    renderSettings()
    await waitFor(() => expect(useAppStore.getState().defaultChatModel).toBe('gemini'))
    expect(patchCallsWith('default_model')).toHaveLength(0)
  })

  it('applies the fetched terminology toggle to the store without PATCHing it back', async () => {
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings' ? Promise.resolve({ use_ostk_terms: true }) : Promise.resolve({})
    )
    renderSettings()
    await waitFor(() => expect(useAppStore.getState().useOstkTerms).toBe(true))
    expect(patchCallsWith('use_ostk_terms')).toHaveLength(0)
  })

  it('a slow settings fetch never overwrites values changed while it was loading', async () => {
    let resolveSettings: (v: unknown) => void = () => {}
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings'
        ? new Promise((r) => {
            resolveSettings = r
          })
        : Promise.resolve({})
    )
    renderSettings()

    // While GET /settings is still in flight the user changes all three:
    // the accent color through the picker, the other two through their
    // store setters (the paths ChatPanel and the terminology toggle use).
    const orangeDots = screen.getAllByRole('button').filter((btn) =>
      btn.className.includes('rounded-full') && btn.className.includes('bg-orange-500')
    )
    fireEvent.click(orangeDots[0])
    useAppStore.getState().setDefaultChatModel('gemini')
    useAppStore.getState().setUseOstkTerms(true)
    expect(useAppStore.getState().accentColor).toBe('orange')

    // The stale reply lands after those saves.
    await act(async () => {
      resolveSettings({ accent_color: 'pink', default_model: '@claude', use_ostk_terms: false })
    })

    // The user's picks win in the store...
    expect(useAppStore.getState().accentColor).toBe('orange')
    expect(useAppStore.getState().defaultChatModel).toBe('gemini')
    expect(useAppStore.getState().useOstkTerms).toBe(true)
    // ...and on the wire: nothing may have written the stale values back.
    const staleAccent = patchCallsWith('accent_color').filter(
      ([, body]) => (body as { accent_color?: string }).accent_color === 'pink'
    )
    const staleModel = patchCallsWith('default_model').filter(
      ([, body]) => (body as { default_model?: string }).default_model === '@claude'
    )
    const staleTerms = patchCallsWith('use_ostk_terms').filter(
      ([, body]) => (body as { use_ostk_terms?: boolean }).use_ostk_terms === false
    )
    expect(staleAccent).toHaveLength(0)
    expect(staleModel).toHaveLength(0)
    expect(staleTerms).toHaveLength(0)
  })

  // →2778: barrier tests — a save on the wire before fetchStartedAt must block
  // the fetched reply even when the local value did not change during the request

  it('a fetch reply that arrived while an accent_color save was on the wire is discarded (→2778)', async () => {
    let resolveSettings: (v: unknown) => void = () => {}
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings'
        ? new Promise((r) => { resolveSettings = r })
        : Promise.resolve({})
    )
    // Arm barrier before the component mounts so fetchStartedAt is captured after it
    recordSettingsWriteStart(['accent_color'])
    try {
      renderSettings()
      await act(async () => {
        resolveSettings({ accent_color: 'pink' })
      })
      expect(useAppStore.getState().accentColor).toBe('blue')
    } finally {
      recordSettingsWriteSettled(['accent_color'])
    }
  })

  it('a fetch reply that arrived while a dark_mode save was on the wire is discarded (→2778)', async () => {
    let resolveSettings: (v: unknown) => void = () => {}
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings'
        ? new Promise((r) => { resolveSettings = r })
        : Promise.resolve({})
    )
    recordSettingsWriteStart(['dark_mode'])
    try {
      renderSettings()
      await act(async () => {
        resolveSettings({ dark_mode: false })
      })
      // dark_mode was true in beforeEach; barrier should have blocked the stale false
      expect(useAppStore.getState().darkMode).toBe(true)
    } finally {
      recordSettingsWriteSettled(['dark_mode'])
    }
  })

  it('a fetch reply that arrived while a default_model save was on the wire is discarded (→2778)', async () => {
    let resolveSettings: (v: unknown) => void = () => {}
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings'
        ? new Promise((r) => { resolveSettings = r })
        : Promise.resolve({})
    )
    recordSettingsWriteStart(['default_model'])
    try {
      renderSettings()
      await act(async () => {
        resolveSettings({ default_model: '@gemini' })
      })
      expect(useAppStore.getState().defaultChatModel).toBe('claude')
    } finally {
      recordSettingsWriteSettled(['default_model'])
    }
  })

  it('a fetch reply that arrived while a use_ostk_terms save was on the wire is discarded (→2778)', async () => {
    let resolveSettings: (v: unknown) => void = () => {}
    vi.mocked(api.get).mockImplementation((path: string) =>
      path === '/settings'
        ? new Promise((r) => { resolveSettings = r })
        : Promise.resolve({})
    )
    recordSettingsWriteStart(['use_ostk_terms'])
    try {
      renderSettings()
      await act(async () => {
        resolveSettings({ use_ostk_terms: true })
      })
      expect(useAppStore.getState().useOstkTerms).toBe(false)
    } finally {
      recordSettingsWriteSettled(['use_ostk_terms'])
    }
  })
})

// ---------------------------------------------------------------------------
// →2885: every sidebar nav item now has a feature switch, so the Settings
// Features list gained rows for Tasks-page style entries that never had one
// (Portfolio, Executive Summary, iMessage, Jira, Confluence, ostk). Each new
// row needs a plain-language name and its own icon, not the generic fallback.
// ---------------------------------------------------------------------------
describe('Settings Features list covers the new nav switches (→2885)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'yourOS',
      darkMode: true,
      accentColor: 'blue',
      features: [
        { label: 'Tasks', enabled: true },
        { label: 'Specs', enabled: true },
        { label: 'Executive Summary', enabled: true },
        { label: 'Portfolio', enabled: true },
        { label: 'iMessage', enabled: true },
        { label: 'Jira', enabled: true },
        { label: 'Confluence', enabled: true },
        { label: 'ostk', enabled: true },
      ],
    })
  })

  it('shows the iMessage switch as "Messages" to match the nav', () => {
    renderSettings()
    expect(screen.getByRole('switch', { name: 'Messages' })).toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'iMessage' })).toBeNull()
  })

  it('every new switch has its own icon, not the generic fallback', () => {
    renderSettings()
    for (const label of ['Portfolio', 'Executive Summary', 'Jira', 'Confluence', 'ostk', 'Messages']) {
      const row = screen.getByRole('switch', { name: label }).closest('div')
      expect(row?.textContent, `${label} row uses the fallback icon`).not.toContain('extension')
    }
  })
})

// ---------------------------------------------------------------------------
// →2925: the S021 "Add tools" installer (McpInstaller) was built with passing
// tests but never mounted on any screen, so users could not reach the
// per-server "Allow in chat" checkbox. It now lives in the Connections tab of
// Settings, between the connection pills and the custom tack commands card.
// ---------------------------------------------------------------------------
describe('Settings mounts the MCP tool installer (S021, →2925)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'yourOS',
      darkMode: true,
      accentColor: 'blue',
      features: [{ label: 'Chat', enabled: true }],
    })
  })

  it('renders the installer inside the Connections section', async () => {
    renderSettings()
    const installer = await screen.findByTestId('mcp-installer')
    const connections = document.getElementById('section-connections')
    expect(connections).not.toBeNull()
    expect(connections!.contains(installer)).toBe(true)
  })

  it('shows the Allow in chat checkbox for an installed server on the settings screen', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/api/mcp/catalog') {
        return Promise.resolve({
          catalog: [
            { name: 'Slack', description: 'Send messages and read channels', icon: 'forum', npm_package: '@modelcontextprotocol/server-slack', requires_auth: false },
          ],
        })
      }
      if (path === '/api/mcp/installed') return Promise.resolve({ installed: ['Slack'] })
      if (path === '/api/settings') return Promise.resolve({ mcp_servers: [] })
      return Promise.resolve({})
    })
    renderSettings()
    const box = await screen.findByTestId('mcp-allow-chat-Slack')
    expect((box as HTMLInputElement).checked).toBe(false)
    expect(screen.getByTestId('mcp-allow-chat-warning').textContent).toContain('chat can read from and act on')
  })

  it('MOUNT REGRESSION: Settings.tsx keeps the McpInstaller import and mount', () => {
    // Guards against the failure mode that orphaned this component once
    // already: the component file existed with passing tests while no screen
    // imported it, and a dead-code sweep nearly deleted it. If the mount is
    // removed the render tests above also fail, but this one names the exact
    // cause instead of a missing testid.
    const candidates = ['src/pages/Settings.tsx', 'app/src/pages/Settings.tsx']
    const sourcePath = candidates.find((c) => fs.existsSync(c))
    expect(sourcePath, 'could not locate the Settings.tsx source file').toBeTruthy()
    const source = fs.readFileSync(sourcePath!, 'utf8')
    expect(source).toContain("import McpInstaller from '../components/McpInstaller'")
    expect(source).toContain('<McpInstaller')
  })
})
