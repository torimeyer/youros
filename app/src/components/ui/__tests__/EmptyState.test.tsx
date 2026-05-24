import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import EmptyState from '../EmptyState'

describe('EmptyState', () => {
  it('renders with required props', () => {
    render(<EmptyState icon="inbox" title="Nothing here yet." />)
    const el = screen.getByTestId('empty-state')
    expect(el).toBeInTheDocument()
    expect(screen.getByText('Nothing here yet.')).toBeInTheDocument()
  })

  it('renders description when provided', () => {
    render(<EmptyState icon="inbox" title="No items." description="Add one to get started." />)
    expect(screen.getByText('Add one to get started.')).toBeInTheDocument()
  })

  it('does not render description when omitted', () => {
    render(<EmptyState icon="inbox" title="No items." />)
    const el = screen.getByTestId('empty-state')
    expect(el.querySelector('p:nth-child(2)')).toBeNull()
  })

  it('renders action button and fires onClick', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    render(
      <EmptyState
        icon="add"
        title="No tasks."
        action={{ label: 'Add your first task', onClick: handler }}
      />
    )
    const btn = screen.getByText('Add your first task')
    expect(btn).toBeInTheDocument()
    await user.click(btn)
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('does not render action button when omitted', () => {
    render(<EmptyState icon="inbox" title="Empty." />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
