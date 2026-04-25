import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import OnboardingWizard from './OnboardingWizard'
import { useAppStore } from '../stores/app'
import { api } from '../lib/api'
import { AGENT_MARKETPLACE } from '../data/agentMarketplace'

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

// Helper: choose personal mode on the Fork step to get past it
function choosePersonalMode() {
  fireEvent.click(screen.getByTestId('fork-personal'))
}

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
      osName: 'myOS',
      darkMode: true,
      defaultChatModel: 'claude',
      instanceMode: 'personal',
      orgName: '',
      teamAccentColor: '#6366f1',
      displayOsName: () => 'myOS',
      setInstanceMode: vi.fn() as unknown as (mode: 'personal' | 'team') => void,
      setOrgName: vi.fn(),
    })
    vi.mocked(api.get).mockReset()
    vi.mocked(api.post).mockReset()
    // Default: api.get returns the adventure templates for the Adventure step
    vi.mocked(api.get).mockResolvedValue(MOCK_ADVENTURES)
    vi.mocked(api.post).mockResolvedValue({})
  })

  it('renders the wizard', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('onboarding-wizard')).toBeInTheDocument()
  })

  it('starts on the Fork step', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('step-fork')).toBeInTheDocument()
    expect(screen.getByText('Who is this for?')).toBeInTheDocument()
  })

  it('shows progress dots equal to the number of steps', () => {
    render(<OnboardingWizard />)
    const dots = screen.getByTestId('progress-dots')
    // 10 steps: Fork, Welcome, You, Name, Instance, Profile, Intent, Theme, Connect, Ready
    expect(dots.children).toHaveLength(10)
  })

  it('does not show Back button on Fork step', () => {
    render(<OnboardingWizard />)
    expect(screen.queryByTestId('back-button')).not.toBeInTheDocument()
  })

  it('does not show Skip button on Fork step', () => {
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

  it('advances to Instance step after Name', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3) // Welcome -> You -> Name -> Instance
    expect(screen.getByTestId('step-instance')).toBeInTheDocument()
    // Plain-language copy that explains product vs instance to the user.
    expect(screen.getByText(/Name your instance/i)).toBeInTheDocument()
  })

  it('renders the instance name input with the product default', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)
    const input = screen.getByTestId('instance-name-input') as HTMLInputElement
    // The step seeds the field so a user can press Next without typing.
    expect(input.value).toBe('myOS')
  })

  it('captures and persists the typed instance name on finish', async () => {
    const user = userEvent.setup()
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(3)
    const input = screen.getByTestId('instance-name-input')
    await user.clear(input)
    await user.type(input, 'toriOS')
    expect(useAppStore.getState().instanceName).toBe('toriOS')

    // Walk the rest of the wizard so finish() PATCHes the settings.
    clickNext(5) // Instance -> Profile -> Intent -> Theme -> Connect -> Ready
    fireEvent.click(screen.getByTestId('finish-button'))
    await waitFor(() => {
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({ instance_name: 'toriOS' }),
      )
    })
  })

  it('advances to Theme step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(6) // Welcome -> You -> Name -> Instance -> Profile -> Intent -> Theme
    expect(screen.getByTestId('step-theme')).toBeInTheDocument()
  })

  it('toggles dark mode on Theme step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(6)

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
    clickNext(6)

    // Start by clicking Light so we have a known starting state, then Dark.
    fireEvent.click(screen.getByTestId('theme-light'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect((screen.getByTestId('onboarding-wizard') as HTMLElement).style.backgroundColor).toBe('rgb(249, 250, 251)')

    fireEvent.click(screen.getByTestId('theme-dark'))
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect((screen.getByTestId('onboarding-wizard') as HTMLElement).style.backgroundColor).toBe('rgb(2, 6, 23)')
  })

  it('dark preview card always renders dark even in light theme', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(6)
    fireEvent.click(screen.getByTestId('theme-light'))
    // Inline style bypasses [data-theme="light"] global overrides on bg-slate-950.
    const preview = screen.getByTestId('theme-dark-preview') as HTMLElement
    expect(preview.style.backgroundColor).toBe('rgb(2, 6, 23)')
  })

  it('advances to Connect step', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7) // Welcome -> You -> Name -> Instance -> Profile -> Intent -> Theme -> Connect
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
  })

  it('shows Anthropic connect option by default', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    expect(screen.getByTestId('connect-anthropic')).toBeInTheDocument()
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

  it('shows both Gemini key paths (Cloud Console recommended, AI Studio fallback) when Gemini is selected', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    // Helper block is a decision tree: Cloud Console is recommended
    // because users with Drive/Calendar/Gmail already have a project.
    // AI Studio is the chat-only fallback.
    const helper = screen.getByTestId('gemini-key-help')
    expect(helper).toBeInTheDocument()
    expect(helper).toHaveTextContent(/Google AI Studio/i)
    expect(helper).toHaveTextContent(/Google Cloud project/i)
    expect(helper).toHaveTextContent(/Recommended\./i)
    expect(helper).toHaveTextContent(/Chat only\./i)
    // Users must be told to enable the Generative Language API FIRST,
    // otherwise the restriction dropdown in Google Cloud is empty.
    expect(helper).toHaveTextContent(/Enable/)
    expect(helper).toHaveTextContent(/Generative Language API/i)
  })

  it('advances to Ready step with summary', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8) // Welcome -> You -> Name -> Instance -> Profile -> Intent -> Theme -> Connect -> Ready

    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    expect(screen.getByTestId('summary-os-name')).toHaveTextContent('myOS')
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Dark')
    expect(screen.getByTestId('summary-provider')).toHaveTextContent('Anthropic')
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
    fireEvent.click(screen.getByTestId('finish-button'))

    expect(useAppStore.getState().onboarded).toBe(true)
    expect(localStorageMock.setItem).toHaveBeenCalledWith('myos-onboarded', 'true')
  })

  it('navigates to the homepage "/" when finished', () => {
    // The wizard renders outside of BrowserRouter, so it uses
    // window.history.replaceState to land the user on the Dashboard.
    // Start from a non-root URL to prove we always end up at "/".
    window.history.replaceState({}, '', '/settings')
    expect(window.location.pathname).toBe('/settings')

    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8)
    fireEvent.click(screen.getByTestId('finish-button'))

    expect(window.location.pathname).toBe('/')
  })

  it('lands on "/" regardless of which persona was picked', () => {
    // Pick the first marketplace persona on the Profile step, walk to Ready,
    // click finish, and confirm the URL is "/" even though a persona was selected.
    window.history.replaceState({}, '', '/agents')
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4) // Welcome -> You -> Name -> Instance -> Profile

    // Click the first persona card. Category names are rendered as buttons.
    const firstPersona = AGENT_MARKETPLACE[0]
    const personaCard = screen.getByText(firstPersona.category)
    fireEvent.click(personaCard)

    clickNext(4) // Profile -> Intent -> Theme -> Connect -> Ready
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('finish-button'))

    expect(useAppStore.getState().onboarded).toBe(true)
    expect(window.location.pathname).toBe('/')
  })

  it('flips onboarded=true AFTER rewriting the URL so App.tsx mounts the router at "/"', () => {
    // Regression: if setOnboarded(true) ran before the URL was rewritten,
    // App.tsx would unmount the wizard and BrowserRouter would mount at
    // whatever stale path the browser was on. We assert order by observing
    // the URL at the moment onboarded flips to true.
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
      fireEvent.click(screen.getByTestId('finish-button'))
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
    // OS name should be empty. The wizard resets osName to '' on mount so
    // stale values from localStorage or prior e2e test runs (e.g.
    // "e2e-browser-os" from scripts/e2e_browser.sh, "e2e-test-os" from
    // scripts/e2e_smoke.sh) do not pre-fill the "Name your OS" field.
    expect(useAppStore.getState().osName).toBe('')
  })

  it('clears a stale leftover osName from localStorage or the store on mount', () => {
    // Simulate a prior e2e test run or an aborted onboarding attempt that
    // wrote a value into the store. The wizard must not show that value
    // on step 4 (Name your OS).
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
    // Skip button should be available
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    // After Connect we land on Ready
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
  })

  it('wizard flow does not include any adventure step names', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // Walk all 8 steps after Fork (Welcome, You, Name, Instance, Profile,
    // Intent, Theme, Connect) and verify none are "step-adventure"
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
    clickNext(4) // Welcome -> You -> Name -> Profile

    for (const cat of AGENT_MARKETPLACE) {
      expect(screen.getByText(cat.category)).toBeInTheDocument()
    }
  })

  it('Profile step shows an Other card', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4)
    expect(screen.getByTestId('persona-card-other')).toBeInTheDocument()
    expect(screen.getByText('Other')).toBeInTheDocument()
  })

  it('Profile step is skippable and goes to Intent', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4)
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-intent')).toBeInTheDocument()
  })

  it('clicking a persona clears customAgentTemplates so marketplace picks do not appear as custom', () => {
    // Regression: older builds stuffed marketplace templates into
    // customAgentTemplates here, which made every persona pick show up
    // with a "custom" badge AND duplicate the cards the Templates tab
    // was already rendering from the backend. Marketplace installs now
    // live only in the backend store, so this handler clears the list.
    const setCustomAgentTemplates = vi.fn()
    useAppStore.setState({ setCustomAgentTemplates } as Partial<ReturnType<typeof useAppStore.getState>>)

    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4)

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
    clickNext(4)

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
    clickNext(4)

    const writerCat = AGENT_MARKETPLACE.find((c) => c.id === 'writer')!
    const cardText = screen.getByText(writerCat.category)
    // Find the closest button ancestor
    const card = cardText.closest('button')!
    expect(card).not.toBeNull()
    // Before click, no blue background
    expect(card.className).not.toContain('bg-blue-500/20')

    fireEvent.click(card)

    // After click, the card should have the picked styling
    const cardAfter = screen.getByText(writerCat.category).closest('button')!
    expect(cardAfter.className).toContain('bg-blue-500/20')
    // And the check_circle icon should appear inside the card
    expect(cardAfter.querySelector('.material-symbols-outlined')).not.toBeNull()
  })

  it('clicking a persona card sets both selectedPersonaId (via API call) and profileRole', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4)

    const pmCat = AGENT_MARKETPLACE.find((c) => c.id === 'pm')!
    fireEvent.click(screen.getByText(pmCat.category))

    // The install-persona API should be called with the persona id
    expect(vi.mocked(api.post)).toHaveBeenCalledWith(
      '/agents/pm-templates/install-persona',
      { persona_id: pmCat.id },
    )
    // The card should be visually selected (profileRole set to category label)
    const card = screen.getByText(pmCat.category).closest('button')!
    expect(card.className).toContain('bg-blue-500/20')
  })

  it('selecting Other card shows free-text input and typing sets profileRole', async () => {
    const user = userEvent.setup()
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4)

    // Before clicking Other, the free-text input should not be visible
    expect(screen.queryByTestId('other-role-input')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('persona-card-other'))

    // Free-text input should now appear
    const otherInput = screen.getByTestId('other-role-input')
    expect(otherInput).toBeInTheDocument()

    // Typing in it should update the role (no API call, just profileRole state)
    await user.type(otherInput, 'Founder')
    expect((otherInput as HTMLInputElement).value).toBe('Founder')

    // No install-persona API call for "Other"
    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith(
      '/agents/pm-templates/install-persona',
      expect.anything(),
    )
  })

  it('selecting a persona card after Other hides the free-text input', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4)

    fireEvent.click(screen.getByTestId('persona-card-other'))
    expect(screen.getByTestId('other-role-input')).toBeInTheDocument()

    // Now pick a real persona — Other input should disappear
    const pmCat = AGENT_MARKETPLACE.find((c) => c.id === 'pm')!
    fireEvent.click(screen.getByText(pmCat.category))
    expect(screen.queryByTestId('other-role-input')).not.toBeInTheDocument()
  })

  it('step count for personal mode is 10 (Intent step added)', () => {
    render(<OnboardingWizard />)
    const dots = screen.getByTestId('progress-dots')
    expect(dots.children).toHaveLength(10)
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
      osName: 'myOS',
      darkMode: false,
    })
  })

  it('Enter on the name input advances from the You step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // After Fork choice we are at Welcome (stepIndex 1). One click reaches You (stepIndex 2).
    clickNext(1)

    const nameInput = await screen.findByTestId('user-name-input')
    fireEvent.change(nameInput, { target: { value: 'Tori' } })
    fireEvent.keyDown(nameInput, { key: 'Enter' })

    // After Enter the OS naming step should appear
    await waitFor(() => {
      expect(screen.getByTestId('step-name')).toBeInTheDocument()
    })
  })

  it('Enter on the OS name input advances from the Name step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // Fork(0) -> Welcome(1) -> You(2) -> Name(3)
    // After choosePersonalMode we are at Welcome(1). Click Next twice to get to Name(3).
    clickNext(1) // Welcome -> You
    fireEvent.change(screen.getByTestId('user-name-input'), { target: { value: 'Tori' } })
    clickNext(1) // You -> Name

    await waitFor(() => {
      expect(screen.getByTestId('step-name')).toBeInTheDocument()
    })

    const osInput = screen.getByTestId('os-name-input')
    fireEvent.change(osInput, { target: { value: 'ToriOS' } })
    fireEvent.keyDown(osInput, { key: 'Enter' })

    // Profile step should follow
    await waitFor(() => {
      expect(screen.queryByTestId('step-name')).not.toBeInTheDocument()
    })
  })

  it('Enter on the other-role input in Profile step advances to Intent step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4) // Welcome -> You -> Name -> Instance -> Profile

    // Click "Other" to reveal the free-text input
    fireEvent.click(screen.getByTestId('persona-card-other'))
    const roleInput = screen.getByTestId('other-role-input')
    fireEvent.change(roleInput, { target: { value: 'Founder' } })
    fireEvent.keyDown(roleInput, { key: 'Enter' })

    // Intent step should follow Profile
    await waitFor(() => {
      expect(screen.getByTestId('step-intent')).toBeInTheDocument()
    })
  })

  it('Enter on the Welcome step advances to You step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    // Now on Welcome step. Fire Enter on the wizard root (not an input).
    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-you')).toBeInTheDocument()
    })
  })

  it('Enter on the Theme step advances to Connect step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(6) // Welcome -> You -> Name -> Instance -> Profile -> Intent -> Theme

    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    })
  })

  it('Enter on the Profile step advances to Intent step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(4) // Welcome -> You -> Name -> Instance -> Profile

    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('step-intent')).toBeInTheDocument()
    })
  })

  it('Enter on the Connect API key input saves and advances to Ready step', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(7) // Welcome -> ... -> Connect

    const keyInput = screen.getByTestId('api-key-input')
    fireEvent.change(keyInput, { target: { value: 'sk-ant-test123' } })
    fireEvent.keyDown(keyInput, { key: 'Enter' })

    await waitFor(() => {
      // Adventure removed from onboarding; Connect now leads directly to Ready
      expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    })
  })

  it('Enter on the Ready step calls finish and sets onboarded', async () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(8) // Welcome -> ... -> Ready

    expect(screen.getByTestId('step-ready')).toBeInTheDocument()

    const wizard = screen.getByTestId('onboarding-wizard')
    fireEvent.keyDown(wizard, { key: 'Enter' })

    await waitFor(() => {
      expect(useAppStore.getState().onboarded).toBe(true)
    })
  })

  it('Enter does not advance when focused on an input with empty required value', () => {
    // You step: empty userName should still advance (no guard) - but the field is optional.
    // This test verifies that pressing Enter on the You step input while empty still calls next
    // (the field is optional - Enter always advances).
    render(<OnboardingWizard />)
    choosePersonalMode()
    clickNext(1) // Welcome -> You

    const nameInput = screen.getByTestId('user-name-input')
    // Input is empty - fire Enter anyway
    fireEvent.keyDown(nameInput, { key: 'Enter' })
    // Should advance to Name step (no validation guard on You step)
    expect(screen.getByTestId('step-name')).toBeInTheDocument()
  })
  it('WelcomeStep mentions the files location', () => {
    render(<OnboardingWizard />)
    choosePersonalMode()
    const note = screen.getByTestId('onboarding-files-location-note')
    expect(note).toHaveTextContent('~/.myos/files')
    expect(note).toHaveTextContent(/change this in Settings/i)
  })

})


describe('OnboardingWizard — provider auto-detection (→931)', () => {
  function navigateToAfterTheme() {
    fireEvent.click(screen.getByTestId('fork-personal'))
    fireEvent.click(screen.getByTestId('next-button'))   // Welcome → You
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByTestId('skip-button')) // You/Name/Instance/Profile/Intent/Theme
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    // Restore safe defaults after clearAllMocks so unrelated api calls don't throw.
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

  it('skips Connect step when Claude Code is detected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: true, anthropic_key: false, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).not.toBeInTheDocument()
    expect(screen.queryByTestId('step-ready')).toBeInTheDocument()
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Claude Code')
  })

  it('skips Connect and shows "Connected via Anthropic" when API key is detected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: true, gemini_key: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).not.toBeInTheDocument()
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Anthropic')
  })

  it('api-key-input is never shown when a provider is detected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: true })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('api-key-input')).not.toBeInTheDocument()
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

  it('skips Connect and shows "Connected via Vertex AI" when vertex_ai is detected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false, vertex_ai: true, bedrock: false })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).not.toBeInTheDocument()
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('Vertex AI')
  })

  it('skips Connect and shows "Connected via AWS Bedrock" when bedrock is detected', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/providers/detect')
        return Promise.resolve({ claude_code: false, anthropic_key: false, gemini_key: false, vertex_ai: false, bedrock: true })
      return Promise.resolve({ google_oauth_available: false })
    })
    render(<OnboardingWizard />)
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith('/providers/detect'))
    navigateToAfterTheme()
    expect(screen.queryByTestId('step-connect')).not.toBeInTheDocument()
    expect(screen.getByTestId('summary-connected-via')).toHaveTextContent('AWS Bedrock')
  })
})


describe('OnboardingWizard — Intent step (S1)', () => {
  // Navigate to the Intent step: Fork → Welcome → You → Name → Instance → Profile → Intent
  function navigateToIntent() {
    fireEvent.click(screen.getByTestId('fork-personal'))
    // Welcome
    fireEvent.click(screen.getByTestId('next-button'))
    // You → Name → Instance → Profile (4 skips)
    for (let i = 0; i < 4; i++) {
      fireEvent.click(screen.getByTestId('skip-button'))
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue({})
    vi.mocked(api.post).mockResolvedValue({})
    vi.mocked(api.patch).mockResolvedValue({})
  })

  it('renders the Intent step after Profile', () => {
    render(<OnboardingWizard />)
    navigateToIntent()
    expect(screen.getByTestId('step-intent')).toBeInTheDocument()
    expect(screen.getByText('What do you want to use AI for?')).toBeInTheDocument()
  })

  it('clicking Coding card POSTs intent=coding and renders starter pack checklist', async () => {
    const mockPack = {
      starter_pack: [
        { kind: 'agent', id: 'builtin-builder', name: 'Builder', description: 'Build things', default_selected: true },
        { kind: 'agent', id: 'builtin-diagnose', name: 'Diagnose', description: 'Fix bugs', default_selected: true },
        { kind: 'agent', id: 'builtin-eng-write-tests', name: 'Write Tests', description: 'Add test coverage', default_selected: false },
      ],
    }
    vi.mocked(api.post).mockResolvedValue(mockPack)

    render(<OnboardingWizard />)
    navigateToIntent()

    fireEvent.click(screen.getByTestId('intent-card-coding'))

    expect(vi.mocked(api.post)).toHaveBeenCalledWith('/onboarding/intent', { intent: 'coding' })

    await waitFor(() => expect(screen.getByTestId('pack-item-builtin-builder')).toBeInTheDocument())

    // Default-selected items should have their checkbox checked
    const builderCheckbox = screen.getByTestId('pack-checkbox-builtin-builder') as HTMLInputElement
    expect(builderCheckbox.checked).toBe(true)
    const testsCheckbox = screen.getByTestId('pack-checkbox-builtin-eng-write-tests') as HTMLInputElement
    expect(testsCheckbox.checked).toBe(false)
  })

  it('clicking Continue on Intent step advances the wizard to Theme', async () => {
    vi.mocked(api.post).mockResolvedValue({ starter_pack: [] })

    render(<OnboardingWizard />)
    navigateToIntent()

    fireEvent.click(screen.getByTestId('intent-card-writing'))
    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledWith('/onboarding/intent', { intent: 'writing' }))

    fireEvent.click(screen.getByTestId('next-button'))
    expect(screen.getByTestId('step-theme')).toBeInTheDocument()
  })
})
