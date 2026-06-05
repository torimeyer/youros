import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import OnboardingWizard, { AtlassianSetupCard } from './OnboardingWizard'
import { OrgNameStep, AdminEmailStep } from './TeamOnboardingSteps'
import { useAppStore } from '../stores/app'
import { api } from '../lib/api'
import { AGENT_MARKETPLACE } from '../data/agentMarketplace'

// Mock the api module so network calls don't fire
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

const MOCK_ADVENTURES = {
  adventures: [
    {
      id: 'build_website',
      title: 'Build a website',
      tagline: 'From idea to live site, with the right pieces planned out for you.',
      icon: 'language',
      placeholder: 'e.g. A recipe site where I can post my own recipes',
      system_prompt: 'website prompt',
    },
    {
      id: 'plan_project',
      title: 'Plan a project',
      tagline: 'Turn a fuzzy idea into a real plan you can start this week.',
      icon: 'bolt',
      placeholder: 'e.g. Launch a small newsletter',
      system_prompt: 'project prompt',
    },
    {
      id: 'learn_skill',
      title: 'Learn something new',
      tagline: 'A starter path so you stop bookmarking courses and actually begin.',
      icon: 'school',
      placeholder: 'e.g. Learn enough Spanish to hold a conversation',
      system_prompt: 'learn prompt',
    },
    {
      id: 'off_plate',
      title: 'Get something off your plate',
      tagline: 'The thing you have been avoiding. Let us break it into doable steps.',
      icon: 'task_alt',
      placeholder: 'e.g. I need to do my taxes',
      system_prompt: 'off_plate prompt',
    },
  ],
}

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

// Clear localStorage before every test so the restore effect never picks up stale state
beforeEach(() => {
  localStorageMock.clear()
})

// Fork step is hidden (TEAM_MODE_VISIBLE = false); this is a no-op kept for call-site compatibility
function choosePersonalMode() {
  // no-op: wizard starts directly on Welcome step
}

// Helper: click Next n times
function clickNext(n: number) {
  for (let i = 0; i < n; i++) {
    fireEvent.click(screen.getByTestId('next-button'))
  }
}

describe('OnboardingWizard', () => {
  beforeEach(() => {
    // Reset URL so a navigation test that fails mid-way can't leave a path or
    // query param that the OAuth-redirect useEffect misinterprets on the next test.
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
    // Default: api.get returns the adventure templates for the Adventure step
    vi.mocked(api.get).mockResolvedValue(MOCK_ADVENTURES)
    vi.mocked(api.post).mockResolvedValue({})
    vi.mocked(api.patch).mockResolvedValue({})
  })

  it('renders the wizard', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('onboarding-wizard')).toBeInTheDocument()
  })

  it('starts on the Welcome step', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('step-welcome')).toBeInTheDocument()
    expect(screen.getByText('this is your OS.')).toBeInTheDocument()
  })

  it('shows progress dots equal to the number of steps', () => {
    render(<OnboardingWizard />)
    const dots = screen.getByTestId('progress-dots')
    // 9 steps: Welcome, You, Name, Profile, Customize, Theme, Tracking, Connect, Ready
    expect(dots.children).toHaveLength(9)
  })

  it('includes the starter-agents step in the wizard flow after Profile', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4) // Welcome -> You -> Name -> Profile -> Customize
    expect(screen.getByTestId('step-customize')).toBeInTheDocument()
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
    choosePersonalMode()
    clickNext(1)
    expect(screen.getByTestId('step-you')).toBeInTheDocument()
  })

  it('shows Back button on You step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(1)
    expect(screen.getByTestId('back-button')).toBeInTheDocument()
  })

  it('shows Skip button on You step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(1)
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
  })

  it('advances to Name step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(2) // Welcome -> You -> Name
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
  })

  it('goes back to You from Name step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(2)
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('back-button'))
    expect(screen.getByTestId('step-you')).toBeInTheDocument()
  })

  it('allows typing an OS name', async () => {
    const user = userEvent.setup()
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(2)

    const input = screen.getByTestId('os-name-input')
    await user.clear(input)
    await user.type(input, 'MyOS')

    expect(useAppStore.getState().osName).toBe('MyOS')
  })

  it('has exactly one naming step with header "Name your OS"', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(2) // Welcome -> You -> Name
    expect(screen.getByText('Name your OS')).toBeInTheDocument()
    // No Instance step should ever appear
    expect(screen.queryByTestId('step-instance')).not.toBeInTheDocument()
  })

  it('does not show an intent step at any point in the flow', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // Walk all 7 steps and verify step-intent never appears
    for (let i = 0; i < 7; i++) {
      expect(screen.queryByTestId('step-intent')).not.toBeInTheDocument()
      fireEvent.click(screen.getByTestId('next-button'))
    }
    expect(screen.queryByTestId('step-intent')).not.toBeInTheDocument()
  })

  it('advances to Theme step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(5) // Welcome -> You -> Name -> Profile -> Theme
    expect(screen.getByTestId('step-theme')).toBeInTheDocument()
  })

  it('toggles dark mode on Theme step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(5)

    const BLUE = 'rgb(59, 130, 246)'

    // Click Dark: dark button should show selected border via inline style.
    fireEvent.click(screen.getByTestId('theme-dark'))
    expect((screen.getByTestId('theme-dark') as HTMLElement).style.borderColor).toBe(BLUE)
    expect((screen.getByTestId('theme-light') as HTMLElement).style.borderColor).not.toBe(BLUE)

    // Click Light: light button should show selected border via inline style.
    fireEvent.click(screen.getByTestId('theme-light'))
    expect((screen.getByTestId('theme-light') as HTMLElement).style.borderColor).toBe(BLUE)
    expect((screen.getByTestId('theme-dark') as HTMLElement).style.borderColor).not.toBe(BLUE)
  })

  it('clicking Dark flips wizard background and data-theme to dark', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(5)

    // Start by clicking Light so we have a known starting state, then Dark.
    fireEvent.click(screen.getByTestId('theme-light'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect((screen.getByTestId('onboarding-wizard') as HTMLElement).style.backgroundColor).toBe('rgb(248, 250, 252)')

    fireEvent.click(screen.getByTestId('theme-dark'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect((screen.getByTestId('onboarding-wizard') as HTMLElement).style.backgroundColor).toBe('rgb(2, 6, 23)')
  })

  it('dark preview card always renders dark even in light theme', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(5)
    fireEvent.click(screen.getByTestId('theme-light'))
    // Inline style bypasses [data-theme="light"] global overrides on bg-slate-950.
    const preview = screen.getByTestId('theme-dark-preview') as HTMLElement
    expect(preview.style.backgroundColor).toBe('rgb(2, 6, 23)')
  })

  it('advances to Connect step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7) // Welcome -> You -> Name -> Profile -> Theme -> Tracking -> Connect
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
  })

  it('shows Anthropic connect option by default', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    await waitFor(() => expect(screen.getByTestId('connect-anthropic')).toBeInTheDocument())
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
  })

  it('switches provider when Gemini is selected', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    // Anthropic connect button should no longer be visible
    expect(screen.queryByTestId('connect-anthropic')).not.toBeInTheDocument()
  })

  it('shows two separate cards when Gemini is selected', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    // AI Studio card is always visible
    expect(screen.getByTestId('gemini-aistudio-heading')).toBeInTheDocument()
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
    // Google AI Studio link is visible (no toggle needed)
    expect(screen.getByTestId('step-connect')).toHaveTextContent(/Google AI Studio/i)
  })

  it('shows AI Studio key heading when Gemini is selected', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    expect(screen.getByTestId('gemini-aistudio-heading')).toHaveTextContent(/AI Studio key/i)
  })

  it('shows pick-a-project guidance when signed in to Google Cloud without a project', async () => {
    vi.mocked(api.get).mockImplementation(((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ vertex_ai_signed_in: true, vertex_ai: false })
      if (path === '/secrets/key-status') return Promise.resolve({ google_oauth_available: true, google_connected: false })
      if (path === '/github/status') return Promise.resolve({})
      return Promise.resolve(MOCK_ADVENTURES)
    }) as typeof api.get)
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    await waitFor(() =>
      expect(screen.getByTestId('vertex-signed-in-no-project')).toBeInTheDocument()
    )
    expect(screen.getByTestId('vertex-signed-in-no-project')).toHaveTextContent(/Pick a project/i)
  })

  it('Connect step sections show Anthropic, Google, Atlassian, GitHub headings', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    const connectEl = screen.getByTestId('step-connect')
    expect(connectEl).toHaveTextContent(/Anthropic/i)
    expect(connectEl).toHaveTextContent(/Google/i)
    expect(connectEl).toHaveTextContent(/Atlassian/i)
    // GitHub heading lives inside GithubSetupCard, which gates on an async
    // status check (connected !== null && connected !== true). Wait for the
    // mock api.get('/github/status') to resolve before asserting.
    await waitFor(() => {
      expect(connectEl).toHaveTextContent(/GitHub/i)
    })
  })

  it('advances to Ready step with summary', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8) // Welcome -> You -> Name -> Profile -> Theme -> Tracking -> Connect -> Ready

    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    expect(screen.getByTestId('summary-os-name')).toHaveTextContent('yourOS')
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Dark')
    expect(screen.getByTestId('summary-provider')).toHaveTextContent('Anthropic')
  })

  it('theme summary reflects store darkMode even when the user never visits Theme step', () => {
    // Store darkMode=true but user navigates straight through without interacting with Theme step.
    // The pickedDarkRef must be initialized from the store, not hard-coded to false.
    useAppStore.setState({ onboarded: false, osName: 'yourOS', darkMode: true })
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8) // skip through all steps including Theme and Tracking without touching them
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Dark')
  })

  it('theme summary shows Light when store darkMode is false and Theme step not interacted', () => {
    useAppStore.setState({ onboarded: false, osName: 'yourOS', darkMode: false })
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8)
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Light')
  })

  it('does not show Skip button on Ready step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8)

    expect(screen.queryByTestId('skip-button')).not.toBeInTheDocument()
  })

  it('shows "Get started" button on Ready step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8)

    expect(screen.getByTestId('finish-button')).toHaveTextContent('Get started')
  })

  it('sets onboarded to true and persists to localStorage when finished', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8)
    fireEvent.click(screen.getByTestId('finish-button')) // Ready → finish

    expect(useAppStore.getState().onboarded).toBe(true)
    expect(localStorageMock.setItem).toHaveBeenCalledWith('myos-onboarded', 'true')
  })

  it('resets agentsLastViewed to now when personal onboarding finishes (Bug 2: stale Finished badge)', () => {
    const setAgentsLastViewed = vi.fn()
    useAppStore.setState({ setAgentsLastViewed: setAgentsLastViewed as unknown as (v: string) => void })

    const before = Date.now()
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8)
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
    choosePersonalMode()
    clickNext(8)
    fireEvent.click(screen.getByTestId('finish-button')) // Ready → finish

    expect(window.location.pathname).toBe('/')
  })

  it('lands on "/" regardless of which persona was picked', () => {
    window.history.replaceState({}, '', '/agents')
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3) // Welcome -> You -> Name -> Profile

    // Click the first persona card.
    const firstPersona = AGENT_MARKETPLACE[0]
    const personaCard = screen.getByText(firstPersona.category)
    fireEvent.click(personaCard)

    clickNext(5) // Profile -> Theme -> Tracking -> Connect -> Ready
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('finish-button')) // Ready → finish

    expect(useAppStore.getState().onboarded).toBe(true)
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
      choosePersonalMode()
      clickNext(8)
      fireEvent.click(screen.getByTestId('finish-button')) // Ready → finish
    } finally {
      unsubscribe()
    }

    expect(urlWhenFlipped).toBe('/')
  })

  it('skipping steps advances without changing settings', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // Welcome -> You
    clickNext(1)
    // Skip You step
    fireEvent.click(screen.getByTestId('skip-button'))
    // Should be on Name step
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
    expect(useAppStore.getState().osName).toBe('')
  })

  it('clears a stale leftover osName from localStorage or the store on mount', () => {
    useAppStore.getState().setOsName('e2e-test-os')
    expect(useAppStore.getState().osName).toBe('e2e-test-os')
    render(<OnboardingWizard />)
    expect(useAppStore.getState().osName).toBe('')
  })

  it('does not show Back button on Ready step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8)

    expect(screen.queryByTestId('back-button')).not.toBeInTheDocument()
  })

  it('Connect step is skippable', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7) // Get to Connect step
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
  })

  it('wizard flow does not include any adventure step names', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // Walk all 8 steps (Welcome, You, Name, Profile, Customize, Theme, Tracking, Connect)
    for (let i = 0; i < 8; i++) {
      expect(screen.queryByTestId('step-adventure')).not.toBeInTheDocument()
      fireEvent.click(screen.getByTestId('next-button'))
    }
    // Now on Ready step
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    expect(screen.queryByTestId('step-adventure')).not.toBeInTheDocument()
  })

  /* ---- Profile step (persona cards merged in) ---- */

  it('Profile step shows all 7 persona category cards', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3) // Welcome -> You -> Name -> Profile

    for (const cat of AGENT_MARKETPLACE) {
      expect(screen.getByText(cat.category)).toBeInTheDocument()
    }
  })

  it('Profile step shows an Other card', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)
    expect(screen.getByTestId('persona-card-other')).toBeInTheDocument()
    expect(screen.getByText('Other')).toBeInTheDocument()
  })

  it('Profile step is skippable and goes to Customize', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-customize')).toBeInTheDocument()
  })

  it('clicking a persona clears customAgentTemplates so marketplace picks do not appear as custom', () => {
    const setCustomAgentTemplates = vi.fn()
    useAppStore.setState({ setCustomAgentTemplates } as Partial<ReturnType<typeof useAppStore.getState>>)

    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)

    const engineerCat = AGENT_MARKETPLACE.find((c) => c.id === 'engineer')!
    fireEvent.click(screen.getByText(engineerCat.category))

    expect(setCustomAgentTemplates).toHaveBeenCalledTimes(1)
    const templatesArg = setCustomAgentTemplates.mock.calls[0][0]
    expect(templatesArg).toEqual([])
  })

  it('clicking the same persona twice keeps customAgentTemplates cleared', () => {
    const setCustomAgentTemplates = vi.fn()
    useAppStore.setState({ setCustomAgentTemplates } as Partial<ReturnType<typeof useAppStore.getState>>)

    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)

    const pmCat = AGENT_MARKETPLACE.find((c) => c.id === 'pm')!
    fireEvent.click(screen.getByText(pmCat.category))
    fireEvent.click(screen.getByText(pmCat.category))

    expect(setCustomAgentTemplates).toHaveBeenCalledTimes(2)
    expect(setCustomAgentTemplates.mock.calls[0][0]).toEqual([])
    expect(setCustomAgentTemplates.mock.calls[1][0]).toEqual([])
  })

  it('clicking a persona visually marks it as picked', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)

    const writerCat = AGENT_MARKETPLACE.find((c) => c.id === 'writer')!
    const cardText = screen.getByText(writerCat.category)
    const card = cardText.closest('button')!
    expect(card).not.toBeNull()
    expect(card.className).not.toContain('bg-blue-500/20')

    fireEvent.click(card)

    const cardAfter = screen.getByText(writerCat.category).closest('button')!
    expect(cardAfter.className).toContain('bg-blue-500/20')
    expect(cardAfter.querySelector('.material-symbols-outlined')).not.toBeNull()
  })

  it('clicking a persona card selects it visually but does NOT fire install (→1521)', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)

    const pmCat = AGENT_MARKETPLACE.find((c) => c.id === 'pm')!
    fireEvent.click(screen.getByText(pmCat.category))

    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith(
      '/agents/pm-templates/install-persona',
      expect.anything(),
    )
    const card = screen.getByText(pmCat.category).closest('button')!
    expect(card.className).toContain('bg-blue-500/20')
  })

  it('selecting Other card shows free-text input and typing sets profileRole', async () => {
    const user = userEvent.setup()
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)

    expect(screen.queryByTestId('other-role-input')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('persona-card-other'))

    const otherInput = screen.getByTestId('other-role-input')
    expect(otherInput).toBeInTheDocument()

    await user.type(otherInput, 'Founder')
    expect((otherInput as HTMLInputElement).value).toBe('Founder')

    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith(
      '/agents/pm-templates/install-persona',
      expect.anything(),
    )
  })

  it('selecting a persona card after Other hides the free-text input', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)

    fireEvent.click(screen.getByTestId('persona-card-other'))
    expect(screen.getByTestId('other-role-input')).toBeInTheDocument()

    const pmCat = AGENT_MARKETPLACE.find((c) => c.id === 'pm')!
    fireEvent.click(screen.getByText(pmCat.category))
    expect(screen.queryByTestId('other-role-input')).not.toBeInTheDocument()
  })

  it('step count for personal mode is 9', () => {
    render(<OnboardingWizard />)
    const dots = screen.getByTestId('progress-dots')
    expect(dots.children).toHaveLength(9)
  })
})

describe('OnboardingWizard - Enter key advances steps', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock.clear()
    vi.mocked(api.get).mockResolvedValue(MOCK_ADVENTURES)
    vi.mocked(api.post).mockResolvedValue({})
    useAppStore.setState({
      onboarded: false,
      osName: 'yourOS',
      darkMode: false,
    })
  })

  it('Enter on the name input advances from the You step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(1)

    const nameInput = await screen.findByTestId('user-name-input')
    fireEvent.change(nameInput, { target: { value: 'Tori' } })
    fireEvent.keyDown(nameInput, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-name')).toBeInTheDocument()
    })
  })

  it('Enter on the OS name input advances from the Name step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(1) // Welcome -> You
    fireEvent.change(screen.getByTestId('user-name-input'), { target: { value: 'Tori' } })
    clickNext(1) // You -> Name

    await waitFor(() => {
      expect(screen.getByTestId('step-name')).toBeInTheDocument()
    })

    const osInput = screen.getByTestId('os-name-input')
    fireEvent.change(osInput, { target: { value: 'ToriOS' } })
    fireEvent.keyDown(osInput, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.queryByTestId('step-name')).not.toBeInTheDocument()
    })
  })

  it('Enter on the other-role input in Profile step advances to Customize step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3) // Welcome -> You -> Name -> Profile

    fireEvent.click(screen.getByTestId('persona-card-other'))
    const roleInput = screen.getByTestId('other-role-input')
    fireEvent.change(roleInput, { target: { value: 'Founder' } })
    fireEvent.keyDown(roleInput, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-customize')).toBeInTheDocument()
    })
  })

  it('Enter on the Welcome step advances to You step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-you')).toBeInTheDocument()
    })
  })

  it('Enter on the Theme step advances to Tracking step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(5) // Welcome -> You -> Name -> Profile -> Theme

    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
    })
  })

  it('Enter on the Profile step advances to Customize step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3) // Welcome -> You -> Name -> Profile

    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-customize')).toBeInTheDocument()
    })
  })

  it('Enter on the Connect API key input saves and advances to Ready step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7) // Welcome -> ... -> Tracking -> Connect

    const keyInput = await waitFor(() => screen.getByTestId('api-key-input'))
    fireEvent.change(keyInput, { target: { value: 'sk-ant-test123' } })
    fireEvent.keyDown(keyInput, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    })
  })

  it('Enter on the Ready step finishes onboarding', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8) // Welcome -> ... -> Ready

    expect(screen.getByTestId('step-ready')).toBeInTheDocument()

    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' }) // Ready → finish

    await waitFor(() => {
      expect(useAppStore.getState().onboarded).toBe(true)
    })
  })

  it('Enter does not advance when focused on an input with empty required value', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(1) // Welcome -> You

    const nameInput = screen.getByTestId('user-name-input')
    fireEvent.keyDown(nameInput, { key: 'Enter' })
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
  })

  it('WelcomeStep does not have a files-location note (picker moved to onboarding step)', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    expect(screen.queryByTestId('onboarding-files-location-note')).not.toBeInTheDocument()
  })

  it('privacy footer is visible on every step including Theme', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // Step 1: Welcome
    expect(screen.getByTestId('onboarding-privacy-link')).toBeInTheDocument()
    // Step 2: You
    fireEvent.click(screen.getByTestId('next-button'))
    expect(screen.getByTestId('onboarding-privacy-link')).toBeInTheDocument()
    // Step 5: Theme (skip through Name, Profile, Customize)
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-theme')).toBeInTheDocument()
    expect(screen.getByTestId('onboarding-privacy-link')).toBeInTheDocument()
    expect(screen.getByTestId('onboarding-privacy-link')).toHaveAttribute('href', '/privacy')
  })

  it('privacy footer link points to /privacy', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    const link = screen.getByTestId('onboarding-privacy-link')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/privacy')
  })


  it('Enter on the Tracking step (window) advances to Connect', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(6) // Welcome -> ... -> Tracking

    expect(screen.getByTestId('step-tracking')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    })
  })

})


describe('OnboardingWizard — provider auto-detection (→931)', () => {
  function navigateToAfterTheme() {

    fireEvent.click(screen.getByTestId('next-button'))   // Welcome → You
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByTestId('skip-button')) // You/Name/Profile/Customize/Theme/Tracking
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
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
    navigateToAfterTheme()
    await waitFor(() => expect(screen.queryByTestId('step-connect')).toBeInTheDocument())
    expect(screen.queryByTestId('step-ready')).not.toBeInTheDocument()
  })

  it('shows Connect with already-connected badge when Claude Code is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: true, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
    expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Claude Code')
    fireEvent.click(screen.getByTestId('skip-button')) // Connect → Ready
    expect(screen.queryByTestId('step-ready')).toBeInTheDocument()
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Claude Code')
  })

  it('shows Connect with badge "Anthropic" when API key is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: true, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
    expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Anthropic')
  })

  it('shows Connect with badge and api-key-input is hidden when provider detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: true })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
    expect(screen.queryByTestId('api-key-input')).not.toBeInTheDocument()
    expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Gemini')
    fireEvent.click(screen.getByTestId('skip-button')) // Connect → Ready
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Gemini')
  })

  it('falls back to showing Connect when detection fetch fails', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.reject(new Error('network'))
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    await waitFor(() => expect(screen.queryByTestId('step-connect')).toBeInTheDocument())
  })

  it('shows Connect with badge "Vertex AI" when vertex_ai is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false, vertex_ai: true, bedrock: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
    expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('Vertex AI')
    fireEvent.click(screen.getByTestId('skip-button')) // Connect → Ready
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Vertex AI')
  })

  it('shows Connect with badge "AWS Bedrock" when bedrock is detected (→1517)', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false, vertex_ai: false, bedrock: true })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).toBeInTheDocument()
    expect(screen.getByTestId('already-connected-badge')).toHaveTextContent('AWS Bedrock')
    fireEvent.click(screen.getByTestId('skip-button')) // Connect → Ready
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('AWS Bedrock')
  })
})



describe('OnboardingWizard — Google Workspace OAuth button on Connect step', () => {
  function navigateToConnect() {
    fireEvent.click(screen.getByTestId('next-button')) // Welcome → You
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByTestId('skip-button')) // You/Name/Profile/Customize/Theme/Tracking → Connect
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ onboarded: false, osName: '', darkMode: false })
  })

  it('shows connect-google-workspace button when google_oauth_available is true', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    navigateToConnect()
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
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('connect-google-workspace')).not.toBeInTheDocument()
  })

  it('connect-google-workspace button is visible regardless of which AI provider is selected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
    })
    // Switch to Gemini — button should still be present
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    expect(screen.getByTestId('connect-google-workspace')).toBeInTheDocument()
  })

  it('shows "Connect your Google apps (optional)" section when google_oauth_available is true', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toHaveTextContent(/Connect your Google apps \(optional\)/i)
    })
    expect(screen.getByTestId('step-connect')).toHaveTextContent(/This connects Drive, Calendar, and Gmail/i)
  })

  it('does not show "Connect your Google apps" section when google_oauth_available is false', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    })
    expect(screen.queryByText(/Connect your Google apps/i)).not.toBeInTheDocument()
  })

  it('shows "Google account" label when connected email is @gmail.com', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({})
      if (path === '/drive/auth/status') return Promise.resolve({ authenticated: true, email: 'user@gmail.com' })
      return Promise.resolve({ google_oauth_available: true })
    })
    render(<OnboardingWizard />)
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-google-workspace-card')).toBeInTheDocument()
    })
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
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-google-workspace-card')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-google-workspace-card')).toHaveTextContent(/Google Workspace/i)
    })
  })
})


describe('OnboardingWizard — Atlassian and GitHub setup cards', () => {
  function navigateToConnect() {
    fireEvent.click(screen.getByTestId('next-button')) // Welcome → You
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByTestId('skip-button')) // You/Name/Profile/Customize/Theme/Tracking → Connect
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
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
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-atlassian-card')).toBeInTheDocument()
    })
  })

  it('GitHub card renders in Connect step when not connected', async () => {
    render(<OnboardingWizard />)
    navigateToConnect()
    await waitFor(() => {
      expect(screen.getByTestId('onboarding-github-card')).toBeInTheDocument()
    })
  })

  it('cards visible but Next still advances to Ready step', async () => {
    render(<OnboardingWizard />)
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('onboarding-atlassian-card')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('next-button'))
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
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
    navigateToConnect()
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
    navigateToConnect()
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
    navigateToConnect()
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
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
    // Give the status check time to resolve
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
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
    await waitFor(() => expect(screen.queryByTestId('onboarding-github-card')).not.toBeInTheDocument())
  })
})


describe('TeamOnboardingSteps — Enter key on inputs', () => {
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

describe('OnboardingWizard — TrackingStep repo path (→1520)', () => {
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
    vi.mocked(api.get).mockResolvedValue(MOCK_ADVENTURES)
    vi.mocked(api.post).mockResolvedValue({})
  })

  it('tracking-folder-input is a text input, not a file picker', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(6) // Welcome -> You -> Name -> Profile -> Theme -> Tracking

    fireEvent.click(screen.getByTestId('tracking-option-repo'))

    const input = screen.getByTestId('tracking-folder-input') as HTMLInputElement
    expect(input.type).toBe('text')
  })

  it('captures the full absolute path typed by the user and sends it to the backend', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(6) // -> Tracking

    fireEvent.click(screen.getByTestId('tracking-option-repo'))

    const pathInput = screen.getByTestId('tracking-folder-input') as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: '/Users/tori/work/myproject' } })
    expect(pathInput.value).toBe('/Users/tori/work/myproject')

    clickNext(2) // Tracking -> Connect -> Ready
    fireEvent.click(screen.getByTestId('finish-button'))

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/onboarding/enable-myos-hooks',
        expect.objectContaining({ scope: 'repo', path: '/Users/tori/work/myproject' }),
      )
    })
  })
})

describe('OnboardingWizard — step persistence via localStorage (→1518)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
  })

  it('saves current step name to localStorage on every transition', () => {
    render(<OnboardingWizard />)
    fireEvent.click(screen.getByTestId('next-button')) // Welcome → You
    const saved = JSON.parse(localStorage.getItem('myos.onboarding.state') ?? 'null')
    expect(saved).not.toBeNull()
    expect(saved.stepName).toBe('You')
  })

  it('restores step from localStorage on mount', () => {
    localStorage.setItem('myos.onboarding.state', JSON.stringify({ stepName: 'Name', mode: 'personal' }))
    render(<OnboardingWizard />)
    expect(screen.queryByTestId('step-name')).toBeInTheDocument()
    expect(screen.queryByTestId('step-welcome')).not.toBeInTheDocument()
  })

  it('clears localStorage when wizard finishes', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7) // Welcome → … → Connect
    fireEvent.click(screen.getByTestId('skip-button')) // → Ready
    fireEvent.click(screen.getByTestId('finish-button'))
    await waitFor(() => {
      expect(localStorage.getItem('myos.onboarding.state')).toBeNull()
    })
  })
})

describe('OnboardingWizard — exit hatch (→1519)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
  })

  it('renders a close button (×) at the top-right of the wizard', () => {
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
    expect(screen.queryByTestId('step-welcome')).toBeInTheDocument()
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

describe('OnboardingWizard — persona install deferred to Next click (→1521)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect') return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    vi.mocked(api.post).mockResolvedValue({})
  })

  it('clicking a persona card does NOT call install API immediately (→1521)', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3) // Welcome → You → Name → Profile

    await waitFor(() => expect(screen.queryByTestId('step-profile')).toBeInTheDocument())
    const cards = screen.queryAllByTestId(/^persona-card-/)
    expect(cards.length).toBeGreaterThan(0)
    fireEvent.click(cards[0])

    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith(
      '/agents/pm-templates/install-persona',
      expect.anything(),
    )
  })

  it('clicking Next on Profile step fires the install API with selected persona (→1521)', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3) // → Profile

    await waitFor(() => expect(screen.queryByTestId('step-profile')).toBeInTheDocument())
    const cards = screen.queryAllByTestId(/^persona-card-/)
    expect(cards.length).toBeGreaterThan(0)
    fireEvent.click(cards[0])

    fireEvent.click(screen.getByTestId('next-button'))

    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/agents/pm-templates/install-persona',
        expect.anything(),
      )
    })
  })
})

describe('OnboardingWizard - provider-select effect (→1703)', () => {
  function navigateToConnect() {
    fireEvent.click(screen.getByTestId('next-button')) // Welcome → You
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByTestId('skip-button'))
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
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
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())

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
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('provider-Google Gemini'))

    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
  })
})

describe('OnboardingWizard - provider detection UI bugs (Bug 1, 3, 4)', () => {
  function navigateToConnect() {
    fireEvent.click(screen.getByTestId('next-button'))
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByTestId('skip-button'))
    }
  }

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
    navigateToConnect()
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
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
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
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
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
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
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
    navigateToConnect()
    await waitFor(() => expect(screen.getByTestId('step-connect')).toBeInTheDocument())
    expect(screen.queryByTestId('atlassian-already-connected')).not.toBeInTheDocument()
  })
})

describe('OnboardingWizard - GitHub step functional (→1693)', () => {
  function navigateToConnect() {
    fireEvent.click(screen.getByTestId('next-button')) // Welcome → You
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByTestId('skip-button'))
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
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
    navigateToConnect()
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
