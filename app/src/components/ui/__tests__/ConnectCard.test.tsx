import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ConnectCard from '../ConnectCard'

const defaultProps = {
  icon: 'calendar_month',
  accentColor: '#3b82f6',
  title: 'Connect Calendar',
  description: 'Link your calendar to see your schedule.',
  primaryAction: <button>Connect</button>,
}

describe('ConnectCard', () => {
  it('renders with required props', () => {
    render(<ConnectCard {...defaultProps} />)
    const el = screen.getByTestId('connect-card')
    expect(el).toBeInTheDocument()
    expect(screen.getByText('Connect Calendar')).toBeInTheDocument()
    expect(screen.getByText('Link your calendar to see your schedule.')).toBeInTheDocument()
  })

  it('renders primaryAction', () => {
    render(<ConnectCard {...defaultProps} />)
    expect(screen.getByText('Connect')).toBeInTheDocument()
  })

  it('does not render ErrorBanner when error is omitted', () => {
    render(<ConnectCard {...defaultProps} />)
    expect(screen.queryByTestId('error-banner')).toBeNull()
  })

  it('renders ErrorBanner when error is provided', () => {
    render(<ConnectCard {...defaultProps} error="Could not connect. Check your credentials." />)
    expect(screen.getByTestId('error-banner')).toBeInTheDocument()
    expect(screen.getByText('Could not connect. Check your credentials.')).toBeInTheDocument()
  })

  it('applies max-w-md centered card layout', () => {
    render(<ConnectCard {...defaultProps} />)
    const el = screen.getByTestId('connect-card')
    expect(el.className).toContain('max-w-md')
    expect(el.className).toContain('mx-auto')
    expect(el.className).toContain('rounded-2xl')
  })
})
