import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ReceiptsPill from './ReceiptsPill'

describe('ReceiptsPill', () => {
  it('renders nothing when status is undefined', () => {
    const { container } = render(<ReceiptsPill status={undefined} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders amber "No receipts" pill for missing status', () => {
    render(<ReceiptsPill status="missing" />)
    const pill = screen.getByTestId('receipts-pill-missing')
    expect(pill).toBeTruthy()
    expect(pill.textContent).toContain('No receipts')
  })

  it('amber pill has tooltip explaining what evidence is needed', () => {
    render(<ReceiptsPill status="missing" />)
    const pill = screen.getByTestId('receipts-pill-missing')
    const title = pill.getAttribute('title') ?? ''
    expect(title).toContain('commit hash')
    expect(title).toContain('evidence')
  })

  it('renders green "receipts" pill for present status', () => {
    render(<ReceiptsPill status="present" />)
    const pill = screen.getByTestId('receipts-pill-present')
    expect(pill).toBeTruthy()
    expect(pill.textContent).toContain('receipts')
  })

  it('present pill shows checkmark', () => {
    render(<ReceiptsPill status="present" />)
    const pill = screen.getByTestId('receipts-pill-present')
    expect(pill.textContent).toContain('✓')
  })

  it('missing pill shows warning icon', () => {
    render(<ReceiptsPill status="missing" />)
    const pill = screen.getByTestId('receipts-pill-missing')
    expect(pill.textContent).toContain('⚠')
  })
})
