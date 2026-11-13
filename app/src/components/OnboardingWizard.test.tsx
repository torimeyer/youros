import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import OnboardingWizard, { AtlassianSetupCard } from './OnboardingWizard'
import { OrgNameStep, AdminEmailStep } from './TeamOnboardingSteps'
import { useAppStore } from '../stores/app'
import { api } from '../lib/api'

// Mock the api module so network calls do not fire
vi.mock('../lib/api', () => {
  class MockApiError extends Error {
    response: { status: number; data: { detail?: string } }
    constructor(status: number, detail?: string) {
      super(detail || `HTTP ${status}`)
      this.response = { status, data: { detail } }
    }
  }
  return {
    api: {
      get: vi.fn().mockResolvedValue({}),
      post: vi.fn().mockResolvedValue({}),
      put: vi.fn().mockResolvedValue({}),
      patch: vi.fn().mockResolvedValue({}),
      delete: vi.fn().mockResolvedValue({}),
    },
    ApiError: MockApiError,
  }
})

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

beforeEach(() => {
  localStorageMock.clear()
})

// Helper: click Next n times
function clickNext(n: number) {
  for (let i = 0; i < n; i++) {
    fireEvent.click(screen.getByTestId('next-button'))
  }
}

describe('OnboardingWizard (3-step personal flow: Connect -> Tracking -> Personalize)', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
    localStorageMock.clear()
    useAppStore.setState({
      onboarded: false,
      osName: 'yourOS',
      darkMode: true,
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
    vi.mocked(api.patch).mockReset()
    vi.mocked(api.get).mockResolvedValue({})
    vi.mocked(api.post).mockResolvedValue({})
    vi.mocked(api.patch).mockResolvedValue({})
  })

  it('renders the wizard', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('onboarding-wizard')).toBeInTheDocument()
  })

  it('starts directly on the Connect step (step 0)', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    expect(screen.queryByTestId('step-welcome')).not.toBeInTheDocument()
  })

  it('shows exactly 3 progress dots for personal mode (Connect, Tracking, Personalize)', () => {
    render(<OnboardingWizard />)
    const dots = screen.getByTestId('progress-dots')
    expect(dots.children).toHaveLength(3)
  })

  it('does not show Back button on Connect (first step), and shows Skip + Next', () => {
    render(<OnboardingWizard />)
    expect(screen.queryByTestId('back-button')).not.toBeInTheDocument()
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    expect(screen.getByTestId('next-button')).toBeInTheDocument()
  })

  it('advances from Connect (0) to Tracking (1) to Personalize (2) and back', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()

    clickNext(1)
    expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
    expect(screen.getByTestId('back-button')).toBeInTheDocument()
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()

    clickNext(1)
    expect(screen.getByTestId('step-personalize')).toBeInTheDocument()
    expect(screen.getByTestId('back-button')).toBeInTheDocument()
    expect(screen.queryByTestId('skip-button')).not.toBeInTheDocument()
    expect(screen.getByTestId('finish-button')).toHaveTextContent('Get started')

    fireEvent.click(screen.getByTestId('back-button'))
    expect(screen.getByTestId('step-tracking')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('back-button'))
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
  })

  it('Connect step is skippable and advances to Tracking', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
  })

  it('never renders legacy Welcome, You, Profile, Ready, Adventure, or Showcase steps', () => {
    render(<OnboardingWizard />)
    for (let i = 0; i < 2; i++) {
      expect(screen.queryByTestId('step-welcome')).not.toBeInTheDocument()
      expect(screen.queryByTestId('step-you')).not.toBeInTheDocument()
      expect(screen.queryByTestId('step-profile')).not.toBeInTheDocument()
      expect(screen.queryByTestId('step-adventure')).not.toBeInTheDocument()
      expect(screen.queryByTestId('step-showcase')).not.toBeInTheDocument()
      expect(screen.queryByText('One thing to try right now')).not.toBeInTheDocument()
      fireEvent.click(screen.getByTestId('next-button'))
    }
    expect(screen.getByTestId('step-personalize')).toBeInTheDocument()
    expect(screen.queryByTestId('step-ready')).not.toBeInTheDocument()
  })

  it('allows typing an OS name on Personalize step', async () => {
    const user = userEvent.setup()
    render(<OnboardingWizard />)
    clickNext(2) // Connect -> Tracking -> Personalize

    expect(screen.getByText('Name your OS')).toBeInTheDocument()
    const input = screen.getByTestId('os-name-input')
    await user.clear(input)
    await user.type(input, 'MyOS')

    expect(useAppStore.getState().osName).toBe('MyOS')
    expect(screen.getByTestId('summary-os-name')).toHaveTextContent('MyOS')
  })

  it('toggles dark mode and flips wizard background + data-theme on Personalize step', () => {
    render(<OnboardingWizard />)
    clickNext(2) // -> Personalize

    const BLUE = 'rgb(59, 130, 246)'

    fireEvent.click(screen.getByTestId('theme-light'))
    expect((screen.getByTestId('theme-light') as HTMLElement).style.borderColor).toBe(BLUE)
    expect((screen.getByTestId('theme-dark') as HTMLElement).style.borderColor).not.toBe(BLUE)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect((screen.getByTestId('onboarding-wizard') as HTMLElement).style.backgroundColor).toBe('rgb(248, 250, 252)')

    fireEvent.click(screen.getByTestId('theme-dark'))
    expect((screen.getByTestId('theme-dark') as HTMLElement).style.borderColor).toBe(BLUE)
    expect((screen.getByTestId('theme-light') as HTMLElement).style.borderColor).not.toBe(BLUE)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect((screen.getByTestId('onboarding-wizard') as HTMLElement).style.backgroundColor).toBe('rgb(2, 6, 23)')
  })

  it('dark preview card always renders dark even in light theme', () => {
    render(<OnboardingWizard />)
    clickNext(2)
    fireEvent.click(screen.getByTestId('theme-light'))
    const preview = screen.getByTestId('theme-dark-preview') as HTMLElement
    expect(preview.style.backgroundColor).toBe('rgb(2, 6, 23)')
  })

  it('theme summary reflects store darkMode even when the user does not click a theme card', () => {
    useAppStore.setState({ onboarded: false, osName: 'yourOS', darkMode: true })
    render(<OnboardingWizard />)
    clickNext(2)
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Dark')
  })

  it('theme summary shows Light when store darkMode is false and theme card not clicked', () => {
    useAppStore.setState({ onboarded: false, osName: 'yourOS', darkMode: false })
    render(<OnboardingWizard />)
    clickNext(2)
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Light')
  })

  it('shows Anthropic connect option by default on Connect step', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('connect-anthropic')).toBeInTheDocument())
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
  })

  it('switches provider when Gemini is selected', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('connect-anthropic')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    expect(screen.queryByTestId('connect-anthropic')).not.toBeInTheDocument()
    expect(screen.getByTestId('gemini-aistudio-heading')).toHaveTextContent(/AI Studio key/i)
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
    expect(screen.getByTestId('step-connect')).toHaveTextContent(/Google AI Studio/i)
  })

  it('offers the Claude subscription (not just a key) when nothing is detected', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('use-claude-subscription')).toBeInTheDocument())
    expect(screen.getByTestId('use-claude-subscription')).toHaveTextContent(/no key/i)
    expect(screen.getByTestId('use-claude-subscription')).toHaveTextContent(/subscription|plan you already/i)
    expect(screen.getByTestId('pay-as-you-go-key')).toBeInTheDocument()
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
  })

  it('reassures the user it will not change their existing AI logins', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('connect-reassurance')).toBeInTheDocument())
    expect(screen.getByTestId('connect-reassurance')).toHaveTextContent(/does not change|will not change|won't change/i)
  })

  it('shows pick-a-project guidance when signed in to Google Cloud without a project', async () => {
    vi.mocked(api.get).mockImplementation(((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ vertex_ai_signed_in: true, vertex_ai: false })
      if (path === '/secrets/key-status') return Promise.resolve({ google_oauth_available: true, google_connected: false })
      if (path === '/github/status') return Promise.resolve({})
      return Promise.resolve({})
    }) as typeof api.get)
    render(<OnboardingWizard />)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    await waitFor(() =>
      expect(screen.getByTestId('vertex-signed-in-no-project')).toBeInTheDocument()
    )
    expect(screen.getByTestId('vertex-signed-in-no-project')).toHaveTextContent(/Pick a project/i)
  })

  it('Connect step sections show Anthropic, Google, Atlassian, GitHub headings', async () => {
    render(<OnboardingWizard />)
    const connectEl = screen.getByTestId('step-connect')
    expect(connectEl).toHaveTextContent(/Anthropic/i)
    expect(connectEl).toHaveTextContent(/Google/i)
    expect(connectEl).toHaveTextContent(/Atlassian/i)
    await waitFor(() => {
      expect(connectEl).toHaveTextContent(/GitHub/i)
    })
  })

  it('sets onboarded to true and persists to localStorage when finished', () => {
    render(<OnboardingWizard />)
    clickNext(2)
    fireEvent.click(screen.getByTestId('finish-button'))

    expect(useAppStore.getState().onboarded).toBe(true)
    expect(localStorageMock.setItem).toHaveBeenCalledWith('myos-onboarded', 'true')
  })

  it('resets agentsLastViewed to now when personal onboarding finishes (Bug 2: stale Finished badge)', () => {
    const setAgentsLastViewed = vi.fn()
    useAppStore.setState({ setAgentsLastViewed: setAgentsLastViewed as unknown as (v: string) => void })

    const before = Date.now()
    render(<OnboardingWizard />)
    clickNext(2)
    fireEvent.click(screen.getByTestId('finish-button'))
    const after = Date.now()

    expect(setAgentsLastViewed).toHaveBeenCalledTimes(1)
    const ts = new Date(setAgentsLastViewed.mock.calls[0][0]).getTime()
    expect(ts).toBeGreaterThanOrEqual(before)
    expect(ts).toBeLessThanOrEqual(after)
  })

  it('navigates to the homepage "/" when finished', () => {
    window.history.replaceState({}, '', '/settings')
    expect(window.location.pathname).toBe('/settings')

    render(<OnboardingWizard />)
    clickNext(2)
    fireEvent.click(screen.getByTestId('finish-button'))

    expect(window.location.pathname).toBe('/')
  })

  it('flips onboarded=true AFTER rewriting the URL so App.tsx mounts the router at "/"', () => {
    window.history.replaceState({}, '', '/calendar')
    let urlWhenFlipped: string | null = null
    const unsubscribe = useAppStore.subscribe((state, prev) => {
      if (state.onboarded && !prev.onboarded) {
        urlWhenFlipped = window.location.pathname
      }
    })

    try {
      render(<OnboardingWizard />)
      clickNext(2)
      fireEvent.click(screen.getByTestId('finish-button'))
    } finally {
      unsubscribe()
    }

    expect(urlWhenFlipped).toBe('/')
  })

  it('clears a stale leftover osName from localStorage or the store on mount', () => {
    useAppStore.getState().setOsName('e2e-test-os')
    expect(useAppStore.getState().osName).toBe('e2e-test-os')
    render(<OnboardingWizard />)
    expect(useAppStore.getState().osName).toBe('')
  })

  it('blanks the name field locally without PATCHing os_name to the server (→2777)', () => {
    useAppStore.setState({ osName: 'toriOS' })
    vi.mocked(api.patch).mockClear()
    render(<OnboardingWizard />)
    expect(useAppStore.getState().osName).toBe('')
    const osNamePatches = vi.mocked(api.patch).mock.calls.filter(
      ([path, body]) =>
        path === '/settings' &&
        !!body &&
        typeof body === 'object' &&
        'os_name' in (body as Record<string, unknown>)
    )
    expect(osNamePatches).toHaveLength(0)
  })

  it('privacy footer is visible on every step', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('onboarding-privacy-link')).toHaveAttribute('href', '/privacy')
    clickNext(1)
    expect(screen.getByTestId('onboarding-privacy-link')).toBeInTheDocument()
    clickNext(1)
    expect(screen.getByTestId('onboarding-privacy-link')).toBeInTheDocument()
  })
})

describe('OnboardingWizard - ostk kernel and HUMANFILE trust posture (→3061)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    window.history.replaceState({}, '', '/')
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
  })

  it('renders humanfile-trust-warning with ostk trust sign recovery instructions when humanfile_valid is false', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') {
        return Promise.resolve({
          claude_code: true,
          ostk_available: true,
          humanfile_valid: false,
          humanfile_warning: 'HUMANFILE at /Users/torimeyer/.HUMANFILE has been modified since signing. Run ostk trust sign ~/.HUMANFILE to re-sign.',
        })
      }
      return Promise.resolve({})
    })

    render(<OnboardingWizard />)

    const warning = await screen.findByTestId('humanfile-trust-warning')
    expect(warning).toHaveTextContent(/HUMANFILE/i)
    expect(warning).toHaveTextContent(/ostk trust sign/i)
  })

  it('hides humanfile-trust-warning when humanfile_valid is true', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') {
        return Promise.resolve({
          claude_code: true,
          ostk_available: true,
          humanfile_valid: true,
          humanfile_warning: null,
        })
      }
      return Promise.resolve({})
    })

    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    expect(screen.queryByTestId('humanfile-trust-warning')).not.toBeInTheDocument()
  })

  it('clicking recheck-humanfile-trust re-runs detection and clears the warning when signed', async () => {
    let signed = false
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') {
        return Promise.resolve({
          claude_code: true,
          ostk_available: true,
          humanfile_valid: signed,
          humanfile_warning: signed ? null : 'HUMANFILE modified since signing. Run ostk trust sign --humanfile.',
        })
      }
      return Promise.resolve({})
    })

    render(<OnboardingWizard />)
    await screen.findByTestId('humanfile-trust-warning')

    signed = true
    fireEvent.click(screen.getByTestId('recheck-humanfile-trust'))

    await waitFor(() => {
      expect(screen.queryByTestId('humanfile-trust-warning')).not.toBeInTheDocument()
    })
  })
})

describe('OnboardingWizard - Enter key advances steps', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    vi.mocked(api.get).mockResolvedValue({})
    vi.mocked(api.post).mockResolvedValue({})
    vi.mocked(api.patch).mockResolvedValue({})
    useAppStore.setState({
      onboarded: false,
      osName: 'yourOS',
      darkMode: false,
    })
  })

  it('Enter on the Connect API key input saves and advances to Tracking step', async () => {
    render(<OnboardingWizard />)

    const keyInput = await waitFor(() => screen.getByTestId('api-key-input'))
    fireEvent.change(keyInput, { target: { value: 'sk-ant-test123' } })
    fireEvent.keyDown(keyInput, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
    })
  })

  it('Enter on the Tracking step (window) advances to Personalize', async () => {
    render(<OnboardingWizard />)
    clickNext(1) // Connect -> Tracking

    expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-personalize')).toBeInTheDocument()
    })
  })

  it('Enter on the OS name input on Personalize finishes onboarding', async () => {
    render(<OnboardingWizard />)
    clickNext(2) // Connect -> Tracking -> Personalize

    const osInput = screen.getByTestId('os-name-input')
    fireEvent.change(osInput, { target: { value: 'ToriOS' } })
    fireEvent.keyDown(osInput, { key: 'Enter' })

    await waitFor(() => {
      expect(useAppStore.getState().onboarded).toBe(true)
    })
  })

  it('Enter on window on Personalize step finishes onboarding', async () => {
    render(<OnboardingWizard />)
    clickNext(2)

    expect(screen.getByTestId('step-personalize')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Enter' })

    await waitFor(() => {
      expect(useAppStore.getState().onboarded).toBe(true)
    })
  })
})

describe('OnboardingWizard - provider auto-detection (→931)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    vi.mocked(api.get).mockResolvedValue({})
    vi.mocked(api.post).mockResolvedValue({})
    vi.mocked(api.patch).mockResolvedValue({})
  })

  it('shows Connect step when no provider is detected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
  })

  it('shows Connect with already-connected badge when Claude Code is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: true, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Claude Code'))
    clickNext(2) // Connect -> Tracking -> Personalize
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Claude Code')
  })

  it('shows Connect with badge "Anthropic" when API key is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: true, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Anthropic'))
  })

  it('shows Connect with badge and api-key-input is hidden when provider detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: true })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Gemini'))
    expect(screen.queryByTestId('api-key-input')).not.toBeInTheDocument()
    clickNext(2)
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Gemini')
  })

  it('falls back to showing Connect when detection fetch fails', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.reject(new Error('network'))
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
  })

  it('shows Connect with badge "Vertex AI" when vertex_ai is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false, vertex_ai: true, bedrock: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Vertex AI'))
    clickNext(2)
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Vertex AI')
  })

  it('shows Connect with badge "AWS Bedrock" when bedrock is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false, vertex_ai: false, bedrock: true })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('AWS Bedrock'))
    clickNext(2)
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('AWS Bedrock')
  })

  it('shows badge "Gemini" when no-key Gemini (gemini_cli) is detected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false, gemini_cli: true })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Gemini'))
    expect(screen.queryByTestId('api-key-input')).not.toBeInTheDocument()
    clickNext(2)
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Gemini')
  })
})

describe('OnboardingWizard - Google Workspace OAuth button on Connect step', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
  })

  it('shows connect-google-workspace button when google_oauth_available is true', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/drive/auth/status') return Promise.resolve({ authenticated: false, credentials_file_present: true, email: null })
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
    })
  })

  it('does not show connect-google-workspace button when google_oauth_available is false', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('connect-google-workspace')).not.toBeInTheDocument()
  })

  it('connect-google-workspace button is visible regardless of which AI provider is selected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/drive/auth/status') return Promise.resolve({ authenticated: false, credentials_file_present: true, email: null })
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
  })

  it('shows "Connect your Google apps (optional)" section when google_oauth_available is true', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toHaveTextContent(/Connect your Google apps \(optional\)/i)
    })
    expect(screen.getByTestId('step-connect')).toHaveTextContent(/This connects Drive, Calendar, and Gmail/i)
  })

  it('shows upload area (not connect button) when google_oauth_available is false (S015: section always shown)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/drive/auth/status') return Promise.resolve({ authenticated: false, credentials_file_present: false, email: null })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('google-credentials-upload')).toBeInTheDocument()
    })
    expect(screen.getByText(/Connect your Google apps/i)).toBeInTheDocument()
    expect(screen.queryByTestId('connect-google-workspace')).not.toBeInTheDocument()
  })

  it('shows "Google account" label when connected email is @gmail.com', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/drive/auth/status') return Promise.resolve({ authenticated: true, email: 'user@gmail.com' })
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-google-workspace-card')).toHaveTextContent(/Google account/i)
    })
    expect(screen.getByTestId('onboarding-google-workspace-card')).not.toHaveTextContent(/Google Workspace/i)
  })

  it('shows "Google Workspace" label when connected email is a work domain', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/drive/auth/status') return Promise.resolve({ authenticated: true, email: 'user@company.com' })
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-google-workspace-card')).toHaveTextContent(/Google Workspace/i)
    })
  })
})

describe('OnboardingWizard - Atlassian and GitHub setup cards', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      if (path === '/github/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    vi.mocked(api.post).mockResolvedValue({})
  })

  it('Atlassian card renders in Connect step when not connected', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-atlassian-card')).toBeInTheDocument()
    })
  })

  it('GitHub card renders in Connect step when not connected', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-github-card')).toBeInTheDocument()
    })
  })

  it('cards visible and Next advances from Connect to Tracking', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('onboarding-atlassian-card')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('next-button'))
    expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
  })

  it('Atlassian card calls /atlassian/defaults on expand and pre-fills site', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/defaults') return Promise.resolve({ site: 'https://myco.atlassian.net', email: '' })
      if (path === '/github/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('onboarding-atlassian-card')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('onboarding-atlassian-setup'))

    await waitFor(() => {
      expect(vi.mocked(api.get)).toHaveBeenCalledWith('/atlassian/defaults')
    })
    await waitFor(() => {
      const siteInput = screen.getByTestId('onboarding-atlassian-site') as HTMLInputElement
      expect(siteInput.value).toBe('https://myco.atlassian.net')
    })
  })

  it('Atlassian connect button posts to /atlassian/connect with correct fields', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('onboarding-atlassian-card')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('onboarding-atlassian-setup'))

    await waitFor(() => expect(screen.getByTestId('onboarding-atlassian-site')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('onboarding-atlassian-site'), { target: { value: 'https://acme.atlassian.net' } })
    fireEvent.change(screen.getByTestId('onboarding-atlassian-email'), { target: { value: 'user@acme.com' } })
    fireEvent.change(screen.getByTestId('onboarding-atlassian-token'), { target: { value: 'mytoken' } })

    fireEvent.click(screen.getByTestId('onboarding-atlassian-connect'))

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/atlassian/connect', {
        site: 'https://acme.atlassian.net',
        email: 'user@acme.com',
        api_token: 'mytoken',
      })
    })
  })

  it('GitHub connect button posts to /github/connect with correct fields', async () => {
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('onboarding-github-card')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('onboarding-github-setup'))

    await waitFor(() => expect(screen.getByTestId('onboarding-github-repo')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('onboarding-github-repo'), { target: { value: 'acme/website' } })
    fireEvent.change(screen.getByTestId('onboarding-github-token'), { target: { value: 'ghp_mytoken' } })

    fireEvent.click(screen.getByTestId('onboarding-github-connect'))

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith('/github/connect', {
        repo: 'acme/website',
        token: 'ghp_mytoken',
      })
    })
  })

  it('Atlassian card is hidden when already connected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/atlassian/status') return Promise.resolve({ connected: true })
      if (path === '/github/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.queryByTestId('onboarding-atlassian-card')).not.toBeInTheDocument())
  })

  it('GitHub card is hidden when already connected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      if (path === '/github/status') return Promise.resolve({ connected: true })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.queryByTestId('onboarding-github-card')).not.toBeInTheDocument())
  })
})

describe('TeamOnboardingSteps - Enter key on inputs', () => {
  it('Enter on OrgName input calls onNext', () => {
    const onNext = vi.fn()
    render(<OrgNameStep orgName="Acme" setOrgName={vi.fn()} onNext={onNext} inputCls="" subtextCls="" />)
    const input = screen.getByTestId('org-name-input')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onNext).toHaveBeenCalledOnce()
  })

  it('Enter on AdminEmail input calls onNext', () => {
    const onNext = vi.fn()
    render(<AdminEmailStep adminEmail="admin@co.com" setAdminEmail={vi.fn()} onNext={onNext} inputCls="" subtextCls="" />)
    const input = screen.getByTestId('admin-email-input')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onNext).toHaveBeenCalledOnce()
  })

  it('Other keys on OrgName input do not call onNext', () => {
    const onNext = vi.fn()
    render(<OrgNameStep orgName="" setOrgName={vi.fn()} onNext={onNext} inputCls="" subtextCls="" />)
    const input = screen.getByTestId('org-name-input')
    fireEvent.keyDown(input, { key: 'Tab' })
    expect(onNext).not.toHaveBeenCalled()
  })
})

describe('OnboardingWizard - TrackingStep repo path (→1520)', () => {
  beforeEach(() => {
    localStorageMock.clear()
    useAppStore.setState({
      onboarded: false,
      osName: 'yourOS',
      darkMode: true,
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
  })

  it('tracking-folder-input is a text input, not a file picker', () => {
    render(<OnboardingWizard />)
    clickNext(1) // Connect -> Tracking

    fireEvent.click(screen.getByTestId('tracking-option-repo'))

    const input = screen.getByTestId('tracking-folder-input') as HTMLInputElement
    expect(input.type).toBe('text')
  })

  it('captures the full absolute path typed by the user and sends it to the backend', async () => {
    render(<OnboardingWizard />)
    clickNext(1) // Connect -> Tracking

    fireEvent.click(screen.getByTestId('tracking-option-repo'))

    const pathInput = screen.getByTestId('tracking-folder-input') as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: '/Users/tori/work/myproject' } })
    expect(pathInput.value).toBe('/Users/tori/work/myproject')

    clickNext(1) // Tracking -> Personalize
    fireEvent.click(screen.getByTestId('finish-button'))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/onboarding/enable-myos-hooks',
        expect.objectContaining({ scope: 'repo', path: '/Users/tori/work/myproject' }),
      )
    })
  })
})

describe('OnboardingWizard - step persistence via localStorage (→1518)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
  })

  it('saves current step name to localStorage on every transition', () => {
    render(<OnboardingWizard />)
    fireEvent.click(screen.getByTestId('next-button')) // Connect -> Tracking
    const saved = JSON.parse(localStorage.getItem('myos.onboarding.state') ?? 'null')
    expect(saved).not.toBeNull()
    expect(saved.stepName).toBe('Tracking')
  })

  it('restores step from localStorage on mount', () => {
    localStorage.setItem('myos.onboarding.state', JSON.stringify({ stepName: 'Personalize', mode: 'personal' }))
    render(<OnboardingWizard />)
    expect(screen.queryByTestId('step-personalize')).toBeInTheDocument()
    expect(screen.queryByTestId('step-connect')).not.toBeInTheDocument()
  })

  it('clears localStorage when wizard finishes', async () => {
    render(<OnboardingWizard />)
    clickNext(2) // Connect -> Tracking -> Personalize
    fireEvent.click(screen.getByTestId('finish-button'))
    await waitFor(() => {
      expect(localStorage.getItem('myos.onboarding.state')).toBeNull()
    })
  })
})

describe('OnboardingWizard - exit hatch (→1519)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
  })

  it('renders a close button at the top-right of the wizard', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('onboarding-close-btn')).toBeInTheDocument()
  })

  it('clicking close button opens a confirm dialog', () => {
    render(<OnboardingWizard />)
    fireEvent.click(screen.getByTestId('onboarding-close-btn'))
    expect(screen.getByTestId('exit-confirm-dialog')).toBeInTheDocument()
  })

  it('confirm dialog contains confirm and cancel buttons', () => {
    render(<OnboardingWizard />)
    fireEvent.click(screen.getByTestId('onboarding-close-btn'))
    expect(screen.getByTestId('exit-confirm-btn')).toBeInTheDocument()
    expect(screen.getByTestId('exit-cancel-btn')).toBeInTheDocument()
  })

  it('cancel keeps wizard open and closes the dialog', () => {
    render(<OnboardingWizard />)
    fireEvent.click(screen.getByTestId('onboarding-close-btn'))
    fireEvent.click(screen.getByTestId('exit-cancel-btn'))
    expect(screen.queryByTestId('exit-confirm-dialog')).not.toBeInTheDocument()
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
  })

  it('confirm sets dismissed flag in localStorage and calls setOnboarded', async () => {
    render(<OnboardingWizard />)
    fireEvent.click(screen.getByTestId('onboarding-close-btn'))
    fireEvent.click(screen.getByTestId('exit-confirm-btn'))
    await waitFor(() => {
      expect(localStorage.getItem('myos.onboarding.dismissed')).toBe('true')
    })
  })
})

describe('OnboardingWizard - provider-select effect (→1703)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
  })

  it('selecting Anthropic shows connected-via status when Claude Code is detected (→1703)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ claude_code: true })
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('anthropic-connected-status')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('provider-Anthropic'))
    expect(screen.getByTestId('anthropic-connected-status')).toBeInTheDocument()
  })

  it('selecting Gemini shows key/connect form even when another provider is detected (→1703)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ claude_code: true })
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('anthropic-connected-status')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
  })
})

describe('OnboardingWizard - provider detection UI bugs (Bug 1, 3, 4)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
    localStorageMock.clear()
    window.history.replaceState({}, '', '/')
  })

  it('does not show paste-key form while /providers/detect is in flight (Bug 1)', async () => {
    let resolveDetect!: (v: unknown) => void
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return new Promise((res) => { resolveDetect = res })
      if (path === '/secrets/key-status') return Promise.resolve({})
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
    expect(screen.queryByTestId('api-key-input')).not.toBeInTheDocument()
    expect(screen.getByTestId('anthropic-detect-loading')).toBeInTheDocument()
    await act(async () => { resolveDetect({}) })
  })

  it('shows inline error when /providers/detect fails (Bug 1)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.reject(new Error('network error'))
      if (path === '/secrets/key-status') return Promise.resolve({})
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('anthropic-detect-error')).toBeInTheDocument())
  })

  it('shows Google already-connected status when key-status returns google_connected (Bug 3)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/secrets/key-status') return Promise.resolve({ google_oauth_available: true, google_connected: true })
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('google-already-connected')).toBeInTheDocument())
  })

  it('shows Jira already-connected badge when /atlassian/status returns connected (Bug 4)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/secrets/key-status') return Promise.resolve({})
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.resolve({ connected: true })
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('atlassian-already-connected')).toBeInTheDocument())
  })

  it('does not crash when /atlassian/status errors (Bug 4)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/secrets/key-status') return Promise.resolve({})
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.reject(new Error('404'))
      return Promise.resolve({})
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
    expect(screen.queryByTestId('atlassian-already-connected')).not.toBeInTheDocument()
  })
})

describe('OnboardingWizard - GitHub step functional (→1693)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      if (path === '/github/status') return Promise.resolve({ connected: false })
      return Promise.resolve({})
    })
  })

  it('GitHub card hides after successful token connect (→1693)', async () => {
    vi.mocked(api.post).mockResolvedValue({ ok: true, user: 'testuser' })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('onboarding-github-card')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('onboarding-github-setup'))
    await waitFor(() => expect(screen.getByTestId('onboarding-github-repo')).toBeInTheDocument())

    fireEvent.change(screen.getByTestId('onboarding-github-repo'), { target: { value: 'acme/website' } })
    fireEvent.change(screen.getByTestId('onboarding-github-token'), { target: { value: 'ghp_mytoken' } })
    fireEvent.click(screen.getByTestId('onboarding-github-connect'))

    await waitFor(() => {
      expect(screen.queryByTestId('onboarding-github-card')).not.toBeInTheDocument()
    })
  })

  it('Atlassian card: Confluence URL field is hidden by default, appears on toggle, and is sent in the connect payload', async () => {
    vi.mocked(api.get).mockImplementation(((path: string) => {
      if (path === '/atlassian/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/defaults') return Promise.resolve({ oauth_available: false })
      return Promise.resolve({})
    }) as typeof api.get)
    vi.mocked(api.post).mockResolvedValue({})

    render(<AtlassianSetupCard darkMode={false} inputCls="" subtextCls="" />)

    await waitFor(() => expect(screen.getByTestId('onboarding-atlassian-setup')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('onboarding-atlassian-setup'))

    await waitFor(() => expect(screen.getByTestId('onboarding-atlassian-site')).toBeInTheDocument())
    expect(screen.queryByTestId('onboarding-atlassian-confluence-site')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('onboarding-atlassian-separate-confluence'))
    expect(screen.getByTestId('onboarding-atlassian-confluence-site')).toBeInTheDocument()

    fireEvent.change(screen.getByTestId('onboarding-atlassian-site'), { target: { value: 'https://jira.acme.net' } })
    fireEvent.change(screen.getByTestId('onboarding-atlassian-email'), { target: { value: 'me@acme.com' } })
    fireEvent.change(screen.getByTestId('onboarding-atlassian-token'), { target: { value: 'tok123' } })
    fireEvent.change(screen.getByTestId('onboarding-atlassian-confluence-site'), { target: { value: 'https://wiki.acme.com' } })
    fireEvent.click(screen.getByTestId('onboarding-atlassian-connect'))

    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledWith('/atlassian/connect', {
      jira_site: 'https://jira.acme.net',
      confluence_site: 'https://wiki.acme.com',
      email: 'me@acme.com',
      api_token: 'tok123',
    }))
  })
})

describe('OnboardingWizard - Atlassian per-product detection (→2611)', () => {
  const disconnectedProduct = { connected: false, site: '', method: null, authenticated_today: false }

  function mockStatus(atlassianStatus: unknown) {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/github/status') return Promise.resolve({ connected: false })
      if (path === '/atlassian/status') return Promise.resolve(atlassianStatus)
      return Promise.resolve({})
    })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    window.history.replaceState({}, '', '/')
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
  })

  it('shows per-product lines when only Jira is connected', async () => {
    mockStatus({
      connected: true,
      products: {
        jira: { connected: true, site: 'company.atlassian.net', method: 'api_key', authenticated_today: true },
        confluence: disconnectedProduct,
      },
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('atlassian-product-jira')).toBeInTheDocument())
    expect(screen.getByTestId('atlassian-product-jira')).toHaveTextContent('Connected: Jira')
    expect(screen.getByTestId('atlassian-product-jira')).toHaveTextContent('company.atlassian.net')
    expect(screen.getByTestId('atlassian-product-confluence')).toHaveTextContent(/not connected yet/i)
    expect(screen.queryByTestId('atlassian-already-connected')).not.toBeInTheDocument()
  })

  it('shows one line per product with its own site when the sites differ', async () => {
    mockStatus({
      connected: true,
      products: {
        jira: { connected: true, site: 'jira.acme.net', method: 'oauth', authenticated_today: false },
        confluence: { connected: true, site: 'wiki.acme.com', method: 'oauth', authenticated_today: false },
      },
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('atlassian-product-jira')).toBeInTheDocument())
    expect(screen.getByTestId('atlassian-product-jira')).toHaveTextContent('Connected: Jira')
    expect(screen.getByTestId('atlassian-product-jira')).toHaveTextContent('jira.acme.net')
    expect(screen.getByTestId('atlassian-product-confluence')).toHaveTextContent('Connected: Confluence')
    expect(screen.getByTestId('atlassian-product-confluence')).toHaveTextContent('wiki.acme.com')
    expect(screen.queryByTestId('atlassian-already-connected')).not.toBeInTheDocument()
  })

  it('keeps the single Atlassian badge when both products share one site', async () => {
    mockStatus({
      connected: true,
      products: {
        jira: { connected: true, site: 'company.atlassian.net', method: 'oauth', authenticated_today: true },
        confluence: { connected: true, site: 'company.atlassian.net', method: 'oauth', authenticated_today: true },
      },
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('atlassian-already-connected')).toBeInTheDocument())
    expect(screen.queryByTestId('atlassian-product-jira')).not.toBeInTheDocument()
    expect(screen.queryByTestId('atlassian-product-confluence')).not.toBeInTheDocument()
  })

  it('falls back to the single badge when the response has no products field', async () => {
    mockStatus({ connected: true })
    render(<OnboardingWizard />)
    await waitFor(() => expect(screen.getByTestId('atlassian-already-connected')).toBeInTheDocument())
    expect(screen.queryByTestId('atlassian-product-jira')).not.toBeInTheDocument()
  })
})
