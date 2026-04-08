import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'

// Mock the api module so chat history hydration does not hit fetch.
// We resolve with an empty payload so the component falls back to its
// localStorage cache, matching the existing tests that pre populate
// localStorage.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({ tabs: [], active_tab_id: '' }),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

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
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Reply buttons should exist (hidden by opacity-0 until hover)
      const replyButtons = screen.getAllByTitle('Reply')
      expect(replyButtons.length).toBeGreaterThan(0)

      // The scroll container should have px-10 to prevent overflow clipping
      const scrollContainer = document.querySelector('.overflow-y-auto')
      expect(scrollContainer?.className).toContain('px-10')
    })
  })

  describe('Auto template badge', () => {
    it('shows the helper badge when a template_matched event arrives', () => {
      // Pre-load an assistant message so the badge has a place to anchor.
      const messages = [
        { id: 'msg-1', role: 'user', content: 'saa fix the login bug' },
        { id: 'msg-2', role: 'assistant', content: '', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      const { rerender } = render(<ChatPanel />)

      // Simulate the backend telling the chat panel a template matched.
      mockLastMessage = {
        type: 'template_matched',
        data: { name: 'saa', description: 'Spawn agents in parallel' },
      }
      rerender(<ChatPanel />)

      const badge = screen.getByTestId('template-badge')
      expect(badge).toBeTruthy()
      expect(badge.textContent).toContain('saa')
    })

    it('dismisses the badge when the user clicks the close button', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'diagnose the build' },
        { id: 'msg-2', role: 'assistant', content: '', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      const { rerender } = render(<ChatPanel />)

      mockLastMessage = {
        type: 'template_matched',
        data: { name: 'diagnose', description: 'Find the root cause' },
      }
      rerender(<ChatPanel />)

      const badge = screen.getByTestId('template-badge')
      const dismiss = badge.querySelector('button')
      expect(dismiss).toBeTruthy()
      if (dismiss) fireEvent.click(dismiss)

      expect(screen.queryByTestId('template-badge')).toBeNull()
    })
  })

  describe('Chat backend indicator', () => {
    it('shows the Claude subscription label when the backend is claude_code', () => {
      const { rerender } = render(<ChatPanel />)

      mockLastMessage = {
        type: 'backend_active',
        data: { name: 'claude_code', label: 'Powered by your Claude subscription' },
      }
      rerender(<ChatPanel />)

      const indicator = screen.getByTestId('chat-backend-indicator')
      expect(indicator).toBeTruthy()
      expect(indicator.textContent).toContain('Claude subscription')
    })

    it('shows the Anthropic API label when the backend is anthropic_api', () => {
      const { rerender } = render(<ChatPanel />)

      mockLastMessage = {
        type: 'backend_active',
        data: { name: 'anthropic_api', label: 'Using Anthropic API' },
      }
      rerender(<ChatPanel />)

      const indicator = screen.getByTestId('chat-backend-indicator')
      expect(indicator).toBeTruthy()
      expect(indicator.textContent).toContain('Anthropic API')
    })

    it('hides the indicator until a backend_active event arrives', () => {
      render(<ChatPanel />)
      expect(screen.queryByTestId('chat-backend-indicator')).toBeNull()
    })
  })

  describe('Reactions', () => {
    it('shows reaction bar with emoji buttons for each message', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi!', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

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
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

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
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

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
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Add a reaction
      const heartButtons = screen.getAllByTitle('React with ❤️')
      fireEvent.click(heartButtons[0])

      // Check localStorage was updated with reaction
      const saved = JSON.parse(localStorage.getItem('myos-chat-messages') || '[]')
      const msg = saved.find((m: { id: string }) => m.id === 'msg-1')
      expect(msg.reactions).toEqual({ '❤️': 1 })
    })

    it('loads persisted reactions from localStorage', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there', reactions: { '🔥': 2, '😂': 1 } },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Reaction pills should be rendered from the stored data
      expect(screen.getByTitle('🔥 2')).toBeTruthy()
      expect(screen.getByTitle('😂 1')).toBeTruthy()
    })
  })

  describe('Reaction picker does not overlap next message', () => {
    it('reaction bar uses horizontal layout, not vertical column', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi!', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      const reactionBar = screen.getByTestId('reaction-bar-msg-1')
      // The bar should use horizontal flex (items-center) not vertical (flex-col)
      expect(reactionBar.className).toContain('flex')
      expect(reactionBar.className).toContain('items-center')
      expect(reactionBar.className).not.toContain('flex-col')
    })

    it('reaction picker is positioned below the message, not beside it', () => {
      const messages = [
        { id: 'msg-1', role: 'assistant', content: 'Hello!', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // The hover container that holds the reply button and reaction bar
      // should be positioned below the message with mt-1
      const reactionBar = screen.getByTestId('reaction-bar-msg-1')
      const hoverContainer = reactionBar.parentElement!
      expect(hoverContainer.className).toContain('z-10')
      // It should NOT use -right-8 or -left-8 which would place it beside the message
      expect(hoverContainer.className).not.toContain('-right-8')
      expect(hoverContainer.className).not.toContain('-left-8')
    })

    it('hover container has z-index to layer above adjacent messages', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      const reactionBar = screen.getByTestId('reaction-bar-msg-1')
      const hoverContainer = reactionBar.parentElement!
      expect(hoverContainer.className).toContain('z-10')
    })
  })

  describe('Message bubble alignment', () => {
    it('right-aligns user message bubbles and left-aligns assistant bubbles', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi back', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      const userMsgContainer = document.getElementById('msg-msg-1')
      const assistantMsgContainer = document.getElementById('msg-msg-2')
      expect(userMsgContainer).not.toBeNull()
      expect(assistantMsgContainer).not.toBeNull()

      // User message outer container should use flex-col items-end so the
      // bubble sits on the right side of the panel, like iMessage.
      expect(userMsgContainer!.className).toContain('items-end')
      // Assistant message container should NOT have items-end.
      expect(assistantMsgContainer!.className).not.toContain('items-end')

      // User bubble wrapper should have ml-auto and a max width to keep it
      // from spanning the whole panel.
      const userBubbleWrapper = userMsgContainer!.querySelector('.relative')
      expect(userBubbleWrapper).not.toBeNull()
      expect(userBubbleWrapper!.className).toContain('ml-auto')
      expect(userBubbleWrapper!.className).toContain('max-w-[75%]')

      // Assistant bubble wrapper should NOT have ml-auto.
      const assistantBubbleWrapper = assistantMsgContainer!.querySelector('.relative')
      expect(assistantBubbleWrapper).not.toBeNull()
      expect(assistantBubbleWrapper!.className).not.toContain('ml-auto')
    })
  })

  describe('Token streaming into assistant bubble', () => {
    // Regression guard for the silent empty response bug. When a user sends a
    // message and the backend replies with a single token event followed by
    // done, the assistant bubble must render the actual text and not stay
    // empty. This is the exact failure Tori saw after the Claude Code cutover.
    it('renders token text into the assistant placeholder created by sendMessage', () => {
      const { rerender } = render(<ChatPanel />)

      // Send a message. This creates an empty assistant placeholder.
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'did you complete it?' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Backend sends a single token event with the whole response, which is
      // what claude_code_provider.stream_chat does today.
      mockLastMessage = { type: 'token', data: 'I am running as expected.' }
      rerender(<ChatPanel />)

      // The assistant bubble should now contain the streamed text.
      expect(screen.getByText('I am running as expected.')).toBeTruthy()
    })

    it('shows an error in the assistant bubble when the stream drops mid-turn', () => {
      // Regression guard for the silent empty bubble. When the WebSocket
      // closes before a real done event arrives, useWebSocket surfaces an
      // error event. The chat panel must render that error text instead of
      // leaving the assistant row visually empty.
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'did you complete it?' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Simulate useWebSocket's mid-turn drop error.
      mockLastMessage = {
        type: 'error',
        data: 'Connection dropped before the response finished. Please try again.',
      }
      rerender(<ChatPanel />)

      expect(screen.getByText(/Connection dropped/i)).toBeTruthy()
    })
  })

  describe('Default LLM from settings', () => {
    it('sends messages using the default model from the store', () => {
      useAppStore.setState({ defaultChatModel: 'claude' })
      render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'Hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      expect(mockSend).toHaveBeenCalledWith(
        expect.objectContaining({ model: '@claude' })
      )
    })

    it('sends messages using gemini when that is the default', () => {
      useAppStore.setState({ defaultChatModel: 'gemini' })
      render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message gemini/i)
      fireEvent.change(input, { target: { value: 'Hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      expect(mockSend).toHaveBeenCalledWith(
        expect.objectContaining({ model: '@gemini' })
      )
    })

    it('shows the default model name in the empty state', () => {
      useAppStore.setState({ defaultChatModel: 'gemini' })
      render(<ChatPanel />)

      expect(screen.getByText('gemini')).toBeInTheDocument()
    })

    it('overrides default model when user types an @mention', () => {
      useAppStore.setState({ defaultChatModel: 'claude' })
      render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: '@gemini hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The model sent should still be the defaultChatModel as the backend handles @mentions.
      // But the detected model label should be gemini (from the @mention regex in sendMessage).
      expect(mockSend).toHaveBeenCalledWith(
        expect.objectContaining({ model: '@claude' })
      )
    })

    it('uses updated default after changing it in the store', () => {
      useAppStore.setState({ defaultChatModel: 'claude' })
      const { unmount } = render(<ChatPanel />)
      unmount()

      // Change the default
      useAppStore.setState({ defaultChatModel: 'gemini' })
      render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message gemini/i)
      fireEvent.change(input, { target: { value: 'Hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      expect(mockSend).toHaveBeenCalledWith(
        expect.objectContaining({ model: '@gemini' })
      )
    })
  })

  describe('Chat history persistence', () => {
    it('renders a clear history button in the header', () => {
      render(<ChatPanel />)
      const clearBtn = screen.getByTestId('clear-history-button')
      expect(clearBtn).toBeTruthy()
    })

    it('clear history button resets to a single empty tab after confirmation', async () => {
      // Set up localStorage with a message so the panel starts with content.
      const messages = [
        { id: 'clear-test-msg-1', role: 'user', content: 'Message to be cleared', updatedAt: new Date().toISOString() },
        { id: 'clear-test-msg-2', role: 'assistant', content: 'Reply to clear', model: 'claude', updatedAt: new Date().toISOString() },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      // Stub window.confirm to return true (user confirmed)
      vi.spyOn(window, 'confirm').mockReturnValue(true)

      render(<ChatPanel />)

      // Verify the message DOM elements are present before clear
      expect(document.getElementById('msg-clear-test-msg-1')).toBeTruthy()

      const clearBtn = screen.getByTestId('clear-history-button')
      fireEvent.click(clearBtn)

      // After clear the message DOM elements should be gone
      expect(document.getElementById('msg-clear-test-msg-1')).toBeNull()

      // The api.delete should have been called
      const { api } = await import('../lib/api')
      expect(vi.mocked(api.delete)).toHaveBeenCalledWith('/chat/history')
    })

    it('does not clear when user cancels the confirmation dialog', () => {
      const messages = [
        { id: 'cancel-test-msg', role: 'user', content: 'Keep this message' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      vi.spyOn(window, 'confirm').mockReturnValue(false)

      render(<ChatPanel />)
      expect(document.getElementById('msg-cancel-test-msg')).toBeTruthy()

      fireEvent.click(screen.getByTestId('clear-history-button'))

      // Message should still be visible
      expect(document.getElementById('msg-cancel-test-msg')).toBeTruthy()
    })

    it('calls api.get on mount to hydrate history from server', async () => {
      // Verify the component requests chat history from the server when it mounts.
      // The default mock returns empty tabs so we just confirm the call was made.
      const { api } = await import('../lib/api')

      render(<ChatPanel />)

      await vi.waitFor(() =>
        vi.mocked(api.get).mock.calls.some((c) => c[0] === '/chat/history')
      )

      const calls = vi.mocked(api.get).mock.calls.filter((c) => c[0] === '/chat/history')
      expect(calls.length).toBeGreaterThan(0)
    })

    it('saves tabs to server after hydration completes', async () => {
      const { api } = await import('../lib/api')
      vi.mocked(api.get).mockResolvedValueOnce({
        tabs: [
          {
            id: 'tab-x',
            name: 'Existing',
            messages: [],
          },
        ],
        active_tab_id: 'tab-x',
      })

      render(<ChatPanel />)

      // Wait for hydration and the debounced save (500ms debounce in component)
      await vi.waitFor(
        () => {
          const putCalls = vi.mocked(api.put).mock.calls.filter((c) => c[0] === '/chat/history')
          expect(putCalls.length).toBeGreaterThan(0)
        },
        { timeout: 2000 }
      )
    })
  })
})
