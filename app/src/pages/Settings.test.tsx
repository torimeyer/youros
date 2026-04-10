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
        { label: 'Hay/Ideas', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Docs', enabled: true },
        { label: 'Transcripts', enabled: false },
      ],
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
      // Click the toggle switch for Transcripts
      const toggle = screen.getByRole('switch', { name: /Transcripts/i })
      fireEvent.click(toggle)

      const updated = useAppStore.getState().features
      const transcripts = updated.find((f) => f.label === 'Transcripts')
      expect(transcripts?.enabled).toBe(true)
    })

    it('persists feature toggles to API', () => {
      renderSettings()
      const toggle = screen.getByRole('switch', { name: /Transcripts/i })
      fireEvent.click(toggle)

      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', expect.objectContaining({
        features: expect.objectContaining({ Transcripts: true }),
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

  describe('Default LLM selector', () => {
    it('renders the Default Chat AI section with Claude and Gemini options', () => {
      renderSettings()
      expect(screen.getByText('Default Chat AI')).toBeInTheDocument()
      // Both provider cards should be rendered via data-testid
      expect(screen.getByTestId('default-llm-anthropic')).toBeInTheDocument()
      expect(screen.getByTestId('default-llm-google-gemini')).toBeInTheDocument()
    })

    it('selects Gemini as default LLM and persists to API', () => {
      renderSettings()
      const geminiCard = screen.getByTestId('default-llm-google-gemini')
      fireEvent.click(geminiCard)

      expect(useAppStore.getState().defaultChatModel).toBe('gemini')
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { default_model: '@gemini' })
    })

    it('selects Claude as default LLM and persists to API', () => {
      renderSettings()
      // First select Gemini, then go back to Claude
      fireEvent.click(screen.getByTestId('default-llm-google-gemini'))
      vi.clearAllMocks()

      fireEvent.click(screen.getByTestId('default-llm-anthropic'))

      expect(useAppStore.getState().defaultChatModel).toBe('claude')
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { default_model: '@claude' })
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

    it('describes the feature in plain language', () => {
      renderSettings()
      expect(
        screen.getByText(/new conversations will use this AI by default/i)
      ).toBeInTheDocument()
    })
  })

  describe('Feature toggle key normalization', () => {
    it('reads lowercase feature keys from backend and applies them correctly', async () => {
      // Simulate a backend that returns lowercase keys (old format)
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockResolvedValue({
        features: { tasks: false, chat: true, transcripts: true },
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
      const transcripts = state.features.find((f) => f.label === 'Transcripts')
      expect(transcripts?.enabled).toBe(true)
    })

    it('reads TitleCase feature keys from backend and applies them correctly', async () => {
      const mockedApiGet = vi.mocked(api.get)
      mockedApiGet.mockResolvedValue({
        features: { Tasks: false, Chat: true, Transcripts: true },
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
      expect(screen.getByText('Managed by ostk (configured in your HUMANFILE)')).toBeInTheDocument()
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

      expect(screen.queryByText('Managed by ostk (configured in your HUMANFILE)')).not.toBeInTheDocument()
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
})
