// →2982: AgentStatusBar and AgentCompactSummary render once per active
// agent. Each used to own a private 1s setInterval, so 5 agents meant 10
// timers firing every second. They must all ride one shared clock.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { AgentStatusBar, AgentCompactSummary } from './Agents'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

// jsdom does not provide window.matchMedia.
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

describe('→2982 per-agent clocks share one timer', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('four mounted per-agent readouts create exactly one interval', () => {
    vi.useFakeTimers()
    const spawnedAt = new Date().toISOString()
    render(
      <>
        <AgentStatusBar spawnedAt={spawnedAt} />
        <AgentStatusBar spawnedAt={spawnedAt} />
        <AgentCompactSummary spawnedAt={spawnedAt} />
        <AgentCompactSummary spawnedAt={spawnedAt} />
      </>
    )
    expect(vi.getTimerCount()).toBe(1)
  })

  it('the shared tick still advances the elapsed readout', () => {
    vi.useFakeTimers()
    const spawnedAt = new Date().toISOString()
    render(<AgentStatusBar spawnedAt={spawnedAt} />)
    act(() => { vi.advanceTimersByTime(61_000) })
    expect(screen.getByTestId('agent-status-bar').textContent).toContain('1:01')
  })
})
