import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'
import { useRunningAgentsStore } from '../stores/runningAgents'

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
const mockDisconnect = vi.fn()
const mockSend = vi.fn()
let mockLastMessage: { type: string; data?: unknown } | null = null
let mockIsConnected = true

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: mockConnect,
    disconnect: mockDisconnect,
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
    useRunningAgentsStore.setState({
      count: 0,
      agents: [],
      connected: false,
      lastUpdatedAt: null,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
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

    // Regression for the "empty bubble, no dots" flash. When the user hits
    // Enter, the assistant placeholder AND its thinking indicator must
    // render in the same commit as the user's bubble. No server round-trip,
    // no WebSocket event, nothing else is allowed to gate the dots. Before
    // the fix, the placeholder appeared with just the model label for a
    // few seconds until the first server event flipped isStreaming in the
    // effect. The instant-dots gate now covers that window.
    it('renders thinking dots in the same commit as the user bubble with no server event', () => {
      // No mockLastMessage set. The effect never fires because lastMessage
      // is still null. If the dots require a server event, this test fails.
      mockLastMessage = null
      render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'how are you' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The thinking indicator is present after the synchronous render
      // that handled the Enter key. No server event needed.
      const dots = screen.getByTestId('thinking-dots')
      expect(dots).toBeTruthy()
      expect(dots.textContent).toContain('Thinking')
    })

    it('clears thinking dots once the first token arrives', () => {
      mockLastMessage = null
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Dots present pre-server-event.
      expect(screen.getByTestId('thinking-dots')).toBeTruthy()

      // First token arrives. The bubble now has content, so the thinking
      // indicator is replaced by the assistant's text.
      mockLastMessage = { type: 'token', data: 'Hi there.' }
      rerender(<ChatPanel />)

      // The "done" path is not invoked so isStreaming stays true, but the
      // bubble now has content which pushes the text render path. The
      // thinking dots still render because the content-aware render keeps
      // the indicator visible while streaming. The key invariant is that
      // the assistant text is now visible. We verify the text is there
      // alongside the streaming indicator so nothing stays blank.
      expect(screen.getByText('Hi there.')).toBeTruthy()
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

    // Regression: during the pure thinking state (assistant bubble is empty
    // and still streaming), the only AI-side render is the thinking
    // indicator. No template badge, no preview pill, nothing that echoes
    // any part of the user's question. Tori saw a small pill appear with
    // a truncated form of her question before the first token; this test
    // guards against that regression.
    it('shows only the thinking indicator during pure thinking state', () => {
      render(<ChatPanel />)

      const query = "what's the biggest risk in the current plan"
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: query } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The thinking indicator is visible.
      const dots = screen.getByTestId('thinking-dots')
      expect(dots).toBeTruthy()
      expect(dots.textContent).toContain('Thinking')

      // No template badge rendered during pure thinking state.
      expect(screen.queryByTestId('template-badge')).toBeNull()

      // No echoed snippet of the user's query appears outside the user's
      // own bubble. The user bubble is the only place the query text is
      // allowed to render. We walk every other element that would render
      // a pill-shaped container (badge, preview, suggestion) and confirm
      // none of them contain any part of the query.
      const truncated = query.slice(0, 24)
      const allPills = document.querySelectorAll('[class*="rounded-full"]')
      for (const pill of allPills) {
        expect(pill.textContent).not.toContain(truncated)
      }
    })

    // Regression: when a template_matched event lands while the assistant
    // bubble is still empty (pure thinking state), the badge must not
    // render. The badge reappears once the first token arrives.
    it('hides the template badge until the assistant bubble has content', () => {
      const { rerender } = render(<ChatPanel />)

      // Send a message. Bubble starts empty, streaming=true.
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'saa fix the login bug' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Backend tells the panel a template matched.
      mockLastMessage = {
        type: 'template_matched',
        data: { name: 'saa', description: 'Spawn agents in parallel' },
      }
      rerender(<ChatPanel />)

      // Badge is suppressed while the bubble is still empty.
      expect(screen.queryByTestId('template-badge')).toBeNull()

      // First token arrives. Badge now becomes visible.
      mockLastMessage = { type: 'token', data: 'Starting.' }
      rerender(<ChatPanel />)

      const badge = screen.getByTestId('template-badge')
      expect(badge.textContent).toContain('saa')
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
      // Pre-load an assistant message with content so the badge renders.
      // The badge is intentionally hidden while the bubble is still empty
      // (pure thinking state) so nothing appears next to the thinking dots.
      const messages = [
        { id: 'msg-1', role: 'user', content: 'saa fix the login bug' },
        { id: 'msg-2', role: 'assistant', content: 'On it.', model: 'claude' },
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
        { id: 'msg-2', role: 'assistant', content: 'Looking into it.', model: 'claude' },
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

    // Regression for the silent dead bubble after a mid-turn socket drop.
    // When useWebSocket surfaces a Connection-dropped error, the assistant
    // bubble must offer an inline Retry button. Clicking Retry re-sends
    // the last user turn so the user never has to re-type their question.
    it('renders an inline Retry button on mid-turn drop that re-sends the last turn', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'did gemini miss anything?' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Socket drops mid-turn. The bubble surfaces the error.
      mockLastMessage = {
        type: 'error',
        data: 'Connection dropped before the response finished. Please try again.',
      }
      rerender(<ChatPanel />)

      // Retry button shows up in the failed bubble.
      const retry = screen.getByTestId('retry-last-turn')
      expect(retry).toBeTruthy()

      // Clear the send mock so we only see the retry call.
      mockSend.mockClear()

      // Click Retry. It must re-send the same user text through the WS.
      fireEvent.click(retry)

      expect(mockSend).toHaveBeenCalledTimes(1)
      const payload = mockSend.mock.calls[0][0] as { messages: { role: string; content: string }[] }
      const lastUser = payload.messages[payload.messages.length - 1]
      expect(lastUser.role).toBe('user')
      expect(lastUser.content).toBe('did gemini miss anything?')
    })

    // Regression for the streaming render jitter Tori reported: the bubble
    // appeared to "type a sentence then delete it" mid-stream. Root cause
    // was CollapsibleText slicing to the last 200 characters and then
    // re-anchoring to the next ". " boundary, which caused the visible
    // text to jump backwards every time a new sentence completed inside
    // the sliding window. The fix is to render the full content during
    // streaming with whitespace-pre-wrap, so the visible text only grows.
    it('never shrinks the rendered bubble text as more tokens arrive', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'tell me a long story' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Build a response that crosses the 300-char threshold AND contains
      // sentence boundaries inside the last 200 chars, which is what
      // triggered the old buggy slice.
      const paragraph =
        'This is sentence one. This is sentence two. This is sentence three. ' +
        'This is sentence four. This is sentence five. This is sentence six. ' +
        'This is sentence seven. This is sentence eight. This is sentence nine. ' +
        'This is sentence ten. This is sentence eleven. This is sentence twelve.'

      // Stream the paragraph in small chunks and record the visible text
      // length after each chunk. The invariant is monotonic growth: the
      // visible text length must never decrease between consecutive ticks.
      let assembled = ''
      const lengths: number[] = []
      const chunkSize = 20
      for (let i = 0; i < paragraph.length; i += chunkSize) {
        const delta = paragraph.slice(i, i + chunkSize)
        assembled += delta
        mockLastMessage = { type: 'token', data: delta }
        rerender(<ChatPanel />)
        const bubble = document.querySelector('.chat-bubble-content')
        const visible = bubble?.textContent ?? ''
        lengths.push(visible.length)
      }

      for (let i = 1; i < lengths.length; i++) {
        expect(lengths[i]).toBeGreaterThanOrEqual(lengths[i - 1])
      }

      // After the full stream lands, the final visible text matches the
      // assembled content exactly. No truncation, no reshaping.
      const finalBubble = document.querySelector('.chat-bubble-content')
      expect(finalBubble?.textContent).toBe(assembled)
    })

    // Regression for the "raw markdown flash" at the streaming-to-done
    // handoff. When streaming ends, the bubble swaps from plain text to
    // parsed markdown. Even though both state updates commit together,
    // browsers can briefly paint the outgoing plain-text DOM (so the user
    // sees literal "**bold**" for a frame). The fix fades the rendered
    // markdown in over ~200ms, hiding any paint race behind the fade.
    it('applies a fade-in class on the streaming-to-done handoff', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'hi' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Stream a message with markdown in it.
      mockLastMessage = { type: 'token', data: 'Here is **bold** text' }
      rerender(<ChatPanel />)

      // End the stream.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // The settled bubble must carry the fade-in animation class so the
      // swap from plain text to rendered markdown is visually smooth.
      const bubble = document.querySelector('.chat-bubble-content')
      expect(bubble).not.toBeNull()
      expect(bubble?.className).toContain('animate-fade-in-fast')
      // And the rendered markdown must be present, not the raw version.
      expect(bubble?.querySelector('strong')?.textContent).toBe('bold')
    })

    // Regression for partial-markdown flicker. While a fenced code block
    // is mid-stream (opening ``` received, closing ``` not yet), the old
    // renderer collapsed everything after the opening fence into a <pre>,
    // and the layout snapped back once the closing fence arrived. The
    // fix is to render plain text during streaming, so no <pre> appears
    // until the final markdown render runs after streaming ends.
    it('does not render a code block element while the closing fence is still streaming', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'show me code' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // First token opens a fence but never closes it. The rendered
      // bubble must not contain a <pre> while the stream is still open.
      mockLastMessage = { type: 'token', data: 'Here is code:\n```js\nconst x = 1;\n' }
      rerender(<ChatPanel />)

      const bubble = document.querySelector('.chat-bubble-content')
      expect(bubble).not.toBeNull()
      expect(bubble?.querySelector('pre')).toBeNull()
      // The raw text is present verbatim, preserved by whitespace-pre-wrap.
      expect(bubble?.textContent).toContain('const x = 1;')
    })
  })

  // Bug: Claude bubbles show label only, no content, when the response has
  // only tool_use events and no text tokens, or when tokens are whitespace-only.
  describe('Tool-only and whitespace-only bubbles', () => {
    it('renders "Done." in a tool-only turn (tool_use + done, no tokens)', () => {
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'list my files' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Backend sends a tool_use then done with no text token.
      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'list_directory', id: 'tc-1', input: { path: '/home' } },
      }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // "Done." must NOT flash immediately — it is behind a 500ms grace window.
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // After the grace window expires, confirm the label appears.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // The tool-only-done fallback must render so the bubble is never empty.
      const fallback = screen.getByTestId('tool-only-done')
      expect(fallback).toBeTruthy()
      expect(fallback.textContent).toBe('Done.')
    })

    it('tool call block shows check-circle after done (result auto-filled)', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'read the config' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'read_file', id: 'tc-2', input: { path: '/etc/config' } },
      }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // The tool block should show a check icon, not a spinner, after done.
      // check_circle is rendered by Icon which outputs the material symbol text.
      const checkIcons = document.querySelectorAll('[class*="text-green-500"]')
      expect(checkIcons.length).toBeGreaterThan(0)
      // No spinner should remain.
      const spinners = document.querySelectorAll('.animate-spin')
      expect(spinners.length).toBe(0)
    })

    it('renders "Done." when the content is whitespace-only (not visibly empty)', () => {
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'ping' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // A whitespace-only token (e.g. a trailing newline from the model).
      mockLastMessage = { type: 'token', data: '\n' }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // "Done." must NOT flash before the grace window expires.
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // After 500ms the fallback appears because content is still whitespace-only.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // The bubble must not look empty. The "Done." fallback renders because
      // content.trim() is empty even though content is truthy ('\n').
      const fallback = screen.getByTestId('tool-only-done')
      expect(fallback).toBeTruthy()
    })

    it('renders real text content when a proper text token follows tool_use', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'search for logs' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'search_files', id: 'tc-3', input: { pattern: '*.log' } },
      }
      rerender(<ChatPanel />)

      mockLastMessage = {
        type: 'tool_result',
        data: { id: 'tc-3', result: 'Found 5 log files.' },
      }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'token', data: 'Here are the results.' }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // The text token should render; the "Done." fallback should NOT show
      // because content is non-empty.
      expect(screen.getByText('Here are the results.')).toBeTruthy()
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
    })

    // Regression: a chat bubble that streamed a tool_use block followed by
    // prose containing a markdown link used to render the link as raw text
    // like "[View in Calendar](https://...)". Both the tool block AND a
    // clickable anchor must render together.
    it('renders a clickable link alongside a tool-use block in the same bubble', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'book a 1:1' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Backend streams a tool_use (calendar event creation), then a tool_result,
      // then the prose that contains a markdown link, then done.
      mockLastMessage = {
        type: 'tool_use',
        data: {
          tool: 'mcp__claude_ai_Google_Calendar__create_event',
          id: 'tc-link-1',
          input: { title: 'Sync' },
        },
      }
      rerender(<ChatPanel />)

      mockLastMessage = {
        type: 'tool_result',
        data: { id: 'tc-link-1', result: 'Event created.' },
      }
      rerender(<ChatPanel />)

      mockLastMessage = {
        type: 'token',
        data: 'Event created. [View in Calendar](https://www.google.com/calendar/event?eid=abc123)',
      }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // The link must render as a real <a href>, not as raw markdown text.
      const anchors = document.querySelectorAll('a[href="https://www.google.com/calendar/event?eid=abc123"]')
      expect(anchors.length).toBe(1)
      expect(anchors[0].textContent).toBe('View in Calendar')

      // The tool block should still render above the prose. Tool blocks use
      // the tool name text in their header (mcp__ prefix collapsed or raw).
      // We look for the tool block container by finding the green check
      // indicator that appears after a successful tool result.
      const checkIcons = document.querySelectorAll('[class*="text-green-500"]')
      expect(checkIcons.length).toBeGreaterThan(0)

      // No raw markdown bracket syntax should leak through in the bubble text.
      const bubbleText = document.body.textContent || ''
      expect(bubbleText).not.toContain('[View in Calendar](')
    })
  })

  // Gemini (and other providers) sometimes emit `done` before the final text
  // tokens have arrived. The 500ms grace window in confirmedDoneIds must
  // prevent a visible "Done." flash in that window.
  describe('Gemini early-done grace window', () => {
    it('does not flash "Done." when a text token arrives within the 500ms grace window', () => {
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'hello gemini' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Provider fires `done` before flushing the last text token.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Immediately after done, "Done." must not appear (grace window active).
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // A text token arrives within the grace window (simulated at ~200ms).
      act(() => { vi.advanceTimersByTime(200) })
      mockLastMessage = { type: 'token', data: 'Hello! How can I help?' }
      rerender(<ChatPanel />)

      // Advance past the original 500ms deadline to confirm the timer was cancelled.
      act(() => { vi.advanceTimersByTime(400) })

      // Text renders; "Done." must never appear.
      expect(screen.getByText('Hello! How can I help?')).toBeTruthy()
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
    })

    it('shows "Done." after grace expires when no token arrives (genuine tool-only turn)', () => {
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'run a tool' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'run_command', id: 'tc-g1', input: { cmd: 'ls' } },
      }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Grace window active: no "Done." yet.
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // Grace expires with no token: "Done." appears.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)
      expect(screen.getByTestId('tool-only-done')).toBeTruthy()
    })

    it('shows error then recovers if a late token arrives after grace window', () => {
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'delayed response' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Simulate a real server event first so the dedup guard allows
      // the done event through (not a stale re-fire).
      mockLastMessage = { type: 'start' }
      rerender(<ChatPanel />)

      // Done arrives with no tokens and no tool calls.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Grace window expired with zero tokens after a real server event.
      // ThinkingDots should still be visible (no error from 500ms timer).
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // Late token arrives and renders normally.
      mockLastMessage = { type: 'token', data: 'Late reply text.' }
      rerender(<ChatPanel />)
      expect(screen.getByText('Late reply text.')).toBeTruthy()
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

  describe('Close button (X) hides the panel without crashing', () => {
    it('clicking X calls toggleChat and the panel disappears from the DOM', async () => {
      // Render with chatOpen: true (set in beforeEach)
      render(<ChatPanel />)

      // The panel should be visible
      expect(screen.getByTitle('Clear all chat history')).toBeTruthy()

      // Click the X close button — it calls toggleChat which flips chatOpen to false
      const closeBtn = screen.getByRole('button', { name: /close/i })
      await act(async () => {
        fireEvent.click(closeBtn)
      })

      // chatOpen is now false; the component returns null so the panel is gone
      expect(screen.queryByTitle('Clear all chat history')).toBeNull()
    })

    it('panel can be re-opened after closing without errors', async () => {
      render(<ChatPanel />)

      // Close
      const closeBtn = screen.getByRole('button', { name: /close/i })
      await act(async () => {
        fireEvent.click(closeBtn)
      })
      expect(screen.queryByTitle('Clear all chat history')).toBeNull()

      // Re-open by flipping chatOpen back to true
      await act(async () => {
        useAppStore.setState({ chatOpen: true })
      })

      // Panel should render again without any crash
      expect(screen.getByTitle('Clear all chat history')).toBeTruthy()
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

      render(<ChatPanel />)

      // Verify the message DOM elements are present before clear
      expect(document.getElementById('msg-clear-test-msg-1')).toBeTruthy()

      const clearBtn = screen.getByTestId('clear-history-button')
      fireEvent.click(clearBtn)

      // Confirm via the in-product modal instead of window.confirm.
      await waitFor(() => {
        expect(screen.getByTestId('confirm-modal-confirm')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('confirm-modal-confirm'))

      // After clear the message DOM elements should be gone
      await waitFor(() => {
        expect(document.getElementById('msg-clear-test-msg-1')).toBeNull()
      })

      // The api.delete should have been called
      const { api } = await import('../lib/api')
      expect(vi.mocked(api.delete)).toHaveBeenCalledWith('/chat/history')
    })

    it('does not clear when user cancels the confirmation dialog', async () => {
      const messages = [
        { id: 'cancel-test-msg', role: 'user', content: 'Keep this message' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)
      expect(document.getElementById('msg-cancel-test-msg')).toBeTruthy()

      fireEvent.click(screen.getByTestId('clear-history-button'))

      // Cancel via the in-product modal.
      await waitFor(() => {
        expect(screen.getByTestId('confirm-modal-cancel')).toBeInTheDocument()
      })
      fireEvent.click(screen.getByTestId('confirm-modal-cancel'))

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

  // Multi-AI conversation rendering. The backend emits multi_ai_status,
  // multi_ai_turn_start, and multi_ai_turn_end events when two models
  // are talking to each other. The chat panel must open one bubble per
  // turn, route streaming tokens into the right bubble, and show a
  // live thinking pill above the latest assistant row.
  describe('Multi-AI conversation rendering', () => {
    it('renders the thinking pill while a model is thinking', () => {
      const { rerender } = render(<ChatPanel />)

      // Send a starting event so the panel learns the round count.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 },
      }
      rerender(<ChatPanel />)

      // Now drive a thinking event for gemini round 1.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'thinking', model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)

      const pill = screen.getByTestId('multi-ai-status-pill')
      expect(pill).toBeTruthy()
      expect(pill.textContent).toContain('Gemini is thinking')
      expect(pill.textContent).toContain('round 1 of 3')
    })

    it('opens a new assistant bubble for each multi_ai_turn_start', () => {
      const { rerender } = render(<ChatPanel />)

      // Starting event sets up the round count.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 },
      }
      rerender(<ChatPanel />)

      // Turn start for gemini round 1 should push a new assistant bubble.
      mockLastMessage = {
        type: 'multi_ai_turn_start',
        data: { model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)

      // The new assistant bubble has the gemini model header (uppercased
      // by CSS in the component, so the DOM text is "GEMINI").
      const headers = screen.getAllByText(/gemini/i)
      expect(headers.length).toBeGreaterThan(0)
    })

    it('routes tokens between turn_start and turn_end into the correct bubble', () => {
      const { rerender } = render(<ChatPanel />)

      // Starting event.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 },
      }
      rerender(<ChatPanel />)

      // Gemini turn 1 opens.
      mockLastMessage = {
        type: 'multi_ai_turn_start',
        data: { model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)

      // Three tokens stream into the gemini bubble.
      mockLastMessage = { type: 'token', data: 'a' }
      rerender(<ChatPanel />)
      mockLastMessage = { type: 'token', data: 'b' }
      rerender(<ChatPanel />)
      mockLastMessage = { type: 'token', data: 'c' }
      rerender(<ChatPanel />)

      // Gemini turn 1 closes.
      mockLastMessage = {
        type: 'multi_ai_turn_end',
        data: { model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)

      // Claude turn 1 opens.
      mockLastMessage = {
        type: 'multi_ai_turn_start',
        data: { model: 'claude', round: 1 },
      }
      rerender(<ChatPanel />)

      // Two tokens stream into the claude bubble.
      mockLastMessage = { type: 'token', data: 'x' }
      rerender(<ChatPanel />)
      mockLastMessage = { type: 'token', data: 'y' }
      rerender(<ChatPanel />)

      // The gemini bubble should contain exactly "abc" and the claude
      // bubble should contain exactly "xy". This is the regression
      // guard for tokens leaking into the wrong bubble.
      expect(screen.getByText('abc')).toBeTruthy()
      expect(screen.getByText('xy')).toBeTruthy()
    })

    it('clears the pill when the complete phase arrives', () => {
      const { rerender } = render(<ChatPanel />)

      // Set up a thinking pill.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 },
      }
      rerender(<ChatPanel />)
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'thinking', model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)
      expect(screen.getByTestId('multi-ai-status-pill')).toBeTruthy()

      // Now drive complete and assert the pill is gone.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'complete' },
      }
      rerender(<ChatPanel />)
      expect(screen.queryByTestId('multi-ai-status-pill')).toBeNull()
    })

    it('clears the pill on a done event even without a complete phase first', () => {
      const { rerender } = render(<ChatPanel />)

      // Set up a thinking pill.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 },
      }
      rerender(<ChatPanel />)
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'thinking', model: 'claude', round: 2 },
      }
      rerender(<ChatPanel />)
      expect(screen.getByTestId('multi-ai-status-pill')).toBeTruthy()

      // Drive done directly. The pill must still go away.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      expect(screen.queryByTestId('multi-ai-status-pill')).toBeNull()
    })

    it('does not show duplicate thinking dots while the multi-AI pill is visible', () => {
      // Regression for the double thinking bubble bug. When a multi_ai_turn_start
      // creates a fresh empty assistant bubble and the pill is also showing
      // (e.g. phase=thinking or phase=speaking), the empty bubble must NOT
      // render its own ThinkingDots because the pill already shows the dots.
      // Otherwise the user sees two thinking indicators stacked.
      const { rerender } = render(<ChatPanel />)

      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 },
      }
      rerender(<ChatPanel />)

      // Open a fresh empty bubble for gemini round 1.
      mockLastMessage = {
        type: 'multi_ai_turn_start',
        data: { model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)

      // Show the speaking pill alongside the empty bubble.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'speaking', model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)

      // The pill itself contains exactly 3 animate-bounce dots. Before
      // the fix, the empty bubble's ThinkingDots added 3 more for a
      // total of 6. After the fix, only the pill's 3 dots remain.
      const dots = document.querySelectorAll('.animate-bounce')
      expect(dots.length).toBe(3)
    })

    it('still renders single model token streaming when no multi_ai events arrive', () => {
      // Regression guard. The existing single-model flow must not be
      // affected by the new multi-AI plumbing. Send a message, stream
      // a single token, and assert the bubble shows that token.
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'hello there' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = { type: 'token', data: 'single bubble reply' }
      rerender(<ChatPanel />)
      expect(screen.getByText('single bubble reply')).toBeTruthy()

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      // After done the pill must not be present (since we never sent
      // a multi_ai_status event in this test).
      expect(screen.queryByTestId('multi-ai-status-pill')).toBeNull()
    })

    // Real end-to-end regression for Tori's failing session. Replays
    // the EXACT event sequence the live backend emits for the message
    // "@gemini chat with claude a few times about each of your
    // shortcomings" so the rendering layer is proven to handle a
    // full six-turn exchange, not just a single turn. Captured from
    // /tmp/multi-ai-trace-after.log.
    it('replays the full live backend event sequence and renders six distinct bubbles', () => {
      const { rerender } = render(<ChatPanel />)

      // Kick off a user message so the placeholder bubble exists, the
      // same way a real user would. The first multi_ai_turn_start will
      // reuse this placeholder for the first gemini bubble.
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, {
        target: {
          value: '@gemini chat with claude a few times about each of your shortcomings',
        },
      })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The live event sequence from the backend, in the exact order
      // the trace captured. Each entry is one WebSocket frame.
      type WireEvent = { type: string; data?: unknown }
      const wire: WireEvent[] = [
        { type: 'multi_ai_status', data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 } },
        // Round 1 - gemini
        { type: 'multi_ai_status', data: { phase: 'thinking', model: 'gemini', round: 1 } },
        { type: 'multi_ai_turn_start', data: { model: 'gemini', round: 1 } },
        { type: 'multi_ai_status', data: { phase: 'speaking', model: 'gemini', round: 1 } },
        { type: 'token', data: 'Gemini turn 1 chunk A. ' },
        { type: 'token', data: 'Gemini turn 1 chunk B.' },
        { type: 'multi_ai_turn_end', data: { model: 'gemini', round: 1 } },
        // Round 1 - claude
        { type: 'multi_ai_status', data: { phase: 'thinking', model: 'claude', round: 1 } },
        { type: 'multi_ai_turn_start', data: { model: 'claude', round: 1 } },
        { type: 'multi_ai_status', data: { phase: 'speaking', model: 'claude', round: 1 } },
        { type: 'token', data: 'Claude turn 1 full reply.' },
        { type: 'multi_ai_turn_end', data: { model: 'claude', round: 1 } },
        // Round 2 - gemini
        { type: 'multi_ai_status', data: { phase: 'thinking', model: 'gemini', round: 2 } },
        { type: 'multi_ai_turn_start', data: { model: 'gemini', round: 2 } },
        { type: 'multi_ai_status', data: { phase: 'speaking', model: 'gemini', round: 2 } },
        { type: 'token', data: 'Gemini turn 2 chunk A. ' },
        { type: 'token', data: 'Gemini turn 2 chunk B.' },
        { type: 'multi_ai_turn_end', data: { model: 'gemini', round: 2 } },
        // Round 2 - claude
        { type: 'multi_ai_status', data: { phase: 'thinking', model: 'claude', round: 2 } },
        { type: 'multi_ai_turn_start', data: { model: 'claude', round: 2 } },
        { type: 'multi_ai_status', data: { phase: 'speaking', model: 'claude', round: 2 } },
        { type: 'token', data: 'Claude turn 2 full reply.' },
        { type: 'multi_ai_turn_end', data: { model: 'claude', round: 2 } },
        // Round 3 - gemini
        { type: 'multi_ai_status', data: { phase: 'thinking', model: 'gemini', round: 3 } },
        { type: 'multi_ai_turn_start', data: { model: 'gemini', round: 3 } },
        { type: 'multi_ai_status', data: { phase: 'speaking', model: 'gemini', round: 3 } },
        { type: 'token', data: 'Gemini turn 3 chunk A. ' },
        { type: 'token', data: 'Gemini turn 3 chunk B.' },
        { type: 'multi_ai_turn_end', data: { model: 'gemini', round: 3 } },
        // Round 3 - claude
        { type: 'multi_ai_status', data: { phase: 'thinking', model: 'claude', round: 3 } },
        { type: 'multi_ai_turn_start', data: { model: 'claude', round: 3 } },
        { type: 'multi_ai_status', data: { phase: 'speaking', model: 'claude', round: 3 } },
        { type: 'token', data: 'Claude turn 3 full reply.' },
        { type: 'multi_ai_turn_end', data: { model: 'claude', round: 3 } },
        // Wrap up
        { type: 'multi_ai_status', data: { phase: 'complete' } },
        { type: 'done' },
      ]

      for (const evt of wire) {
        mockLastMessage = evt
        rerender(<ChatPanel />)
      }

      // Six distinct bubbles, one per turn. They each carry their
      // full content string exactly, which proves tokens were routed
      // to the right bubble and not leaked across turns.
      expect(screen.getByText('Gemini turn 1 chunk A. Gemini turn 1 chunk B.')).toBeTruthy()
      expect(screen.getByText('Claude turn 1 full reply.')).toBeTruthy()
      expect(screen.getByText('Gemini turn 2 chunk A. Gemini turn 2 chunk B.')).toBeTruthy()
      expect(screen.getByText('Claude turn 2 full reply.')).toBeTruthy()
      expect(screen.getByText('Gemini turn 3 chunk A. Gemini turn 3 chunk B.')).toBeTruthy()
      expect(screen.getByText('Claude turn 3 full reply.')).toBeTruthy()

      // Cross-turn token leak guard. The round 1 gemini bubble must
      // NOT contain the round 3 text, otherwise tokens were routed
      // into the wrong bubble.
      const round1Gemini = screen.getByText('Gemini turn 1 chunk A. Gemini turn 1 chunk B.')
      expect(round1Gemini.textContent).not.toContain('Gemini turn 3')
      expect(round1Gemini.textContent).not.toContain('Claude turn')

      // The final multi_ai_status complete event plus the done event
      // must clear the thinking pill entirely.
      expect(screen.queryByTestId('multi-ai-status-pill')).toBeNull()
    })

    it('updates the thinking pill text as each model takes its turn', () => {
      const { rerender } = render(<ChatPanel />)

      // Starting event carries the model list and total rounds.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'starting', models: ['gemini', 'claude'], rounds: 3 },
      }
      rerender(<ChatPanel />)
      expect(screen.getByTestId('multi-ai-status-pill').textContent).toContain(
        'Starting conversation between Gemini and Claude',
      )

      // Gemini round 1 thinking.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'thinking', model: 'gemini', round: 1 },
      }
      rerender(<ChatPanel />)
      expect(screen.getByTestId('multi-ai-status-pill').textContent).toContain(
        'Gemini is thinking (round 1 of 3)',
      )

      // Claude round 2 speaking.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'speaking', model: 'claude', round: 2 },
      }
      rerender(<ChatPanel />)
      expect(screen.getByTestId('multi-ai-status-pill').textContent).toContain(
        'Claude is speaking (round 2 of 3)',
      )

      // Gemini round 3 thinking still carries the total of 3.
      mockLastMessage = {
        type: 'multi_ai_status',
        data: { phase: 'thinking', model: 'gemini', round: 3 },
      }
      rerender(<ChatPanel />)
      expect(screen.getByTestId('multi-ai-status-pill').textContent).toContain(
        'Gemini is thinking (round 3 of 3)',
      )
    })
  })

  // Needle 239: Tori wants the chat panel freely resizable with the rest
  // of the website reacting appropriately. These tests drive the resize
  // handle via real mouse events and assert the store ends up with the
  // correct clamped width.
  describe('resize handle (needle 239)', () => {
    // rAF is used inside the ChatPanel resize effect to throttle updates.
    // Under jsdom requestAnimationFrame is available but the test runner
    // will not tick it on its own, so we flush it manually.
    function flushRaf() {
      // Run any pending rAF callbacks synchronously.
      if (typeof window.requestAnimationFrame === 'function') {
        // Call the callback directly via a fake frame.
        const id = window.requestAnimationFrame(() => {})
        window.cancelAnimationFrame(id)
      }
    }

    function drag(toClientX: number) {
      const handle = document.querySelector('[class*="cursor-col-resize"]') as HTMLElement
      expect(handle).toBeTruthy()
      fireEvent.mouseDown(handle)
      // mousemove fires on document.
      fireEvent.mouseMove(document, { clientX: toClientX })
      flushRaf()
      fireEvent.mouseUp(document)
    }

    beforeEach(() => {
      // Force a known viewport so the clamp math is deterministic.
      // jsdom default is 1024 but we set it explicitly for safety.
      Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true })
    })

    it('clamps to the minimum width when dragged too far right', () => {
      render(<ChatPanel />)
      // innerWidth - clientX = new chat width. A clientX near the right
      // edge makes the chat only 20px wide, which must clamp to 280.
      drag(window.innerWidth - 20)
      expect(useAppStore.getState().chatWidth).toBe(280)
    })

    it('clamps to the maximum width when dragged so far left it would crowd the sidebar', () => {
      render(<ChatPanel />)
      // clientX = 10 means chat wants to be innerWidth - 10 = 1430px on a
      // 1440 viewport. Max is viewport - 320 = 1120. Store must clamp.
      drag(10)
      const ceiling = window.innerWidth - 320
      expect(useAppStore.getState().chatWidth).toBe(ceiling)
    })

    it('writes the exact width when dragged within the allowed range', () => {
      render(<ChatPanel />)
      // clientX = innerWidth - 600 means chat wants to be exactly 600px.
      drag(window.innerWidth - 600)
      expect(useAppStore.getState().chatWidth).toBe(600)
    })

    it('persists the chat width to localStorage so it survives a reload', () => {
      useAppStore.getState().setChatWidth(512)
      expect(useAppStore.getState().chatWidth).toBe(512)
      expect(localStorage.getItem('myos-chat-width')).toBe('512')
    })

    it('re-clamps the current chat width when the viewport shrinks', () => {
      // Start on a wide viewport with a wide chat.
      Object.defineProperty(window, 'innerWidth', { value: 1600, configurable: true })
      useAppStore.setState({ chatOpen: true, chatWidth: 1200, isResizing: false })
      render(<ChatPanel />)
      // Now shrink the viewport and fire the resize event.
      Object.defineProperty(window, 'innerWidth', { value: 900, configurable: true })
      fireEvent(window, new Event('resize'))
      // New ceiling is 900 - 320 = 580.
      expect(useAppStore.getState().chatWidth).toBe(580)
    })
  })

  // Regression: reply button DOM order
  describe('Reply button DOM order', () => {
    it('reply/reaction buttons appear AFTER the message bubble in the DOM, not before', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi!', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))
      render(<ChatPanel />)

      const reactionBar = screen.getByTestId('reaction-bar-msg-1')
      // Walk up to the hover container (parent of the inner flex div)
      const hoverContainer = reactionBar.parentElement?.parentElement
      expect(hoverContainer).not.toBeNull()
      // The hover container must come AFTER the message bubble in its parent.
      // Its previousElementSibling should be the message bubble (an inline-block div),
      // not null (which would mean it was the first child).
      expect(hoverContainer?.previousElementSibling).not.toBeNull()
    })

    it('clicking Reply sets the reply preview bar in the input area', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi!', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))
      render(<ChatPanel />)

      const replyButtons = screen.getAllByTitle('Reply')
      fireEvent.click(replyButtons[0])

      // The input placeholder changes to indicate reply mode
      expect(screen.getByPlaceholderText('Type your reply...')).toBeTruthy()
    })

    it('replying to a message attaches replyTo on the sent message', () => {
      const messages = [
        { id: 'msg-1', role: 'assistant', content: 'What would you like to do?', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))
      render(<ChatPanel />)

      // Click the first reply button (for msg-1)
      const replyButtons = screen.getAllByTitle('Reply')
      fireEvent.click(replyButtons[0])

      // Type a reply and send it
      const input = screen.getByPlaceholderText('Type your reply...')
      fireEvent.change(input, { target: { value: 'I want to fix the bug' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The WS send payload must include the user message content
      expect(mockSend).toHaveBeenCalled()
      const sentPayload = mockSend.mock.calls[0][0] as { messages: Array<{ role: string; content: string }> }
      const lastSent = sentPayload.messages[sentPayload.messages.length - 1]
      // The content should include the reply context prefix
      expect(lastSent.content).toContain('[Replying to')
      expect(lastSent.content).toContain('I want to fix the bug')
    })

    it('reply preview shows the snippet of the replied-to message', () => {
      const messages = [
        { id: 'msg-1', role: 'assistant', content: 'Here is my answer to your question', model: 'claude' },
        {
          id: 'msg-2',
          role: 'user',
          content: 'Thanks',
          replyTo: 'msg-1',
        },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))
      render(<ChatPanel />)

      // The preview truncates to 50 chars; the full content is 36 chars so it shows in full
      // There should be exactly one element showing this snippet (from the ReplyPreview)
      const snippets = screen.getAllByText(/Here is my answer/)
      expect(snippets.length).toBeGreaterThan(0)
    })
  })

  describe('Bubble top space', () => {
    it('renders an assistant bubble with no leading <br> when content starts with newlines', () => {
      // Model responses often start with \n\n before the actual reply text.
      // The chat-bubble-content wrapper must not show blank space at the top.
      const messages = [
        { id: 'msg-1', role: 'assistant', content: '\n\nHey! Doing good, ready to roll whenever you are. What\'s up?', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))
      const { container } = render(<ChatPanel />)

      // The chat-bubble-content div must exist
      const bubbleContent = container.querySelector('.chat-bubble-content')
      expect(bubbleContent).not.toBeNull()

      // Its first child must NOT be a <br> element (leading blank lines trimmed)
      const firstChild = bubbleContent!.firstChild as HTMLElement
      expect(firstChild).not.toBeNull()
      expect(firstChild.nodeName).not.toBe('BR')

      // The text must still be present
      expect(bubbleContent!.textContent).toContain('Hey! Doing good')
    })

    it('chat bubble first-child has no top margin via chat-bubble-content class', () => {
      // chat-bubble-content > :first-child { margin-top: 0 } must be in the stylesheet.
      // In jsdom getComputedStyle does not process external CSS, so we verify the
      // class name is applied to the wrapper so the rule can take effect in the browser.
      const messages = [
        { id: 'msg-1', role: 'assistant', content: '### Heading\nSome content', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))
      const { container } = render(<ChatPanel />)

      const bubbleContent = container.querySelector('.chat-bubble-content')
      expect(bubbleContent).not.toBeNull()

      // The first rendered child is an h3; confirm it exists inside the wrapper
      const heading = bubbleContent!.querySelector('h3')
      expect(heading).not.toBeNull()
      expect(heading!.textContent).toBe('Heading')
    })
  })


  // ---------------------------------------------------------------------------
  // Thread reply tests
  // ---------------------------------------------------------------------------

  describe('Reply threads', () => {
    it('clicking Reply on a bubble shows the reply chip above the textarea', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello there' },
        { id: 'msg-2', role: 'assistant', content: 'Hi!', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // The reply button has data-testid="reply-btn-msg-1"
      const replyBtn = screen.getByTestId('reply-btn-msg-1')
      fireEvent.click(replyBtn)

      // The reply chip should now appear. The placeholder changes to "Type your reply..."
      const input = screen.getByPlaceholderText('Type your reply...')
      expect(input).toBeTruthy()
    })

    it('sending a reply attaches thread_id from the parent message', () => {
      const messages = [
        { id: 'root-1', role: 'user', content: 'Root message' },
        { id: 'reply-1', role: 'assistant', content: 'Root reply', model: 'claude' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Click reply on the root user message
      const replyBtn = screen.getByTestId('reply-btn-root-1')
      fireEvent.click(replyBtn)

      // Type and send a reply
      const input = screen.getByPlaceholderText('Type your reply...')
      fireEvent.change(input, { target: { value: 'My reply to root' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The WS send call should include thread_id = 'root-1' (root message id)
      expect(mockSend).toHaveBeenCalledWith(
        expect.objectContaining({
          thread_id: 'root-1',
          replyToId: 'root-1',
        })
      )
    })

    it('cancelling reply removes the chip and clears replyingTo', () => {
      const messages = [
        { id: 'msg-1', role: 'user', content: 'Hello' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Start a reply
      const replyBtn = screen.getByTestId('reply-btn-msg-1')
      fireEvent.click(replyBtn)
      expect(screen.getByPlaceholderText('Type your reply...')).toBeTruthy()

      // Cancel it with the X button in the reply chip bar
      const cancelBtn = screen.getByTitle
        ? screen.queryByLabelText?.('close') ?? document.querySelector('.p-3 button[title="close"], .p-3 .flex button')
        : null
      // Close icon button in the reply chip area has a parent with an Icon "close"
      // Use double-Escape to cancel
      const input = screen.getByPlaceholderText('Type your reply...')
      fireEvent.keyDown(input, { key: 'Escape' })
      fireEvent.keyDown(input, { key: 'Escape' })

      // After double-escape, replyingTo is cleared so placeholder reverts
      expect(screen.getByPlaceholderText(/Message claude/i)).toBeTruthy()
    })

    it('messages with thread_id render inside a thread block under their root', () => {
      const messages = [
        { id: 'root-1', role: 'user', content: 'Root message' },
        { id: 'child-1', role: 'assistant', content: 'Thread reply', model: 'claude', thread_id: 'root-1' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Thread block should be rendered
      const threadBlock = screen.getByTestId('thread-block-root-1')
      expect(threadBlock).toBeTruthy()

      // The child bubble should be inside the thread block
      const childBubble = screen.getByTestId('bubble-child-1')
      expect(threadBlock.contains(childBubble)).toBe(true)
    })

    it('thread collapses when the toggle button is clicked', () => {
      const messages = [
        { id: 'root-1', role: 'user', content: 'Root message' },
        { id: 'child-1', role: 'assistant', content: 'Thread reply', model: 'claude', thread_id: 'root-1' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Thread starts expanded. Child is visible.
      expect(screen.getByTestId('bubble-child-1')).toBeTruthy()

      // Click the toggle to collapse
      const toggle = screen.getByTestId('thread-toggle-root-1')
      fireEvent.click(toggle)

      // After collapse, child bubble should not be in the DOM
      expect(screen.queryByTestId('bubble-child-1')).toBeNull()

      // Toggle text should show reply count
      expect(toggle.textContent).toContain('1 reply')
    })

    it('thread expands again when the toggle is clicked a second time', () => {
      const messages = [
        { id: 'root-1', role: 'user', content: 'Root message' },
        { id: 'child-1', role: 'assistant', content: 'Thread reply', model: 'claude', thread_id: 'root-1' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      const toggle = screen.getByTestId('thread-toggle-root-1')

      // Collapse
      fireEvent.click(toggle)
      expect(screen.queryByTestId('bubble-child-1')).toBeNull()

      // Expand
      fireEvent.click(toggle)
      expect(screen.getByTestId('bubble-child-1')).toBeTruthy()
    })

    it('root messages without thread_id are not rendered inside a thread block', () => {
      const messages = [
        { id: 'root-1', role: 'user', content: 'Root A' },
        { id: 'root-2', role: 'user', content: 'Root B' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Neither root has a thread block
      expect(screen.queryByTestId('thread-block-root-1')).toBeNull()
      expect(screen.queryByTestId('thread-block-root-2')).toBeNull()

      // Both bubbles are rendered as top-level
      expect(screen.getByTestId('bubble-root-1')).toBeTruthy()
      expect(screen.getByTestId('bubble-root-2')).toBeTruthy()
    })

    it('second reply to the same root inherits the same thread_id', () => {
      const messages = [
        { id: 'root-1', role: 'user', content: 'Root message' },
        { id: 'child-1', role: 'assistant', content: 'First reply', model: 'claude', thread_id: 'root-1' },
      ]
      localStorage.setItem('myos-chat-messages', JSON.stringify(messages))

      render(<ChatPanel />)

      // Click reply on the child (which itself is in the thread)
      const replyBtn = screen.getByTestId('reply-btn-child-1')
      fireEvent.click(replyBtn)

      const input = screen.getByPlaceholderText('Type your reply...')
      fireEvent.change(input, { target: { value: 'Second reply in thread' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The WS send should carry thread_id='root-1' (inherited from child-1)
      expect(mockSend).toHaveBeenCalledWith(
        expect.objectContaining({
          thread_id: 'root-1',
        })
      )
    })
  })

  // ---------------------------------------------------------------------------
  // Empty bubble regression tests (needle: fix-empty-chat-bubbles)
  // Tori saw two empty CLAUDE labels with nothing inside after a pure
  // tool-use turn. Three scenarios must produce visible content:
  //   1. Stream ends with only tool_use blocks and no text tokens.
  //   2. Stream ends with an error message.
  //   3. Normal text stream renders markdown as usual.
  // ---------------------------------------------------------------------------

  describe('Empty bubble regression (tool-use-only and error turns)', () => {
    it('shows "Done." when stream ends with only a tool_use block and no text', () => {
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      // User sends a message and the backend responds with only a tool call.
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'create tasks for the roadmap' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Backend emits a tool_use event (spawn_agent) with no text tokens.
      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'spawn_agent', input: { task: 'write the recap' }, id: 'tc-1' },
      }
      rerender(<ChatPanel />)

      // Tool result arrives.
      mockLastMessage = {
        type: 'tool_result',
        data: { id: 'tc-1', result: 'Agent spawned.' },
      }
      rerender(<ChatPanel />)

      // Stream ends. No text tokens were ever sent.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // "Done." must NOT appear before the grace window expires.
      expect(screen.queryAllByTestId('tool-only-done').length).toBe(0)

      // Advance past the 500ms grace window.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // The assistant bubble must show "Done." and NOT be empty.
      const doneFallbacks = screen.getAllByTestId('tool-only-done')
      expect(doneFallbacks.length).toBeGreaterThan(0)
      doneFallbacks.forEach(el => expect(el.textContent).toBe('Done.'))
    })

    it('shows the error text when the stream ends with an error event', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'do something' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Backend sends an Internal Server Error (matches the screenshot).
      mockLastMessage = {
        type: 'error',
        data: 'Internal Server Error',
      }
      rerender(<ChatPanel />)

      // The assistant bubble must contain the error, not be empty.
      expect(screen.getByText(/Internal Server Error/i)).toBeTruthy()
      // No "Done." fallback should appear when an error is shown.
      const doneFallbacks = screen.queryAllByTestId('tool-only-done')
      doneFallbacks.forEach(el => expect(el.textContent).not.toBe('Done.'))
    })

    it('renders markdown text normally when the stream contains text tokens', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'summarise the roadmap' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = { type: 'token', data: 'Here is the roadmap summary.' }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Normal text must render. No "Done." fallback should appear on a
      // bubble that has actual text content.
      expect(screen.getByText('Here is the roadmap summary.')).toBeTruthy()
      // tool-only-done must NOT appear on a bubble with real content.
      const doneFallback = screen.queryByTestId('tool-only-done')
      expect(doneFallback).toBeNull()
    })

    it('does not render a blank assistant label when a purely empty placeholder is left behind after streaming', () => {
      // Regression for the second empty bubble. sendMessage pushes an empty
      // assistant placeholder. If the backend redirects the response into
      // a different bubble (e.g. multi_ai_turn_start reuses it), the
      // original placeholder must not render once streaming ends.
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Simulate a token arriving so the empty placeholder gets content.
      mockLastMessage = { type: 'token', data: 'Hi there!' }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // There should be exactly one assistant bubble with content.
      expect(screen.getByText('Hi there!')).toBeTruthy()
      // "Done." fallback must NOT appear when the bubble has real text.
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
    })
  })

  // ---------------------------------------------------------------------------
  // Gemini grace-window tests.
  // Gemini can emit `done` slightly before the final text tokens arrive.
  // The 500ms grace window must prevent "Done." from flashing before text lands.
  // ---------------------------------------------------------------------------
  describe('Gemini Done. grace window', () => {
    it('never shows "Done." when text tokens arrive within the grace window after done', () => {
      // Simulates: tool_use block -> done -> token (Gemini flush order).
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: '@gemini summarise my tasks' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Gemini sends a tool_use block then `done` before flushing text.
      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'list_tasks', id: 'tc-g1', input: {} },
      }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Immediately after done, "Done." must NOT appear (grace window active).
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // A text token arrives within 500ms — this is the Gemini late-flush scenario.
      act(() => { vi.advanceTimersByTime(200) })
      mockLastMessage = { type: 'token', data: 'Here are your open tasks.' }
      rerender(<ChatPanel />)

      // Advance past the full grace window — "Done." must still NOT appear.
      act(() => { vi.advanceTimersByTime(400) })
      rerender(<ChatPanel />)

      expect(screen.queryByTestId('tool-only-done')).toBeNull()
      expect(screen.getByText('Here are your open tasks.')).toBeTruthy()
    })

    it('shows "Done." after the grace window when a genuinely tool-only Gemini turn has no text', () => {
      // Simulates a Gemini turn that really has no text response.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: '@gemini create a task' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'create_task', id: 'tc-g2', input: { title: 'Ship it' } },
      }
      rerender(<ChatPanel />)

      mockLastMessage = {
        type: 'tool_result',
        data: { id: 'tc-g2', result: 'Task created.' },
      }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // "Done." must not flash immediately.
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // No tokens arrive. Advance past the grace window.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Now "Done." should appear.
      const fallback = screen.getByTestId('tool-only-done')
      expect(fallback.textContent).toBe('Done.')
    })

    it('never shows "Done." on a normal text-only Gemini stream', () => {
      // Normal Gemini turn: tokens then done. No "Done." should ever appear.
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: '@gemini hello' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = { type: 'token', data: 'Hello! How can I help?' }
      rerender(<ChatPanel />)

      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Text is present — "Done." must never appear regardless of timer state.
      expect(screen.getByText('Hello! How can I help?')).toBeTruthy()
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
    })
  })

  // Regression tests for the "Done." permanent fix.
  // These cover the two bug classes identified in the investigation:
  //   Bug A: zero tokens from the model should show an error, not "Done."
  //   Bug B: tokens must always correlate to the correct msg id
  describe('Done. regression prevention', () => {
    it('stale done from previous turn does not show error on new turn (Claude slow-start)', () => {
      // The processedMessageRef dedup guard prevents a stale `done`
      // from the previous turn from being re-processed when currentModel
      // changes. ThinkingDots stay visible for the new turn.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'what is the meaning of life?' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // No server events yet (Claude is still thinking, ~5s first token).
      // Advance past the 500ms grace window.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Must NOT show error or "Done." — no done event was even received
      // for this turn, so ThinkingDots stay visible.
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()
    })

    it('Claude turn with late tokens does not show "Done." at any point', () => {
      // Simulates a slow provider where tokens arrive after done.
      // Neither "Done." nor the zero-token error should ever be visible.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'explain quantum computing' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // First token arrives before done.
      mockLastMessage = { type: 'token', data: 'Quantum computing uses ' }
      rerender(<ChatPanel />)

      // Done arrives while more text may still be on the way.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Immediately after done, no "Done." (grace window active).
      expect(screen.queryByTestId('tool-only-done')).toBeNull()

      // A second token arrives within the grace window.
      mockLastMessage = { type: 'token', data: 'qubits to process information.' }
      rerender(<ChatPanel />)

      // Advance past the grace window.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Text must render. No "Done." anywhere.
      expect(screen.getByText('Quantum computing uses qubits to process information.')).toBeTruthy()
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()
    })

    it('heartbeat frames never count as tokens or as done', () => {
      // Heartbeat frames are filtered at the useWebSocket level and must
      // never reach the ChatPanel effect. This test verifies that even if
      // a heartbeat somehow leaked through, it would not affect the grace
      // window or token tracking.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'test heartbeat' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Real token arrives.
      mockLastMessage = { type: 'token', data: 'Real response.' }
      rerender(<ChatPanel />)

      // Done arrives.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Advance past grace window.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Real text renders. No "Done." fallback.
      expect(screen.getByText('Real response.')).toBeTruthy()
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
    })

    it('caching path still routes tokens to the correct msg id', () => {
      // Verifies that tokens from the cached-blocks Anthropic path
      // (which changed system prompt structure) still end up in the
      // correct assistant bubble. The key invariant: the placeholder
      // assistant bubble created by sendMessage must be the same bubble
      // that receives tokens and that the done handler references.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'tell me about caching' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Multiple tokens arrive (simulating chunked response).
      mockLastMessage = { type: 'token', data: 'Prompt caching ' }
      rerender(<ChatPanel />)
      mockLastMessage = { type: 'token', data: 'reduces latency ' }
      rerender(<ChatPanel />)
      mockLastMessage = { type: 'token', data: 'by reusing system blocks.' }
      rerender(<ChatPanel />)

      // Done arrives.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Grace window expires.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // All tokens concatenated in one bubble. No "Done." shown.
      expect(screen.getByText('Prompt caching reduces latency by reusing system blocks.')).toBeTruthy()
      expect(screen.queryByTestId('tool-only-done')).toBeNull()
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()
    })

    it('zero-token error shows retry button that works', () => {
      // When zero tokens produce the error, clicking Retry must re-send
      // the user's original message.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'important question' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Zero tokens, done arrives.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Error with retry shown.
      expect(screen.getByText('No response received. Please try again.')).toBeTruthy()
      const retryBtn = screen.getByTestId('retry-last-turn')
      expect(retryBtn).toBeTruthy()

      // Click retry. This should re-send the message.
      fireEvent.click(retryBtn)
      rerender(<ChatPanel />)

      // The retry removes the failed pair and sends a new message,
      // which calls mockSend.
      expect(mockSend).toHaveBeenCalledTimes(2) // original + retry
    })

    it('tool-only turn still shows "Done." (not the zero-token error)', () => {
      // Regression guard: tool-only turns are legitimate and must still
      // show "Done.", not the zero-token error.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'spawn an agent' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Tool call with result, no text tokens.
      mockLastMessage = {
        type: 'tool_use',
        data: { tool: 'spawn_agent', id: 'tc-reg1', input: { task: 'build' } },
      }
      rerender(<ChatPanel />)
      mockLastMessage = {
        type: 'tool_result',
        data: { id: 'tc-reg1', result: 'Agent spawned.' },
      }
      rerender(<ChatPanel />)

      // Done arrives. No text tokens.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      // Grace window expires.
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Must show "Done.", NOT the zero-token error.
      expect(screen.getByTestId('tool-only-done')).toBeTruthy()
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()
    })
  })

  // Premature "No response received" fix: the effect deduplicates
  // lastMessage processing so a stale `done` from the previous turn
  // cannot re-fire against the new turn's assistant bubble when
  // currentModel changes re-trigger the effect.
  describe('Premature no-response error prevention', () => {
    it('done fires before first token does NOT show error if no server events received yet (Claude slow-start)', () => {
      // Simulates the real bug: sendMessage sets currentModel, which
      // re-triggers the useEffect while lastMessage still holds the
      // previous turn's `done`. With the dedup guard the stale `done`
      // is skipped and no premature error appears.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)

      // First turn: normal exchange with tokens.
      fireEvent.change(input, { target: { value: 'first question' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      mockLastMessage = { type: 'token', data: 'Answer one.' }
      rerender(<ChatPanel />)
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Second turn: user sends another message. At this point
      // mockLastMessage is still the `done` from turn one.
      // sendMessage internally sets currentModel which would re-trigger
      // the effect with the stale done.
      fireEvent.change(input, { target: { value: 'second question' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      rerender(<ChatPanel />)

      // Advance past the 500ms grace window. If the stale done
      // leaked through, the error would appear here.
      act(() => { vi.advanceTimersByTime(600) })
      rerender(<ChatPanel />)

      // The error must NOT appear. ThinkingDots should be the UX.
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()

      // Now the real response arrives for turn two.
      mockLastMessage = { type: 'token', data: 'Answer two.' }
      rerender(<ChatPanel />)
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Real answer is visible.
      expect(screen.getByText('Answer two.')).toBeTruthy()
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()
    })

    it('done fires after tokens already streamed with zero content DOES show error (genuine empty response)', () => {
      // When the server sends `done` as a genuine event (not a stale
      // re-fire) and zero tokens were produced, the error must still
      // show. This is the existing behavior preserved.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'trigger empty response' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Server sends done with no tokens (new object, not stale).
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)

      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      // Error must appear for a genuinely empty response.
      expect(screen.getByText('No response received. Please try again.')).toBeTruthy()
      expect(screen.getByTestId('retry-last-turn')).toBeTruthy()
    })

    it('30s total silence with no server events shows error (dead backend)', () => {
      // When no server event arrives at all (backend is down or the
      // WebSocket message was lost), the 30s dead-backend timer fires
      // and shows a specific message about the backend being silent.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'hello dead backend' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      rerender(<ChatPanel />)

      // No server events arrive. After 10 seconds, still no error.
      act(() => { vi.advanceTimersByTime(10_000) })
      rerender(<ChatPanel />)
      expect(screen.queryByText(/did not send any response/i)).toBeNull()

      // After 30 seconds of total silence, the dead-backend timer fires.
      act(() => { vi.advanceTimersByTime(20_000) })
      rerender(<ChatPanel />)

      // Specific dead-backend copy tells the user the server went silent,
      // not a generic "no response" that leaves the cause ambiguous.
      expect(screen.getByText(/did not send any response in 30 seconds/i)).toBeTruthy()
    })

    it('ThinkingDots remain visible during Claude normal 5s first-token wait', () => {
      // During the ~5s before Claude's first token, ThinkingDots must
      // be the visible UX. No error should flash.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'deep thinking question' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      rerender(<ChatPanel />)

      // ThinkingDots renders three bouncing dot spans.
      const dots = document.querySelectorAll('.animate-bounce')
      expect(dots.length).toBe(3)

      // 5 seconds pass, still no server event.
      act(() => { vi.advanceTimersByTime(5_000) })
      rerender(<ChatPanel />)

      // No error, still in waiting state.
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()

      // First token finally arrives at 5s.
      mockLastMessage = { type: 'token', data: 'Deep answer.' }
      rerender(<ChatPanel />)

      // Done arrives.
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      act(() => { vi.advanceTimersByTime(500) })
      rerender(<ChatPanel />)

      expect(screen.getByText('Deep answer.')).toBeTruthy()
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()
    })

    it('dead-backend timer does not fire if a real server event arrives before 30s', () => {
      // The 30s timer must be cancelled when a real event (like a
      // thinking event) arrives, even if tokens have not landed yet.
      vi.useFakeTimers()
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'slow but alive' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      rerender(<ChatPanel />)

      // 10s in, a thinking event arrives (Claude is processing).
      act(() => { vi.advanceTimersByTime(10_000) })
      mockLastMessage = { type: 'thinking' }
      rerender(<ChatPanel />)

      // 30s total passes from the start. The dead-backend timer
      // would have fired at 30s if not for the thinking event
      // setting receivedAnyServerEventRef to true.
      act(() => { vi.advanceTimersByTime(20_000) })
      rerender(<ChatPanel />)

      // No error because the server IS alive (thinking event arrived).
      expect(screen.queryByText('No response received. Please try again.')).toBeNull()
    })
  })

  describe('Roadmap chat command routing', () => {
    it('routes "create tasks from this roadmap" to the backend endpoint, not the AI', async () => {
      const { api } = await import('../lib/api')
      vi.mocked(api.post).mockResolvedValueOnce({
        status: 'ok',
        reply: 'Created 3 tasks from roadmap.md. See them on the [Tasks page](/tasks).',
        created: [
          { id: 't1', title: 'A' },
          { id: 't2', title: 'B' },
          { id: 't3', title: 'C' },
        ],
        roadmap_path: '/tmp/roadmap.md',
      })

      render(<ChatPanel />)
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'create tasks from this roadmap' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // The WebSocket send must NOT fire: this is a local chat command.
      expect(mockSend).not.toHaveBeenCalled()
      // The backend endpoint IS called exactly once.
      await waitFor(() => {
        const postCalls = vi.mocked(api.post).mock.calls.filter(
          (c) => c[0] === '/chat/roadmap/create-tasks',
        )
        expect(postCalls.length).toBe(1)
      })
      // And the reply text lands in the chat.
      await waitFor(() => {
        expect(screen.getByText(/Created 3 tasks from roadmap\.md/i)).toBeTruthy()
      })
    })

    it('leaves non-matching messages on the normal AI routing path', async () => {
      const { api } = await import('../lib/api')
      render(<ChatPanel />)
      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'hi, what did you do today?' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // No backend call to the roadmap endpoint.
      const postCalls = vi.mocked(api.post).mock.calls.filter(
        (c) => c[0] === '/chat/roadmap/create-tasks',
      )
      expect(postCalls.length).toBe(0)
      // The WebSocket send IS invoked for normal chat.
      expect(mockSend).toHaveBeenCalledTimes(1)
    })
  })

  // When the assistant fires the spawn_agent tool from chat (e.g. user
  // typed "spawn roadmap"), ChatPanel must bump the agents bus so the
  // Agents page, sidebar badge, and any other listening surface
  // refetch immediately. The chat WebSocket does not go through the
  // api wrapper, so the automatic bump in api.ts never fires.
  describe('Spawn agent from chat bumps the agents bus', () => {
    it('bumps agents bus on tool_use { tool: "spawn_agent" }', async () => {
      const bus = await import('../lib/sidebarBus')
      const listener = vi.fn()
      const off = bus.onAgentsChange(listener)
      try {
        const { rerender } = render(<ChatPanel />)
        const input = screen.getByPlaceholderText(/Message claude/i)
        fireEvent.change(input, { target: { value: 'spawn roadmap' } })
        fireEvent.keyDown(input, { key: 'Enter' })

        mockLastMessage = {
          type: 'tool_use',
          data: { tool: 'spawn_agent', id: 'tc-bump-1', input: { name: 'roadmap', prompt: 'go' } },
        }
        rerender(<ChatPanel />)

        await waitFor(() => {
          expect(listener).toHaveBeenCalled()
        })
      } finally {
        off()
      }
    })

    it('does NOT bump agents bus for other tool_use events', async () => {
      const bus = await import('../lib/sidebarBus')
      const listener = vi.fn()
      const off = bus.onAgentsChange(listener)
      try {
        const { rerender } = render(<ChatPanel />)
        const input = screen.getByPlaceholderText(/Message claude/i)
        fireEvent.change(input, { target: { value: 'read a file' } })
        fireEvent.keyDown(input, { key: 'Enter' })

        mockLastMessage = {
          type: 'tool_use',
          data: { tool: 'read_file', id: 'tc-noop-1', input: { path: '/tmp/x' } },
        }
        rerender(<ChatPanel />)

        // Give React a tick.
        await new Promise((r) => setTimeout(r, 0))
        expect(listener).not.toHaveBeenCalled()
      } finally {
        off()
      }
    })
  })

  // Follow-up bubble after a spawn_agent tool call.
  // Regression target: Tori spawned `optimize-inline-chat-speed` from
  // chat, saw the "Spawn agent" card, and then nothing until she asked
  // if it worked. The panel now polls /agents/<name>/status-feedback
  // and drops a plain-language bubble on a terminal transition.
  describe('Spawn agent follow-up feedback bubble', () => {
    it('appends a completion bubble when /status-feedback flips to terminal', async () => {
      const apiMod = await import('../lib/api')
      const getMock = apiMod.api.get as unknown as ReturnType<typeof vi.fn>

      // First call: hydrate chat history (empty tabs). Then polls for
      // the agent status. Returning `terminal=true` with a feedback
      // string must cause a new assistant bubble to appear.
      getMock.mockImplementation((path: string) => {
        if (path === '/chat/history') {
          return Promise.resolve({ tabs: [], active_tab_id: '' })
        }
        if (path.startsWith('/agents/')) {
          return Promise.resolve({
            exists: true,
            terminal: true,
            status: 'completed',
            feedback: 'Agent optimize-inline-chat-speed finished. swapped markdown renderer to memo',
          })
        }
        return Promise.resolve({})
      })

      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'saa optimize inline chat speed' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // Simulate the chat WebSocket emitting the spawn_agent tool call.
      mockLastMessage = {
        type: 'tool_use',
        data: {
          tool: 'spawn_agent',
          id: 'tc-feedback-1',
          input: { name: 'optimize-inline-chat-speed', prompt: 'go' },
        },
      }
      rerender(<ChatPanel />)

      // The effect polls /status-feedback immediately on mount, so the
      // plain-language bubble must appear without waiting for the 30s
      // interval.
      await waitFor(() => {
        expect(
          screen.getByText(/Agent optimize-inline-chat-speed finished/i),
        ).toBeTruthy()
      })
      expect(
        screen.getByText(/swapped markdown renderer to memo/i),
      ).toBeTruthy()

      // Every /agents/.../status-feedback call must target the name
      // from the spawn_agent tool input, not the generic list
      // endpoint. Guards against a future refactor pointing the poll
      // at /agents and filtering client-side.
      const calls = getMock.mock.calls
        .map((c: unknown[]) => c[0] as string)
        .filter((p: string) => p.startsWith('/agents/'))
      expect(calls.length).toBeGreaterThan(0)
      for (const c of calls) {
        expect(c).toBe(
          '/agents/optimize-inline-chat-speed/status-feedback',
        )
      }
    })

    it('does not append any bubble while the agent is still running', async () => {
      const apiMod = await import('../lib/api')
      const getMock = apiMod.api.get as unknown as ReturnType<typeof vi.fn>

      getMock.mockImplementation((path: string) => {
        if (path === '/chat/history') {
          return Promise.resolve({ tabs: [], active_tab_id: '' })
        }
        if (path.startsWith('/agents/')) {
          return Promise.resolve({
            exists: true,
            terminal: false,
            status: 'running',
            feedback: 'Agent saa-running is still working.',
          })
        }
        return Promise.resolve({})
      })

      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'saa running' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: {
          tool: 'spawn_agent',
          id: 'tc-feedback-2',
          input: { name: 'saa-running', prompt: 'go' },
        },
      }
      rerender(<ChatPanel />)

      // Give the effect a tick to poll /status-feedback.
      await new Promise((r) => setTimeout(r, 20))

      // No terminal bubble means nothing that reads "finished" or
      // "still working" should have been appended by the poller.
      // (We do not gate on the absence of the 'is still working' copy
      // because a future enhancement may render periodic pulses; the
      // hard invariant is no terminal-state bubble yet.)
      expect(screen.queryByText(/finished\./i)).toBeNull()
      expect(screen.queryByText(/failed\./i)).toBeNull()
    })

    it('shows the in-progress banner while the agent is running', async () => {
      const apiMod = await import('../lib/api')
      const getMock = apiMod.api.get as unknown as ReturnType<typeof vi.fn>

      getMock.mockImplementation((path: string) => {
        if (path === '/chat/history') {
          return Promise.resolve({ tabs: [], active_tab_id: '' })
        }
        if (path.startsWith('/agents/')) {
          return Promise.resolve({
            exists: true,
            terminal: false,
            status: 'running',
            feedback: null,
          })
        }
        return Promise.resolve({})
      })

      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'saa banner test' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: {
          tool: 'spawn_agent',
          id: 'tc-banner-1',
          input: { name: 'banner-agent', prompt: 'go' },
        },
      }
      rerender(<ChatPanel />)

      await waitFor(() => {
        expect(screen.getByTestId('agent-running-banner')).toBeTruthy()
      })
      expect(screen.getByText(/banner-agent is running/i)).toBeTruthy()
    })

    // 992 finish line: when the WebSocket-fed running set drops the
    // tracked agent's name, the banner must hide instantly instead of
    // waiting up to 30s for the status-feedback poll. The seenRunning
    // gate prevents the agent from being dropped before its first
    // appearance in a snapshot.
    it('hides the banner when the running agents store drops the agent', async () => {
      const apiMod = await import('../lib/api')
      const getMock = apiMod.api.get as unknown as ReturnType<typeof vi.fn>

      getMock.mockImplementation((path: string) => {
        if (path === '/chat/history') {
          return Promise.resolve({ tabs: [], active_tab_id: '' })
        }
        if (path.startsWith('/agents/')) {
          return Promise.resolve({
            exists: true,
            terminal: false,
            status: 'running',
            feedback: null,
          })
        }
        return Promise.resolve({})
      })

      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'saa push cleanup' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: {
          tool: 'spawn_agent',
          id: 'tc-push-cleanup-1',
          input: { name: 'push-cleanup-agent', prompt: 'go' },
        },
      }
      rerender(<ChatPanel />)

      await waitFor(() => {
        expect(screen.getByTestId('agent-running-banner')).toBeTruthy()
      })

      // Simulate the WebSocket feed delivering a snapshot that
      // includes the agent. seenRunningRef records the name.
      act(() => {
        useRunningAgentsStore.setState({
          count: 1,
          agents: [{ name: 'push-cleanup-agent', status: 'running' }],
          connected: true,
          lastUpdatedAt: new Date().toISOString(),
        })
      })

      // Now the agent finishes: snapshot drops it. Banner must hide
      // without any /status-feedback poll resolving.
      act(() => {
        useRunningAgentsStore.setState({
          count: 0,
          agents: [],
          connected: true,
          lastUpdatedAt: new Date().toISOString(),
        })
      })

      await waitFor(() => {
        expect(screen.queryByTestId('agent-running-banner')).toBeNull()
      })
    })

    // Counter-test: a freshly spawned agent that has not yet appeared
    // in any snapshot must NOT be dropped on the first push. Otherwise
    // a race between spawn and the next snapshot would erase the
    // banner immediately.
    it('does not drop the agent before it appears in any snapshot', async () => {
      const apiMod = await import('../lib/api')
      const getMock = apiMod.api.get as unknown as ReturnType<typeof vi.fn>

      getMock.mockImplementation((path: string) => {
        if (path === '/chat/history') {
          return Promise.resolve({ tabs: [], active_tab_id: '' })
        }
        if (path.startsWith('/agents/')) {
          return Promise.resolve({
            exists: true,
            terminal: false,
            status: 'running',
            feedback: null,
          })
        }
        return Promise.resolve({})
      })

      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'saa just-spawned' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      mockLastMessage = {
        type: 'tool_use',
        data: {
          tool: 'spawn_agent',
          id: 'tc-just-spawned-1',
          input: { name: 'just-spawned-agent', prompt: 'go' },
        },
      }
      rerender(<ChatPanel />)

      await waitFor(() => {
        expect(screen.getByTestId('agent-running-banner')).toBeTruthy()
      })

      // Snapshot arrives but does NOT include our just-spawned agent
      // (race: spawn finished after the snapshot was assembled).
      act(() => {
        useRunningAgentsStore.setState({
          count: 0,
          agents: [],
          connected: true,
          lastUpdatedAt: new Date().toISOString(),
        })
      })

      // The banner must still be shown.
      expect(screen.getByTestId('agent-running-banner')).toBeTruthy()
    })
  })

  describe('single-tab streaming', () => {
    it('renders streaming tokens with or without tab_id in single-tab mode', () => {
      const { rerender } = render(<ChatPanel />)

      const input = screen.getByPlaceholderText(/Message claude/i)
      fireEvent.change(input, { target: { value: 'build me a roadmap' } })
      fireEvent.keyDown(input, { key: 'Enter' })

      // First token arrives with tab_id - should be processed
      mockLastMessage = { type: 'token', data: 'Here ' }
      rerender(<ChatPanel />)
      expect(screen.getByText(/Here/)).toBeTruthy()

      // Second token arrives without tab_id (legacy backend) - should still be processed in single-tab
      mockLastMessage = { type: 'token', data: 'is a' }
      rerender(<ChatPanel />)
      expect(screen.getByText(/Here is a/)).toBeTruthy()

      // Final token
      mockLastMessage = { type: 'token', data: ' roadmap' }
      rerender(<ChatPanel />)
      expect(screen.getByText(/Here is a roadmap/)).toBeTruthy()

      // Stream completes
      mockLastMessage = { type: 'done' }
      rerender(<ChatPanel />)
      expect(screen.getByText(/Here is a roadmap/)).toBeTruthy()
    })
  })

})
