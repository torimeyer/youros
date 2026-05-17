/**
 * Regression test for →900.
 *
 * The chat panel must never stream raw tool-call input JSON into the
 * visible assistant text body. Anthropic emits `input_json_delta`
 * fragments while a tool_use block is still assembling its arguments;
 * the backend forwards each fragment as a `tool_use_delta` websocket
 * event keyed by tool_use id. This test mocks that exact stream order
 * (text prose → tool_use start → input_json_delta fragments → tool
 * result → done) and asserts:
 *
 *   (a) The bubble's text body contains ONLY the prose. No JSON
 *       fragments leak through.
 *   (b) A compact tool pill renders with the tool label.
 *   (c) Clicking the pill reveals the assembled JSON args that were
 *       accumulated from the input_json_delta fragments.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { useAppStore } from '../stores/app'

// Mirror the mocks from ChatPanel.test.tsx so history hydration and the
// websocket hook behave deterministically.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({ tabs: [], active_tab_id: '' }),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// scrollIntoView is not implemented in jsdom.
Element.prototype.scrollIntoView = vi.fn()

const mockConnect = vi.fn()
const mockSend = vi.fn()
let mockLastMessage: { type: string; data?: unknown } | null = null

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    connect: mockConnect,
    send: mockSend,
    get lastMessage() {
      return mockLastMessage
    },
    isConnected: true,
  }),
}))

let uuidCounter = 0
vi.stubGlobal('crypto', {
  randomUUID: () => `test-uuid-${++uuidCounter}`,
})

describe('ChatPanel tool_use_delta routing (→900)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    uuidCounter = 0
    mockLastMessage = null
    localStorage.clear()
    useAppStore.setState({
      chatOpen: true,
      chatWidth: 380,
      isResizing: false,
      defaultChatModel: 'claude',
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('accumulates input_json_delta fragments into the tool pill, never into the bubble body', () => {
    const { rerender, container } = render(<ChatPanel />)

    // User asks the assistant to read a file. Send the prompt so the
    // assistant placeholder bubble exists when stream events start
    // arriving.
    const input = screen.getByTestId('chat-input')
    fireEvent.change(input, { target: { value: 'read my notes' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    // 1. Prose token arrives first. This is the ONLY text that should
    //    end up in the bubble body.
    mockLastMessage = { type: 'token', data: 'Reading your notes now.' }
    rerender(<ChatPanel />)

    // 2. Tool use starts with empty input (matches the backend's
    //    content_block_start forwarding).
    mockLastMessage = {
      type: 'tool_use',
      data: { tool: 'read_file', id: 'tu_abc123', input: {} },
    }
    rerender(<ChatPanel />)

    // 3. input_json_delta fragments stream in. Each fragment is a chunk
    //    of the JSON args. These must NEVER append to the bubble's
    //    prose body.
    const fragments = [
      '{"path":',
      ' "/Users/tori',
      '/notes.md"',
      ', "limit": 50}',
    ]
    for (const partial of fragments) {
      mockLastMessage = {
        type: 'tool_use_delta',
        data: { id: 'tu_abc123', partial_json: partial },
      }
      rerender(<ChatPanel />)
    }

    // 4. Tool result arrives and the stream ends.
    mockLastMessage = {
      type: 'tool_result',
      data: { id: 'tu_abc123', result: 'Read 12 lines.' },
    }
    rerender(<ChatPanel />)

    mockLastMessage = { type: 'done' }
    rerender(<ChatPanel />)

    // --- Assertion (a): the bubble body carries only the prose. ---
    // Raw JSON fragments must not appear anywhere in the visible body.
    // The tool pill is allowed to show them in its collapsed summary
    // (the path string), but the body itself must be prose only.
    const bubbleContent = container.querySelector('.chat-bubble-content')
    expect(bubbleContent).toBeTruthy()
    const bodyText = bubbleContent?.textContent ?? ''
    expect(bodyText).toContain('Reading your notes now.')
    expect(bodyText).not.toContain('"path"')
    expect(bodyText).not.toContain('partial_json')
    expect(bodyText).not.toContain('input_json_delta')
    // Assemble the full raw JSON and confirm no substring of it leaked
    // into the body. This catches the regression where a single
    // fragment like '{"path":' gets appended to content.
    const fullRaw = fragments.join('')
    expect(bodyText).not.toContain(fullRaw)

    // --- Assertion (b): the compact tool pill renders with the label. ---
    const pill = screen.getByTestId('tool-call-pill')
    expect(pill).toBeTruthy()
    // TOOL_LABELS maps read_file → "Read file".
    expect(pill.textContent).toContain('Read file')
    // The one-line summary shows the path the fragments assembled into.
    expect(pill.textContent).toContain('/Users/tori/notes.md')

    // Args are collapsed by default; body must not contain the raw
    // JSON until the user expands the pill.
    expect(screen.queryByTestId('tool-call-args')).toBeNull()

    // --- Assertion (c): expanding the pill shows the assembled JSON. ---
    fireEvent.click(pill)
    const args = screen.getByTestId('tool-call-args')
    expect(args).toBeTruthy()
    const argsText = args.textContent ?? ''
    // The expanded view shows the parsed, pretty-printed arguments the
    // backend streamed in as input_json_delta fragments.
    expect(argsText).toContain('"path"')
    expect(argsText).toContain('/Users/tori/notes.md')
    expect(argsText).toContain('"limit"')
    expect(argsText).toContain('50')
  })

  it('ignores tool_use_delta events whose id does not match any open tool call', () => {
    // Defensive path: a stray delta with an unknown id must be silently
    // dropped. The assistant bubble must not grow and no tool pill
    // must appear.
    const { rerender, container } = render(<ChatPanel />)

    const input = screen.getByTestId('chat-input')
    fireEvent.change(input, { target: { value: 'hello' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    mockLastMessage = { type: 'token', data: 'Hi there.' }
    rerender(<ChatPanel />)

    mockLastMessage = {
      type: 'tool_use_delta',
      data: { id: 'tu_does_not_exist', partial_json: '{"evil": "payload"}' },
    }
    rerender(<ChatPanel />)

    const bubbleContent = container.querySelector('.chat-bubble-content')
    expect(bubbleContent?.textContent).toContain('Hi there.')
    expect(bubbleContent?.textContent).not.toContain('evil')
    expect(bubbleContent?.textContent).not.toContain('payload')
    expect(screen.queryByTestId('tool-call-pill')).toBeNull()
  })
})
