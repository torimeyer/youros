import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { PlanBanner } from './PlanBanner'

describe('PlanBanner', () => {
  it('renders the plan text', () => {
    render(
      <PlanBanner
        plan="Step 1: read the file\nStep 2: edit it"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
        onRevise={vi.fn()}
      />
    )
    expect(screen.getByTestId('plan-banner')).toBeTruthy()
    expect(screen.getByText(/Step 1: read the file/)).toBeTruthy()
  })

  it('calls onConfirm when Approve is clicked', () => {
    const onConfirm = vi.fn()
    render(
      <PlanBanner
        plan="do the thing"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
        onRevise={vi.fn()}
      />
    )
    fireEvent.click(screen.getByTestId('plan-banner-confirm'))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onCancel when Reject is clicked', () => {
    const onCancel = vi.fn()
    render(
      <PlanBanner
        plan="do the thing"
        onConfirm={vi.fn()}
        onCancel={onCancel}
        onRevise={vi.fn()}
      />
    )
    fireEvent.click(screen.getByTestId('plan-banner-cancel'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('shows a comment input when Revise is clicked', () => {
    render(
      <PlanBanner
        plan="do the thing"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
        onRevise={vi.fn()}
      />
    )
    fireEvent.click(screen.getByTestId('plan-banner-revise'))
    expect(screen.getByTestId('plan-banner-revise-input')).toBeTruthy()
  })

  it('calls onRevise with the typed comment when submitted', () => {
    const onRevise = vi.fn()
    render(
      <PlanBanner
        plan="do the thing"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
        onRevise={onRevise}
      />
    )
    fireEvent.click(screen.getByTestId('plan-banner-revise'))
    const input = screen.getByTestId('plan-banner-revise-input')
    fireEvent.change(input, { target: { value: 'change step 2 first' } })
    fireEvent.click(screen.getByTestId('plan-banner-revise-submit'))
    expect(onRevise).toHaveBeenCalledWith('change step 2 first')
  })
})
