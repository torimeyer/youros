import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { CrossSourceSearch } from '../CrossSourceSearch';

const mockFetch = vi.fn();
global.fetch = mockFetch;

function mockSuccess(results: object[] = []) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      results,
      providers_used: ['slack'],
      providers_skipped: [],
    }),
  });
}

describe('CrossSourceSearch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders search input and button', () => {
    render(<CrossSourceSearch />);
    expect(screen.getByRole('textbox')).toBeTruthy();
    expect(screen.getByRole('button', { name: /search/i })).toBeTruthy();
  });

  it('calls /api/cross-source with query on submit', async () => {
    mockSuccess([]);
    render(<CrossSourceSearch />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hello' } });
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith('/api/cross-source', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ query: 'hello' }),
    })));
  });

  it('renders results with deep links', async () => {
    mockSuccess([{
      text: 'some message',
      source_id: '123',
      source_title: 'Slack #general',
      deep_link: 'https://slack.com/archives/C123/p456',
      score: 1.0,
      access_denied: false,
      provider: 'slack',
    }]);
    render(<CrossSourceSearch />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'test' } });
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    await waitFor(() => {
      const link = screen.getByRole('link', { name: /slack #general/i });
      expect(link).toBeTruthy();
      expect(link.getAttribute('href')).toBe('https://slack.com/archives/C123/p456');
    });
  });

  it('renders result without link when deep_link is null', async () => {
    mockSuccess([{
      text: 'local doc',
      source_id: 'src1',
      source_title: 'My Document',
      deep_link: null,
      score: 1.0,
      access_denied: false,
      provider: 'source_library',
    }]);
    render(<CrossSourceSearch />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'doc' } });
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    await waitFor(() => expect(screen.getByText('My Document')).toBeTruthy());
  });

  it('shows error on failed fetch', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
    render(<CrossSourceSearch />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'oops' } });
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    await waitFor(() => expect(screen.getByText(/500/i)).toBeTruthy());
  });
});
