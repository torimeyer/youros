import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import Button from '../Button'

describe('Button', () => {
  it('renders with required props and defaults', () => {
    render(<Button>Click me</Button>)
    const btn = screen.getByTestId('button')
    expect(btn).toBeInTheDocument()
    expect(btn).toHaveTextContent('Click me')
    expect(btn.className).toContain('bg-blue-600')
    expect(btn.className).toContain('px-4 py-2')
  })

  it('applies secondary variant classes', () => {
    render(<Button variant="secondary">Secondary</Button>)
    const btn = screen.getByTestId('button')
    expect(btn.className).toContain('bg-slate-800')
    expect(btn.className).toContain('border-slate-700')
  })

  it('applies danger variant classes', () => {
    render(<Button variant="danger">Delete</Button>)
    expect(screen.getByTestId('button').className).toContain('bg-red-600')
  })

  it('applies ghost variant classes', () => {
    render(<Button variant="ghost">Ghost</Button>)
    expect(screen.getByTestId('button').className).toContain('text-slate-400')
  })

  it('applies size sm classes', () => {
    render(<Button size="sm">Small</Button>)
    expect(screen.getByTestId('button').className).toContain('px-3 py-1.5')
  })

  it('applies size lg classes', () => {
    render(<Button size="lg">Large</Button>)
    expect(screen.getByTestId('button').className).toContain('py-3')
    expect(screen.getByTestId('button').className).toContain('rounded-xl')
  })

  it('fires onClick handler', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    render(<Button onClick={handler}>Go</Button>)
    await user.click(screen.getByTestId('button'))
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('is disabled when disabled prop is true and does not fire onClick', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    render(<Button disabled onClick={handler}>Disabled</Button>)
    const btn = screen.getByTestId('button')
    expect(btn).toBeDisabled()
    await user.click(btn)
    expect(handler).not.toHaveBeenCalled()
  })

  it('shows spinner and is disabled when loading', () => {
    render(<Button loading>Save</Button>)
    const btn = screen.getByTestId('button')
    expect(btn).toBeDisabled()
    expect(btn.querySelector('.animate-spin')).toBeTruthy()
  })

  it('applies w-full class when fullWidth is true', () => {
    render(<Button fullWidth>Full</Button>)
    expect(screen.getByTestId('button').className).toContain('w-full')
  })
})
