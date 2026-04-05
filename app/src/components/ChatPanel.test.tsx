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

  describe('Reactions', () => {
    it('shows reaction bar with emoji buttons for each message', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi!', model: 'claude' },
      ]
      localStorage.setItem('youros-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Each message should have a reaction bar with emoji buttons
      const reactionBar1 = screen.getByTestId('reaction-bar-msg-1')
      const reactionBar2 = screen.getByTestId('reaction-bar-msg-2')
      expect(reactionBar1).toBeTruthy()
      expect(reactionBar2).toBeTruthy()

      // Each bar should have 6 emoji buttons
      const emojiButtons = screen.getAllByTitle(/React with/)
      expect(emojiButtons.length).toBe(12) // 6 per message, 2 messages
    })

    it('adds a reaction pill when clicking an emoji', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
      ]
      localStorage.setItem('youros-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Click the thumbs up reaction
      const thumbsUpButtons = screen.getAllByTitle('React with 👍')
      fireEvent.click(thumbsUpButtons[0])

      // A reaction pill should appear with the emoji and count
      const pill = screen.getByTitle('👍 1')
      expect(pill).toBeTruthy()
      expect(pill.textContent).toContain('👍')
      expect(pill.textContent).toContain('1')
    })

    it('removes a reaction when clicking the same emoji again', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
      ]
      localStorage.setItem('youros-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Click thumbs up to add it
      const thumbsUpButtons = screen.getAllByTitle('React with 👍')
      fireEvent.click(thumbsUpButtons[0])
      expect(screen.getByTitle('👍 1')).toBeTruthy()

      // Click the reaction pill to remove it
      const pill = screen.getByTitle('👍 1')
      fireEvent.click(pill)

      // The pill should be gone
      expect(screen.queryByTitle('👍 1')).toBeNull()
    })

    it('persists reactions to localStorage', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
      ]
      localStorage.setItem('youros-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Add a reaction
      const heartButtons = screen.getAllByTitle('React with ❤️')
      fireEvent.click(heartButtons[0])

      // Check localStorage was updated with reaction
      const saved = JSON.parse(localStorage.getItem('youros-chat-messages') || '[]')
      const msg = saved.find((m: { id: string }) => m.id === 'msg-1')
      expect(msg.reactions).toEqual({ '❤️': 1 })
    })

    it('loads persisted reactions from localStorage', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there', reactions: { '🔥': 2, '😂': 1 } },
      ]
      localStorage.setItem('youros-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Reaction pills should be rendered from the stored data
      expect(screen.getByTitle('🔥 2')).toBeTruthy()
      expect(screen.getByTitle('😂 1')).toBeTruthy()
    })
  })
})
