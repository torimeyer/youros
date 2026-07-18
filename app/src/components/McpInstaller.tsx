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

  return (
    <div className="mcp-installer" data-testid="mcp-installer">
      {toast && (
        <div
          className={`mcp-installer__toast ${toast.ok ? 'mcp-installer__toast--ok' : 'mcp-installer__toast--err'}`}
          data-testid="mcp-installer-toast"
        >
          {toast.message}
        </div>
      )}

      <div className="mcp-installer__header">
        <h2 className="mcp-installer__title">Add tools</h2>
        {onClose && (
          <button className="mcp-installer__close" onClick={onClose} aria-label="Close">
            <Icon name="close" />
          </button>
        )}
      </div>

      <div className="mcp-installer__search">
        <Icon name="search" />
        <input
          type="text"
          placeholder="Search tools..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          data-testid="mcp-installer-search"
        />
      </div>

      {installed.size > 0 && (
        <p className="mcp-installer__chat-warning" data-testid="mcp-allow-chat-warning">
          Allowing a tool in chat means chat can read from and act on that service.
        </p>
      )}

      <ul className="mcp-installer__list" data-testid="mcp-installer-list">
        {filtered.map(entry => {
          const isInstalled = installed.has(entry.name);
          const isInstalling = installing === entry.name;

          return (
            <li key={entry.name} className="mcp-installer__item" data-testid={`mcp-entry-${entry.name}`}>
              <span className="mcp-installer__item-icon">
                <Icon name={entry.icon} />
              </span>
              <div className="mcp-installer__item-body">
                <span className="mcp-installer__item-name">{entry.name}</span>
                <span className="mcp-installer__item-desc">{entry.description}</span>
                {entry.requires_auth && entry.auth_hint && (
                  <span className="mcp-installer__item-hint">{entry.auth_hint}</span>
                )}
              </div>
              <div className="mcp-installer__item-action">
                {isInstalled ? (
                  <>
                    <span
                      className="mcp-installer__badge mcp-installer__badge--installed"
                      data-testid={`mcp-badge-${entry.name}`}
                    >
                      Installed
                    </span>
                    <label className="mcp-installer__chat-toggle">
                      <input
                        type="checkbox"
                        checked={isAllowedInChat(entry.name)}
                        onChange={() => handleToggleChat(entry.name)}
                        data-testid={`mcp-allow-chat-${entry.name}`}
                      />
                      Allow in chat
                    </label>
                  </>
                ) : (
                  <button
                    className="mcp-installer__install-btn"
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
