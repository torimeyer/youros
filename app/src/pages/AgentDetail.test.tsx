import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CapabilitiesSection } from './AgentDetail'

describe('CapabilitiesSection', () => {
  it('renders capabilities-section with all three sub-fields when present', () => {
    render(
      <CapabilitiesSection
        writesTo="app/src/pages/*.tsx"
        budget={3}
        model="sonnet"
        timeLimit="30m"
      />
    )

    expect(screen.getByTestId('capabilities-section')).toBeDefined()
    expect(screen.getByText('Writes to')).toBeDefined()
    expect(screen.getByText('app/src/pages/*.tsx')).toBeDefined()
    expect(screen.getByText('Token budget')).toBeDefined()
    expect(screen.getByText('~500k tokens')).toBeDefined()
    expect(screen.getByText('Time limit')).toBeDefined()
    expect(screen.getByText('30m')).toBeDefined()
  })

  it('hides writes-to row when writesTo is absent', () => {
    render(
      <CapabilitiesSection
        budget={2}
        model="sonnet"
        timeLimit="1h"
      />
    )

    expect(screen.getByTestId('capabilities-section')).toBeDefined()
    expect(screen.queryByText('Writes to')).toBeNull()
    expect(screen.getByText('Time limit')).toBeDefined()
    expect(screen.getByText('Token budget')).toBeDefined()
  })

  it('hides time-limit row when timeLimit is absent', () => {
    render(
      <CapabilitiesSection
        writesTo="src/**"
        budget={2}
        model="haiku"
      />
    )

    expect(screen.getByTestId('capabilities-section')).toBeDefined()
    expect(screen.getByText('Writes to')).toBeDefined()
    expect(screen.queryByText('Time limit')).toBeNull()
  })

  it('returns null when no displayable fields are present', () => {
    const { container } = render(
      <CapabilitiesSection />
    )

    expect(container.firstChild).toBeNull()
  })
})
