import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
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

  it('starts on the Welcome step', () => {
    render(<OnboardingWizard />)
    expect(screen.getByTestId('step-welcome')).toBeInTheDocument()
    expect(screen.getByText('Welcome!')).toBeInTheDocument()
  })

  it('shows progress dots equal to the number of steps', () => {
    render(<OnboardingWizard />)
    const dots = screen.getByTestId('progress-dots')
    // 8 steps: Welcome, You, Name, Persona, Theme, Connect, Adventure, Ready
    expect(dots.children).toHaveLength(8)
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
    clickNext(4) // Welcome -> You -> Name -> Persona -> Theme
    expect(screen.getByTestId('step-theme')).toBeInTheDocument()
  })

  it('toggles dark mode on Theme step', () => {
    render(<OnboardingWizard />)
    clickNext(4)

    // Currently dark. Click Light.
    fireEvent.click(screen.getByTestId('theme-light'))
    expect(useAppStore.getState().darkMode).toBe(false)

    // Click Dark.
    fireEvent.click(screen.getByTestId('theme-dark'))
    expect(useAppStore.getState().darkMode).toBe(true)
  })

  it('advances to Connect step', () => {
    render(<OnboardingWizard />)
    clickNext(5) // Welcome -> You -> Name -> Persona -> Theme -> Connect
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
  })

  it('shows Anthropic connect option by default', () => {
    render(<OnboardingWizard />)
    clickNext(5)
    expect(screen.getByTestId('connect-anthropic')).toBeInTheDocument()
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
  })

  it('switches provider when Gemini is selected', () => {
    render(<OnboardingWizard />)
    clickNext(5)
    fireEvent.click(screen.getByTestId('provider-Google Gemini'))
    // Anthropic connect button should no longer be visible
    expect(screen.queryByTestId('connect-anthropic')).not.toBeInTheDocument()
  })

  it('advances to Adventure step', async () => {
    render(<OnboardingWizard />)
    clickNext(6) // Welcome -> You -> Name -> Persona -> Theme -> Connect -> Adventure
    expect(screen.getByTestId('step-adventure')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('adventure-phase-pick')).toBeInTheDocument()
    })
  })

  it('advances to Ready step with summary', () => {
    render(<OnboardingWizard />)
    clickNext(7) // Welcome -> You -> Name -> Persona -> Theme -> Connect -> Adventure -> Ready

    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    expect(screen.getByTestId('summary-os-name')).toHaveTextContent('myOS')
    expect(screen.getByTestId('summary-theme')).toHaveTextContent('Dark')
    expect(screen.getByTestId('summary-provider')).toHaveTextContent('Anthropic')
  })

  it('does not show Skip button on Ready step', () => {
    render(<OnboardingWizard />)
    clickNext(7)

    expect(screen.queryByTestId('skip-button')).not.toBeInTheDocument()
  })

  it('shows "Get started" button on Ready step', () => {
    render(<OnboardingWizard />)
    clickNext(7)

    expect(screen.getByTestId('finish-button')).toHaveTextContent('Get started')
  })

  it('sets onboarded to true and persists to localStorage when finished', () => {
    render(<OnboardingWizard />)
    clickNext(7)
    fireEvent.click(screen.getByTestId('finish-button'))

    expect(useAppStore.getState().onboarded).toBe(true)
    expect(localStorageMock.setItem).toHaveBeenCalledWith('myos-onboarded', 'true')
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
    expect(useAppStore.getState().osName).toBe('myOS')
  })

  it('does not show Back button on Ready step', () => {
    render(<OnboardingWizard />)
    clickNext(7)

    expect(screen.queryByTestId('back-button')).not.toBeInTheDocument()
  })

  it('Connect step is skippable', async () => {
    render(<OnboardingWizard />)
    clickNext(5) // Get to Connect step
    expect(screen.getByTestId('step-connect')).toBeInTheDocument()
    // Skip button should be available
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-adventure')).toBeInTheDocument()
  })

  it('Adventure step is skippable', () => {
    render(<OnboardingWizard />)
    clickNext(6) // Get to Adventure step
    expect(screen.getByTestId('step-adventure')).toBeInTheDocument()
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-ready')).toBeInTheDocument()
  })

  it('Adventure picker fetches templates from /adventures/templates', async () => {
    render(<OnboardingWizard />)
    clickNext(6)
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/adventures/templates')
    })
  })

  it('Adventure picker shows all four cards', async () => {
    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    expect(screen.getByTestId('adventure-card-plan_project')).toBeInTheDocument()
    expect(screen.getByTestId('adventure-card-learn_skill')).toBeInTheDocument()
    expect(screen.getByTestId('adventure-card-off_plate')).toBeInTheDocument()
  })

  it('clicking an adventure card moves to the describe phase', async () => {
    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    expect(screen.getByTestId('adventure-phase-describe')).toBeInTheDocument()
    expect(screen.getByTestId('adventure-description-input')).toBeInTheDocument()
  })

  it('describe phase shows the placeholder for the chosen adventure', async () => {
    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    const input = screen.getByTestId('adventure-description-input')
    expect(input).toHaveAttribute('placeholder', 'e.g. A recipe site where I can post my own recipes')
  })

  it('back-to-pick button returns to the picker', async () => {
    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))
    expect(screen.getByTestId('adventure-phase-describe')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('adventure-back-to-pick'))
    expect(screen.getByTestId('adventure-phase-pick')).toBeInTheDocument()
  })

  it('adventure submit is disabled when description is empty', async () => {
    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    expect(screen.getByTestId('adventure-submit')).toBeDisabled()
  })

  it('adventure submit is enabled when description is entered', async () => {
    const user = userEvent.setup()
    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    await user.type(screen.getByTestId('adventure-description-input'), 'A recipe site')
    expect(screen.getByTestId('adventure-submit')).not.toBeDisabled()
  })

  it('adventure shows loading state during API call', async () => {
    const user = userEvent.setup()
    let resolvePost: (v: unknown) => void
    vi.mocked(api.post).mockImplementationOnce(
      () => new Promise((resolve) => { resolvePost = resolve })
    )

    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    await user.type(screen.getByTestId('adventure-description-input'), 'Recipe site')
    await user.click(screen.getByTestId('adventure-submit'))

    expect(screen.getByTestId('adventure-submit')).toHaveTextContent('Building your plan...')

    await act(async () => {
      resolvePost!({
        adventure_id: 'build_website',
        goal: { title: 'Recipe site', description: 'A site for recipes' },
        tasks: [{ title: 'Pick a domain', priority: 'P1' }],
      })
    })
  })

  it('adventure shows results after successful API call', async () => {
    const user = userEvent.setup()
    const mockResult = {
      adventure_id: 'build_website',
      goal: { title: 'Recipe site for friends', description: 'A site where you post recipes.' },
      tasks: [
        { title: 'Pick a name and grab the domain', priority: 'P1' },
        { title: 'Sign up for a free Vercel account', priority: 'P1' },
      ],
    }
    vi.mocked(api.post).mockResolvedValueOnce(mockResult)

    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    await user.type(screen.getByTestId('adventure-description-input'), 'Recipe site')
    await user.click(screen.getByTestId('adventure-submit'))

    await waitFor(() => {
      expect(screen.getByTestId('adventure-phase-show')).toBeInTheDocument()
    })

    expect(screen.getByTestId('adventure-goal-title')).toHaveTextContent('Recipe site for friends')
    expect(screen.getByTestId('adventure-tasks')).toBeInTheDocument()

    const tasks = screen.getAllByTestId('adventure-task')
    expect(tasks).toHaveLength(2)
  })

  it('adventure advances on API error', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post).mockRejectedValueOnce(new Error('fail'))

    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    await user.type(screen.getByTestId('adventure-description-input'), 'Recipe site')
    await user.click(screen.getByTestId('adventure-submit'))

    await waitFor(() => {
      expect(screen.getByTestId('step-ready')).toBeInTheDocument()
    })
  })

  it('adventure sends correct payload to /adventures/start', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post).mockResolvedValueOnce({
      adventure_id: 'build_website',
      goal: { title: 'Test', description: 'Test' },
      tasks: [],
    })

    render(<OnboardingWizard />)
    clickNext(6)

    await waitFor(() => {
      expect(screen.getByTestId('adventure-card-build_website')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('adventure-card-build_website'))

    await user.type(screen.getByTestId('adventure-description-input'), 'A recipe site')
    await user.click(screen.getByTestId('adventure-submit'))

    expect(api.post).toHaveBeenCalledWith('/adventures/start', {
      adventure_id: 'build_website',
      description: 'A recipe site',
    })
  })

  /* ---- Persona step ---- */

  it('advances to Persona step', () => {
    render(<OnboardingWizard />)
    clickNext(3) // Welcome -> You -> Name -> Persona
    expect(screen.getByText('How will you use myOS?')).toBeInTheDocument()
  })

  it('Persona step shows all 7 category names', () => {
    render(<OnboardingWizard />)
    clickNext(3)

    for (const cat of AGENT_MARKETPLACE) {
      expect(screen.getByText(cat.category)).toBeInTheDocument()
    }
  })

  it('Persona step is skippable', () => {
    render(<OnboardingWizard />)
    clickNext(3)
    expect(screen.getByTestId('skip-button')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('skip-button'))
    expect(screen.getByTestId('step-theme')).toBeInTheDocument()
  })

  it('clicking a persona populates customAgentTemplates with that category templates', () => {
    const setCustomAgentTemplates = vi.fn()
    useAppStore.setState({ setCustomAgentTemplates } as Partial<ReturnType<typeof useAppStore.getState>>)

    render(<OnboardingWizard />)
    clickNext(3)

    const engineerCat = AGENT_MARKETPLACE.find((c) => c.id === 'engineer')!
    fireEvent.click(screen.getByText(engineerCat.category))

    expect(setCustomAgentTemplates).toHaveBeenCalledTimes(1)
    const templatesArg = setCustomAgentTemplates.mock.calls[0][0]
    expect(templatesArg).toHaveLength(engineerCat.templates.length)
    // Each call template should match name, description, icon, model, budget
    for (let i = 0; i < engineerCat.templates.length; i++) {
      const expected = engineerCat.templates[i]
      const actual = templatesArg[i]
      expect(actual.name).toBe(expected.name)
      expect(actual.description).toBe(expected.description)
      expect(actual.icon).toBe(expected.icon)
      expect(actual.model).toBe(expected.model)
      expect(actual.budget).toBe(expected.budget)
    }
  })

  it('clicking the same persona twice does not accumulate duplicates', () => {
    const setCustomAgentTemplates = vi.fn()
    useAppStore.setState({ setCustomAgentTemplates } as Partial<ReturnType<typeof useAppStore.getState>>)

    render(<OnboardingWizard />)
    clickNext(3)

    const pmCat = AGENT_MARKETPLACE.find((c) => c.id === 'pm')!
    fireEvent.click(screen.getByText(pmCat.category))
    fireEvent.click(screen.getByText(pmCat.category))

    // Each click sends the full category templates list, not appended
    expect(setCustomAgentTemplates).toHaveBeenCalledTimes(2)
    const lastCall = setCustomAgentTemplates.mock.calls[1][0]
    expect(lastCall).toHaveLength(pmCat.templates.length)
    // Final state matches the picked category
    const lastNames = lastCall.map((t: { name: string }) => t.name).sort()
    const expectedNames = pmCat.templates.map((t) => t.name).sort()
    expect(lastNames).toEqual(expectedNames)
  })

  it('clicking a persona visually marks it as picked', () => {
    render(<OnboardingWizard />)
    clickNext(3)

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
})
