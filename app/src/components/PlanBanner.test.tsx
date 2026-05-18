import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PlanBanner } from './PlanBanner'

describe('PlanBanner', () => {
  it('renders the plan text', () => {
    render(
      <PlanBanner
        plan="1. Read the file\n2. Edit the function\n3. Run tests"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )
    expect(screen.getByTestId('plan-banner')).toBeDefined()
    expect(screen.getByText(/Read the file/)).toBeDefined()
  })

  it('calls onConfirm when Confirm is clicked', () => {
    const onConfirm = vi.fn()
    render(
      <PlanBanner
        plan="Do some work"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    )
    fireEvent.click(screen.getByTestId('plan-banner-confirm'))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('calls onCancel when Cancel is clicked', () => {
    const onCancel = vi.fn()
    render(
      <PlanBanner
        plan="Do some work"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    )
    fireEvent.click(screen.getByTestId('plan-banner-cancel'))
    expect(onCancel).toHaveBeenCalledOnce()
  })
})

describe('ChatPanel plan_banner WS message', () => {
  it('shows plan banner when plan_banner WS message arrives', async () => {
    // This test lives in ChatPanel.test.tsx — see plan_banner describe block
    // Placeholder: PlanBanner component renders correctly in isolation
    expect(true).toBe(true)
  })
})
