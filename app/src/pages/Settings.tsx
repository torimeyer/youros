import { useState, useEffect, useRef } from 'react';
import { useAppStore, PROVIDER_TO_MODEL, type AccentColor } from '../stores/app';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { api } from '../lib/api';

interface MCPServer {
  name: string;
  url: string;
  auth_token?: string;
  enabled: boolean;
}

interface OstkMCPServer {
  name: string;
  command: string;
}

interface MCPDirectoryEntry {
  name: string;
  description: string;
  icon: string;
  npmPackage: string;
  setupCommand: string;
  requiresAuth: boolean;
  authHint?: string;
}

const MCP_DIRECTORY: MCPDirectoryEntry[] = [
  { name: 'Filesystem', description: 'Access local files and folders on your machine', icon: 'folder_open', npmPackage: '@modelcontextprotocol/server-filesystem', setupCommand: 'npx -y @modelcontextprotocol/server-filesystem /path/to/allowed/dir', requiresAuth: false },
  { name: 'GitHub', description: 'Access repos, issues, and pull requests', icon: 'code', npmPackage: '@modelcontextprotocol/server-github', setupCommand: 'npx -y @modelcontextprotocol/server-github', requiresAuth: true, authHint: 'Needs a GitHub personal access token (set GITHUB_PERSONAL_ACCESS_TOKEN)' },
  { name: 'Postgres', description: 'Query and manage your databases', icon: 'database', npmPackage: '@modelcontextprotocol/server-postgres', setupCommand: 'npx -y @modelcontextprotocol/server-postgres postgresql://localhost/mydb', requiresAuth: false },
  { name: 'Brave Search', description: 'Search the web using Brave', icon: 'travel_explore', npmPackage: '@modelcontextprotocol/server-brave-search', setupCommand: 'npx -y @modelcontextprotocol/server-brave-search', requiresAuth: true, authHint: 'Needs a Brave Search API key (set BRAVE_API_KEY)' },
  { name: 'Puppeteer', description: 'Automate a web browser for scraping or testing', icon: 'web', npmPackage: '@modelcontextprotocol/server-puppeteer', setupCommand: 'npx -y @modelcontextprotocol/server-puppeteer', requiresAuth: false },
  { name: 'Memory', description: 'Persistent notes and memory that last across sessions', icon: 'psychology', npmPackage: '@modelcontextprotocol/server-memory', setupCommand: 'npx -y @modelcontextprotocol/server-memory', requiresAuth: false },
  { name: 'Google Calendar', description: 'View and manage your calendar events', icon: 'calendar_month', npmPackage: 'mcp-server-google-calendar', setupCommand: 'npx -y mcp-server-google-calendar', requiresAuth: true, authHint: 'Needs Google OAuth credentials (client ID and secret)' },
  { name: 'Slack', description: 'Send messages and read channels', icon: 'forum', npmPackage: '@modelcontextprotocol/server-slack', setupCommand: 'npx -y @modelcontextprotocol/server-slack', requiresAuth: true, authHint: 'Needs a Slack Bot token (set SLACK_BOT_TOKEN)' },
  { name: 'Figma', description: 'Access and inspect design files', icon: 'palette', npmPackage: 'figma-mcp', setupCommand: 'npx -y figma-mcp', requiresAuth: true, authHint: 'Needs a Figma personal access token (set FIGMA_TOKEN)' },
  { name: 'Google Drive', description: 'Access Google Docs, Sheets, and Drive files', icon: 'folder_shared', npmPackage: '@modelcontextprotocol/server-gdrive', setupCommand: 'npx -y @modelcontextprotocol/server-gdrive', requiresAuth: true, authHint: 'Needs Google OAuth credentials (client ID and secret)' },
];

interface SettingsData {
  dark_mode?: boolean;
  accent_color?: string;
  os_name?: string;
  features?: Record<string, boolean>;
  provider?: string;
  model?: string;
  notifications?: Record<string, boolean>;
  quiet_hours?: boolean;
  mcp_servers?: MCPServer[];
  [key: string]: unknown;
}

const featureIcons: Record<string, string> = {
  'Chat': 'chat',
  'Tasks': 'task_alt',
  'Hay/Ideas': 'lightbulb',
  'Agents': 'smart_toy',
  'Projects': 'folder',
  'Docs': 'description',
  'Transcripts': 'mic',
};

export default function Settings() {
  const {
    osName, setOsName,
    darkMode, toggleDarkMode,
    accentColor, setAccentColor,
    features, setFeatures,
    setDefaultChatModel,
    useOstkTerms, setUseOstkTerms,
    powerUserMode, setPowerUserMode,
  } = useAppStore();

  const [selectedProvider, setSelectedProvider] = useState('Anthropic');
  const [defaultLlm, setDefaultLlm] = useState('Anthropic');
  // Chat backend preference: "auto" picks the subscription when ready,
  // otherwise falls back to the Anthropic key. Users can force either
  // pathway from the Settings page.
  const [chatBackendPreference, setChatBackendPreference] = useState<'auto' | 'claude_code' | 'anthropic_api'>('auto');
  const [claudeCodeReady, setClaudeCodeReady] = useState<boolean | null>(null);
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({ Anthropic: '', 'Google Gemini': '' });
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [selectedModel, setSelectedModel] = useState('claude-opus-4-20250514');
  const [notifications, setNotifications] = useState([
    { label: 'Agent Complete', enabled: true },
    { label: 'Agent Needs Input', enabled: true },
    { label: 'Agent Failed', enabled: true },
    { label: 'Approval Needed', enabled: true },
  ]);
  const [quietHours, setQuietHours] = useState(true);
  const [autoTemplateMatching, setAutoTemplateMatching] = useState(true);
  const [morningBriefingEnabled, setMorningBriefingEnabled] = useState(true);
  const [showAllKeys, setShowAllKeys] = useState(false);
  const [keySaveStatus, setKeySaveStatus] = useState<string | null>(null);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [newMcpName, setNewMcpName] = useState('');
  const [newMcpUrl, setNewMcpUrl] = useState('');
  const [newMcpToken, setNewMcpToken] = useState('');
  const [showBrowse, setShowBrowse] = useState(false);
  const [browseSearch, setBrowseSearch] = useState('');
  const [expandedEntry, setExpandedEntry] = useState<string | null>(null);
  const [googleOAuthAvailable, setGoogleOAuthAvailable] = useState(false);
  const [googleConnected, setGoogleConnected] = useState(false);
  const [keyAvailable, setKeyAvailable] = useState<Record<string, boolean>>({ Anthropic: false, 'Google Gemini': false });
  const [keySource, setKeySource] = useState<Record<string, string>>({});
  const [ostkMcpServers, setOstkMcpServers] = useState<OstkMCPServer[]>([]);

  // Sync state
  const [syncConfigured, setSyncConfigured] = useState(false);
  const [syncRepoUrl, setSyncRepoUrl] = useState<string | null>(null);
  const [syncLastSynced, setSyncLastSynced] = useState<string | null>(null);
  const [syncRepoInput, setSyncRepoInput] = useState('');
  const [syncStatus, setSyncStatus] = useState<string | null>(null);
  const [syncLoading, setSyncLoading] = useState(false);

  // Shared links state
  interface ShareRecord {
    token: string;
    share_type: string;
    title: string;
    created_at: string;
    expires_at: string;
    expired: boolean;
  }
  const [shares, setShares] = useState<ShareRecord[]>([]);
  const [sharesLoading, setSharesLoading] = useState(false);
  const [revokingToken, setRevokingToken] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await api.get<SettingsData>('/settings');
        if (data.accent_color) setAccentColor(data.accent_color as AccentColor);
        if (data.os_name) setOsName(data.os_name);
        if (data.dark_mode !== undefined && data.dark_mode !== darkMode) {
          // Set directly via store to avoid a toggle flash
          localStorage.setItem('myos-dark-mode', String(data.dark_mode));
          useAppStore.setState({ darkMode: data.dark_mode });
        }
        if (data.features) {
          // Build a case-insensitive lookup so both "tasks" and "Tasks" work.
          const featureLookup: Record<string, boolean> = {};
          for (const [key, val] of Object.entries(data.features)) {
            featureLookup[key.toLowerCase()] = val as boolean;
          }
          setFeatures(
            features.map((f) => ({
              ...f,
              enabled: featureLookup[f.label.toLowerCase()] ?? f.enabled,
            }))
          );
        }
        if (data.provider) {
          setSelectedProvider(data.provider);
        }
        // Load the default LLM from the saved default_model field
        if ((data as any).default_model) {
          const raw = (data as any).default_model.replace(/^@/, '');
          const providerName = raw === 'gemini' ? 'Google Gemini' : 'Anthropic';
          setDefaultLlm(providerName);
          setDefaultChatModel(raw);
        } else if (data.provider) {
          // Fall back to provider if default_model is not set
          setDefaultLlm(data.provider);
          const chatModel = PROVIDER_TO_MODEL[data.provider] ?? 'claude';
          setDefaultChatModel(chatModel);
        }
        // API keys are now stored in the system keychain, not in settings.
        // The input fields start empty. Users type a new key to save it.
        if (data.model) setSelectedModel(data.model);
        if (data.notifications) {
          setNotifications((prev) =>
            prev.map((n) => ({
              ...n,
              enabled: data.notifications![n.label] ?? n.enabled,
            }))
          );
        }
        if (data.quiet_hours !== undefined) setQuietHours(data.quiet_hours);
        if ((data as any).auto_template_matching !== undefined) {
          setAutoTemplateMatching((data as any).auto_template_matching);
        }
        if ((data as any).morning_briefing_enabled !== undefined) {
          setMorningBriefingEnabled((data as any).morning_briefing_enabled);
        }
        if ((data as any).use_ostk_terms !== undefined) setUseOstkTerms((data as any).use_ostk_terms);
        if (data.mcp_servers) setMcpServers(data.mcp_servers);
        const prefRaw = (data as any).chat_backend_preference;
        if (prefRaw === 'auto' || prefRaw === 'claude_code' || prefRaw === 'anthropic_api') {
          setChatBackendPreference(prefRaw);
        }
      } catch {
        // API not available, use defaults
      }
    };
    fetchSettings();
    // Check whether the local Claude subscription program is ready.
    api.get<{ claude_code_available?: boolean }>('/settings/chat-backend-status')
      .then((data) => setClaudeCodeReady(!!data.claude_code_available))
      .catch(() => setClaudeCodeReady(false));
    api.get<{ google_oauth_available?: boolean; google_connected?: boolean; anthropic?: boolean; gemini?: boolean; anthropic_source?: string; gemini_source?: string }>('/secrets/key-status')
      .then((data) => {
        setGoogleOAuthAvailable(data.google_oauth_available ?? false);
        setGoogleConnected(data.google_connected ?? false);
        setKeyAvailable({ Anthropic: data.anthropic ?? false, 'Google Gemini': data.gemini ?? false });
        setKeySource({ Anthropic: data.anthropic_source ?? 'none', 'Google Gemini': data.gemini_source ?? 'none' });
      })
      .catch(() => {});
    api.get<{ ostk_servers?: OstkMCPServer[] }>('/settings/mcp-servers')
      .then((data) => setOstkMcpServers(data.ostk_servers ?? []))
      .catch(() => {});
    api.get<{ configured?: boolean; remote_url?: string | null; last_synced?: string | null }>('/sync/status')
      .then((data) => {
        setSyncConfigured(data.configured ?? false);
        setSyncRepoUrl(data.remote_url ?? null);
        setSyncLastSynced(data.last_synced ?? null);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const accentColors = [
    { color: 'bg-blue-500', name: 'blue' },
    { color: 'bg-pink-500', name: 'pink' },
    { color: 'bg-purple-500', name: 'purple' },
    { color: 'bg-cyan-500', name: 'cyan' },
    { color: 'bg-orange-500', name: 'orange' },
  ];

  const shortcuts = [
    { label: 'Command Palette', keys: '\u2318K' },
    { label: 'Toggle Chat', keys: '\u2318L' },
    { label: 'New Task', keys: '\u2318N' },
  ];

  const allShortcuts = [
    ...shortcuts,
    { label: 'Go to Home', keys: '\u23181' },
    { label: 'Go to Tasks', keys: '\u23182' },
    { label: 'Go to Timeline', keys: '\u23183' },
    { label: 'Go to Ideas', keys: '\u23184' },
    { label: 'Go to Agents', keys: '\u23185' },
    { label: 'Go to Files', keys: '\u23186' },
    { label: 'Go to Transcripts', keys: '\u23187' },
    { label: 'Go to Settings', keys: '\u23188' },
  ];

  const providers = [
    { name: 'Anthropic', model: 'Claude' },
    { name: 'Google Gemini', model: 'Gemini' },
  ];

  const fetchShares = async () => {
    setSharesLoading(true);
    try {
      const res = await api.get<{ shares: ShareRecord[] }>('/shares');
      setShares(res.shares ?? []);
    } catch {
      // ignore
    } finally {
      setSharesLoading(false);
    }
  };

  useEffect(() => {
    fetchShares();
  }, []);

  const revokeShare = async (token: string) => {
    setRevokingToken(token);
    try {
      await api.delete(`/shares/${token}`);
      setShares((prev) => prev.filter((s) => s.token !== token));
    } catch {
      // ignore
    } finally {
      setRevokingToken(null);
    }
  };

  const saveMcpServers = (servers: MCPServer[]) => {
    setMcpServers(servers);
    api.patch('/settings', { mcp_servers: servers }).catch(() => {});
  };

  const handleAddMcpServer = () => {
    const name = newMcpName.trim();
    const url = newMcpUrl.trim();
    if (!name || !url) return;
    const server: MCPServer = { name, url, auth_token: newMcpToken.trim() || undefined, enabled: true };
    saveMcpServers([...mcpServers, server]);
    setNewMcpName('');
    setNewMcpUrl('');
    setNewMcpToken('');
  };

  const handleRemoveMcpServer = (index: number) => {
    saveMcpServers(mcpServers.filter((_, i) => i !== index));
  };

  const handleToggleMcpServer = (index: number) => {
    saveMcpServers(mcpServers.map((s, i) => i === index ? { ...s, enabled: !s.enabled } : s));
  };

  const handleSelectDirectoryServer = (entry: MCPDirectoryEntry) => {
    setNewMcpName(entry.name);
    setNewMcpUrl('');
    setNewMcpToken('');
    setShowBrowse(false);
    setBrowseSearch('');
  };

  const isServerAdded = (entryName: string) =>
    mcpServers.some((s) => s.name.toLowerCase() === entryName.toLowerCase());

  const filteredDirectory = MCP_DIRECTORY.filter((entry) => {
    const q = browseSearch.toLowerCase();
    return entry.name.toLowerCase().includes(q) || entry.description.toLowerCase().includes(q);
  });

  const handleDarkModeToggle = (wantDark: boolean) => {
    if (wantDark !== darkMode) {
      toggleDarkMode();
    }
    api.patch('/settings', { dark_mode: wantDark }).catch(() => {});
  };

  const handleAccentColor = (name: string) => {
    setAccentColor(name as AccentColor);
    api.patch('/settings', { accent_color: name }).catch(() => {});
  };

  const handleOsNameBlur = () => {
    api.patch('/settings', { os_name: osName }).catch(() => {});
  };

  const handleFeatureToggle = (index: number) => {
    const updated = features.map((f, i) =>
      i === index ? { ...f, enabled: !f.enabled } : f
    );
    setFeatures(updated);
    const featuresObj: Record<string, boolean> = {};
    updated.forEach((f: { label: string; enabled: boolean }) => {
      featuresObj[f.label] = f.enabled;
    });
    api.patch('/settings', { features: featuresObj }).catch(() => {});
  };

  const handleProviderSelect = (name: string) => {
    setSelectedProvider(name);
    api.patch('/settings', { provider: name }).catch(() => {});
  };

  const handleChatBackendPreferenceChange = (value: 'auto' | 'claude_code' | 'anthropic_api') => {
    setChatBackendPreference(value);
    api.patch('/settings', { chat_backend_preference: value }).catch(() => {});
  };

  const handleDefaultLlmChange = (name: string) => {
    setDefaultLlm(name);
    const chatModel = PROVIDER_TO_MODEL[name] ?? 'claude';
    setDefaultChatModel(chatModel);
    api.patch('/settings', { default_model: `@${chatModel}` }).catch(() => {});
  };

  const PROVIDER_SECRET_NAME: Record<string, string> = {
    Anthropic: 'ANTHROPIC_API_KEY',
    'Google Gemini': 'GEMINI_API_KEY',
  };

  const handleApiKeySave = () => {
    const secretName = PROVIDER_SECRET_NAME[selectedProvider];
    const value = apiKeys[selectedProvider]?.trim();
    if (secretName && value) {
      api.post('/secrets', { key: secretName, value })
        .then(() => {
          setKeySaveStatus('Saved to keychain');
          setApiKeys(prev => ({ ...prev, [selectedProvider]: '' }));
          setKeyAvailable(prev => ({ ...prev, [selectedProvider]: true }));
          setKeySource(prev => ({ ...prev, [selectedProvider]: 'keychain' }));
          setTimeout(() => setKeySaveStatus(null), 2000);
        })
        .catch(() => {
          setKeySaveStatus('Error saving');
          setTimeout(() => setKeySaveStatus(null), 2000);
        });
    }
  };

  const handleModelChange = (model: string) => {
    setSelectedModel(model);
    api.patch('/settings', { model }).catch(() => {});
  };

  const handleNotificationToggle = (index: number) => {
    const updated = notifications.map((n, i) =>
      i === index ? { ...n, enabled: !n.enabled } : n
    );
    setNotifications(updated);
    const notifObj: Record<string, boolean> = {};
    updated.forEach((n) => {
      notifObj[n.label] = n.enabled;
    });
    api.patch('/settings', { notifications: notifObj }).catch(() => {});
  };

  const handleQuietHoursToggle = () => {
    const next = !quietHours;
    setQuietHours(next);
    api.patch('/settings', { quiet_hours: next }).catch(() => {});
  };

  const handleAutoTemplateMatchingToggle = () => {
    const next = !autoTemplateMatching;
    setAutoTemplateMatching(next);
    api.patch('/settings', { auto_template_matching: next }).catch(() => {});
  };

  const handleMorningBriefingToggle = () => {
    const next = !morningBriefingEnabled;
    setMorningBriefingEnabled(next);
    api.patch('/settings', { morning_briefing_enabled: next }).catch(() => {});
  };

  const handleImport = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      await api.put('/settings', parsed);
      // Reload to pick up new settings
      window.location.reload();
    } catch {
      // Invalid JSON or API error
    }
    // Reset the input so the same file can be selected again
    e.target.value = '';
  };

  const handleExport = async () => {
    try {
      const data = await api.get<SettingsData>('/settings');
      // Strip sensitive fields before exporting
      const safe = { ...data } as Record<string, unknown>;
      delete safe.anthropic_api_key;
      delete safe.gemini_api_key;
      delete safe.gemini_oauth_access_token;
      delete safe.gemini_oauth_refresh_token;
      delete safe.gemini_auth_method;
      const blob = new Blob([JSON.stringify(safe, null, 2)], {
        type: 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'myos-settings.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      // handle error silently
    }
  };

  const handleSetupSync = async () => {
    const url = syncRepoInput.trim();
    if (!url) return;
    setSyncLoading(true);
    setSyncStatus(null);
    try {
      await api.post('/sync/configure', { repo_url: url });
      setSyncConfigured(true);
      setSyncRepoUrl(url);
      setSyncRepoInput('');
      setSyncStatus('Connected');
      setTimeout(() => setSyncStatus(null), 3000);
    } catch {
      setSyncStatus('Could not connect. Check the URL and try again.');
    } finally {
      setSyncLoading(false);
    }
  };

  const handleSyncNow = async () => {
    setSyncLoading(true);
    setSyncStatus(null);
    try {
      await api.post('/sync/push', {});
      await api.post('/sync/pull', {});
      const statusData = await api.get<{ last_synced?: string | null }>('/sync/status');
      setSyncLastSynced(statusData.last_synced ?? null);
      setSyncStatus('Synced');
      setTimeout(() => setSyncStatus(null), 3000);
    } catch {
      setSyncStatus('Sync failed. Check your network and repo access.');
    } finally {
      setSyncLoading(false);
    }
  };

  const handleDisconnectSync = async () => {
    setSyncLoading(true);
    try {
      await api.post('/sync/disconnect', {});
      setSyncConfigured(false);
      setSyncRepoUrl(null);
      setSyncLastSynced(null);
      setSyncStatus('Disconnected');
      setTimeout(() => setSyncStatus(null), 3000);
    } catch {
      setSyncStatus('Could not disconnect.');
    } finally {
      setSyncLoading(false);
    }
  };

  const cardClass =
    'bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors';

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Settings" />

      <div className="pt-20 p-8 space-y-6">
        {/* Row 1: Appearance + Shortcuts */}
        <div className="grid grid-cols-2 gap-6">
          {/* Appearance */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-5">Appearance</h2>

            {/* Color Mode */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">Color Mode</label>
              <div className="flex gap-2">
                <button
                  onClick={() => handleDarkModeToggle(false)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    !darkMode
                      ? 'accent-bg !text-white'
                      : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  Light
                </button>
                <button
                  onClick={() => handleDarkModeToggle(true)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    darkMode
                      ? 'accent-bg !text-white'
                      : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  Dark
                </button>
              </div>
            </div>

            {/* Accent Color */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">Accent Color</label>
              <div className="flex gap-3">
                {accentColors.map((c) => (
                  <button
                    key={c.name}
                    onClick={() => handleAccentColor(c.name)}
                    className={`w-8 h-8 rounded-full ${c.color} flex items-center justify-center transition-transform hover:scale-110`}
                  >
                    {accentColor === c.name && (
                      <Icon name="check" className="text-white" size={16} />
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* OS Identifier */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">OS Identifier</label>
              <input
                type="text"
                value={osName}
                onChange={(e) => setOsName(e.target.value)}
                onBlur={handleOsNameBlur}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>

            {/* Terminology */}
            <div>
              <label className="text-sm text-slate-400 mb-2 block">Terminology</label>
              <div className="flex gap-2">
                <button
                  onClick={() => { setUseOstkTerms(false); api.patch('/settings', { use_ostk_terms: false }).catch(() => {}) }}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    !useOstkTerms ? 'accent-bg !text-white' : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  Standard
                </button>
                <button
                  onClick={() => { setUseOstkTerms(true); api.patch('/settings', { use_ostk_terms: true }).catch(() => {}) }}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    useOstkTerms ? 'accent-bg !text-white' : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  ostk
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                {useOstkTerms
                  ? 'Using ostk terms: Needles, Hay, Straws'
                  : 'Using standard terms: Tasks, Ideas, Notes'}
              </p>
            </div>
          </div>

          {/* Shortcuts */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-5">Shortcuts</h2>
            <div className="space-y-3">
              {shortcuts.map((s) => (
                <div key={s.label} className="flex items-center justify-between py-2">
                  <span className="text-sm text-slate-300">{s.label}</span>
                  <kbd className="px-2.5 py-1 bg-slate-800 border border-slate-700 rounded-md text-xs text-slate-300 font-mono">
                    {s.keys}
                  </kbd>
                </div>
              ))}
            </div>
            <button
              onClick={() => setShowAllKeys(!showAllKeys)}
              className="mt-4 text-sm text-blue-400 hover:text-blue-300 transition-colors"
            >
              {showAllKeys ? 'Show Less' : 'View All Keys'}
            </button>
            {showAllKeys && (
              <div className="mt-3 pt-3 border-t border-slate-800 space-y-3">
                {allShortcuts.slice(shortcuts.length).map((s) => (
                  <div key={s.label} className="flex items-center justify-between py-1">
                    <span className="text-sm text-slate-300">{s.label}</span>
                    <kbd className="px-2.5 py-1 bg-slate-800 border border-slate-700 rounded-md text-xs text-slate-300 font-mono">
                      {s.keys}
                    </kbd>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Row 2: System Features */}
        <div className={cardClass}>
          <h2 className="text-lg font-semibold mb-5">System Features</h2>
          <div className="flex gap-4 flex-wrap">
            {features.map((f: { label: string; enabled: boolean }, index: number) => (
              <div
                key={f.label}
                onClick={() => handleFeatureToggle(index)}
                className="flex items-center gap-2.5 px-4 py-3 bg-slate-800/50 rounded-lg border border-slate-700/50 cursor-pointer hover:border-slate-600 transition-colors"
              >
                <Icon name={featureIcons[f.label] || 'extension'} className="text-slate-300" size={20} />
                <span className="text-sm text-slate-300">{f.label}</span>
                <span
                  className={`w-2.5 h-2.5 rounded-full ${
                    f.enabled ? 'bg-green-400' : 'bg-slate-600'
                  }`}
                />
              </div>
            ))}
          </div>

          {/* Power user mode toggle */}
          <div className="mt-6 pt-6 border-t border-slate-800">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-slate-200">Power user mode</span>
                <div className="group relative">
                  <Icon name="help_outline" size={16} className="text-slate-500 hover:text-slate-300 cursor-help" />
                  <div className="absolute left-0 top-full mt-1 w-80 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                    <p className="font-semibold text-white mb-1">What this unlocks</p>
                    <p className="mb-2"><strong>Delegate tab:</strong> see suggested tasks an agent could pick up and hand them off with one click.</p>
                    <p className="mb-2"><strong>Shared Workspace tab:</strong> a message board where multiple agents leave each other notes mid-task, so they can build on each other's findings.</p>
                    <p>Both are useful when you run multiple agents in parallel. Most people will not need them.</p>
                  </div>
                </div>
              </div>
              <button
                onClick={() => setPowerUserMode(!powerUserMode)}
                className={`relative w-11 h-6 rounded-full transition-colors ${powerUserMode ? 'bg-blue-500' : 'bg-slate-700'}`}
                aria-pressed={powerUserMode}
                aria-label="Toggle power user mode"
              >
                <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${powerUserMode ? 'translate-x-5' : ''}`} />
              </button>
            </div>
            <p className="text-xs text-slate-500 mt-2">Shows advanced agent tabs (Delegate and Shared Workspace) in the Agents page.</p>
          </div>
        </div>

        {/* Row 3: AI Provider + Notifications */}
        <div className="grid grid-cols-2 gap-6">
          {/* AI Provider */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-5">AI Provider</h2>

            {/* Default Chat AI */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">Default Chat AI</label>
              <p className="text-xs text-slate-500 mb-3">
                New conversations will use this AI by default. You can still switch mid-chat by typing @gemini or @claude.
              </p>
              <div className="grid grid-cols-2 gap-3">
                {providers.map((p) => (
                  <div
                    key={`default-${p.name}`}
                    onClick={() => handleDefaultLlmChange(p.name)}
                    data-testid={`default-llm-${p.name.toLowerCase().replace(/\s+/g, '-')}`}
                    className={`p-3 rounded-lg border text-center cursor-pointer transition-colors ${
                      defaultLlm === p.name
                        ? 'accent-border accent-highlight'
                        : 'border-slate-700 bg-slate-800/50 hover:border-slate-600'
                    }`}
                  >
                    <p className="text-sm font-medium">{p.model}</p>
                    <p className="text-xs text-slate-500">{p.name}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Provider for API Key setup */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">Set Up Provider</label>
              <div className="grid grid-cols-2 gap-3">
                {providers.map((p) => (
                  <div
                    key={p.name}
                    onClick={() => handleProviderSelect(p.name)}
                    className={`p-3 rounded-lg border text-center cursor-pointer transition-colors ${
                      selectedProvider === p.name
                        ? 'accent-border accent-highlight'
                        : 'border-slate-700 bg-slate-800/50 hover:border-slate-600'
                    }`}
                  >
                    <p className="text-sm font-medium">{p.name}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Connection */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-3 block">Connect {selectedProvider}</label>

              {/* Status indicator */}
              {keyAvailable[selectedProvider] ? (
                <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-white border border-green-300 rounded-lg">
                  <Icon name="check_circle" size={16} className="text-green-600" />
                  <span className="text-sm text-green-700">
                    Key available (stored in {keySource[selectedProvider] === 'keychain' ? 'system keychain' : keySource[selectedProvider] === 'env' ? 'environment' : 'settings'})
                  </span>
                </div>
              ) : (
                <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-amber-900/20 border border-amber-800/30 rounded-lg">
                  <Icon name="warning" size={16} className="text-amber-400" />
                  <span className="text-sm text-amber-400">No key found. Paste one below to save it securely.</span>
                </div>
              )}

              {/* Option 1: Sign in (Gemini OAuth or Anthropic console link) */}
              {selectedProvider === 'Google Gemini' ? (
                googleConnected ? (
                  <div className="mb-3 px-4 py-3 bg-white border border-green-300 rounded-lg flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Icon name="check_circle" size={18} className="text-green-600" />
                      <span className="text-sm text-green-700 font-medium">Connected with Google</span>
                    </div>
                    <button
                      onClick={async () => {
                        await api.post('/secrets/google/disconnect', {});
                        setGoogleConnected(false);
                        setKeyAvailable(prev => ({ ...prev, 'Google Gemini': false }));
                        setKeySource(prev => ({ ...prev, 'Google Gemini': 'none' }));
                      }}
                      className="text-xs text-slate-400 hover:text-red-400 transition-colors"
                    >
                      Disconnect
                    </button>
                  </div>
                ) : googleOAuthAvailable ? (
                  <button
                    onClick={() => window.open('/api/auth/google', '_self')}
                    className="w-full mb-3 px-4 py-2.5 bg-slate-800 border border-slate-700 rounded-lg text-sm font-medium text-white hover:border-blue-500 transition-colors flex items-center gap-2"
                  >
                    <Icon name="login" size={18} />
                    Sign in with Google
                  </button>
                ) : (
                  <p className="text-sm text-slate-400 mb-3">
                    Google sign-in is not set up yet. Paste a Gemini API key below.
                  </p>
                )
              ) : (
                <button
                  onClick={() => window.open('https://console.anthropic.com/settings/keys', '_blank')}
                  className="w-full mb-3 px-4 py-2.5 bg-slate-800 border border-slate-700 rounded-lg text-sm font-medium text-white hover:border-blue-500 transition-colors flex items-center gap-2"
                >
                  <Icon name="open_in_new" size={18} />
                  Get a key from Anthropic
                </button>
              )}

              {/* Gemini: recommend Cloud Console first, AI Studio as fallback */}
              {selectedProvider === 'Google Gemini' && (
                <div
                  className={`mb-3 p-3 rounded-lg text-xs space-y-2 border bg-gradient-to-r from-blue-500/10 to-cyan-500/10 border-blue-500/30 ${
                    darkMode ? 'text-slate-200' : 'text-slate-700'
                  }`}
                >
                  <p className={`font-medium ${darkMode ? 'text-white' : 'text-slate-900'}`}>Where to get a Gemini API key</p>
                  <p>
                    <span className={`font-medium ${darkMode ? 'text-white' : 'text-slate-900'}`}>Recommended.</span>{' '}
                    Use the same{' '}
                    <a
                      href="https://console.cloud.google.com"
                      target="_blank"
                      rel="noreferrer"
                      className={`underline ${darkMode ? 'text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
                    >
                      Google Cloud project
                    </a>{' '}
                    you already set up for Drive, Calendar, or Gmail. Three steps:
                  </p>
                  <ol className="list-decimal ml-5 space-y-1">
                    <li>
                      Enable{' '}
                      <a
                        href="https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com"
                        target="_blank"
                        rel="noreferrer"
                        className={`underline ${darkMode ? 'text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
                      >
                        "Generative Language API"
                      </a>{' '}
                      in the API library. It takes about 30 seconds.
                    </li>
                    <li>Open Credentials and click Create credentials, API key.</li>
                    <li>
                      Edit the new key and restrict it to "Generative Language API" under API restrictions. It only appears in the dropdown after step 1.
                    </li>
                  </ol>
                  <p>
                    <span className={`font-medium ${darkMode ? 'text-white' : 'text-slate-900'}`}>Chat only.</span>{' '}
                    Only using Gemini chat and nothing else from Google? Grab a free key at{' '}
                    <a
                      href="https://aistudio.google.com/apikey"
                      target="_blank"
                      rel="noreferrer"
                      className={`underline ${darkMode ? 'text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
                    >
                      Google AI Studio
                    </a>{' '}
                    instead. It ties to your personal Google account and is one click.
                  </p>
                </div>
              )}

              {/* Option 2: Paste API key (saved to system keychain) */}
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type={apiKeyVisible ? 'text' : 'password'}
                    value={apiKeys[selectedProvider] || ''}
                    onChange={(e) => setApiKeys(prev => ({ ...prev, [selectedProvider]: e.target.value }))}
                    onKeyDown={(e) => e.key === 'Enter' && handleApiKeySave()}
                    placeholder={keyAvailable[selectedProvider]
                      ? 'Paste a new key to replace the current one'
                      : (selectedProvider === 'Anthropic' ? 'Paste API key (sk-ant-xxxx...)' : 'Paste API key (AIzaSy...)')}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 pr-10 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
                  />
                  <button
                    onClick={() => setApiKeyVisible(!apiKeyVisible)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white transition-colors"
                  >
                    <Icon
                      name={apiKeyVisible ? 'visibility_off' : 'visibility'}
                      size={18}
                    />
                  </button>
                </div>
                <button
                  onClick={handleApiKeySave}
                  disabled={!apiKeys[selectedProvider]?.trim()}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                >
                  {keySaveStatus || 'Save to Keychain'}
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Keys are stored securely in your system keychain, not in a file.
              </p>
            </div>

            {/* Model Selector */}
            <div>
              <label className="text-sm text-slate-400 mb-2 block">Model</label>
              <select
                value={selectedModel}
                onChange={(e) => handleModelChange(e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors appearance-none cursor-pointer"
              >
                <option>claude-opus-4-20250514</option>
                <option>claude-sonnet-4-20250514</option>
                <option>claude-haiku-35-20241022</option>
              </select>
            </div>

            {/* Chat backend: pick the subscription or the API key.
                Default is auto, which uses the subscription when the
                local program is signed in and falls back to the key
                otherwise. */}
            <div className="mt-6 pt-5 border-t border-slate-800">
              <label className="text-sm text-slate-400 mb-2 block">Chat backend</label>
              <p className="text-xs text-slate-500 mb-3">
                Pick which sign-in powers your chat. Your Claude subscription is free if you already pay for Pro or Max. Using your Anthropic key costs money per message.
              </p>
              <div className="space-y-2" data-testid="chat-backend-radios">
                {[
                  { value: 'auto' as const, label: 'Auto pick the best one' },
                  { value: 'claude_code' as const, label: 'Always use my Claude subscription' },
                  { value: 'anthropic_api' as const, label: 'Always use my Anthropic API key' },
                ].map((opt) => (
                  <label
                    key={opt.value}
                    className={`flex items-center gap-3 p-2.5 rounded-lg border cursor-pointer transition-colors ${
                      chatBackendPreference === opt.value
                        ? 'accent-border accent-highlight'
                        : 'border-slate-700 bg-slate-800/50 hover:border-slate-600'
                    }`}
                  >
                    <input
                      type="radio"
                      name="chat-backend-preference"
                      value={opt.value}
                      checked={chatBackendPreference === opt.value}
                      onChange={() => handleChatBackendPreferenceChange(opt.value)}
                      className="accent-blue-500"
                    />
                    <span className="text-sm text-slate-200">{opt.label}</span>
                  </label>
                ))}
              </div>
              <div
                data-testid="claude-code-ready-indicator"
                className="flex items-center gap-2 mt-3 text-xs"
              >
                {claudeCodeReady === null ? (
                  <>
                    <span className="w-2 h-2 rounded-full bg-slate-500" />
                    <span className="text-slate-500">Checking your Claude sign-in...</span>
                  </>
                ) : claudeCodeReady ? (
                  <>
                    <span className="w-2 h-2 rounded-full bg-green-500" />
                    <span className="text-green-400">Claude subscription is ready</span>
                  </>
                ) : (
                  <>
                    <span className="w-2 h-2 rounded-full bg-slate-500" />
                    <span className="text-slate-400">Claude subscription is not installed or signed in</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Notifications */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-5">Notifications</h2>
            <div className="space-y-3">
              {notifications.map((n, index) => (
                <div key={n.label} className="flex items-center justify-between py-2">
                  <span className="text-sm text-slate-300">{n.label}</span>
                  <button
                    onClick={() => handleNotificationToggle(index)}
                    className={`w-10 h-6 rounded-full relative transition-colors ${
                      n.enabled ? 'accent-bg' : 'bg-slate-700'
                    }`}
                  >
                    <span
                      className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        n.enabled ? 'left-5' : 'left-1'
                      }`}
                    />
                  </button>
                </div>
              ))}
            </div>
            <div className="mt-5 pt-4 border-t border-slate-800">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-slate-300">Quiet Hours</p>
                  <p className="text-xs text-slate-500">10pm - 7am</p>
                </div>
                <button
                  onClick={handleQuietHoursToggle}
                  className={`w-10 h-6 rounded-full relative transition-colors ${
                    quietHours ? 'accent-bg' : 'bg-slate-700'
                  }`}
                >
                  <span
                    className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                      quietHours ? 'left-5' : 'left-1'
                    }`}
                  />
                </button>
              </div>
            </div>
            <div className="mt-5 pt-4 border-t border-slate-800">
              <h3 className="text-sm font-semibold text-slate-200 mb-3">Smart suggestions</h3>
              <div className="flex items-center justify-between">
                <div className="pr-3">
                  <p className="text-sm text-slate-300">Pick the right helper automatically</p>
                  <p className="text-xs text-slate-500">
                    When you ask a question, myOS chooses the best built-in or saved helper for the job.
                  </p>
                </div>
                <button
                  data-testid="auto-template-toggle"
                  onClick={handleAutoTemplateMatchingToggle}
                  className={`w-10 h-6 rounded-full relative transition-colors flex-shrink-0 ${
                    autoTemplateMatching ? 'accent-bg' : 'bg-slate-700'
                  }`}
                >
                  <span
                    className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                      autoTemplateMatching ? 'left-5' : 'left-1'
                    }`}
                  />
                </button>
              </div>
            </div>
            <div className="mt-5 pt-4 border-t border-slate-800">
              <h3 className="text-sm font-semibold text-slate-200 mb-3">Daily habits</h3>
              <div className="flex items-center justify-between">
                <div className="pr-3">
                  <p className="text-sm text-slate-300">Morning briefing</p>
                  <p className="text-xs text-slate-500">
                    Show a short summary of your day when you open the dashboard before noon.
                  </p>
                </div>
                <button
                  data-testid="morning-briefing-toggle"
                  onClick={handleMorningBriefingToggle}
                  className={`w-10 h-6 rounded-full relative transition-colors flex-shrink-0 ${
                    morningBriefingEnabled ? 'accent-bg' : 'bg-slate-700'
                  }`}
                >
                  <span
                    className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                      morningBriefingEnabled ? 'left-5' : 'left-1'
                    }`}
                  />
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Row 4: MCP Servers */}
        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-5">
            <h2 className="text-lg font-semibold">Connected Tools</h2>
            <span className="text-xs text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">MCP</span>
            <div className="group relative ml-1">
              <Icon name="help_outline" size={18} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                Connect external tool servers (like Stitch, Gmail, Calendar). ToriChat can use their tools the same way it uses built-in tools.
              </div>
            </div>
            <button
              onClick={() => setShowBrowse(true)}
              className="ml-auto flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
            >
              <Icon name="explore" size={16} />
              Browse
            </button>
          </div>

          {/* ostk-managed servers */}
          {ostkMcpServers.length > 0 && (
            <div className="mb-4">
              <p className="text-xs text-slate-500 mb-2 flex items-center gap-1.5">
                <Icon name="settings_suggest" size={14} />
                Managed by ostk (configured in your HUMANFILE)
              </p>
              <div className="space-y-2">
                {ostkMcpServers.map((server) => (
                  <div key={server.name} className="flex items-center gap-3 px-3 py-2.5 bg-slate-800/50 rounded-lg border border-emerald-900/40">
                    <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" title="Managed by ostk" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-slate-200 font-medium">{server.name}</p>
                      <p className="text-xs text-slate-500 truncate font-mono">{server.command}</p>
                    </div>
                    <span className="text-xs text-emerald-400/70 flex-shrink-0">ostk</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Manually added servers */}
          {mcpServers.length > 0 && (
            <div className="space-y-2 mb-4">
              {ostkMcpServers.length > 0 && (
                <p className="text-xs text-slate-500 mb-1 flex items-center gap-1.5">
                  <Icon name="tune" size={14} />
                  Added manually
                </p>
              )}
              {mcpServers.map((server, index) => (
                <div key={index} className="flex items-center gap-3 px-3 py-2.5 bg-slate-800/50 rounded-lg border border-slate-700/50">
                  <button
                    onClick={() => handleToggleMcpServer(index)}
                    className={`w-8 h-5 rounded-full relative flex-shrink-0 transition-colors ${server.enabled ? 'accent-bg' : 'bg-slate-700'}`}
                    title={server.enabled ? 'Disable' : 'Enable'}
                  >
                    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${server.enabled ? 'left-3.5' : 'left-0.5'}`} />
                  </button>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-slate-200 font-medium">{server.name}</p>
                    <p className="text-xs text-slate-500 truncate">{server.url}</p>
                  </div>
                  <button
                    onClick={() => handleRemoveMcpServer(index)}
                    className="p-1 text-slate-600 hover:text-red-400 transition-colors flex-shrink-0"
                    title="Remove"
                  >
                    <Icon name="delete" size={16} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Add new server */}
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                type="text"
                value={newMcpName}
                onChange={e => setNewMcpName(e.target.value)}
                placeholder="Server name (e.g. Stitch)"
                className="w-36 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
              <input
                type="text"
                value={newMcpUrl}
                onChange={e => setNewMcpUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAddMcpServer()}
                placeholder="Paste your server URL after running setup"
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
              <input
                type="password"
                value={newMcpToken}
                onChange={e => setNewMcpToken(e.target.value)}
                placeholder="Auth token (optional)"
                className="w-44 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
              <button
                onClick={handleAddMcpServer}
                disabled={!newMcpName.trim() || !newMcpUrl.trim()}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
              >
                Add
              </button>
            </div>
          </div>

          {/* Browse directory modal */}
          {showBrowse && (
            <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center" onClick={() => { setShowBrowse(false); setBrowseSearch(''); setExpandedEntry(null); }}>
              <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-lg max-h-[80vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
                {/* Modal header */}
                <div className="flex items-center justify-between px-5 pt-5 pb-3">
                  <h3 className="text-base font-semibold text-white">Server Directory</h3>
                  <button
                    onClick={() => { setShowBrowse(false); setBrowseSearch(''); setExpandedEntry(null); }}
                    className="p-1 text-slate-400 hover:text-white transition-colors"
                  >
                    <Icon name="close" size={20} />
                  </button>
                </div>

                {/* Search input */}
                <div className="px-5 pb-3">
                  <div className="relative">
                    <Icon name="search" size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                    <input
                      type="text"
                      value={browseSearch}
                      onChange={e => setBrowseSearch(e.target.value)}
                      placeholder="Search servers..."
                      autoFocus
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
                    />
                  </div>
                </div>

                {/* Info note */}
                <div className="px-5 pb-3">
                  <p className="text-xs text-slate-500 leading-relaxed">
                    MCP servers run locally on your machine. Run the setup command in your terminal, then add the server URL (usually http://localhost:PORT) here.
                  </p>
                </div>

                {/* Server list */}
                <div className="flex-1 overflow-y-auto px-5 pb-5 space-y-2">
                  {filteredDirectory.length === 0 && (
                    <p className="text-sm text-slate-500 text-center py-6">No servers match your search.</p>
                  )}
                  {filteredDirectory.map((entry) => {
                    const added = isServerAdded(entry.name);
                    const isExpanded = expandedEntry === entry.name;
                    return (
                      <div
                        key={entry.name}
                        className="bg-slate-800/50 rounded-lg border border-slate-700/50 hover:border-slate-600 transition-colors"
                      >
                        <div
                          className="flex items-center gap-3 px-3 py-2.5 cursor-pointer"
                          onClick={() => setExpandedEntry(isExpanded ? null : entry.name)}
                        >
                          <div className="w-8 h-8 rounded-lg bg-slate-700/60 flex items-center justify-center flex-shrink-0">
                            <Icon name={entry.icon} size={18} className="text-slate-300" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm text-slate-200 font-medium">{entry.name}</p>
                            <p className="text-xs text-slate-500">{entry.description}</p>
                          </div>
                          <Icon name={isExpanded ? 'expand_less' : 'expand_more'} size={20} className="text-slate-500 flex-shrink-0" />
                        </div>
                        {isExpanded && (
                          <div className="px-3 pb-3 space-y-2.5">
                            <div className="border-t border-slate-700/50 pt-2.5" />
                            {/* npm package */}
                            <div>
                              <p className="text-xs text-slate-400 mb-1">Package</p>
                              <code className="text-xs text-blue-400 bg-slate-900/80 px-2 py-1 rounded font-mono block break-all">{entry.npmPackage}</code>
                            </div>
                            {/* Setup command */}
                            <div>
                              <p className="text-xs text-slate-400 mb-1">Setup command</p>
                              <div className="flex items-center gap-2">
                                <code className="text-xs text-emerald-400 bg-slate-900/80 px-2 py-1 rounded font-mono flex-1 break-all">{entry.setupCommand}</code>
                                <button
                                  onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(entry.setupCommand); }}
                                  className="p-1 text-slate-500 hover:text-white transition-colors flex-shrink-0"
                                  title="Copy command"
                                >
                                  <Icon name="content_copy" size={14} />
                                </button>
                              </div>
                            </div>
                            {/* Auth hint */}
                            {entry.requiresAuth && entry.authHint && (
                              <div className="flex items-start gap-2 text-xs text-amber-400/80 bg-amber-900/20 px-2 py-1.5 rounded">
                                <Icon name="key" size={14} className="flex-shrink-0 mt-0.5" />
                                <span>{entry.authHint}</span>
                              </div>
                            )}
                            {/* Add button */}
                            {added ? (
                              <span className="flex items-center gap-1 text-xs text-green-400">
                                <Icon name="check_circle" size={16} />
                                Already added
                              </span>
                            ) : (
                              <button
                                onClick={(e) => { e.stopPropagation(); handleSelectDirectoryServer(entry); }}
                                className="w-full px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-md text-xs font-medium text-white transition-colors"
                              >
                                Add
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Row 5: Sync */}
        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-5">
            <h2 className="text-lg font-semibold">Sync</h2>
            <div className="group relative ml-1">
              <Icon name="help_outline" size={18} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                Keep your settings the same across all your devices using a private git repo you own.
              </div>
            </div>
          </div>

          {syncConfigured ? (
            <div>
              <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-emerald-900/20 border border-emerald-800/30 rounded-lg">
                <Icon name="check_circle" size={16} className="text-emerald-400" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-emerald-300 font-medium">Sync is on</p>
                  <p className="text-xs text-slate-400 truncate">{syncRepoUrl}</p>
                </div>
              </div>
              {syncLastSynced && (
                <p className="text-xs text-slate-500 mb-4">
                  Last synced: {new Date(syncLastSynced).toLocaleString()}
                </p>
              )}
              {syncStatus && (
                <p className="text-sm text-slate-300 mb-3">{syncStatus}</p>
              )}
              <div className="flex gap-3">
                <button
                  onClick={handleSyncNow}
                  disabled={syncLoading}
                  data-testid="sync-now-button"
                  className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm font-medium text-white transition-colors"
                >
                  <Icon name="sync" size={18} />
                  {syncLoading ? 'Syncing...' : 'Sync now'}
                </button>
                <button
                  onClick={handleDisconnectSync}
                  disabled={syncLoading}
                  data-testid="sync-disconnect-button"
                  className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-400 hover:text-red-400 hover:border-red-800 transition-colors"
                >
                  <Icon name="link_off" size={18} />
                  Disconnect
                </button>
              </div>
            </div>
          ) : (
            <div>
              <p className="text-sm text-slate-400 mb-4">
                Enter the URL of a private git repo you own. myOS will use it to keep your settings the same across all your devices.
              </p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={syncRepoInput}
                  onChange={(e) => setSyncRepoInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSetupSync()}
                  placeholder="git@github.com:you/myos-sync.git"
                  data-testid="sync-repo-input"
                  className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
                />
                <button
                  onClick={handleSetupSync}
                  disabled={syncLoading || !syncRepoInput.trim()}
                  data-testid="sync-setup-button"
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                >
                  {syncLoading ? 'Setting up...' : 'Set up sync'}
                </button>
              </div>
              {syncStatus && (
                <p className="text-sm text-red-400 mt-2">{syncStatus}</p>
              )}
            </div>
          )}
        </div>

        {/* Row 6: Data Management */}
        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-2">
            <h2 className="text-lg font-semibold">Data Management</h2>
            <div className="group relative">
              <Icon name="help_outline" size={18} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                Export saves your preferences (theme, features, notifications) to a JSON file. API keys are stored separately in the system keychain and are not exported. Import loads a previously exported file to restore your preferences.
              </div>
            </div>
          </div>
          <div className="flex gap-3">
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleFileChange}
              className="hidden"
            />
            <button
              onClick={handleImport}
              className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
            >
              <Icon name="upload" size={18} />
              Import Config
            </button>
            <button
              onClick={handleExport}
              className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
            >
              <Icon name="download" size={18} />
              Export Config
            </button>
            <button
              onClick={async () => {
                // Clear both the localStorage cache and the server side
                // onboarded flag so the user can run setup again. The
                // server is the source of truth, so the patch must
                // succeed before we navigate.
                try {
                  await api.patch('/settings', { onboarded: false });
                } catch {
                  // best effort, still let the user reset locally
                }
                try {
                  localStorage.removeItem('myos-onboarded');
                } catch {
                  /* ignore */
                }
                useAppStore.setState({ onboarded: false });
                window.location.href = '/';
              }}
              className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
            >
              <Icon name="restart_alt" size={18} />
              Restart Setup
            </button>
          </div>
        </div>

        {/* Row 7: Shared Links */}
        <div className={cardClass}>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Shared links</h2>
            <button
              onClick={fetchShares}
              disabled={sharesLoading}
              className="text-xs text-slate-400 hover:text-white disabled:opacity-50 transition-colors"
            >
              {sharesLoading ? 'Loading...' : 'Refresh'}
            </button>
          </div>
          {shares.length === 0 && !sharesLoading && (
            <p className="text-sm text-slate-500">No active shared links. Use the Share button on tasks, transcripts, or labels to create one.</p>
          )}
          {shares.length > 0 && (
            <div className="flex flex-col gap-2">
              {shares.map((share) => (
                <div
                  key={share.token}
                  className={`flex items-center justify-between gap-3 p-3 rounded-lg border ${share.expired ? 'border-slate-800 opacity-50' : 'border-slate-700 bg-slate-800/40'}`}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-200 truncate">{share.title}</p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      {share.share_type === 'task_list' ? 'Task list' : share.share_type === 'agent_output' ? 'Agent output' : 'Label view'}
                      {' '}&middot; Expires {new Date(share.expires_at).toLocaleDateString()}
                      {share.expired && ' (expired)'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => navigator.clipboard.writeText(`${window.location.origin}/share/${share.token}`)}
                      className="text-xs text-slate-400 hover:text-blue-400 transition-colors px-2 py-1 rounded"
                      title="Copy link"
                    >
                      Copy link
                    </button>
                    <button
                      onClick={() => revokeShare(share.token)}
                      disabled={revokingToken === share.token}
                      className="text-xs text-red-400 hover:text-red-300 disabled:opacity-50 transition-colors px-2 py-1 rounded"
                    >
                      {revokingToken === share.token ? 'Revoking...' : 'Revoke'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
