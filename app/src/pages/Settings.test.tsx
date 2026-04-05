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
      osName: 'YourOS',
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
      const input = screen.getByDisplayValue('YourOS')
      expect(input).toBeInTheDocument()
    })

    it('updates osName in the store on change', () => {
      renderSettings()
      const input = screen.getByDisplayValue('YourOS')
      fireEvent.change(input, { target: { value: 'MyOS' } })
      expect(useAppStore.getState().osName).toBe('MyOS')
    })

    it('persists osName to API on blur', () => {
      renderSettings()
      const input = screen.getByDisplayValue('YourOS')
      fireEvent.change(input, { target: { value: 'MyOS' } })
      fireEvent.blur(input)
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { os_name: 'MyOS' })
    })
  })

  describe('Feature toggles', () => {
    it('updates features in the global store when toggled', () => {
      renderSettings()
      // Click on the Transcripts feature (which starts as disabled)
      const transcriptsFeature = screen.getByText('Transcripts').closest('div[class*="cursor-pointer"]')
      expect(transcriptsFeature).toBeTruthy()
      fireEvent.click(transcriptsFeature!)

      const updated = useAppStore.getState().features
      const transcripts = updated.find((f) => f.label === 'Transcripts')
      expect(transcripts?.enabled).toBe(true)
    })

    it('persists feature toggles to API', () => {
      renderSettings()
      const transcriptsFeature = screen.getByText('Transcripts').closest('div[class*="cursor-pointer"]')
      fireEvent.click(transcriptsFeature!)

      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', expect.objectContaining({
        features: expect.objectContaining({ Transcripts: true }),
      }))
    })

    it('disabling a feature updates the store', () => {
      renderSettings()
      const tasksFeature = screen.getByText('Tasks').closest('div[class*="cursor-pointer"]')
      fireEvent.click(tasksFeature!)

      const updated = useAppStore.getState().features
      const tasks = updated.find((f) => f.label === 'Tasks')
      expect(tasks?.enabled).toBe(false)
    })
  })

  describe('API Key', () => {
    it('persists API key to API on Save Key click', () => {
      renderSettings()
      const input = screen.getByPlaceholderText('sk-ant-xxxx...')
      fireEvent.change(input, { target: { value: 'sk-ant-test123' } })
      const saveBtn = screen.getByText('Save Key')
      fireEvent.click(saveBtn)
      expect(mockedApiPatch).toHaveBeenCalledWith('/settings', { anthropic_api_key: 'sk-ant-test123' })
    })

    it('toggles API key visibility', () => {
      renderSettings()
      const input = screen.getByPlaceholderText('sk-ant-xxxx...')
      expect(input).toHaveAttribute('type', 'password')

      // Find the visibility toggle button (the one inside the API key section)
      const visToggle = input.parentElement?.querySelector('button')
      expect(visToggle).toBeTruthy()
      fireEvent.click(visToggle!)
      expect(input).toHaveAttribute('type', 'text')
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
})
