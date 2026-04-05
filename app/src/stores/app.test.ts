import { describe, it, expect, beforeEach } from 'vitest'
import { useAppStore } from './app'

describe('useAppStore', () => {
  beforeEach(() => {
    // Reset store to initial state before each test
    useAppStore.setState({
      onboarded: false,
      chatOpen: true,
      chatWidth: 380,
      isResizing: false,
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

  it('has correct initial state', () => {
    const state = useAppStore.getState()
    expect(state.chatOpen).toBe(true)
    expect(state.osName).toBe('YourOS')
    expect(state.darkMode).toBe(true)
    expect(state.accentColor).toBe('blue')
    expect(state.features).toHaveLength(7)
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

  it('setChatWidth clamps to min 300', () => {
    useAppStore.getState().setChatWidth(100)
    expect(useAppStore.getState().chatWidth).toBe(300)
  })

  it('setChatWidth clamps to half viewport width', () => {
    const halfViewport = Math.floor(window.innerWidth / 2)
    useAppStore.getState().setChatWidth(99999)
    expect(useAppStore.getState().chatWidth).toBe(halfViewport)
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
    expect(useAppStore.getState().isFeatureEnabled('Transcripts')).toBe(false)
  })

  it('isFeatureEnabled returns true for unknown features', () => {
    expect(useAppStore.getState().isFeatureEnabled('Unknown')).toBe(true)
  })

  it('toggling a feature updates isFeatureEnabled', () => {
    expect(useAppStore.getState().isFeatureEnabled('Transcripts')).toBe(false)
    const updated = useAppStore.getState().features.map((f) =>
      f.label === 'Transcripts' ? { ...f, enabled: true } : f
    )
    useAppStore.getState().setFeatures(updated)
    expect(useAppStore.getState().isFeatureEnabled('Transcripts')).toBe(true)
  })
})
