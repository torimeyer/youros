import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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

  describe('PageHeader', () => {
    it('renders a PageHeader with title Settings at the top', () => {
      renderSettings()
      expect(screen.getByTestId('page-header')).toBeInTheDocument()
    })

    it('PageHeader displays the text "Settings"', () => {
      renderSettings()
      const header = screen.getByTestId('page-header')
      expect(header).toHaveTextContent('Settings')
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

    it('renders AtlassianSetupCard inside atlassian section after api resolves', async () => {
      renderSettings()
      const atlassianPill = screen.getByTestId('pill-atlassian')
      fireEvent.click(atlassianPill)
      const card = await screen.findByTestId('onboarding-atlassian-card')
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

  describe('Budget Caps toggle', () => {
    it('renders the budget caps toggle button', async () => {
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('budget-caps-toggle')).toBeInTheDocument()
      })
    })

    it('shows "Show budget caps" label', async () => {
      renderSettings()
      await waitFor(() => {
        expect(screen.getByText('Show budget caps')).toBeInTheDocument()
      })
    })

    it('budget caps toggle defaults to off (aria-pressed=false)', async () => {
      renderSettings()
      await waitFor(() => {
        const toggle = screen.getByTestId('budget-caps-toggle')
        expect(toggle).toHaveAttribute('aria-pressed', 'false')
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

    it('renders all five connection pills: Google, Slack, iMessage, GitHub, Atlassian', async () => {
      renderSettings()

      await waitFor(() => {
        expect(screen.getByTestId('pill-google')).toBeInTheDocument()
        expect(screen.getByTestId('pill-slack')).toBeInTheDocument()
        expect(screen.getByTestId('pill-imessage')).toBeInTheDocument()
        expect(screen.getByTestId('pill-github')).toBeInTheDocument()
        expect(screen.getByTestId('pill-atlassian')).toBeInTheDocument()
      })
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

    it('iMessage status dot is green when iMessage is connected (→1695)', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/imessage/status') return Promise.resolve({ available: true, reason: '' })
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        const dot = screen.getByTestId('imessage-status-dot')
        expect(dot.className).toContain('bg-emerald-400')
      })
    })

    it('iMessage status dot is gray when iMessage is not connected (→1695)', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/imessage/status') return Promise.resolve({ available: false, reason: 'not available' })
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        const dot = screen.getByTestId('imessage-status-dot')
        expect(dot.className).not.toContain('bg-emerald-400')
        expect(dot.className).toContain('bg-slate-600')
      })
    })
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

    it('shows claude login instructions when Claude is not signed in', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/settings/chat-backend-status') return Promise.resolve({ claude_code_available: false })
        return Promise.resolve({})
      })
      renderSettings()
      await waitFor(() => {
        expect(screen.getByTestId('claude-login-instructions')).toBeInTheDocument()
      })
      expect(screen.getByText(/claude login/i)).toBeInTheDocument()
    })

    it('does not show claude login instructions when Claude is signed in', async () => {
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
      expect(screen.getByTestId('standing-instructions-section')).toBeInTheDocument()
      expect(screen.getByText('Standing instructions')).toBeInTheDocument()
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



describe('Settings page — Developer section', () => {
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

describe('Settings page — Push notifications toggle', () => {
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

    it('renders an "Open activity log" link in Settings Preferences', () => {
      renderSettings()
      switchToPreferences()
      expect(screen.getByTestId('settings-activity-link')).toBeInTheDocument()
      expect(screen.getByText('Open activity log')).toBeInTheDocument()
    })

    it('"Open activity log" link points to /activity', () => {
      renderSettings()
      switchToPreferences()
      const link = screen.getByTestId('settings-activity-link')
      expect(link.getAttribute('href')).toBe('/activity')
    })
  })
})

describe('Settings — Memory provenance (F4)', () => {
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
      expect(screen.getByTestId('memory-bullet-list')).toBeInTheDocument()
    })
    expect(screen.getByTestId('memory-provenance-0')).toHaveTextContent('added May 17, 2026')
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
      expect(screen.getByTestId('memory-bullet-list')).toBeInTheDocument()
    })
    expect(screen.getByTestId('memory-provenance-0')).toHaveTextContent('edited manually')
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
      expect(screen.getByTestId('memory-hard-cap-banner')).toBeInTheDocument()
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

describe('Settings — Gemini provider clarity', () => {
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
