import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useAppStore, TEAM_MODE_VISIBLE, DEFAULT_DASHBOARD_WIDGETS, DASHBOARD_WIDGET_LABELS, HYDRATION_SETTINGS_TIMEOUT_MS, HYDRATION_ENTERPRISE_TIMEOUT_MS } from './app'
import { api } from '../lib/api'

// Mock the api module so no real network calls fire and we can assert
// on what the store wrote to the server.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

describe('useAppStore', () => {
  beforeEach(() => {
    // Reset mocks so each test starts with a clean call history
    vi.mocked(api.get).mockReset().mockResolvedValue({})
    vi.mocked(api.patch).mockReset().mockResolvedValue({})
    // Reset store to initial state before each test
    useAppStore.setState({
      onboarded: false,
      chatOpen: true,
      chatWidth: 380,
      isResizing: false,
      osName: 'myOS',
      darkMode: true,
      accentColor: 'blue',
      defaultChatModel: 'claude',
      useOstkTerms: false,
      tourComplete: false,
      whatsNewLastSeen: '',
      customAgentTemplates: [],
      features: [
        { label: 'Chat', enabled: true },
        { label: 'Break Room', enabled: true },
        { label: 'Tasks', enabled: true },
        { label: 'Agents', enabled: true },
        { label: 'Activity', enabled: true },
        { label: 'Projects', enabled: true },
        { label: 'Drive', enabled: true },
        { label: 'Calendar', enabled: true },
        { label: 'Gmail', enabled: true },
        { label: 'Slack', enabled: true },
        { label: 'GitHub', enabled: true },
        { label: 'Specs', enabled: true },
        { label: 'Automations', enabled: true },
        { label: 'Cost Tracking', enabled: true },
      ],
      navOrder: [],
    })
    // Clear localStorage between tests so cached values do not leak
    try { localStorage.clear() } catch { /* noop */ }
  })

  it('has correct initial state', () => {
    const state = useAppStore.getState()
    expect(state.chatOpen).toBe(true)
    expect(state.osName).toBe('myOS')
    expect(state.darkMode).toBe(true)
    expect(state.accentColor).toBe('blue')
    expect(state.features).toHaveLength(14)
  })

  it('toggleChat flips chatOpen from true to false', () => {
    expect(useAppStore.getState().chatOpen).toBe(true)
    useAppStore.getState().toggleChat()
    expect(useAppStore.getState().chatOpen).toBe(false)
  })

  it('toggleChat flips chatOpen from false to true', () => {
    useAppStore.setState({ chatOpen: false })
    useAppStore.getState().toggleChat()
    expect(useAppStore.getState().chatOpen).toBe(true)
  })

  it('toggleChat called twice returns to original state', () => {
    const original = useAppStore.getState().chatOpen
    useAppStore.getState().toggleChat()
    useAppStore.getState().toggleChat()
    expect(useAppStore.getState().chatOpen).toBe(original)
  })

  it('setOsName updates osName', () => {
    useAppStore.getState().setOsName('MyCustomOS')
    expect(useAppStore.getState().osName).toBe('MyCustomOS')
  })

  it('setOsName can set to empty string', () => {
    useAppStore.getState().setOsName('')
    expect(useAppStore.getState().osName).toBe('')
  })

  it('toggleDarkMode flips darkMode from true to false', () => {
    expect(useAppStore.getState().darkMode).toBe(true)
    useAppStore.getState().toggleDarkMode()
    expect(useAppStore.getState().darkMode).toBe(false)
  })

  it('toggleDarkMode flips darkMode from false to true', () => {
    useAppStore.setState({ darkMode: false })
    useAppStore.getState().toggleDarkMode()
    expect(useAppStore.getState().darkMode).toBe(true)
  })

  it('setChatWidth updates chatWidth', () => {
    useAppStore.getState().setChatWidth(500)
    expect(useAppStore.getState().chatWidth).toBe(500)
  })

  it('setChatWidth clamps to min 280', () => {
    useAppStore.getState().setChatWidth(100)
    expect(useAppStore.getState().chatWidth).toBe(280)
  })

  it('setChatWidth clamps to viewport minus sidebar reserve', () => {
    // Max width leaves room for the sidebar plus a sliver of main content
    // so nothing ever gets hidden behind the chat.
    const ceiling = window.innerWidth - 320
    useAppStore.getState().setChatWidth(99999)
    expect(useAppStore.getState().chatWidth).toBe(ceiling)
  })

  it('setChatWidth writes the new width to localStorage', () => {
    useAppStore.getState().setChatWidth(612)
    expect(localStorage.getItem('myos-chat-width')).toBe('612')
  })

  it('setChatWidth writes the clamped value to localStorage when over max', () => {
    useAppStore.getState().setChatWidth(99999)
    const ceiling = window.innerWidth - 320
    expect(localStorage.getItem('myos-chat-width')).toBe(String(ceiling))
  })

  it('setIsResizing updates isResizing', () => {
    expect(useAppStore.getState().isResizing).toBe(false)
    useAppStore.getState().setIsResizing(true)
    expect(useAppStore.getState().isResizing).toBe(true)
    useAppStore.getState().setIsResizing(false)
    expect(useAppStore.getState().isResizing).toBe(false)
  })

  it('state changes are independent of each other', () => {
    useAppStore.getState().toggleChat()
    useAppStore.getState().setOsName('NewOS')
    useAppStore.getState().toggleDarkMode()

    const state = useAppStore.getState()
    expect(state.chatOpen).toBe(false)
    expect(state.osName).toBe('NewOS')
    expect(state.darkMode).toBe(false)
  })

  it('setAccentColor updates accentColor', () => {
    useAppStore.getState().setAccentColor('pink')
    expect(useAppStore.getState().accentColor).toBe('pink')
  })

  it('setAccentColor can be set to any valid color', () => {
    const colors = ['blue', 'pink', 'purple', 'cyan', 'orange'] as const
    for (const color of colors) {
      useAppStore.getState().setAccentColor(color)
      expect(useAppStore.getState().accentColor).toBe(color)
    }
  })

  it('setFeatures updates features array', () => {
    const newFeatures = [
      { label: 'Chat', enabled: false },
      { label: 'Tasks', enabled: false },
    ]
    useAppStore.getState().setFeatures(newFeatures)
    expect(useAppStore.getState().features).toEqual(newFeatures)
  })

  it('isFeatureEnabled returns true for enabled features', () => {
    expect(useAppStore.getState().isFeatureEnabled('Chat')).toBe(true)
    expect(useAppStore.getState().isFeatureEnabled('Tasks')).toBe(true)
  })

  it('isFeatureEnabled returns false for disabled features', () => {
    // Explicitly disable a feature, then confirm the getter reflects it.
    const updated = useAppStore.getState().features.map((f) =>
      f.label === 'Automations' ? { ...f, enabled: false } : f
    )
    useAppStore.getState().setFeatures(updated)
    expect(useAppStore.getState().isFeatureEnabled('Automations')).toBe(false)
  })

  it('isFeatureEnabled returns true for unknown features', () => {
    expect(useAppStore.getState().isFeatureEnabled('Unknown')).toBe(true)
  })

  describe('navOrder persistence (nav drag order decoupled from feature toggles)', () => {
    it('store has navOrder array and setNavOrder action', () => {
      const state = useAppStore.getState()
      expect(Array.isArray(state.navOrder)).toBe(true)
      expect(typeof state.setNavOrder).toBe('function')
    })

    it('setNavOrder updates the navOrder array', () => {
      useAppStore.getState().setNavOrder(['iMessage', 'Gmail', 'Slack'])
      expect(useAppStore.getState().navOrder).toEqual(['iMessage', 'Gmail', 'Slack'])
    })

    it('setNavOrder persists to localStorage so order survives a reload', () => {
      useAppStore.getState().setNavOrder(['iMessage', 'ostk', 'Jira'])
      const stored = localStorage.getItem('myos-nav-order')
      expect(stored).not.toBeNull()
      const parsed = JSON.parse(stored!)
      expect(parsed).toContain('iMessage')
      expect(parsed).toContain('ostk')
      expect(parsed).toContain('Jira')
    })

    it('setNavOrder for unregistered featureLabel (iMessage) does not add phantom entries to features', () => {
      const featuresBefore = useAppStore.getState().features.map((f) => f.label)
      useAppStore.getState().setNavOrder(['iMessage', 'ostk', 'Jira', 'Confluence'])
      const featuresAfter = useAppStore.getState().features.map((f) => f.label)
      expect(featuresAfter).toEqual(featuresBefore)
    })

    it('setNavOrder for a registry-backed item (Gmail) still persists correctly', () => {
      useAppStore.getState().setNavOrder(['Drive', 'Gmail', 'Calendar'])
      expect(useAppStore.getState().navOrder).toContain('Gmail')
      const stored = JSON.parse(localStorage.getItem('myos-nav-order')!)
      expect(stored).toContain('Gmail')
    })
  })

  it('toggling a feature updates isFeatureEnabled', () => {
    // All features default to true now, so first confirm it is on.
    expect(useAppStore.getState().isFeatureEnabled('Automations')).toBe(true)
    const disabled = useAppStore.getState().features.map((f) =>
      f.label === 'Automations' ? { ...f, enabled: false } : f
    )
    useAppStore.getState().setFeatures(disabled)
    expect(useAppStore.getState().isFeatureEnabled('Automations')).toBe(false)
    const reEnabled = useAppStore.getState().features.map((f) =>
      f.label === 'Automations' ? { ...f, enabled: true } : f
    )
    useAppStore.getState().setFeatures(reEnabled)
    expect(useAppStore.getState().isFeatureEnabled('Automations')).toBe(true)
  })

  describe('defaultChatModel', () => {
    it('defaults to claude', () => {
      expect(useAppStore.getState().defaultChatModel).toBe('claude')
    })

    it('setDefaultChatModel updates the value to gemini', () => {
      useAppStore.getState().setDefaultChatModel('gemini')
      expect(useAppStore.getState().defaultChatModel).toBe('gemini')
    })

    it('setDefaultChatModel updates the value back to claude', () => {
      useAppStore.getState().setDefaultChatModel('gemini')
      useAppStore.getState().setDefaultChatModel('claude')
      expect(useAppStore.getState().defaultChatModel).toBe('claude')
    })

    it('changing defaultChatModel does not affect other state', () => {
      useAppStore.getState().setDefaultChatModel('gemini')
      const state = useAppStore.getState()
      expect(state.defaultChatModel).toBe('gemini')
      expect(state.osName).toBe('myOS')
      expect(state.darkMode).toBe(true)
      expect(state.chatOpen).toBe(true)
    })
  })

  // ---- Server side persistence ----
  // These tests lock in the fix where the server is the source of truth
  // for user state, not localStorage. Every setter should write to the
  // server and hydrateFromServer should pull the latest values on boot.
  describe('server persistence', () => {
    it('setOnboarded writes to the server via api.patch', () => {
      useAppStore.getState().setOnboarded(true)
      expect(useAppStore.getState().onboarded).toBe(true)
      expect(api.patch).toHaveBeenCalledWith('/settings', { onboarded: true })
    })

    it('toggleDarkMode writes to the server', () => {
      // darkMode starts at true, toggling makes it false
      useAppStore.getState().toggleDarkMode()
      expect(useAppStore.getState().darkMode).toBe(false)
      expect(api.patch).toHaveBeenCalledWith('/settings', { dark_mode: false })
    })

    it('setOsName writes to the server', () => {
      useAppStore.getState().setOsName('TorisOS')
      expect(useAppStore.getState().osName).toBe('TorisOS')
      expect(api.patch).toHaveBeenCalledWith('/settings', { os_name: 'TorisOS' })
    })

    it('setAccentColor writes to the server', () => {
      useAppStore.getState().setAccentColor('pink')
      expect(api.patch).toHaveBeenCalledWith('/settings', { accent_color: 'pink' })
    })

    it('setDefaultChatModel writes to the server with the @ prefix', () => {
      useAppStore.getState().setDefaultChatModel('gemini')
      expect(api.patch).toHaveBeenCalledWith('/settings', { default_model: '@gemini' })
    })

    it('setUseOstkTerms writes to the server', () => {
      useAppStore.getState().setUseOstkTerms(true)
      expect(api.patch).toHaveBeenCalledWith('/settings', { use_ostk_terms: true })
    })

    it('setTourComplete writes to the server', () => {
      useAppStore.getState().setTourComplete(true)
      expect(useAppStore.getState().tourComplete).toBe(true)
      expect(api.patch).toHaveBeenCalledWith('/settings', { tour_complete: true })
    })

    it('setWhatsNewLastSeen writes to the server', () => {
      useAppStore.getState().setWhatsNewLastSeen('2026-04-06')
      expect(useAppStore.getState().whatsNewLastSeen).toBe('2026-04-06')
      expect(api.patch).toHaveBeenCalledWith('/settings', { whats_new_last_seen: '2026-04-06' })
    })

    it('setCustomAgentTemplates writes to the server', () => {
      const templates = [
        { name: 'Helper', description: 'Does things', icon: 'star', model: 'sonnet', budget: 2 },
      ]
      useAppStore.getState().setCustomAgentTemplates(templates)
      expect(useAppStore.getState().customAgentTemplates).toEqual(templates)
      expect(api.patch).toHaveBeenCalledWith('/settings', { custom_agent_templates: templates })
    })
  })

  describe('hydrateFromServer', () => {
    it('overwrites store values with server values when present', async () => {
      vi.mocked(api.get).mockResolvedValueOnce({
        onboarded: true,
        os_name: 'ServerOS',
        dark_mode: false,
        accent_color: 'purple',
        default_model: '@gemini',
        use_ostk_terms: true,
      })

      await useAppStore.getState().hydrateFromServer()

      const state = useAppStore.getState()
      expect(state.onboarded).toBe(true)
      expect(state.osName).toBe('ServerOS')
      expect(state.darkMode).toBe(false)
      expect(state.accentColor).toBe('purple')
      expect(state.defaultChatModel).toBe('gemini')
      expect(state.useOstkTerms).toBe(true)
    })

    it('translates server @claude model format to plain claude', async () => {
      vi.mocked(api.get).mockResolvedValueOnce({ default_model: '@claude' })
      await useAppStore.getState().hydrateFromServer()
      expect(useAppStore.getState().defaultChatModel).toBe('claude')
    })

    it('keeps localStorage value when server is silent and writes it back', async () => {
      // Simulate a user who onboarded before settings were server backed.
      // localStorage says onboarded, server file is silent on the field.
      useAppStore.setState({ onboarded: true })
      vi.mocked(api.get).mockResolvedValueOnce({
        os_name: 'ServerOS',
        dark_mode: true,
      })

      await useAppStore.getState().hydrateFromServer()

      const state = useAppStore.getState()
      // Local onboarded value is preserved
      expect(state.onboarded).toBe(true)
      // Server side os_name still wins
      expect(state.osName).toBe('ServerOS')
      // And we wrote the migration back to the server
      expect(api.patch).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({ onboarded: true }),
      )
    })

    it('does not overwrite local values when the server fetch fails', async () => {
      useAppStore.setState({ onboarded: true, osName: 'LocalOS' })
      vi.mocked(api.get).mockRejectedValueOnce(new Error('network down'))

      await useAppStore.getState().hydrateFromServer()

      const state = useAppStore.getState()
      expect(state.onboarded).toBe(true)
      expect(state.osName).toBe('LocalOS')
    })

    it('treats null server values as silent and falls back to local', async () => {
      useAppStore.setState({ onboarded: true })
      vi.mocked(api.get).mockResolvedValueOnce({
        onboarded: null,
        os_name: 'ServerOS',
      })

      await useAppStore.getState().hydrateFromServer()

      expect(useAppStore.getState().onboarded).toBe(true)
      expect(useAppStore.getState().osName).toBe('ServerOS')
    })

    it('hydrates tour_complete, whats_new_last_seen, and custom_agent_templates', async () => {
      const templates = [
        { name: 'Reviewer', description: 'Reviews code', icon: 'code', model: 'sonnet', budget: 1.5 },
      ]
      vi.mocked(api.get).mockResolvedValueOnce({
        tour_complete: true,
        whats_new_last_seen: '2026-04-05',
        custom_agent_templates: templates,
      })

      await useAppStore.getState().hydrateFromServer()

      const state = useAppStore.getState()
      expect(state.tourComplete).toBe(true)
      expect(state.whatsNewLastSeen).toBe('2026-04-05')
      expect(state.customAgentTemplates).toEqual(templates)
    })

    it('backfills local custom templates when server is silent', async () => {
      const templates = [
        { name: 'Drafter', description: 'Drafts emails', icon: 'mail', model: 'sonnet', budget: 2 },
      ]
      useAppStore.setState({ customAgentTemplates: templates })
      vi.mocked(api.get).mockResolvedValueOnce({})

      await useAppStore.getState().hydrateFromServer()

      // Local templates are preserved, and we wrote them back to the server.
      expect(useAppStore.getState().customAgentTemplates).toEqual(templates)
      expect(api.patch).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({ custom_agent_templates: templates }),
      )
    })
  })
})

// Bug 1: team mode must never leak through when TEAM_MODE_VISIBLE = false

describe('useAppStore — team mode gate (TEAM_MODE_VISIBLE=false)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset().mockResolvedValue({})
    vi.mocked(api.patch).mockReset().mockResolvedValue({})
    useAppStore.setState({ instanceMode: 'personal', orgName: '' })
    try { localStorage.clear() } catch { /* noop */ }
  })

  it('TEAM_MODE_VISIBLE is false in this build', () => {
    expect(TEAM_MODE_VISIBLE).toBe(false)
  })

  it('setInstanceMode("team") is a no-op when TEAM_MODE_VISIBLE=false', () => {
    useAppStore.setState({ instanceMode: 'personal' })
    useAppStore.getState().setInstanceMode('team')
    expect(useAppStore.getState().instanceMode).toBe('personal')
  })

  it('setInstanceMode("team") does not patch the server when TEAM_MODE_VISIBLE=false', () => {
    useAppStore.setState({ instanceMode: 'personal' })
    useAppStore.getState().setInstanceMode('team')
    expect(api.patch).not.toHaveBeenCalled()
  })

  it('setInstanceMode("personal") still works when TEAM_MODE_VISIBLE=false', () => {
    useAppStore.setState({ instanceMode: 'personal' })
    useAppStore.getState().setInstanceMode('personal')
    expect(useAppStore.getState().instanceMode).toBe('personal')
  })

  it('hydrateFromServer forces instanceMode to personal when server has team and flag is off', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ instance_mode: 'team', onboarded: true })
    await useAppStore.getState().hydrateFromServer()
    expect(useAppStore.getState().instanceMode).toBe('personal')
  })

  it('displayOsName returns osName even when orgName is set and server had team mode', async () => {
    useAppStore.setState({ osName: 'myOS', orgName: 'Meyer', instanceMode: 'personal' })
    // Simulate stale server returning team mode — hydration should neutralise it
    vi.mocked(api.get).mockResolvedValueOnce({ instance_mode: 'team', onboarded: true })
    await useAppStore.getState().hydrateFromServer()
    expect(useAppStore.getState().displayOsName()).toBe('myOS')
  })

  it('enterprise auto-migration does not switch to team when TEAM_MODE_VISIBLE=false', async () => {
    useAppStore.setState({ instanceMode: 'personal' })
    vi.mocked(api.get)
      .mockResolvedValueOnce({ onboarded: true })  // /settings
      .mockResolvedValueOnce({ authenticated: true, enterprise: true, email: 'a@b.com', role: 'member' })  // /enterprise/me
      .mockResolvedValueOnce({ org: { name: 'Acme' } })  // /enterprise
    await useAppStore.getState().hydrateFromServer()
    expect(useAppStore.getState().instanceMode).toBe('personal')
  })

  it('hydrated is set to true before enterprise fetch resolves', async () => {
    let resolveEnterprise!: (v: unknown) => void
    const enterprisePromise = new Promise((res) => { resolveEnterprise = res })

    vi.mocked(api.get)
      .mockResolvedValueOnce({ onboarded: true })  // /settings — resolves immediately
      .mockResolvedValueOnce({ authenticated: true, enterprise: true, email: 'a@b.com', role: 'member' })  // /enterprise/me — resolves immediately
      .mockReturnValueOnce(enterprisePromise as Promise<unknown>)  // /enterprise — deferred

    const hydrationPromise = useAppStore.getState().hydrateFromServer()

    // Flush microtasks up to the await on /enterprise/me
    await new Promise((res) => setTimeout(res, 0))

    // hydrated must be true even though /enterprise is still pending
    expect(useAppStore.getState().hydrated).toBe(true)

    // Unblock enterprise and let hydration finish cleanly
    resolveEnterprise({ org: { name: 'Acme' } })
    await hydrationPromise
  })

  it('settings timeout causes fast fallback with hydrated=true within timeout window', async () => {
    const settingsError = Object.assign(new Error('timeout'), { name: 'AbortError' })
    vi.mocked(api.get).mockRejectedValueOnce(settingsError)

    await useAppStore.getState().hydrateFromServer()

    // Must resolve quickly (fallback path), not hang for REQUEST_TIMEOUT_MS
    expect(useAppStore.getState().hydrated).toBe(true)
  })

  it('enterprise/me timeout leaves hydrated=true and enterpriseUser=null', async () => {
    const enterpriseError = Object.assign(new Error('timeout'), { name: 'AbortError' })
    vi.mocked(api.get)
      .mockResolvedValueOnce({ onboarded: true })  // /settings
      .mockRejectedValueOnce(enterpriseError)       // /enterprise/me

    await useAppStore.getState().hydrateFromServer()

    expect(useAppStore.getState().hydrated).toBe(true)
    expect(useAppStore.getState().enterpriseUser).toBeNull()
  })

  it('settings fetch is called with HYDRATION_SETTINGS_TIMEOUT_MS option', async () => {
    vi.mocked(api.get).mockResolvedValue({})
    await useAppStore.getState().hydrateFromServer()

    expect(vi.mocked(api.get)).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({ timeoutMs: HYDRATION_SETTINGS_TIMEOUT_MS }),
    )
  })

  it('enterprise/me fetch is called with HYDRATION_ENTERPRISE_TIMEOUT_MS option', async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ onboarded: true })  // /settings
      .mockResolvedValueOnce({ authenticated: true, enterprise: true, email: 'a@b.com', role: 'member' })  // /enterprise/me
      .mockResolvedValueOnce({ org: {} })  // /enterprise

    await useAppStore.getState().hydrateFromServer()

    expect(vi.mocked(api.get)).toHaveBeenCalledWith(
      '/enterprise/me',
      expect.objectContaining({ timeoutMs: HYDRATION_ENTERPRISE_TIMEOUT_MS }),
    )
  })
})

describe('widget list invariants', () => {
  it('DEFAULT_DASHBOARD_WIDGETS and DASHBOARD_WIDGET_LABELS have exactly the same keys', () => {
    const labelKeys = Object.keys(DASHBOARD_WIDGET_LABELS).sort()
    const defaultKeys = [...DEFAULT_DASHBOARD_WIDGETS].sort()
    expect(defaultKeys).toEqual(labelKeys)
  })

  it('ostk_files in saved server state is stripped during hydration', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      dashboard_widgets: ['briefing', 'ostk_files', 'todays_focus'],
    })
    await useAppStore.getState().hydrateFromServer()
    const widgets = useAppStore.getState().dashboardWidgets
    expect(widgets).not.toContain('ostk_files')
    expect(widgets).toContain('briefing')
    expect(widgets).toContain('todays_focus')
  })
})
