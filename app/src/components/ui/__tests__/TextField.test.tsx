import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import TextField from '../TextField'

describe('TextField', () => {
  it('renders with no props without crashing', () => {
    render(<TextField />)
    expect(screen.getByTestId('text-field')).toBeInTheDocument()
  })

  it('renders label when provided', () => {
    render(<TextField label="Email address" />)
    expect(screen.getByText('Email address')).toBeInTheDocument()
    const input = screen.getByTestId('text-field').querySelector('input')
    expect(input?.id).toBe('email-address')
  })

  it('shows error message and applies error styles', () => {
    render(<TextField label="Name" error="This field is required." />)
    expect(screen.getByText('This field is required.')).toBeInTheDocument()
    const input = screen.getByTestId('text-field').querySelector('input')
    expect(input?.className).toContain('border-red-500/50')
  })

  it('shows help text when no error', () => {
    render(<TextField help="Enter your full name." />)
    expect(screen.getByText('Enter your full name.')).toBeInTheDocument()
  })

  it('hides help text when error is shown', () => {
    render(<TextField help="Some help." error="An error." />)
    expect(screen.queryByText('Some help.')).not.toBeInTheDocument()
    expect(screen.getByText('An error.')).toBeInTheDocument()
  })

  it('applies sm size classes', () => {
    render(<TextField size="sm" />)
    const input = screen.getByTestId('text-field').querySelector('input')
    expect(input?.className).toContain('px-3 py-1.5 text-sm')
  })

  it('applies lg size classes', () => {
    render(<TextField size="lg" />)
    const input = screen.getByTestId('text-field').querySelector('input')
    expect(input?.className).toContain('px-4 py-3 text-base')
  })

  it('passes through input props like placeholder and onChange', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<TextField placeholder="Type here" onChange={onChange} />)
    const input = screen.getByTestId('text-field').querySelector('input') as HTMLInputElement
    expect(input.placeholder).toBe('Type here')
    await user.type(input, 'hello')
    expect(onChange).toHaveBeenCalled()
  })
})
