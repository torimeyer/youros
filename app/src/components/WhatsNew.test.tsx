import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import WhatsNew, { getUnseenCount } from './WhatsNew'
import releaseNotes from '../data/releaseNotes'
import { useAppStore } from '../stores/app'

// Mock the api module so the patch call inside the store does not
// hit the network. The store still calls localStorage to keep the
// fast first paint cache up to date.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// Mock react-router-dom's useNavigate so we can assert the button
// navigates to the /releases page.
const navigateMock = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

// Stub localStorage
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

function renderWhatsNew() {
  return render(
    <MemoryRouter>
      <WhatsNew />
    </MemoryRouter>
  )
}

describe('WhatsNew', () => {
  beforeEach(() => {
    localStorageMock.clear()
    vi.clearAllMocks()
    // Reset the persisted last-seen date in the store between tests so
    // a previous test does not leak its state.
    useAppStore.setState({ whatsNewLastSeen: '' })
  })

  it('renders the sparkle button', () => {
    const { getByTestId } = renderWhatsNew()
    expect(getByTestId('whats-new-button')).toBeTruthy()
  })

  it('shows a badge when there are unseen updates', () => {
    // No last-seen date set, so all groups are unseen
    const { getByTestId } = renderWhatsNew()
    expect(getByTestId('whats-new-badge')).toBeTruthy()
  })

  it('navigates to /releases when the button is clicked', () => {
    const { getByTestId } = renderWhatsNew()
    fireEvent.click(getByTestId('whats-new-button'))
    expect(navigateMock).toHaveBeenCalledWith('/releases')
  })

  it('does not render a drawer or modal anymore', () => {
    const { queryByTestId } = renderWhatsNew()
    expect(queryByTestId('whats-new-modal')).toBeNull()
    expect(queryByTestId('whats-new-portal')).toBeNull()
  })

  it('hides the badge after the store marks the latest release as seen', () => {
    const { getByTestId, queryByTestId } = renderWhatsNew()
    expect(getByTestId('whats-new-badge')).toBeTruthy()
    // Simulate the Releases page mounting and updating the store. Wrap
    // in act because this triggers a React state update via the store
    // subscription inside the component.
    act(() => {
      useAppStore.setState({ whatsNewLastSeen: releaseNotes[0].date })
    })
    expect(queryByTestId('whats-new-badge')).toBeNull()
  })

  it('does not show a badge when the user has already seen all updates', () => {
    // Server backed: prime the store value so the component reads it.
    useAppStore.setState({ whatsNewLastSeen: releaseNotes[0].date })
    const { queryByTestId } = renderWhatsNew()
    expect(queryByTestId('whats-new-badge')).toBeNull()
  })
})

describe('getUnseenCount', () => {
  beforeEach(() => {
    localStorageMock.clear()
    vi.clearAllMocks()
  })

  it('returns 1 when no last-seen date is set and notes exist', () => {
    expect(getUnseenCount()).toBe(1)
  })

  it('returns 0 when the user has seen the latest date', () => {
    localStorageMock.setItem('myos-whats-new-last-seen', releaseNotes[0].date)
    expect(getUnseenCount()).toBe(0)
  })

  it('returns the number of groups newer than last-seen date', () => {
    // Set last-seen to a date before all entries
    localStorageMock.setItem('myos-whats-new-last-seen', '2020-01-01')
    expect(getUnseenCount()).toBe(releaseNotes.length)
  })
})
