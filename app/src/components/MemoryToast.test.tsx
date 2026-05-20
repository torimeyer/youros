/**
 * MemoryToast.test.tsx
 *
 * Covers:
 * - 'added' mode: renders on memory_added, auto-dismisses, dismiss button works
 * - 'forgot' mode: renders "Forgot." + Undo button; Undo calls restore API; auto-dismisses
 * - 'ambiguous' mode: renders picker with candidates
 * - 'failed' mode: renders "I don't have a preference about that."
 */

import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import MemoryToast from './MemoryToast'
import { useMemoryToastStore } from '../stores/memoryToast'

beforeEach(() => {
  useMemoryToastStore.setState({ mode: null, bullet: null, candidates: [], section: 'Preferences' })
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }))
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// ── added mode ────────────────────────────────────────────────────────────────

describe('MemoryToast — added mode', () => {
  it('renders nothing when store is empty', () => {
    render(<MemoryToast />)
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })

  it('renders toast when trigger() is called', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().trigger('Use plain language') })
    expect(screen.getByTestId('memory-toast')).toBeTruthy()
    expect(screen.getByText('Use plain language')).toBeTruthy()
    expect(screen.getByText("Got it. I'll remember that.")).toBeTruthy()
  })

  it('auto-dismisses after 3 seconds', async () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().trigger('No jargon') })
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })

  it('dismiss button clears immediately', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().trigger('Always use plain language') })
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(screen.queryByTestId('memory-toast')).toBeNull()
  })
})

// ── forgot mode ───────────────────────────────────────────────────────────────

describe('MemoryToast — forgot mode', () => {
  it('renders Forgot toast with Undo button', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerForgot('I prefer plain language', 'Preferences') })
    expect(screen.getByTestId('memory-toast-forgot')).toBeTruthy()
    expect(screen.getByText('I prefer plain language')).toBeTruthy()
    expect(screen.getByRole('button', { name: /undo/i })).toBeTruthy()
  })

  it('Undo calls restore API and clears toast', async () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerForgot('I prefer plain language', 'Preferences') })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /undo/i }))
    })
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      '/api/memory/restore',
      expect.objectContaining({ method: 'POST' }),
    )
    await waitFor(() => expect(screen.queryByTestId('memory-toast-forgot')).toBeNull())
  })

  it('auto-dismisses after 3 seconds without Undo', async () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerForgot('I prefer plain language', 'Preferences') })
    expect(screen.getByTestId('memory-toast-forgot')).toBeTruthy()
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(screen.queryByTestId('memory-toast-forgot')).toBeNull()
  })
})

// ── ambiguous mode ────────────────────────────────────────────────────────────

describe('MemoryToast — ambiguous mode', () => {
  it('renders picker with all candidates', () => {
    render(<MemoryToast />)
    act(() => {
      useMemoryToastStore.getState().triggerAmbiguous([
        'I prefer plain language',
        'Always use plain style',
      ])
    })
    expect(screen.getByTestId('memory-toast-ambiguous')).toBeTruthy()
    expect(screen.getByText('I prefer plain language')).toBeTruthy()
    expect(screen.getByText('Always use plain style')).toBeTruthy()
    expect(screen.getByText(/which one should i forget/i)).toBeTruthy()
  })
})

// ── failed mode ───────────────────────────────────────────────────────────────

describe('MemoryToast — failed mode', () => {
  it('renders failure message', () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerFailed() })
    expect(screen.getByTestId('memory-toast-failed')).toBeTruthy()
    expect(screen.getByText(/don't have a preference about that/i)).toBeTruthy()
  })

  it('auto-dismisses after 3 seconds', async () => {
    render(<MemoryToast />)
    act(() => { useMemoryToastStore.getState().triggerFailed() })
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(screen.queryByTestId('memory-toast-failed')).toBeNull()
  })
})
