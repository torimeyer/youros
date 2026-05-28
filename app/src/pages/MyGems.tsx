import { useState, useEffect, useCallback } from 'react';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import ConfirmModal from '../components/ConfirmModal';
import GemImportModal from '../components/GemImportModal';
import GemChatPanel from '../components/GemChatPanel';
import { useConfirm } from '../hooks/useConfirm';
import { api, ApiError, ApiTimeoutError } from '../lib/api';

export interface GeminiConversation {
  conversation_id: string;
  gem_name: string | null;
  turn_count: number;
  last_timestamp: string;
}

export interface GeminiTurn {
  conversation_id: string;
  role: string;
  text: string;
  timestamp: string;
  turn_index: number;
  gem_name: string | null;
}

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

  const [captures, setCaptures] = useState<GeminiConversation[]>([]);
  const [capturesLoading, setCapturesLoading] = useState(true);
  const [capturesExpanded, setCapturesExpanded] = useState(true);
  const [captureDrawer, setCaptureDrawer] = useState<{ id: string; turns: GeminiTurn[] } | null>(null);
  const [captureDrawerLoading, setCaptureDrawerLoading] = useState(false);

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

  const fetchCaptures = useCallback(async () => {
    setCapturesLoading(true);
    try {
      const res = await api.get<GeminiConversation[]>('/gemini-captures/conversations');
      setCaptures(Array.isArray(res) ? res : []);
    } catch {
      setCaptures([]);
    } finally {
      setCapturesLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCaptures();
  }, [fetchCaptures]);

  const openCaptureDrawer = async (conversationId: string) => {
    setCaptureDrawerLoading(true);
    setCaptureDrawer({ id: conversationId, turns: [] });
    try {
      const turns = await api.get<GeminiTurn[]>(`/gemini-captures/conversations/${conversationId}`);
      setCaptureDrawer({ id: conversationId, turns: Array.isArray(turns) ? turns : [] });
    } catch {
      setCaptureDrawer({ id: conversationId, turns: [] });
    } finally {
      setCaptureDrawerLoading(false);
    }
  };

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
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // Already gone — keep the optimistic remove, just inform the user.
        setToast({ kind: 'info', message: `"${gem.name}" was already deleted. Your list is up to date.` });
      } else if (err instanceof ApiTimeoutError) {
        setGems(previous);
        setToast({ kind: 'error', message: `Couldn't reach the server in time. Make sure yourOS is running, then try again.` });
      } else {
        setGems(previous);
        setToast({ kind: 'error', message: `Something went wrong deleting "${gem.name}". Try again, or restart yourOS if this keeps happening.` });
      }
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
            <p data-testid="gems-page-subtitle" className="text-sm text-slate-400 mt-2">
              Personas you chat with. Each Gem has its own instructions and knowledge files. They answer questions, they don't run jobs.
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
                    data-testid={`gem-history-${gem.id}`}
                    onClick={() => handleChat(gem)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
                    title="View past chats with this Gem"
                  >
                    <Icon name="history" className="text-base" />
                    Past chats
                  </button>
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

        {/* Gemini Captures section */}
        <div className="mt-8" data-testid="captures-section">
          <button
            className="flex items-center gap-2 w-full text-left mb-4"
            onClick={() => setCapturesExpanded((v) => !v)}
            aria-expanded={capturesExpanded}
            data-testid="captures-toggle"
          >
            <Icon
              name={capturesExpanded ? 'expand_less' : 'expand_more'}
              className="text-slate-400 text-base"
            />
            <h2 className="text-base font-semibold text-slate-200">Gemini Captures</h2>
            {captures.length > 0 && (
              <span className="text-xs text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded ml-1">
                {captures.length}
              </span>
            )}
          </button>

          {capturesExpanded && (
            <>
              {capturesLoading && (
                <p className="text-sm text-slate-500 py-2" data-testid="captures-loading">
                  Loading captures...
                </p>
              )}

              {!capturesLoading && captures.length === 0 && (
                <div
                  data-testid="captures-empty-state"
                  className="text-sm py-4 border border-slate-800 rounded-xl px-5"
                >
                  <p className="mb-3 font-medium text-slate-300">No captures yet</p>
                  <p className="mb-3 text-slate-400">To start capturing your Gemini chats, follow these steps:</p>
                  <ol className="list-decimal list-inside space-y-2 text-slate-400">
                    <li>
                      Open Chrome and go to{' '}
                      <code className="text-slate-300 bg-slate-800/60 px-1 rounded text-xs">chrome://extensions</code>.
                      Turn on <strong className="text-slate-300">Developer mode</strong> (toggle in the top right corner).
                    </li>
                    <li>
                      Click <strong className="text-slate-300">Load unpacked</strong> and select the{' '}
                      <code className="text-slate-300 bg-slate-800/60 px-1 rounded text-xs">extension</code>{' '}
                      folder inside your yourOS installation.
                    </li>
                    <li>
                      Click the yourOS icon in your Chrome toolbar, then open{' '}
                      <strong className="text-slate-300">Settings</strong>. Paste your auth token — find it
                      by opening a terminal and running{' '}
                      <code className="text-slate-300 bg-slate-800/60 px-1 rounded text-xs">cat ~/.myos/extension_token</code>.
                    </li>
                    <li>
                      Toggle <strong className="text-slate-300">Capture conversations</strong> on, then open{' '}
                      <strong className="text-slate-300">gemini.google.com</strong>. Your chats will appear here automatically.
                    </li>
                  </ol>
                </div>
              )}

              {!capturesLoading && captures.length > 0 && (
                <div className="flex flex-col gap-2" data-testid="captures-list">
                  {captures.map((conv) => (
                    <button
                      key={conv.conversation_id}
                      data-testid={`capture-conversation-row-${conv.conversation_id}`}
                      onClick={() => openCaptureDrawer(conv.conversation_id)}
                      className="flex items-center justify-between gap-4 bg-slate-900/60 border border-slate-800 rounded-xl px-5 py-3 hover:border-slate-700 transition-colors text-left w-full"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <Icon name="chat" className="text-sm text-purple-400 flex-shrink-0" />
                          <span className="font-medium text-slate-200 text-sm truncate">
                            {conv.gem_name || conv.conversation_id}
                          </span>
                          <span className="text-[10px] text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded flex-shrink-0">
                            {conv.turn_count} turn{conv.turn_count !== 1 ? 's' : ''}
                          </span>
                        </div>
                        <p className="text-[11px] text-slate-600 mt-0.5 ml-6">
                          Last active {timeAgo(conv.last_timestamp)}
                        </p>
                      </div>
                      <Icon name="chevron_right" className="text-slate-600 text-base flex-shrink-0" />
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Capture conversation drawer */}
      {captureDrawer && (
        <div
          data-testid="capture-drawer"
          className="fixed inset-0 z-40 flex items-stretch"
          onClick={() => setCaptureDrawer(null)}
        >
          <div className="flex-1" />
          <div
            className="w-full max-w-lg bg-slate-900 border-l border-slate-800 flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
              <h3 className="font-semibold text-slate-100 text-sm">Conversation</h3>
              <button
                onClick={() => setCaptureDrawer(null)}
                className="text-slate-500 hover:text-slate-300"
                aria-label="Close"
                data-testid="capture-drawer-close"
              >
                <Icon name="close" size={18} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-4">
              {captureDrawerLoading && (
                <p className="text-sm text-slate-500">Loading turns...</p>
              )}
              {!captureDrawerLoading && captureDrawer.turns.map((turn, i) => (
                <div
                  key={i}
                  className={`flex flex-col gap-1 ${turn.role === 'user' ? 'items-end' : 'items-start'}`}
                >
                  <span className="text-[10px] text-slate-600 capitalize">{turn.role}</span>
                  <div
                    className={`rounded-xl px-4 py-2.5 text-sm max-w-[85%] whitespace-pre-wrap ${
                      turn.role === 'user'
                        ? 'bg-blue-600/30 text-slate-100'
                        : 'bg-slate-800 text-slate-200'
                    }`}
                  >
                    {turn.text}
                  </div>
                </div>
              ))}
              {!captureDrawerLoading && captureDrawer.turns.length === 0 && (
                <p className="text-sm text-slate-500">No turns found.</p>
              )}
            </div>
          </div>
        </div>
      )}

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
