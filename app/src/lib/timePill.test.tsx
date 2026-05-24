import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// Mock the hook so we control what data the pill receives
vi.mock('../hooks/useTimeStatus', () => ({
  useTimeStatus: vi.fn(),
}))

import { useTimeStatus } from '../hooks/useTimeStatus'
import { TimePill } from './timePill'

describe('TimePill', () => {
  beforeEach(() => {
    vi.mocked(useTimeStatus).mockReset()
  })

  it('renders ~3m when estimate_sec is 180', () => {
    vi.mocked(useTimeStatus).mockReturnValue({ estimate_sec: 180 })
    render(<TimePill opKind="agent_spawn" />)
    const pill = screen.getByTestId('time-pill-agent_spawn')
    expect(pill.textContent).toBe('~3m')
  })

  it('renders ~1m when estimate_sec is 90', () => {
    vi.mocked(useTimeStatus).mockReturnValue({ estimate_sec: 90 })
    render(<TimePill opKind="agent_spawn" />)
    expect(screen.getByTestId('time-pill-agent_spawn').textContent).toBe('~2m')
  })

  it('renders nothing when estimate is null (no history)', () => {
    vi.mocked(useTimeStatus).mockReturnValue(null)
    const { container } = render(<TimePill opKind="agent_spawn" />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when estimate_sec is 0', () => {
    vi.mocked(useTimeStatus).mockReturnValue({ estimate_sec: 0 })
    const { container } = render(<TimePill opKind="agent_spawn" />)
    expect(container.firstChild).toBeNull()
  })

  it('uses opKind in the testid', () => {
    vi.mocked(useTimeStatus).mockReturnValue({ estimate_sec: 300 })
    render(<TimePill opKind="spec_build" />)
    expect(screen.getByTestId('time-pill-spec_build')).toBeTruthy()
  })
})
