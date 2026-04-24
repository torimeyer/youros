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
      osName: 'myOS',
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

  describe('Dark/Light mode toggle', () => {
    it('toggles darkMode in the store when Light is clicked', () => {
      renderSettings()
      const lightBtn = screen.getByText('Light')
      fireEvent.click(lightBtn)
      expect(useAppStore.getState().darkMode).toBe(false)
    })

    it('toggles darkMode in the store when Dark is clicked after setting to light', () => {
      useAppStore.setState({ darkMode: false })
      renderSettings()
      const darkBtn = screen.getByText('Dark')
      fireEvent.click(darkBtn)
      expect(useAppStore.getState().darkMode).toBe(true)
    })

    it('persists dark mode change to API', () => {
      renderSettings()
      const lightBtn = screen.getByText('Light')
      fireEvent.click(lightBtn)
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { dark_mode: false })
    })

    it('does not toggle when clicking already-active mode', () => {
      renderSettings()
      // darkMode is true, clicking Dark should not toggle
      const darkBtn = screen.getByText('Dark')
      fireEvent.click(darkBtn)
      expect(useAppStore.getState().darkMode).toBe(true)
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
      const input = screen.getByDisplayValue('myOS')
      expect(input).toBeInTheDocument()
    })

    it('updates osName in the store on change', () => {
      renderSettings()
      const input = screen.getByDisplayValue('myOS')
      fireEvent.change(input, { target: { value: 'MyOS' } })
      expect(useAppStore.getState().osName).toBe('MyOS')
    })

    it('persists osName to API on blur', () => {
      renderSettings()
      const input = screen.getByDisplayValue('myOS')
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

      // Helper block is a decision tree: Cloud Console is recommended,
      // AI Studio is the chat-only fallback. Both paths must still appear.
      expect(screen.getByText(/Where to get a Gemini API key/i)).toBeInTheDocument()
      expect(screen.getByText(/Google AI Studio/i)).toBeInTheDocument()
      expect(screen.getByText(/Google Cloud project/i)).toBeInTheDocument()
      expect(screen.getByText(/Recommended\./i)).toBeInTheDocument()
      expect(screen.getByText(/Chat only\./i)).toBeInTheDocument()
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
    it('renders import and export buttons', () => {
      renderSettings()
      expect(screen.getByText('Import Config')).toBeInTheDocument()
      expect(screen.getByText('Export Config')).toBeInTheDocument()
    })
  })

  describe('AI Provider selector', () => {
    it('renders the AI Provider section with Anthropic and Google Gemini options', () => {
      renderSettings()
      expect(screen.getByText('AI Provider')).toBeInTheDocument()
      expect(screen.getByText('Anthropic')).toBeInTheDocument()
      expect(screen.getByText('Google Gemini')).toBeInTheDocument()
    })

    it('selects Gemini provider and persists to API', () => {
      renderSettings()
      // Find the Google Gemini provider card and click it
      const geminiCard = screen.getByText('Google Gemini').closest('div[class*="cursor-pointer"]')!
      fireEvent.click(geminiCard)

      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { provider: 'Google Gemini' })
    })

    it('selects Anthropic provider and persists to API', () => {
      renderSettings()
      // First select Gemini, then go back to Anthropic
      const geminiCard = screen.getByText('Google Gemini').closest('div[class*="cursor-pointer"]')!
      fireEvent.click(geminiCard)
      vi.clearAllMocks()

      const anthropicCard = screen.getByText('Anthropic').closest('div[class*="cursor-pointer"]')!
      fireEvent.click(anthropicCard)

      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { provider: 'Anthropic' })
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

  describe('ostk-managed MCP servers', () => {
    it('shows ostk-managed servers from the API', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/settings/mcp-servers') {
          return Promise.resolve({
            ostk_servers: [
              { name: 'linear', command: 'npx -y @anthropic/linear-mcp-server' },
              { name: 'github', command: 'gh mcp-server' },
            ],
            manual_servers: [],
          })
        }
        if (path === '/secrets/key-status') {
          return Promise.resolve({ google_oauth_available: false })
        }
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        expect(screen.getByText('linear')).toBeInTheDocument()
      })
      expect(screen.getByText('github')).toBeInTheDocument()
      expect(screen.getByText('Managed automatically (configured in your profile)')).toBeInTheDocument()
    })

    it('does not show the ostk section when no ostk servers exist', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/settings/mcp-servers') {
          return Promise.resolve({ ostk_servers: [], manual_servers: [] })
        }
        if (path === '/secrets/key-status') {
          return Promise.resolve({ google_oauth_available: false })
        }
        return Promise.resolve({})
      })

      renderSettings()

      // Wait for render cycle
      await waitFor(() => {
        expect(screen.getByText('Connected Tools')).toBeInTheDocument()
      })

      expect(screen.queryByText('Managed automatically (configured in your profile)')).not.toBeInTheDocument()
    })

    it('shows "Added manually" label when both ostk and manual servers exist', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockImplementation((path: string) => {
        if (path === '/settings/mcp-servers') {
          return Promise.resolve({
            ostk_servers: [{ name: 'linear', command: 'npx -y @anthropic/linear-mcp-server' }],
            manual_servers: [],
          })
        }
        if (path === '/secrets/key-status') {
          return Promise.resolve({ google_oauth_available: false })
        }
        return Promise.resolve({
          mcp_servers: [{ name: 'stitch', url: 'https://stitch.example.com', enabled: true }],
        })
      })

      renderSettings()

      await waitFor(() => {
        expect(screen.getByText('linear')).toBeInTheDocument()
      })
      expect(screen.getByText('stitch')).toBeInTheDocument()
      expect(screen.getByText('Added manually')).toBeInTheDocument()
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

      // Now resolve all four and confirm the dots render in the same
      // subsequent render pass (all loading states clear together).
      resolvers['/gmail/auth/status']()
      resolvers['/calendar/auth/status']()
      resolvers['/drive/auth/status']()
      resolvers['/slack/status']()

      await waitFor(() => {
        expect(screen.getByTestId('connection-dot-gmail')).toHaveAttribute('data-connected', 'yes')
        expect(screen.getByTestId('connection-dot-calendar')).toHaveAttribute('data-connected', 'yes')
        expect(screen.getByTestId('connection-dot-drive')).toHaveAttribute('data-connected', 'yes')
        expect(screen.getByTestId('connection-dot-slack')).toHaveAttribute('data-connected', 'yes')
      })
    })

    it('shows a red dot for disconnected services', async () => {
      vi.mocked(api.get).mockImplementation((path: string) => {
        if (path === '/gmail/auth/status') return Promise.resolve({ authenticated: false, email: null })
        if (path === '/calendar/auth/status') return Promise.resolve({ authenticated: false, email: null })
        if (path === '/drive/auth/status') return Promise.resolve({ authenticated: false, email: null })
        if (path === '/slack/status') return Promise.resolve({ connected: false, team_name: '' })
        return Promise.resolve({})
      })

      renderSettings()

      await waitFor(() => {
        expect(screen.getByTestId('connection-dot-gmail')).toHaveAttribute('data-connected', 'no')
        expect(screen.getByTestId('connection-dot-slack')).toHaveAttribute('data-connected', 'no')
      })
    })
  })
})

describe('Settings - Enter key submit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue({})
    useAppStore.setState({ osName: 'myOS', darkMode: false })
  })

  it('Enter on the OS Identifier field saves the name', async () => {
    renderSettings()

    // Navigate to the Appearance section where OS Identifier lives
    await waitFor(() => {
      expect(screen.getByText('Appearance')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Appearance'))

    const osInput = await screen.findByDisplayValue('myOS')
    fireEvent.change(osInput, { target: { value: 'ToriOS' } })
    fireEvent.keyDown(osInput, { key: 'Enter' })

    // handleOsNameBlur sets store and patches settings
    await waitFor(() => {
      expect(useAppStore.getState().osName).toBe('ToriOS')
    })
  })

  it('Enter on the MCP URL field adds the server', async () => {
    mockedApiPatch.mockResolvedValue({ ok: true })
    renderSettings()

    const nameInput = await screen.findByPlaceholderText('Server name (e.g. Stitch)')
    const urlInput = await screen.findByPlaceholderText('Paste your server URL after running setup')

    fireEvent.change(nameInput, { target: { value: 'TestServer' } })
    fireEvent.change(urlInput, { target: { value: 'https://example.com/mcp' } })
    fireEvent.keyDown(urlInput, { key: 'Enter' })

    await waitFor(() => {
      expect(mockedApiPatch).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({ mcp_servers: expect.arrayContaining([
          expect.objectContaining({ name: 'TestServer', url: 'https://example.com/mcp' })
        ]) }),
      )
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


describe('Settings page — Files location', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.endsWith('/settings')) {
        return { onboarded: true, files_dir: '/Users/me/.myos/files' }
      }
      return {}
    })
    window.confirm = vi.fn().mockReturnValue(true)
  })

  const renderIt = () =>
    render(
      <MemoryRouter><Settings /></MemoryRouter>
    )

  it('renders a Files location field and submits a new path via PUT', async () => {
    renderIt()
    const input = await screen.findByTestId('files-dir-input') as HTMLInputElement
    await waitFor(() => expect(input.value).toBe('/Users/me/.myos/files'))

    fireEvent.change(input, { target: { value: '/tmp/new-files' } })
    fireEvent.click(screen.getByTestId('files-dir-change'))

    expect(window.confirm).toHaveBeenCalled()
    await waitFor(() => {
      expect(vi.mocked(api.put)).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({ files_dir: '/tmp/new-files' }),
      )
    })
  })
})

describe('Settings page — Developer section', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({
      osName: 'myOS',
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

  it('renders the Developer section with a heading', () => {
    renderSettings()
    const section = screen.getByTestId('developer-section')
    expect(section).toBeInTheDocument()
    expect(section).toHaveTextContent('Developer')
  })

  it('Developer section shows a "View activity log" link pointing to /activity', () => {
    renderSettings()
    const link = screen.getByTestId('developer-activity-link')
    expect(link).toBeInTheDocument()
    expect(link).toHaveTextContent('View activity log')
    expect(link).toHaveAttribute('href', '/activity')
  })

  it('Developer section shows a "View transcripts" link pointing to /transcripts', () => {
    renderSettings()
    const link = screen.getByTestId('developer-transcripts-link')
    expect(link).toBeInTheDocument()
    expect(link).toHaveTextContent('View transcripts')
    expect(link).toHaveAttribute('href', '/transcripts')
  })
})
