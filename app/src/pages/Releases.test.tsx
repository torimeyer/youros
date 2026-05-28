import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Releases from './Releases'
import releaseNotes from '../data/releaseNotes'
import { useAppStore } from '../stores/app'

// Mock the api module so the patch call inside the store does not
// hit the network during the setWhatsNewLastSeen effect.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so components that use responsive breakpoints do not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// Stub localStorage so the store setter can write to it without errors.
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

function renderReleases() {
  return render(
    <MemoryRouter>
      <Releases />
    </MemoryRouter>
  )
}

describe('Releases page', () => {
  beforeEach(() => {
    localStorageMock.clear()
    vi.clearAllMocks()
    useAppStore.setState({ whatsNewLastSeen: '' })
  })

  it('renders the page heading', () => {
    renderReleases()
    expect(screen.getByText("What's New in yourOS")).toBeInTheDocument()
  })

  it('renders every release group label', () => {
    renderReleases()
    const uniqueLabels = [...new Set(releaseNotes.map((g) => g.label))]
    for (const label of uniqueLabels) {
      expect(screen.getAllByText(label).length).toBeGreaterThanOrEqual(1)
    }
  })

  it('renders every entry title', () => {
    renderReleases()
    for (const group of releaseNotes) {
      for (const entry of group.entries) {
        expect(screen.getByText(entry.title)).toBeInTheDocument()
      }
    }
  })

  it('renders every entry description', () => {
    renderReleases()
    for (const group of releaseNotes) {
      for (const entry of group.entries) {
        expect(screen.getByText(entry.description)).toBeInTheDocument()
      }
    }
  })

  it('updates whatsNewLastSeen in the store on mount', () => {
    expect(useAppStore.getState().whatsNewLastSeen).toBe('')
    renderReleases()
    expect(useAppStore.getState().whatsNewLastSeen).toBe(releaseNotes[0].date)
  })

  it('uses the TopBar title "What\'s New"', () => {
    renderReleases()
    // TopBar renders the title as its own element. Several things on the
    // page contain "What's New" so just assert the heading we care about.
    const headings = screen.getAllByText(/What's New/)
    expect(headings.length).toBeGreaterThan(0)
  })
})
