import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MyGems from './MyGems';

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  };
});

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

import { api } from '../lib/api';

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);
const mockedDelete = vi.mocked(api.delete);

const mockGems = [
  {
    id: 'gem-1',
    name: 'Code Reviewer',
    system_prompt: 'You are a senior code reviewer.',
    knowledge_files: ['style-guide.md'],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    id: 'gem-2',
    name: 'Research Assistant',
    system_prompt: 'You are a research assistant.',
    knowledge_files: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
];

function renderMyGems() {
  return render(
    <MemoryRouter>
      <MyGems />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('MyGems page', () => {
  it('shows empty state when no gems are returned', async () => {
    mockedGet.mockResolvedValue([]);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gems-empty-state')).toBeInTheDocument();
    });
    expect(screen.getByText(/No Gems yet/i)).toBeInTheDocument();
  });

  it('shows loading indicator while fetching', () => {
    mockedGet.mockImplementation(() => new Promise(() => {}));
    renderMyGems();
    expect(screen.getByTestId('gems-loading')).toBeInTheDocument();
  });

  it('shows error message when fetch fails', async () => {
    mockedGet.mockRejectedValue(new Error('Network error'));
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gems-error')).toBeInTheDocument();
    });
  });

  it('renders gem cards after fetch', async () => {
    mockedGet.mockResolvedValue(mockGems);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gems-list')).toBeInTheDocument();
    });
    expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
    expect(screen.getByTestId('gem-card-gem-2')).toBeInTheDocument();
    expect(screen.getByText('Code Reviewer')).toBeInTheDocument();
    expect(screen.getByText('Research Assistant')).toBeInTheDocument();
  });

  it('shows knowledge file count badge', async () => {
    mockedGet.mockResolvedValue(mockGems);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
    });
    expect(screen.getByText('1 file')).toBeInTheDocument();
  });

  it('empty state has no Create Gem button inside it', async () => {
    mockedGet.mockResolvedValue([]);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gems-empty-state')).toBeInTheDocument();
    });
    expect(screen.getByText(/No Gems yet/i)).toBeInTheDocument();
    const emptyState = screen.getByTestId('gems-empty-state');
    const buttons = emptyState.querySelectorAll('button');
    expect(buttons).toHaveLength(0);
  });

  it('header Create Gem button is present when empty state is shown', async () => {
    mockedGet.mockResolvedValue([]);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gems-empty-state')).toBeInTheDocument();
    });
    expect(screen.getByTestId('create-gem-button')).toBeInTheDocument();
  });

  it('opens create modal when "Create Gem" is clicked', async () => {
    mockedGet.mockResolvedValue([]);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gems-empty-state')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('create-gem-button'));
    expect(screen.getByTestId('gem-import-modal')).toBeInTheDocument();
  });

  it('opens edit modal pre-filled when "Edit" is clicked', async () => {
    mockedGet.mockResolvedValue(mockGems);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('gem-edit-gem-1'));
    const modal = screen.getByTestId('gem-import-modal');
    expect(modal).toBeInTheDocument();
    expect(screen.getByTestId('gem-name-input')).toHaveValue('Code Reviewer');
    expect(screen.getByTestId('gem-system-prompt-input')).toHaveValue('You are a senior code reviewer.');
  });

  it('shows delete confirm modal and deletes on confirm', async () => {
    mockedGet.mockResolvedValue(mockGems);
    mockedDelete.mockResolvedValue({});
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('gem-delete-gem-1'));

    await waitFor(() => {
      expect(screen.getByTestId('confirm-modal-backdrop')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('confirm-modal-confirm'));

    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith('/gems/gem-1');
    });

    await waitFor(() => {
      expect(screen.queryByTestId('gem-card-gem-1')).not.toBeInTheDocument();
    });
  });

  it('cancels delete when cancel is clicked', async () => {
    mockedGet.mockResolvedValue(mockGems);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('gem-delete-gem-1'));
    await waitFor(() => {
      expect(screen.getByTestId('confirm-modal-backdrop')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('confirm-modal-cancel'));

    await waitFor(() => {
      expect(screen.queryByTestId('confirm-modal-backdrop')).not.toBeInTheDocument();
    });
    expect(mockedDelete).not.toHaveBeenCalled();
    expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
  });

  it('shows retry button on error and retries fetch when clicked', async () => {
    mockedGet.mockRejectedValue(new Error('Network error'));
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gems-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('gems-retry')).toBeInTheDocument();

    mockedGet.mockResolvedValue(mockGems);
    fireEvent.click(screen.getByTestId('gems-retry'));

    await waitFor(() => {
      expect(screen.getByTestId('gems-list')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('gems-error')).not.toBeInTheDocument();
  });

  it('shows a toast when Chat is clicked (stub)', async () => {
    mockedGet.mockResolvedValue(mockGems);
    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('gem-chat-gem-1'));
    expect(screen.getByTestId('gems-toast')).toBeInTheDocument();
    expect(screen.getByText(/will be wired in next pass/i)).toBeInTheDocument();
  });

  it('refreshes gem list after create modal saves', async () => {
    // Start with one gem; after save the mock returns both
    const firstGem = [mockGems[0]];
    mockedGet.mockResolvedValue(firstGem);
    mockedPost.mockResolvedValue(mockGems[1]);

    renderMyGems();
    await waitFor(() => {
      expect(screen.getByTestId('gem-card-gem-1')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('gem-card-gem-2')).not.toBeInTheDocument();

    // Switch mock BEFORE triggering save so the refresh picks it up
    mockedGet.mockResolvedValue(mockGems);

    fireEvent.click(screen.getByTestId('create-gem-button'));
    expect(screen.getByTestId('gem-import-modal')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('gem-name-input'), { target: { value: 'Research Assistant' } });
    fireEvent.change(screen.getByTestId('gem-system-prompt-input'), { target: { value: 'You are a research assistant.' } });
    fireEvent.click(screen.getByTestId('gem-modal-submit'));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith('/gems', {
        name: 'Research Assistant',
        system_prompt: 'You are a research assistant.',
        knowledge_files: [],
      });
    });

    await waitFor(() => {
      expect(screen.queryByTestId('gem-import-modal')).not.toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByTestId('gem-card-gem-2')).toBeInTheDocument();
    });
  });
});
