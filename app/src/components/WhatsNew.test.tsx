import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import WhatsNew, { getUnseenCount } from './WhatsNew'
import releaseNotes from '../data/releaseNotes'

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

describe('WhatsNew', () => {
  beforeEach(() => {
    localStorageMock.clear()
    vi.clearAllMocks()
  })

  it('renders the sparkle button', () => {
    const { getByTestId } = render(<WhatsNew />)
    expect(getByTestId('whats-new-button')).toBeTruthy()
  })

  it('shows a badge when there are unseen updates', () => {
    // No last-seen date set, so all groups are unseen
    const { getByTestId } = render(<WhatsNew />)
    expect(getByTestId('whats-new-badge')).toBeTruthy()
  })

  it('hides the badge after the user opens the modal', () => {
    const { getByTestId, queryByTestId } = render(<WhatsNew />)
    expect(getByTestId('whats-new-badge')).toBeTruthy()
    fireEvent.click(getByTestId('whats-new-button'))
    expect(queryByTestId('whats-new-badge')).toBeNull()
  })

  it('opens the modal when the button is clicked', () => {
    const { getByTestId, queryByTestId } = render(<WhatsNew />)
    expect(queryByTestId('whats-new-modal')).toBeNull()
    fireEvent.click(getByTestId('whats-new-button'))
    expect(getByTestId('whats-new-modal')).toBeTruthy()
  })

  it('closes the modal when the close button is clicked', () => {
    const { getByTestId, queryByTestId } = render(<WhatsNew />)
    fireEvent.click(getByTestId('whats-new-button'))
    expect(getByTestId('whats-new-modal')).toBeTruthy()
    fireEvent.click(getByTestId('whats-new-close'))
    expect(queryByTestId('whats-new-modal')).toBeNull()
  })

  it('displays release notes grouped by date', () => {
    const { getByTestId, getByText } = render(<WhatsNew />)
    fireEvent.click(getByTestId('whats-new-button'))
    for (const group of releaseNotes) {
      expect(getByText(group.label)).toBeTruthy()
    }
  })

  it('displays entry titles and descriptions', () => {
    const { getByTestId, getByText } = render(<WhatsNew />)
    fireEvent.click(getByTestId('whats-new-button'))
    const first = releaseNotes[0].entries[0]
    expect(getByText(first.title)).toBeTruthy()
    expect(getByText(first.description)).toBeTruthy()
  })

  it('stores last-seen date in localStorage when opened', () => {
    const { getByTestId } = render(<WhatsNew />)
    fireEvent.click(getByTestId('whats-new-button'))
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      'myos-whats-new-last-seen',
      releaseNotes[0].date,
    )
  })

  it('does not show a badge when the user has already seen all updates', () => {
    localStorageMock.setItem('myos-whats-new-last-seen', releaseNotes[0].date)
    const { queryByTestId } = render(<WhatsNew />)
    expect(queryByTestId('whats-new-badge')).toBeNull()
  })

  it('closes the modal when clicking the backdrop', () => {
    const { getByTestId, queryByTestId } = render(<WhatsNew />)
    fireEvent.click(getByTestId('whats-new-button'))
    expect(getByTestId('whats-new-modal')).toBeTruthy()
    // Click the backdrop (the fixed overlay behind the modal)
    const backdrop = getByTestId('whats-new-modal').previousElementSibling
    fireEvent.click(backdrop!)
    expect(queryByTestId('whats-new-modal')).toBeNull()
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
