import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import GemChatPanel from './GemChatPanel';
import type { Gem } from '../pages/MyGems';

Element.prototype.scrollIntoView = vi.fn();

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

const mockGem: Gem = {
  id: 'gem-42',
  name: 'Test Gem',
  system_prompt: 'You are a helpful assistant.',
  knowledge_files: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

function makeSseResponse(frames: string[]): Response {
  const body = frames.map((f) => `data: ${f}\n\n`).join('');
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

function makeHistoryResponse(turns: Array<{ role: string; text: string }> = []): Response {
  return new Response(JSON.stringify(turns), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * Set up a fetch mock that routes by URL:
 *  - /chat/history → returns `history` (default [])
 *  - /chat (POST)  → returns the SSE frames
 * This correctly handles the on-mount history hydration + send flow.
 */
function mockFetch(
  sseFrames: string[] = [],
  history: Array<{ role: string; text: string }> = []
) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (url: RequestInfo | URL) => {
    if (String(url).includes('/chat/history')) {
      return makeHistoryResponse(history);
    }
    return makeSseResponse(sseFrames);
  });
}

function renderPanel(props: Partial<React.ComponentProps<typeof GemChatPanel>> = {}) {
  const onClose = vi.fn();
  const result = render(
    <MemoryRouter>
      <GemChatPanel gem={mockGem} onClose={onClose} {...props} />
    </MemoryRouter>
  );
  return { ...result, onClose };
}

beforeEach(() => {
  vi.restoreAllMocks();
  // Default: history returns empty list, SSE responses are empty
  mockFetch();
});

describe('GemChatPanel', () => {
  it('renders the panel with gem name in header', async () => {
    renderPanel();
    expect(screen.getByTestId('gem-chat-panel')).toBeInTheDocument();
    expect(screen.getByText('Test Gem')).toBeInTheDocument();
    // Wait for history load to settle
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());
  });

  it('shows loading state while history is fetching', () => {
    // Never resolve history fetch
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderPanel();
    expect(screen.getByTestId('gem-chat-loading-history')).toBeInTheDocument();
  });

  it('shows empty state after history loads with no turns', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('gem-chat-empty')).toBeInTheDocument());
  });

  it('hydrates history from the server and renders prior turns', async () => {
    const priorTurns = [
      { role: 'user', text: 'What is the codebase about?' },
      { role: 'model', text: 'It is a personal OS built on top of ostk.' },
    ];
    mockFetch([], priorTurns);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId('gem-chat-user-bubble-0')).toHaveTextContent(
        'What is the codebase about?'
      );
      expect(screen.getByTestId('gem-chat-assistant-bubble-1')).toHaveTextContent(
        'It is a personal OS'
      );
    });
    // Empty state should NOT show
    expect(screen.queryByTestId('gem-chat-empty')).not.toBeInTheDocument();
  });

  it('does not show empty state while history is still loading', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderPanel();
    expect(screen.queryByTestId('gem-chat-empty')).not.toBeInTheDocument();
  });

  it('calls the history endpoint with the gem id on mount', async () => {
    const fetchSpy = mockFetch();
    renderPanel();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/gems/gem-42/chat/history',
        expect.objectContaining({ credentials: 'include' })
      );
    });
  });

  it('calls onClose when X button is clicked', async () => {
    const { onClose } = renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());
    fireEvent.click(screen.getByTestId('gem-chat-close'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('calls onClose when clicking the backdrop', async () => {
    const { onClose } = renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());
    const backdrop = screen.getByTestId('gem-chat-panel');
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('send button is disabled when input is empty', async () => {
    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());
    expect(screen.getByTestId('gem-chat-send')).toBeDisabled();
  });

  it('send button is enabled when input has text', async () => {
    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());
    fireEvent.change(screen.getByTestId('gem-chat-input'), { target: { value: 'hi' } });
    expect(screen.getByTestId('gem-chat-send')).not.toBeDisabled();
  });

  it('user bubble appears after sending, assistant bubble appears with streamed content', async () => {
    const frames = [
      JSON.stringify({ type: 'token', data: 'Hello' }),
      JSON.stringify({ type: 'token', data: ' there!' }),
      JSON.stringify({ type: 'done' }),
    ];
    mockFetch(frames);

    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());

    fireEvent.change(screen.getByTestId('gem-chat-input'), { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('gem-chat-send'));

    // user bubble appears immediately
    await waitFor(() => {
      expect(screen.getByTestId('gem-chat-user-bubble-0')).toHaveTextContent('hi');
    });

    // input clears after send
    expect(screen.getByTestId('gem-chat-input')).toHaveValue('');

    // assistant bubble appears with full streamed content
    await waitFor(() => {
      expect(screen.getByTestId('gem-chat-assistant-bubble-1')).toHaveTextContent('Hello there!');
    });
  });

  it('sends correct payload to /api/gems/:id/chat', async () => {
    const frames = [JSON.stringify({ type: 'done' })];
    const fetchSpy = mockFetch(frames);

    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());

    fireEvent.change(screen.getByTestId('gem-chat-input'), { target: { value: 'test message' } });
    fireEvent.click(screen.getByTestId('gem-chat-send'));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/gems/gem-42/chat',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ message: 'test message', history: [] }),
        })
      );
    });
  });

  it('shows error bubble when fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url: RequestInfo | URL) => {
      if (String(url).includes('/chat/history')) return makeHistoryResponse();
      throw new Error('network error');
    });

    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());

    fireEvent.change(screen.getByTestId('gem-chat-input'), { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('gem-chat-send'));

    await waitFor(() => {
      expect(screen.getByTestId('gem-chat-assistant-bubble-1')).toHaveTextContent('Connection error');
    });
  });

  it('pressing Enter sends the message', async () => {
    const frames = [JSON.stringify({ type: 'done' })];
    const fetchSpy = mockFetch(frames);

    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());

    const input = screen.getByTestId('gem-chat-input');
    fireEvent.change(input, { target: { value: 'enter test' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: false });

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith('/api/gems/gem-42/chat', expect.anything());
      expect(screen.getByTestId('gem-chat-user-bubble-0')).toHaveTextContent('enter test');
    });
  });

  it('Shift+Enter does not send', async () => {
    const fetchSpy = mockFetch();

    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());

    const input = screen.getByTestId('gem-chat-input');
    fireEvent.change(input, { target: { value: 'newline test' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });

    // Only the history call should have happened — no chat POST
    expect(
      fetchSpy.mock.calls.filter(([url]) => String(url).includes('/chat') && !String(url).includes('/history'))
    ).toHaveLength(0);
  });

  it('assistant message renders markdown: bold, italic, list items, no raw markers', async () => {
    const mdText = '**bold** and *italic* and\n\n1. listed';
    const frames = [
      JSON.stringify({ type: 'token', data: mdText }),
      JSON.stringify({ type: 'done' }),
    ];
    mockFetch(frames);

    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());

    fireEvent.change(screen.getByTestId('gem-chat-input'), { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('gem-chat-send'));

    await waitFor(() => {
      const bubble = screen.getByTestId('gem-chat-assistant-bubble-1');
      expect(bubble.querySelector('strong')).toBeTruthy();
      expect(bubble.querySelector('strong')?.textContent).toBe('bold');
      expect(bubble.querySelector('em')).toBeTruthy();
      expect(bubble.querySelector('em')?.textContent).toBe('italic');
      expect(bubble.querySelector('li')).toBeTruthy();
      expect(bubble.textContent).not.toContain('**');
      expect(bubble.textContent).not.toContain('*italic*');
    });
  });

  it('user message is plain text and does not render markdown tags', async () => {
    const frames = [JSON.stringify({ type: 'done' })];
    mockFetch(frames);

    renderPanel();
    await waitFor(() => expect(screen.queryByTestId('gem-chat-loading-history')).not.toBeInTheDocument());

    const userMsg = '**not bold**';
    fireEvent.change(screen.getByTestId('gem-chat-input'), { target: { value: userMsg } });
    fireEvent.click(screen.getByTestId('gem-chat-send'));

    await waitFor(() => {
      const userBubble = screen.getByTestId('gem-chat-user-bubble-0');
      expect(userBubble.querySelector('strong')).toBeNull();
      expect(userBubble.textContent).toContain('**not bold**');
    });
  });
});
