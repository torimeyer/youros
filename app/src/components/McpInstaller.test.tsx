import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import McpInstaller from './McpInstaller';

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { api } from '../lib/api';
const mockApi = api as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> };

const CATALOG = [
  { name: 'GitHub', description: 'Access repos, issues, and pull requests', icon: 'code', npm_package: '@modelcontextprotocol/server-github', requires_auth: true, auth_hint: 'Needs a GitHub access key.' },
  { name: 'Slack', description: 'Send messages and read channels', icon: 'forum', npm_package: '@modelcontextprotocol/server-slack', requires_auth: true, auth_hint: 'Needs a Slack bot key.' },
  { name: 'Memory', description: 'Persistent notes and memory that last across sessions', icon: 'psychology', npm_package: '@modelcontextprotocol/server-memory', requires_auth: false },
];

beforeEach(() => {
  mockApi.get.mockImplementation((url: string) => {
    if (url === '/api/mcp/catalog') return Promise.resolve({ catalog: CATALOG });
    if (url === '/api/mcp/installed') return Promise.resolve({ installed: ['Slack'] });
    return Promise.resolve({});
  });
  mockApi.post.mockResolvedValue({ ok: true, message: 'GitHub was added successfully.' });
});

describe('McpInstaller', () => {
  it('renders the list of catalog entries', async () => {
    render(<McpInstaller />);
    await waitFor(() => {
      expect(screen.getByTestId('mcp-entry-GitHub')).toBeDefined();
      expect(screen.getByTestId('mcp-entry-Slack')).toBeDefined();
      expect(screen.getByTestId('mcp-entry-Memory')).toBeDefined();
    });
  });

  it('shows "Installed" badge for already-installed entries', async () => {
    render(<McpInstaller />);
    await waitFor(() => {
      expect(screen.getByTestId('mcp-badge-Slack')).toBeDefined();
      expect(screen.getByTestId('mcp-badge-Slack').textContent).toBe('Installed');
    });
  });

  it('shows Add button for entries that are not installed', async () => {
    render(<McpInstaller />);
    await waitFor(() => {
      expect(screen.getByTestId('mcp-install-btn-GitHub')).toBeDefined();
      expect(screen.getByTestId('mcp-install-btn-Memory')).toBeDefined();
    });
  });

  it('does not show Add button for installed entries', async () => {
    render(<McpInstaller />);
    await waitFor(() => {
      expect(screen.queryByTestId('mcp-install-btn-Slack')).toBeNull();
    });
  });

  it('calls install endpoint and shows success toast', async () => {
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-install-btn-GitHub'));

    fireEvent.click(screen.getByTestId('mcp-install-btn-GitHub'));

    expect(mockApi.post).toHaveBeenCalledWith('/api/mcp/install', { name: 'GitHub' });

    await waitFor(() => {
      expect(screen.getByTestId('mcp-installer-toast')).toBeDefined();
      expect(screen.getByTestId('mcp-installer-toast').textContent).toContain('successfully');
    });
  });

  it('shows "Installed" badge after successful install', async () => {
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-install-btn-GitHub'));

    fireEvent.click(screen.getByTestId('mcp-install-btn-GitHub'));

    await waitFor(() => {
      expect(screen.getByTestId('mcp-badge-GitHub')).toBeDefined();
    });
  });

  it('shows error toast on install failure', async () => {
    mockApi.post.mockResolvedValue({ ok: false, message: 'Something went wrong. Please try again.' });

    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-install-btn-GitHub'));

    fireEvent.click(screen.getByTestId('mcp-install-btn-GitHub'));

    await waitFor(() => {
      const toast = screen.getByTestId('mcp-installer-toast');
      expect(toast.textContent).toContain('went wrong');
    });
  });

  it('filters entries by search text', async () => {
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-entry-GitHub'));

    fireEvent.change(screen.getByTestId('mcp-installer-search'), { target: { value: 'slack' } });

    expect(screen.queryByTestId('mcp-entry-GitHub')).toBeNull();
    expect(screen.getByTestId('mcp-entry-Slack')).toBeDefined();
  });

  it('calls onClose when close button is clicked', async () => {
    const onClose = vi.fn();
    render(<McpInstaller onClose={onClose} />);
    await waitFor(() => screen.getByTestId('mcp-installer'));

    fireEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
