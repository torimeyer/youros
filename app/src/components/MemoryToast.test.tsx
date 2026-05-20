/**
 * MemoryToast.test.tsx (FR-006, FR-014)
 *
 * Covers:
 * - "Got it" toast renders on memory_added (via store)
 * - "Got it" toast auto-dismisses after 3 seconds
 * - dismiss button clears immediately
 * - empty store shows nothing
 * - "Forgot" toast renders on memory_removed
 * - Undo button visible on forgot toast; calls undo-remove endpoint
 * - Forgot toast auto-dismisses after 3 seconds
 * - Ambiguous picker toast renders on memory_remove_ambiguous
 */

import { render, screen, act, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import MemoryToast from './MemoryToast'
import { useMemoryToastStore } from '../stores/memoryToast'

beforeEach(() => {
  useMemoryToastStore.setState({ bullet: null, forgot: null, forgotCandidates: null })
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }))
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('MemoryToast — remembered', () => {
  it('renders nothing when store is empty', () => {
    render(<MemoryToast />)
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })

  it('renders the "Got it" toast when bullet is set', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().trigger('Use plain language') })
    expect(screen.getByTestId('memory-toast')).toBeTruthy()
    expect(screen.getByText('Use plain language')).toBeTruthy()
    expect(screen.getByText("Got it. I'll remember that.")).toBeTruthy()
  })

  it('auto-dismisses after 3 seconds', async () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().trigger('No jargon please') })
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })

  it('dismisses on close button click', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().trigger('Always use plain language') })
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })
})

describe('MemoryToast — forgot', () => {
  it('renders the "Forgot" toast when forgot is set', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerForgot('Use plain language') })
    expect(screen.getByTestId('memory-forgot-toast')).toBeTruthy()
    expect(screen.getByText('Forgot.')).toBeTruthy()
    expect(screen.getByText('Use plain language')).toBeTruthy()
  })

  it('shows Undo button on forgot toast', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerForgot('Use plain language') })
    expect(screen.getByTestId('memory-undo-button')).toBeTruthy()
  })

  it('Undo button calls undo-remove endpoint', async () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerForgot('Use plain language') })
    await act(async () => {
      fireEvent.click(screen.getByTestId('memory-undo-button'))
    })
    expect(vi.mocked(fetch)).toHaveBeenCalledWith('/api/memory/user/undo-remove', { method: 'POST' })
  })

  it('auto-dismisses after 3 seconds', async () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerForgot('No jargon') })
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(screen.queryByTestId('memory-forgot-toast')).toBeNull()
  })
})

describe('MemoryToast — ambiguous picker', () => {
  it('renders ambiguous picker when candidates set', () => {
    render(<MemoryToast />)
    act(() => {
      useMemoryToastStore.getState().triggerForgotAmbiguous(['Use plain language.', 'Always use plain speech.'])
    })
    expect(screen.getByTestId('memory-ambiguous-toast')).toBeTruthy()
    expect(screen.getByText('Which one to forget?')).toBeTruthy()
    expect(screen.getByText('Use plain language.')).toBeTruthy()
    expect(screen.getByText('Always use plain speech.')).toBeTruthy()
  })
})
