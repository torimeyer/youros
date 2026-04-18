import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import ErrorBanner from '../ErrorBanner'

describe('ErrorBanner', () => {
  it('renders with required message', () => {
    render(<ErrorBanner message="Something went wrong." />)
    const el = screen.getByTestId('error-banner')
    expect(el).toBeInTheDocument()
    expect(el).toHaveTextContent('Something went wrong.')
  })

  it('applies red styling', () => {
    render(<ErrorBanner message="Error" />)
    const el = screen.getByTestId('error-banner')
    expect(el.className).toContain('bg-red-500/10')
    expect(el.className).toContain('border-red-500/30')
    expect(el.className).toContain('rounded-xl')
    expect(el.className).toContain('text-red-400')
  })

  it('renders action button when action is provided', () => {
    render(<ErrorBanner message="Error." action={{ label: 'Try again', onClick: vi.fn() }} />)
    expect(screen.getByText('Try again')).toBeInTheDocument()
  })

  it('fires action onClick when button is clicked', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    render(<ErrorBanner message="Error." action={{ label: 'Retry', onClick: handler }} />)
    await user.click(screen.getByText('Retry'))
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('does not render action button when action is omitted', () => {
    render(<ErrorBanner message="Error." />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
