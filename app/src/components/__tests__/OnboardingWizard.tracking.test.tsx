// Tests for OnboardingWizard Tracking step (→1224)
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import OnboardingWizard from '../OnboardingWizard'
import { useAppStore } from '../../stores/app'
import { api } from '../../lib/api'

vi.mock('../../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

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

// Navigate to Tracking step (index 5 in PERSONAL_STEPS_NO_FORK)
function clickNext(n: number) {
  for (let i = 0; i < n; i++) {
    fireEvent.click(screen.getByTestId('next-button'))
  }
}

function goToTrackingStep() {
  clickNext(5) // Welcome(0)->You->Name->Profile->Theme->Tracking(5)
}

describe('OnboardingWizard - Tracking step', () => {
  beforeEach(() => {
    localStorageMock.clear()
    useAppStore.setState({
      onboarded: false,
      osName: 'yourOS',
      darkMode: false,
      defaultChatModel: 'claude',
      instanceMode: 'personal',
      orgName: '',
      teamAccentColor: '#6366f1',
      displayOsName: () => 'yourOS',
      setInstanceMode: vi.fn() as unknown as (mode: 'personal' | 'team') => void,
      setOrgName: vi.fn(),
      setAgentsLastViewed: vi.fn() as unknown as (v: string) => void,
    })
    vi.mocked(api.get).mockReset()
    vi.mocked(api.post).mockReset()
    vi.mocked(api.get).mockResolvedValue({})
    vi.mocked(api.post).mockResolvedValue({})
    vi.mocked(api.patch).mockResolvedValue({})
  })

  it('renders the Tracking step after Theme', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
  })

  it('shows all three radio options', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    expect(screen.getByTestId('tracking-option-everywhere')).toBeInTheDocument()
    expect(screen.getByTestId('tracking-option-repo')).toBeInTheDocument()
    expect(screen.getByTestId('tracking-option-myos-only')).toBeInTheDocument()
  })

  it('shows exact copy for option 1', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    const opt = screen.getByTestId('tracking-option-everywhere')
    expect(opt).toHaveTextContent('Track everything: yourOS is my main dashboard.')
    expect(opt).toHaveTextContent("Every Claude Code conversation on this computer shows up in yourOS, no matter which folder you're in.")
  })

  it('shows exact copy for option 2', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    const opt = screen.getByTestId('tracking-option-repo')
    expect(opt).toHaveTextContent('Track one specific project.')
    expect(opt).toHaveTextContent('Only conversations inside the project you choose show up in yourOS. Other projects stay untouched.')
  })

  it('shows exact copy for option 3', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    const opt = screen.getByTestId('tracking-option-myos-only')
    expect(opt).toHaveTextContent('I just want to try yourOS without touching anything else.')
    expect(opt).toHaveTextContent('Only conversations inside the yourOS folder show up. Nothing else on your computer changes.')
  })

  it('folder picker is hidden until option 2 is selected', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    expect(screen.queryByTestId('tracking-folder-picker')).not.toBeInTheDocument()
  })

  it('folder picker appears when option 2 (repo) is selected', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    fireEvent.click(screen.getByTestId('tracking-option-repo'))
    expect(screen.getByTestId('tracking-folder-picker')).toBeInTheDocument()
    expect(screen.getByTestId('tracking-folder-input')).toBeInTheDocument()
  })

  it('folder picker disappears when switching away from option 2', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    fireEvent.click(screen.getByTestId('tracking-option-repo'))
    expect(screen.getByTestId('tracking-folder-picker')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('tracking-option-everywhere'))
    expect(screen.queryByTestId('tracking-folder-picker')).not.toBeInTheDocument()
  })

  it('displays selected folder name after picking', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    fireEvent.click(screen.getByTestId('tracking-option-repo'))

    const input = screen.getByTestId('tracking-folder-input')
    fireEvent.change(input, { target: { value: 'myproject' } })

    expect((input as HTMLInputElement).value).toBe('myproject')
  })

  it('calls enable-myos-hooks with scope=everywhere when option 1 is picked and wizard finishes', async () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    fireEvent.click(screen.getByTestId('tracking-option-everywhere'))

    // Advance to Ready then finish
    clickNext(2) // Tracking -> Connect -> Ready
    fireEvent.click(screen.getByTestId('finish-button'))

    await waitFor(() => {
      const calls = vi.mocked(api.post).mock.calls
      const hookCall = calls.find(([url]) => url === '/onboarding/enable-myos-hooks')
      expect(hookCall).toBeTruthy()
      expect(hookCall![1]).toEqual({ scope: 'everywhere' })
    })
  })

  it('calls enable-myos-hooks with scope=repo and path when option 2 is picked', async () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    fireEvent.click(screen.getByTestId('tracking-option-repo'))

    const input = screen.getByTestId('tracking-folder-input')
    fireEvent.change(input, { target: { value: 'workproject' } })

    clickNext(2) // Tracking -> Connect -> Ready
    fireEvent.click(screen.getByTestId('finish-button'))

    await waitFor(() => {
      const calls = vi.mocked(api.post).mock.calls
      const hookCall = calls.find(([url]) => url === '/onboarding/enable-myos-hooks')
      expect(hookCall).toBeTruthy()
      expect(hookCall![1]).toEqual({ scope: 'repo', path: 'workproject' })
    })
  })

  it('calls enable-myos-hooks with scope=myos-only when option 3 is picked', async () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    fireEvent.click(screen.getByTestId('tracking-option-myos-only'))

    clickNext(2) // Tracking -> Connect -> Ready
    fireEvent.click(screen.getByTestId('finish-button'))

    await waitFor(() => {
      const calls = vi.mocked(api.post).mock.calls
      const hookCall = calls.find(([url]) => url === '/onboarding/enable-myos-hooks')
      expect(hookCall).toBeTruthy()
      expect(hookCall![1]).toEqual({ scope: 'myos-only' })
    })
  })

  it('does not call enable-myos-hooks when no tracking option is picked', async () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    // Skip tracking step without picking anything
    clickNext(2) // Tracking -> Connect -> Ready
    fireEvent.click(screen.getByTestId('finish-button'))

    await waitFor(() => {
      expect(useAppStore.getState().onboarded).toBe(true)
    })
    const calls = vi.mocked(api.post).mock.calls
    const hookCall = calls.find(([url]) => url === '/onboarding/enable-myos-hooks')
    expect(hookCall).toBeUndefined()
  })

  it('Tracking step is skippable via Skip button', () => {
    render(<OnboardingWizard />)
    goToTrackingStep()
    expect(screen.getByTestId('tracking-option-everywhere')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
  })
})
