import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import OnboardingWizard from './OnboardingWizard'
import { useAppStore } from '../stores/app'

// Mock the api module so network calls don't fire
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => { store[key] = value }),
    removeItem: vi.fn((key: string) => { delete store[key] }),
    clear: vi.fn(() => { store = {} }),
  }
})()
Object.defineProperty(window, 'localStorage', { value: localStorageMock })

// Helper: click Next n times
function clickNext(n: number) {
  for (let i = 0; i < n; i++) {
    fireEvent.click(screen.getByTestId('next-button'))
  }
}

describe('OnboardingWizard', () => {
  beforeEach(() => {
    localStorageMock.clear()
    useAppStore.setState({
      onboarded: false,
      osName: 'YourOS',
      darkMode: true,
      defaultChatModel: 'claude',
    })
  })

  it('renders the wizard', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('onboarding-wizard')).toBeInTheDocument()
  })

  it('starts on the Welcome step', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('step-welcome')).toBeInTheDocument()
    expect(screen.getByText('Welcome!')).toBeInTheDocument()
  })

  it('shows progress dots equal to the number of steps', () => {
    render(<OnboardingWizard />)
    const dots = screen.getByTestId('progress-dots')
    // 6 steps: Welcome, You, Name, Theme, Connect, Ready
    expect(dots.children).toHaveLength(6)
  })

  it('does not show Back button on Welcome step', () => {
    render(<OnboardingWizard />)
    expect(screen.queryByTestId('back-button')).not.toBeInTheDocument()
  })

  it('does not show Skip button on Welcome step', () => {
    render(<OnboardingWizard />)
    expect(screen.queryByTestId('skip-button')).not.toBeInTheDocument()
  })

  it('advances to You step when Next is clicked', async () => {
    render(<OnboardingWizard />)
    clickNext(1)
    expect(screen.getByTestId('step-you')).toBeInTheDocument()
  })

  it('shows Back button on You step', () => {
    render(<OnboardingWizard />)
    clickNext(1)
    expect(screen.getByTestId('back-button')).toBeInTheDocument()
  })

  it('shows Skip button on You step', () => {
    render(<OnboardingWizard />)
    clickNext(1)
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
  })

  it('advances to Name step', () => {
    render(<OnboardingWizard />)
    clickNext(2) // Welcome -> You -> Name
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
  })

  it('goes back to You from Name step', () => {
    render(<OnboardingWizard />)
    clickNext(2)
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('back-button'))
    expect(screen.getByTestId('step-you')).toBeInTheDocument()
  })

  it('allows typing an OS name', async () => {
    const user = userEvent.setup()
    render(<OnboardingWizard />)
    clickNext(2)

    const input = screen.getByTestId('os-name-input')
    await user.clear(input)
    await user.type(input, 'MyOS')

    expect(useAppStore.getState().osName).toBe('MyOS')
  })

  it('advances to Theme step', () => {
    render(<OnboardingWizard />)
    clickNext(3) // Welcome -> You -> Name -> Theme
    expect(screen.getByTestId('step-theme')).toBeInTheDocument()
  })

  it('toggles dark mode on Theme step', () => {
    render(<OnboardingWizard />)
    clickNext(3)

    // Currently dark. Click Light.
    fireEvent.click(screen.getByTestId('theme-light'))
    expect(useAppStore.getState().darkMode).toBe(false)

    // Click Dark.
    fireEvent.click(screen.getByTestId('theme-dark'))
    expect(useAppStore.getState().darkMode).toBe(true)
  })

  it('advances to Connect step', () => {
    render(<OnboardingWizard />)
    clickNext(4) // Welcome -> You -> Name -> Theme -> Connect
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
  })

  it('shows Anthropic connect option by default', () => {
    render(<OnboardingWizard />)
    clickNext(4)
    expect(screen.getByTestId('connect-anthropic')).toBeInTheDocument()
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
  })

  it('switches provider when Gemini is selected', () => {
    render(<OnboardingWizard />)
    clickNext(4)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    // Anthropic connect button should no longer be visible
    expect(screen.queryByTestId('connect-anthropic')).not.toBeInTheDocument()
  })

  it('advances to Ready step with summary', () => {
    render(<OnboardingWizard />)
    clickNext(5) // Welcome -> You -> Name -> Theme -> Connect -> Ready

    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    expect(screen.getByTestId('summary-os-name')).toHaveTextContent('YourOS')
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Dark')
    expect(screen.getByTestId('summary-provider')).toHaveTextContent('Anthropic')
  })

  it('does not show Skip button on Ready step', () => {
    render(<OnboardingWizard />)
    clickNext(5)

    expect(screen.queryByTestId('skip-button')).not.toBeInTheDocument()
  })

  it('shows "Get started" button on Ready step', () => {
    render(<OnboardingWizard />)
    clickNext(5)

    expect(screen.getByTestId('finish-button')).toHaveTextContent('Get started')
  })

  it('sets onboarded to true and persists to localStorage when finished', () => {
    render(<OnboardingWizard />)
    clickNext(5)
    fireEvent.click(screen.getByTestId('finish-button'))

    expect(useAppStore.getState().onboarded).toBe(true)
    expect(localStorageMock.setItem).toHaveBeenCalledWith('youros-onboarded', 'true')
  })

  it('skipping steps advances without changing settings', () => {
    render(<OnboardingWizard />)
    // Welcome -> You
    clickNext(1)
    // Skip You step
    fireEvent.click(screen.getByTestId('skip-button'))
    // Should be on Name step
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
    // OS name should still be default
    expect(useAppStore.getState().osName).toBe('YourOS')
  })

  it('does not show Back button on Ready step', () => {
    render(<OnboardingWizard />)
    clickNext(5)

    expect(screen.queryByTestId('back-button')).not.toBeInTheDocument()
  })

  it('Connect step is skippable', () => {
    render(<OnboardingWizard />)
    clickNext(4) // Get to Connect step
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    // Skip button should be available
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
  })
})
