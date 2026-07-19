import { useState, useEffect } from 'react';
import { api } from '../lib/api';
import Icon from './Icon';

interface CatalogEntry {
  name: string;
  description: string;
  icon: string;
  npm_package: string;
  requires_auth: boolean;
  auth_hint?: string;
}

// One entry in the settings mcp_servers list. Entries are free-form dicts
// on the backend; allowed_in_chat is the only field this screen changes.
// Absent means not allowed, so chat stays locked until the user opts in.
interface McpServerEntry {
  name?: string;
  url?: string;
  allowed_in_chat?: boolean;
  [key: string]: unknown;
}

interface McpInstallerProps {
  onClose?: () => void;
}

export default function McpInstaller({ onClose }: McpInstallerProps) {
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const [installing, setInstalling] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; ok: boolean } | null>(null);
  const [search, setSearch] = useState('');
  const [mcpServers, setMcpServers] = useState<McpServerEntry[]>([]);

  useEffect(() => {
    api.get<{ catalog: CatalogEntry[] }>('/api/mcp/catalog').then(data => {
      setCatalog(data.catalog ?? []);
    });
    api.get<{ installed: string[] }>('/api/mcp/installed').then(data => {
      setInstalled(new Set(data.installed ?? []));
    });
    api.get<{ mcp_servers?: McpServerEntry[] }>('/api/settings').then(data => {
      setMcpServers(data.mcp_servers ?? []);
    });
  }, []);

  const showToast = (message: string, ok: boolean) => {
    setToast({ message, ok });
    setTimeout(() => setToast(null), 3500);
  };

  const handleInstall = async (entry: CatalogEntry) => {
    setInstalling(entry.name);
    try {
      const result = await api.post<{ ok: boolean; message: string }>('/api/mcp/install', { name: entry.name });
      if (result.ok) {
        setInstalled(prev => new Set([...prev, entry.name]));
        showToast(result.message, true);
      } else {
        showToast(result.message, false);
      }
    } catch {
      showToast('Something went wrong. Please try again.', false);
    } finally {
      setInstalling(null);
    }
  };

  const isAllowedInChat = (name: string) =>
    mcpServers.some(s => s.name === name && s.allowed_in_chat === true);

  const handleToggleChat = async (name: string) => {
    const exists = mcpServers.some(s => s.name === name);
    const next = exists
      ? mcpServers.map(s =>
          s.name === name ? { ...s, allowed_in_chat: !(s.allowed_in_chat === true) } : s
        )
      : [...mcpServers, { name, allowed_in_chat: true }];
    const previous = mcpServers;
    setMcpServers(next);
    try {
      await api.patch('/api/settings', { mcp_servers: next });
    } catch {
      setMcpServers(previous);
      showToast('Could not save that change. Please try again.', false);
    }
  };

  const filtered = catalog.filter(entry =>
    entry.name.toLowerCase().includes(search.toLowerCase()) ||
    entry.description.toLowerCase().includes(search.toLowerCase())
  );

  // Styled to sit inside the Settings card wrapper (Settings.tsx supplies the
  // card background and border), matching the Tailwind patterns used by the
  // other Settings cards in both light and dark mode.
  return (
    <div className="space-y-4" data-testid="mcp-installer">
      {toast && (
        <div
          className={`rounded-lg border px-3 py-2 text-sm ${
            toast.ok
              ? 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'
              : 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300'
          }`}
          data-testid="mcp-installer-toast"
        >
          {toast.message}
        </div>
      )}

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Add tools</h2>
        {onClose && (
          <button
            className="p-1 rounded-lg text-slate-500 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            onClick={onClose}
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        )}
      </div>

      <div className="relative">
        <Icon
          name="search"
          size={18}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none"
        />
        <input
          type="text"
          placeholder="Search tools..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg pl-9 pr-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
          data-testid="mcp-installer-search"
        />
      </div>

      {installed.size > 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400" data-testid="mcp-allow-chat-warning">
          Allowing a tool in chat means chat can read from and act on that service.
        </p>
      )}

      <ul className="divide-y divide-slate-200 dark:divide-slate-800" data-testid="mcp-installer-list">
        {filtered.map(entry => {
          const isInstalled = installed.has(entry.name);
          const isInstalling = installing === entry.name;

          return (
            <li
              key={entry.name}
              className="flex items-start gap-3 py-3 first:pt-0 last:pb-0"
              data-testid={`mcp-entry-${entry.name}`}
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
                <Icon name={entry.icon} size={20} className="text-slate-600 dark:text-slate-400" />
              </span>
              <div className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-slate-900 dark:text-white">{entry.name}</span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">{entry.description}</span>
                {entry.requires_auth && entry.auth_hint && (
                  <span className="mt-0.5 block text-[11px] text-amber-600 dark:text-amber-400">{entry.auth_hint}</span>
                )}
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1.5">
                {isInstalled ? (
                  <>
                    <span
                      className="rounded-full border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/30 px-2 py-0.5 text-xs font-medium text-green-700 dark:text-green-400"
                      data-testid={`mcp-badge-${entry.name}`}
                    >
                      Installed
                    </span>
                    <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
                      <input
                        type="checkbox"
                        checked={isAllowedInChat(entry.name)}
                        onChange={() => handleToggleChat(entry.name)}
                        className="h-3.5 w-3.5 accent-blue-600"
                        data-testid={`mcp-allow-chat-${entry.name}`}
                      />
                      Allow in chat
                    </label>
                  </>
                ) : (
                  <button
                    className="px-3 py-1.5 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-medium text-slate-900 dark:text-white hover:border-blue-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={() => handleInstall(entry)}
                    disabled={isInstalling}
                    data-testid={`mcp-install-btn-${entry.name}`}
                  >
                    {isInstalling ? 'Adding...' : 'Add'}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
