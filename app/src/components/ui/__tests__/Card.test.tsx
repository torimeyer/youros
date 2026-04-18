import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import Card from '../Card'

describe('Card', () => {
  it('renders with required props and default variant/padding', () => {
    render(<Card>Hello</Card>)
    const card = screen.getByTestId('card')
    expect(card).toBeInTheDocument()
    expect(card).toHaveTextContent('Hello')
    expect(card.className).toContain('bg-slate-900/40')
    expect(card.className).toContain('border-slate-800')
    expect(card.className).toContain('rounded-xl')
    expect(card.className).toContain('p-5')
  })

  it('applies correct classes for elevated variant with lg padding', () => {
    render(<Card variant="elevated" padding="lg">Content</Card>)
    const card = screen.getByTestId('card')
    expect(card.className).toContain('bg-slate-900')
    expect(card.className).toContain('border-slate-700')
    expect(card.className).toContain('shadow-lg')
    expect(card.className).toContain('p-6')
  })

  it('applies outlined variant with sm padding', () => {
    render(<Card variant="outlined" padding="sm">Content</Card>)
    const card = screen.getByTestId('card')
    expect(card.className).toContain('bg-transparent')
    expect(card.className).toContain('p-4')
  })

  it('adds hover classes when hover prop is true', () => {
    render(<Card hover>Hoverable</Card>)
    const card = screen.getByTestId('card')
    expect(card.className).toContain('hover:border-slate-700')
    expect(card.className).toContain('transition-colors')
  })

  it('fires onClick when clicked', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    render(<Card onClick={handler}>Clickable</Card>)
    await user.click(screen.getByTestId('card'))
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('passes custom className through', () => {
    render(<Card className="custom-class">Content</Card>)
    expect(screen.getByTestId('card').className).toContain('custom-class')
  })
})
