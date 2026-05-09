import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { IMessageConfirmBubble } from './IMessageConfirmBubble'

describe('IMessageConfirmBubble', () => {
  const baseProps = {
    resolving: false,
    displayName: 'Lil Oatmeal',
    messageText: 'goodnight',
    onSend: vi.fn(),
    onDismiss: vi.fn(),
  }

  it('shows resolving state', () => {
    render(<IMessageConfirmBubble {...baseProps} resolving={true} />)
    expect(screen.getByTestId('imessage-confirm-bubble-resolving')).toBeTruthy()
    expect(screen.queryByTestId('imessage-confirm-bubble')).toBeNull()
  })

  it('shows contact name and message preview', () => {
    render(<IMessageConfirmBubble {...baseProps} />)
    expect(screen.getByTestId('imessage-confirm-bubble')).toBeTruthy()
    expect(screen.getByText('Lil Oatmeal')).toBeTruthy()
    expect(screen.getByText(/goodnight/)).toBeTruthy()
  })

  it('calls onSend when Send button clicked', () => {
    const onSend = vi.fn()
    render(<IMessageConfirmBubble {...baseProps} onSend={onSend} />)
    fireEvent.click(screen.getByTestId('imessage-send-btn'))
    expect(onSend).toHaveBeenCalledOnce()
  })

  it('calls onDismiss when Not now clicked', () => {
    const onDismiss = vi.fn()
    render(<IMessageConfirmBubble {...baseProps} onDismiss={onDismiss} />)
    fireEvent.click(screen.getByTestId('imessage-dismiss-btn'))
    expect(onDismiss).toHaveBeenCalledOnce()
  })

  it('truncates long message previews at 120 chars', () => {
    const longMsg = 'a'.repeat(130)
    render(<IMessageConfirmBubble {...baseProps} messageText={longMsg} />)
    const preview = screen.getByText(/a{120}\.\.\./)
    expect(preview).toBeTruthy()
  })

  it('shows send button with aria-label including contact name', () => {
    render(<IMessageConfirmBubble {...baseProps} />)
    const btn = screen.getByTestId('imessage-send-btn')
    expect(btn.getAttribute('aria-label')).toContain('Lil Oatmeal')
    expect(btn.getAttribute('aria-label')).toContain('iMessage')
  })

  it('does not show resolving state when resolving is false', () => {
    render(<IMessageConfirmBubble {...baseProps} resolving={false} />)
    expect(screen.queryByTestId('imessage-confirm-bubble-resolving')).toBeNull()
    expect(screen.getByTestId('imessage-confirm-bubble')).toBeTruthy()
  })
})
