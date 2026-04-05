import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'

// Mock scrollIntoView (not available in jsdom)
Element.prototype.scrollIntoView = vi.fn()

// Mock the useWebSocket hook
const mockConnect = vi.fn()
const mockSend = vi.fn()
let mockLastMessage: { type: string; data?: unknown } | null = null
let mockIsConnected = true

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: mockConnect,
    send: mockSend,
    get lastMessage() { return mockLastMessage },
    isConnected: mockIsConnected,
  }),
}))

// Mock crypto.randomUUID
let uuidCounter = 0
vi.stubGlobal('crypto', {
  randomUUID: () => `test-uuid-${++uuidCounter}`,
})

describe('ChatPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    uuidCounter = 0
    mockLastMessage = null
    mockIsConnected = true
    localStorage.clear()
    useAppStore.setState({
      chatOpen: true,
      chatWidth: 380,
      isResizing: false,
      defaultChatModel: 'claude',
    })
  })

  // Bug 1: ThinkingDots should show when waiting for a response
  describe('Thinking bubble', () => {
    it('shows thinking dots in the assistant placeholder after sending a message', () => {
      render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'Hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // After sending, an assistant placeholder is created with ThinkingDots.
      // ThinkingDots renders three bouncing dot spans.
      const dots = document.querySelectorAll('.animate-bounce')
      expect(dots.length).toBe(3)
    })

    it('does not create a duplicate assistant message on model_boundary', () => {
      const { rerender } = render(<ChatPanel />)

      // Send a message to create the assistant placeholder
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'Hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Count assistant labels before model_boundary
      const countBefore = screen.getAllByText(/claude/i).length

      // Simulate model_boundary arriving from WebSocket
      mockLastMessage = { type: 'model_boundary' }
      rerender(<ChatPanel />)

      // Should NOT create a second empty assistant message
      const countAfter = screen.getAllByText(/claude/i).length
      expect(countAfter).toBe(countBefore)
    })

    it('still shows thinking dots after model_boundary event', () => {
      const { rerender } = render(<ChatPanel />)

      // Send a message
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'Hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Simulate model_boundary
      mockLastMessage = { type: 'model_boundary' }
      rerender(<ChatPanel />)

      // ThinkingDots should still be visible (3 bouncing dots)
      const dots = document.querySelectorAll('.animate-bounce')
      expect(dots.length).toBe(3)
    })
  })

  // Bug 3: Reply arrow should not be clipped
  describe('Reply arrow visibility', () => {
    it('message scroll container has enough horizontal padding for reply buttons', () => {
      render(<ChatPanel />)

      // The messages scroll container should have px-10 (40px) padding
      // to prevent the reply button at -right-8 (32px) from being clipped
      const scrollContainer = document.querySelector('.overflow-y-auto')
      expect(scrollContainer).not.toBeNull()
      expect(scrollContainer?.className).toContain('px-10')
    })

    it('reply button is visible on hover without clipping', () => {
      // Pre-load a message into localStorage so it renders
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi!', model: 'claude' },
      ]
      localStorage.setItem('youros-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Reply buttons should exist (hidden by opacity-0 until hover)
      const replyButtons = screen.getAllByTitle('Reply')
      expect(replyButtons.length).toBeGreaterThan(0)

      // The scroll container should have px-10 to prevent overflow clipping
      const scrollContainer = document.querySelector('.overflow-y-auto')
      expect(scrollContainer?.className).toContain('px-10')
    })
  })
})
