import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import McpInstaller from './McpInstaller';

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
  },
}));

import { api } from '../lib/api';
const mockApi = api as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
};

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

  it('no longer uses the dead BEM class names (no stylesheet defines them)', async () => {
    const { container } = render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-entry-GitHub'));
    expect(container.querySelector('[class*="mcp-installer"]')).toBeNull();
  });

  it('styles the rows with Tailwind classes, dark mode included', async () => {
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-entry-GitHub'));
    const row = screen.getByTestId('mcp-entry-GitHub');
    expect(row.className).toContain('flex');
    const search = screen.getByTestId('mcp-installer-search');
    expect(search.className).toContain('dark:');
  });
});

describe('McpInstaller allow in chat', () => {
  const settingsWith = (servers: unknown[]) => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/api/mcp/catalog') return Promise.resolve({ catalog: CATALOG });
      if (url === '/api/mcp/installed') return Promise.resolve({ installed: ['Slack'] });
      if (url === '/api/settings') return Promise.resolve({ mcp_servers: servers });
      return Promise.resolve({});
    });
  };

  it('shows an Allow in chat toggle for installed servers only', async () => {
    settingsWith([]);
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-allow-chat-Slack'));
    expect(screen.queryByTestId('mcp-allow-chat-GitHub')).toBeNull();
    expect(screen.queryByTestId('mcp-allow-chat-Memory')).toBeNull();
  });

  it('toggle is off by default', async () => {
    settingsWith([]);
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-allow-chat-Slack'));
    const box = screen.getByTestId('mcp-allow-chat-Slack') as HTMLInputElement;
    expect(box.checked).toBe(false);
  });

  it('shows the plain-language warning sentence', async () => {
    settingsWith([]);
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-allow-chat-warning'));
    expect(screen.getByTestId('mcp-allow-chat-warning').textContent).toContain(
      'chat can read from and act on'
    );
  });

  it('reflects an already-approved server from settings', async () => {
    settingsWith([{ name: 'Slack', allowed_in_chat: true }]);
    render(<McpInstaller />);
    await waitFor(() => {
      const box = screen.getByTestId('mcp-allow-chat-Slack') as HTMLInputElement;
      expect(box.checked).toBe(true);
    });
  });

  it('turning the toggle on saves allowed_in_chat true for that server', async () => {
    settingsWith([{ name: 'GitHub', url: 'https://gh.example/mcp' }]);
    mockApi.patch.mockResolvedValue({ result: 'updated' });
    render(<McpInstaller />);
    await waitFor(() => screen.getByTestId('mcp-allow-chat-Slack'));

    fireEvent.click(screen.getByTestId('mcp-allow-chat-Slack'));

    await waitFor(() => {
      expect(mockApi.patch).toHaveBeenCalledWith('/api/settings', {
        mcp_servers: [
          { name: 'GitHub', url: 'https://gh.example/mcp' },
          { name: 'Slack', allowed_in_chat: true },
        ],
      });
    });
  });

  it('turning the toggle off saves allowed_in_chat false, keeping the entry', async () => {
    settingsWith([{ name: 'Slack', allowed_in_chat: true }]);
    mockApi.patch.mockResolvedValue({ result: 'updated' });
    render(<McpInstaller />);
    await waitFor(() => {
      expect((screen.getByTestId('mcp-allow-chat-Slack') as HTMLInputElement).checked).toBe(true);
    });

    fireEvent.click(screen.getByTestId('mcp-allow-chat-Slack'));

    await waitFor(() => {
      expect(mockApi.patch).toHaveBeenCalledWith('/api/settings', {
        mcp_servers: [{ name: 'Slack', allowed_in_chat: false }],
      });
    });
  });
});
