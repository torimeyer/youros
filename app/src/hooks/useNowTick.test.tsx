// →2982 one shared 1-second clock for every per-agent elapsed readout,
// instead of one setInterval per mounted card.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { useNowTick } from './useNowTick'

function Probe({ id }: { id: string }) {
  const now = useNowTick()
  return <span data-testid={id}>{String(now)}</span>
}

describe('useNowTick shared clock (→2982)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('any number of subscribers share exactly one interval', () => {
    vi.useFakeTimers()
    render(
      <>
        <Probe id="a" />
        <Probe id="b" />
        <Probe id="c" />
      </>
    )
    expect(vi.getTimerCount()).toBe(1)
  })

  it('one tick updates every subscriber', () => {
    vi.useFakeTimers()
    render(
      <>
        <Probe id="a" />
        <Probe id="b" />
      </>
    )
    const before = Number(screen.getByTestId('a').textContent)
    act(() => { vi.advanceTimersByTime(1000) })
    const afterA = Number(screen.getByTestId('a').textContent)
    const afterB = Number(screen.getByTestId('b').textContent)
    expect(afterA).toBeGreaterThanOrEqual(before + 1000)
    expect(afterB).toBe(afterA)
  })

  it('stops the interval when the last subscriber unmounts', () => {
    vi.useFakeTimers()
    const { unmount } = render(<Probe id="a" />)
    expect(vi.getTimerCount()).toBe(1)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
