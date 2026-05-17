/**
 * MemoryToast.test.tsx (FR-008)
 *
 * Covers:
 * - toast renders when memory_added event arrives (via store)
 * - toast auto-dismisses after 3 seconds
 * - dismiss button clears the toast immediately
 * - empty store shows nothing
 */

import { render, screen, act, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import MemoryToast from './MemoryToast'
import { useMemoryToastStore } from '../stores/memoryToast'

beforeEach(() => {
  // Reset store before each test.
  useMemoryToastStore.setState({ bullet: null })
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('MemoryToast', () => {
  it('renders nothing when no memory event has fired', () => {
    render(<MemoryToast />)
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })

  it('renders the toast when a bullet is set in the store', () => {
    render(<MemoryToast />)
    act(() => {
      useMemoryToastStore.getState().trigger('Use plain language')
    })
    expect(screen.getByTestId('memory-toast')).toBeTruthy()
    expect(screen.getByText('Use plain language')).toBeTruthy()
    expect(screen.getByText("Got it. I'll remember that.")).toBeTruthy()
  })

  it('auto-dismisses after 3 seconds', async () => {
    render(<MemoryToast />)
    act(() => {
      useMemoryToastStore.getState().trigger('No jargon please')
    })
    expect(screen.getByTestId('memory-toast')).toBeTruthy()
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })

  it('dismisses immediately when the close button is clicked', () => {
    render(<MemoryToast />)
    act(() => {
      useMemoryToastStore.getState().trigger('Always use plain language')
    })
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })
})
