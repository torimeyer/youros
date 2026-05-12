import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import GemImportModal from './GemImportModal';
import type { Gem } from '../pages/MyGems';

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

const mockedPost = vi.mocked(api.post);
const mockedPatch = vi.mocked(api.patch);

const mockGem: Gem = {
  id: 'gem-99',
  name: 'My Gem',
  system_prompt: 'Be helpful.',
  knowledge_files: ['notes.md'],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

function renderModal(props: Partial<React.ComponentProps<typeof GemImportModal>> = {}) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  const result = render(
    <MemoryRouter>
      <GemImportModal gem={null} onClose={onClose} onSaved={onSaved} {...props} />
    </MemoryRouter>
  );
  return { ...result, onClose, onSaved };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('GemImportModal', () => {
  describe('create mode', () => {
    it('renders create modal with empty fields', () => {
      renderModal();
      expect(screen.getByTestId('gem-import-modal')).toBeInTheDocument();
      expect(screen.getAllByText('Create Gem').length).toBeGreaterThan(0);
      expect(screen.getByTestId('gem-name-input')).toHaveValue('');
      expect(screen.getByTestId('gem-system-prompt-input')).toHaveValue('');
    });

    it('calls onClose when Cancel is clicked', () => {
      const { onClose } = renderModal();
      fireEvent.click(screen.getByTestId('gem-modal-cancel'));
      expect(onClose).toHaveBeenCalledOnce();
    });

    it('calls onClose when X button is clicked', () => {
      const { onClose } = renderModal();
      fireEvent.click(screen.getByTestId('gem-modal-close'));
      expect(onClose).toHaveBeenCalledOnce();
    });

    it('calls onClose when clicking the backdrop', () => {
      const { onClose } = renderModal();
      const backdrop = screen.getByTestId('gem-import-modal');
      fireEvent.click(backdrop);
      expect(onClose).toHaveBeenCalledOnce();
    });

    it('disables submit when name is empty', () => {
      renderModal();
      expect(screen.getByTestId('gem-modal-submit')).toBeDisabled();
    });

    it('disables submit when only name is filled', () => {
      renderModal();
      fireEvent.change(screen.getByTestId('gem-name-input'), { target: { value: 'My Gem' } });
      expect(screen.getByTestId('gem-modal-submit')).toBeDisabled();
    });

    it('enables submit when both name and instructions are filled', () => {
      renderModal();
      fireEvent.change(screen.getByTestId('gem-name-input'), { target: { value: 'My Gem' } });
      fireEvent.change(screen.getByTestId('gem-system-prompt-input'), { target: { value: 'Be helpful.' } });
      expect(screen.getByTestId('gem-modal-submit')).not.toBeDisabled();
    });

    it('POSTs to /gems and calls onSaved on submit', async () => {
      mockedPost.mockResolvedValue({ id: 'new-gem', name: 'My Gem', system_prompt: 'Be helpful.', knowledge_files: [] });
      const { onSaved } = renderModal();

      fireEvent.change(screen.getByTestId('gem-name-input'), { target: { value: 'My Gem' } });
      fireEvent.change(screen.getByTestId('gem-system-prompt-input'), { target: { value: 'Be helpful.' } });
      fireEvent.click(screen.getByTestId('gem-modal-submit'));

      await waitFor(() => {
        expect(mockedPost).toHaveBeenCalledWith('/gems', {
          name: 'My Gem',
          system_prompt: 'Be helpful.',
          knowledge_files: [],
        });
      });
      expect(onSaved).toHaveBeenCalledOnce();
    });

    it('shows error when POST fails', async () => {
      mockedPost.mockRejectedValue(new Error('Server error'));
      renderModal();

      fireEvent.change(screen.getByTestId('gem-name-input'), { target: { value: 'My Gem' } });
      fireEvent.change(screen.getByTestId('gem-system-prompt-input'), { target: { value: 'Be helpful.' } });
      fireEvent.click(screen.getByTestId('gem-modal-submit'));

      await waitFor(() => {
        expect(screen.getByTestId('gem-submit-error')).toBeInTheDocument();
      });
    });

    it('trims whitespace from name and instructions before submit', async () => {
      mockedPost.mockResolvedValue({ id: 'x', name: 'Trimmed', system_prompt: 'Clean.', knowledge_files: [] });
      renderModal();

      fireEvent.change(screen.getByTestId('gem-name-input'), { target: { value: '  Trimmed  ' } });
      fireEvent.change(screen.getByTestId('gem-system-prompt-input'), { target: { value: '  Clean.  ' } });
      fireEvent.click(screen.getByTestId('gem-modal-submit'));

      await waitFor(() => {
        expect(mockedPost).toHaveBeenCalledWith('/gems', expect.objectContaining({
          name: 'Trimmed',
          system_prompt: 'Clean.',
        }));
      });
    });
  });

  describe('edit mode', () => {
    it('renders edit modal pre-filled with gem data', () => {
      renderModal({ gem: mockGem });
      expect(screen.getByText('Edit Gem')).toBeInTheDocument();
      expect(screen.getByTestId('gem-name-input')).toHaveValue('My Gem');
      expect(screen.getByTestId('gem-system-prompt-input')).toHaveValue('Be helpful.');
    });

    it('shows existing knowledge files in edit mode', () => {
      renderModal({ gem: mockGem });
      expect(screen.getByTestId('gem-file-list')).toBeInTheDocument();
      expect(screen.getByTestId('gem-file-item-notes.md')).toBeInTheDocument();
    });

    it('can remove an existing knowledge file', () => {
      renderModal({ gem: mockGem });
      expect(screen.getByTestId('gem-file-item-notes.md')).toBeInTheDocument();
      fireEvent.click(screen.getByTestId('gem-file-remove-notes.md'));
      expect(screen.queryByTestId('gem-file-item-notes.md')).not.toBeInTheDocument();
    });

    it('PATCHes /gems/:id and calls onSaved on submit', async () => {
      mockedPatch.mockResolvedValue({ ...mockGem, name: 'Updated Gem' });
      const { onSaved } = renderModal({ gem: mockGem });

      fireEvent.change(screen.getByTestId('gem-name-input'), { target: { value: 'Updated Gem' } });
      fireEvent.click(screen.getByTestId('gem-modal-submit'));

      await waitFor(() => {
        expect(mockedPatch).toHaveBeenCalledWith('/gems/gem-99', {
          name: 'Updated Gem',
          system_prompt: 'Be helpful.',
          knowledge_files: ['notes.md'],
        });
      });
      expect(onSaved).toHaveBeenCalledOnce();
    });

    it('shows "Save changes" as the submit label in edit mode', () => {
      renderModal({ gem: mockGem });
      expect(screen.getByTestId('gem-modal-submit')).toHaveTextContent('Save changes');
    });
  });

  describe('knowledge file drop zone', () => {
    it('renders the drop zone', () => {
      renderModal();
      expect(screen.getByTestId('gem-drop-zone')).toBeInTheDocument();
    });

    it('shows drag-over style when a file is dragged over', () => {
      renderModal();
      const zone = screen.getByTestId('gem-drop-zone');
      fireEvent.dragOver(zone, { dataTransfer: { files: [] } });
      expect(zone.className).toMatch(/border-blue-500/);
    });

    it('resets drag-over style on drag leave', () => {
      renderModal();
      const zone = screen.getByTestId('gem-drop-zone');
      fireEvent.dragOver(zone, { dataTransfer: { files: [] } });
      fireEvent.dragLeave(zone);
      expect(zone.className).not.toMatch(/border-blue-500/);
    });
  });
});
