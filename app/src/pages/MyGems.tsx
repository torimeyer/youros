import { useState, useEffect, useCallback } from 'react';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import ConfirmModal from '../components/ConfirmModal';
import GemImportModal from '../components/GemImportModal';
import GemChatPanel from '../components/GemChatPanel';
import { useConfirm } from '../hooks/useConfirm';
import { api } from '../lib/api';

export interface Gem {
  id: string;
  name: string;
  system_prompt: string;
  knowledge_files: string[];
  created_at: string;
  updated_at: string;
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

export default function MyGems() {
  const [gems, setGems] = useState<Gem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Gem | null>(null);
  const [chatTarget, setChatTarget] = useState<Gem | null>(null);

  const [toast, setToast] = useState<{ kind: 'success' | 'error' | 'info'; message: string } | null>(null);

  const { confirm, confirmProps } = useConfirm();

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(id);
  }, [toast]);

  const fetchGems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<Gem[]>('/gems');
      setGems(Array.isArray(res) ? res : []);
    } catch {
      setError('Could not load your Gems. Make sure the backend is running.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGems();
  }, [fetchGems]);

  const openCreate = () => {
    setEditTarget(null);
    setModalOpen(true);
  };

  const openEdit = (gem: Gem) => {
    setEditTarget(gem);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditTarget(null);
  };

  const handleSaved = () => {
    closeModal();
    fetchGems();
  };

  const handleDelete = async (gem: Gem) => {
    const ok = await confirm({
      title: `Delete "${gem.name}"?`,
      message: 'This removes the Gem and its settings. Chats linked to it will still exist but will lose the custom instructions.',
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      danger: true,
    });
    if (!ok) return;

    const previous = gems;
    setGems((curr) => curr.filter((g) => g.id !== gem.id));
    try {
      await api.delete(`/gems/${gem.id}`);
      setToast({ kind: 'success', message: `"${gem.name}" deleted.` });
    } catch {
      setGems(previous);
      setToast({ kind: 'error', message: `Could not delete "${gem.name}". Try again.` });
    }
  };

  const handleChat = (gem: Gem) => {
    setChatTarget(gem);
  };

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="My Gems" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8 max-w-4xl mx-auto">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">My Gems</h1>
            <p className="text-sm text-slate-400 mt-0.5">
              Custom Gemini assistants you&apos;ve imported. Each Gem has its own instructions and knowledge.
            </p>
          </div>
          <button
            data-testid="create-gem-button"
            onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
          >
            <Icon name="add" className="text-base" />
            Create Gem
          </button>
        </div>

        {/* Loading */}
        {loading && gems.length === 0 && (
          <p data-testid="gems-loading" className="text-sm text-slate-500 py-4">
            Loading your Gems...
          </p>
        )}

        {/* Error */}
        {error && (
          <div
            data-testid="gems-error"
            className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm mb-4"
          >
            <Icon name="error" className="text-lg flex-shrink-0" />
            <span className="flex-1">{error}</span>
            <button
              data-testid="gems-retry"
              onClick={fetchGems}
              className="flex-shrink-0 px-3 py-1.5 text-xs font-medium text-red-300 hover:text-white border border-red-500/40 hover:border-red-400 rounded-lg transition-colors"
            >
              Try again
            </button>
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && gems.length === 0 && (
          <div
            data-testid="gems-empty-state"
            className="text-center py-16 text-slate-500"
          >
            <Icon name="auto_awesome" className="text-5xl mb-3 text-slate-600" />
            <p className="text-slate-300 font-medium mb-1">No Gems yet</p>
            <p className="text-sm">
              Create your first Gem to give Gemini a custom persona and knowledge base.
            </p>
          </div>
        )}

        {/* Gem list */}
        {gems.length > 0 && (
          <div className="flex flex-col gap-2" data-testid="gems-list">
            {gems.map((gem) => (
              <div
                key={gem.id}
                data-testid={`gem-card-${gem.id}`}
                className="flex items-center justify-between gap-4 bg-slate-900/60 border border-slate-800 rounded-xl px-5 py-4 hover:border-slate-700 transition-colors"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Icon name="auto_awesome" className="text-base text-blue-400 flex-shrink-0" />
                    <span className="font-medium text-slate-100 truncate" data-testid={`gem-name-${gem.id}`}>
                      {gem.name}
                    </span>
                    {gem.knowledge_files.length > 0 && (
                      <span className="text-[10px] text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded flex-shrink-0">
                        {gem.knowledge_files.length} file{gem.knowledge_files.length !== 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                  {gem.system_prompt && (
                    <p className="text-xs text-slate-500 truncate mt-0.5 ml-6">
                      {gem.system_prompt.slice(0, 120)}
                    </p>
                  )}
                  <p className="text-[11px] text-slate-600 mt-1 ml-6">
                    Edited {timeAgo(gem.updated_at || gem.created_at)}
                  </p>
                </div>

                <div className="flex items-center gap-1 flex-shrink-0">
                  <button
                    data-testid={`gem-chat-${gem.id}`}
                    onClick={() => handleChat(gem)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
                    title="Chat with this Gem"
                  >
                    <Icon name="chat" className="text-base" />
                    Chat
                  </button>
                  <button
                    data-testid={`gem-edit-${gem.id}`}
                    onClick={() => openEdit(gem)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
                    title="Edit Gem"
                  >
                    <Icon name="edit" className="text-base" />
                    Edit
                  </button>
                  <button
                    data-testid={`gem-delete-${gem.id}`}
                    onClick={() => handleDelete(gem)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors"
                    title="Delete Gem"
                  >
                    <Icon name="delete" className="text-base" />
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Import / edit modal */}
      {modalOpen && (
        <GemImportModal
          gem={editTarget}
          onClose={closeModal}
          onSaved={handleSaved}
        />
      )}

      {/* Gem chat panel */}
      {chatTarget && (
        <GemChatPanel gem={chatTarget} onClose={() => setChatTarget(null)} />
      )}

      {/* In-app confirm dialog */}
      <ConfirmModal {...confirmProps} />

      {/* Transient toast */}
      {toast && (
        <div
          role="status"
          data-testid="gems-toast"
          className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-start gap-3 px-4 py-3 rounded-xl shadow-lg text-sm border ${
            toast.kind === 'error'
              ? 'bg-red-950 border-red-800 text-red-200'
              : toast.kind === 'info'
              ? 'bg-slate-800 border-slate-700 text-slate-300'
              : 'bg-slate-800 border-slate-700 text-slate-200'
          }`}
        >
          <Icon
            name={toast.kind === 'error' ? 'error' : toast.kind === 'info' ? 'info' : 'check_circle'}
            size={18}
            className={
              toast.kind === 'error' ? 'text-red-400' :
              toast.kind === 'info' ? 'text-blue-400' :
              'text-green-400'
            }
          />
          <span>{toast.message}</span>
          <button
            onClick={() => setToast(null)}
            className="text-slate-500 hover:text-slate-300"
            aria-label="Dismiss"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
