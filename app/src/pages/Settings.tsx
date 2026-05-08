import { useState, useEffect, useRef } from 'react';
import { useAppStore, PROVIDER_TO_MODEL, type AccentColor } from '../stores/app';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { PageHeader } from '../components/ui';
import ConfirmModal from '../components/ConfirmModal';
import { useConfirm } from '../hooks/useConfirm';
import { api } from '../lib/api';
import { isPushSupported, isSubscribed, subscribe as pushSubscribe, unsubscribe as pushUnsubscribe } from '../lib/pushNotifications';
import SlackConnect from '../components/SlackConnect';
import { AtlassianSetupCard, GithubSetupCard } from '../components/OnboardingWizard';
import CustomVerbs from '../components/CustomVerbs';

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
  { name: 'GitHub', description: 'Access repos, issues, and pull requests', icon: 'code', npmPackage: '@modelcontextprotocol/server-github', setupCommand: 'npx -y @modelcontextprotocol/server-github', requiresAuth: true, authHint: 'Needs a GitHub access key. Create one at github.com/settings/tokens.' },
  { name: 'Postgres', description: 'Query and manage your databases', icon: 'database', npmPackage: '@modelcontextprotocol/server-postgres', setupCommand: 'npx -y @modelcontextprotocol/server-postgres postgresql://localhost/mydb', requiresAuth: false },
  { name: 'Brave Search', description: 'Search the web using Brave', icon: 'travel_explore', npmPackage: '@modelcontextprotocol/server-brave-search', setupCommand: 'npx -y @modelcontextprotocol/server-brave-search', requiresAuth: true, authHint: 'Needs a Brave Search API key (set BRAVE_API_KEY)' },
  { name: 'Puppeteer', description: 'Automate a web browser for scraping or testing', icon: 'web', npmPackage: '@modelcontextprotocol/server-puppeteer', setupCommand: 'npx -y @modelcontextprotocol/server-puppeteer', requiresAuth: false },
  { name: 'Memory', description: 'Persistent notes and memory that last across sessions', icon: 'psychology', npmPackage: '@modelcontextprotocol/server-memory', setupCommand: 'npx -y @modelcontextprotocol/server-memory', requiresAuth: false },
  { name: 'Google Calendar', description: 'View and manage your calendar events', icon: 'calendar_month', npmPackage: 'mcp-server-google-calendar', setupCommand: 'npx -y mcp-server-google-calendar', requiresAuth: true, authHint: 'Needs Google OAuth credentials (client ID and secret)' },
  { name: 'Slack', description: 'Send messages and read channels', icon: 'forum', npmPackage: '@modelcontextprotocol/server-slack', setupCommand: 'npx -y @modelcontextprotocol/server-slack', requiresAuth: true, authHint: 'Needs a Slack bot key from your Slack app settings.' },
  { name: 'Figma', description: 'Access and inspect design files', icon: 'palette', npmPackage: 'figma-mcp', setupCommand: 'npx -y figma-mcp', requiresAuth: true, authHint: 'Needs a Figma access key from figma.com/developers.' },
  { name: 'Google Drive', description: 'Access Google Docs, Sheets, and Drive files', icon: 'folder_shared', npmPackage: '@modelcontextprotocol/server-gdrive', setupCommand: 'npx -y @modelcontextprotocol/server-gdrive', requiresAuth: true, authHint: 'Needs Google OAuth credentials (client ID and secret)' },
  { name: 'Linear', description: 'Manage issues, projects, and teams in Linear', icon: 'track_changes', npmPackage: '@linear/mcp-server', setupCommand: 'npx -y @linear/mcp-server', requiresAuth: true, authHint: 'Needs a Linear API key from linear.app/settings/api.' },
  { name: 'Notion', description: 'Read and write pages, databases, and blocks in Notion', icon: 'article', npmPackage: '@notionhq/mcp', setupCommand: 'npx -y @notionhq/mcp', requiresAuth: true, authHint: 'Needs a Notion integration token from notion.so/my-integrations.' },
  { name: 'Jira', description: 'Search, create, and update Jira issues and projects', icon: 'bug_report', npmPackage: 'mcp-server-jira', setupCommand: 'npx -y mcp-server-jira', requiresAuth: true, authHint: 'Needs a Jira API token and your Atlassian account email.' },
  { name: 'Sentry', description: 'Query errors, issues, and performance data from Sentry', icon: 'error_outline', npmPackage: '@sentry/mcp-server', setupCommand: 'npx -y @sentry/mcp-server', requiresAuth: true, authHint: 'Needs a Sentry auth token from sentry.io/settings/account/api/auth-tokens.' },
  { name: 'Stripe', description: 'Look up customers, payments, and subscriptions', icon: 'credit_card', npmPackage: '@stripe/mcp', setupCommand: 'npx -y @stripe/mcp --tools=all', requiresAuth: true, authHint: 'Needs a Stripe secret key (set STRIPE_SECRET_KEY).' },
  { name: 'Airtable', description: 'Read and write Airtable bases, tables, and records', icon: 'table_chart', npmPackage: 'airtable-mcp-server', setupCommand: 'npx -y airtable-mcp-server', requiresAuth: true, authHint: 'Needs an Airtable personal access token from airtable.com/create/tokens.' },
];

const DEFAULT_FILES_DIR_HINT = "~/.myos/files";

interface SettingsData {
  dark_mode?: boolean;
  files_dir?: string | null;
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
  'Activity': 'history',
  'Agents': 'smart_toy',
  'Projects': 'folder',
  'Drive': 'cloud',
  'Calendar': 'calendar_month',
  'Gmail': 'mail',
  'Specs': 'description',
  'Transcripts': 'mic',
  'Automations': 'account_tree',
  'Cost Tracking': 'payments',
};

// Display names for features. Internal keys use ostk terminology that users should not see.
const featureDisplayNames: Record<string, string> = {
  'Projects': 'Files',
  'Transcripts': 'History',
  'Cost Tracking': 'Usage',
};


// --- Enterprise Setup Wizard ---
export default function Settings() {
  const {
    osName, setOsName,
    darkMode,
    accentColor, setAccentColor,
    features, setFeatures,
    setDefaultChatModel,
    setUseOstkTerms,
    powerUserMode, setPowerUserMode,
    instanceMode,
    compactMode, setCompactMode,
    greetingStyle, setGreetingStyle,
    showBudgetCaps, setShowBudgetCaps,
  } = useAppStore();

  const { confirm, confirmProps } = useConfirm();

  const [selectedProvider, setSelectedProvider] = useState('Anthropic');
  // Chat backend preference: "auto" picks the subscription when ready,
  // otherwise falls back to the Anthropic key. Users can force either
  // pathway from the Settings page.
  const [chatBackendPreference, setChatBackendPreference] = useState<'auto' | 'claude_code' | 'anthropic_api'>('auto');
  const [claudeCodeReady, setClaudeCodeReady] = useState<boolean | null>(null);
  const [recheckingClaude, setRecheckingClaude] = useState(false);
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({ Anthropic: '', 'Google Gemini': '' });
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [selectedModel, setSelectedModel] = useState('claude-sonnet-4-6');
  // Model list and default come from the backend so there's a single source
  // of truth. When Anthropic ships a new model we edit one file on the
  // server and the dropdown updates everywhere.
  const [anthropicModels, setAnthropicModels] = useState<{ id: string; label: string; tier: string }[]>([
    { id: 'claude-opus-4-6', label: 'Opus 4.6', tier: 'opus' },
    { id: 'claude-sonnet-4-6', label: 'Sonnet 4.6', tier: 'sonnet' },
    { id: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5', tier: 'haiku' },
  ]);
  const [notifications, setNotifications] = useState([
    { label: 'Agent Complete', enabled: true },
    { label: 'Agent Needs Input', enabled: true },
    { label: 'Agent Failed', enabled: true },
    { label: 'Approval Needed', enabled: true },
  ]);
  const [quietHours, setQuietHours] = useState(true);
  const [autoTemplateMatching, setAutoTemplateMatching] = useState(true);
  const [briefingEnabled, setBriefingEnabled] = useState(true);
  const [chatMemoryEnabled, setChatMemoryEnabled] = useState(true);
  const [standingInstructions, setStandingInstructions] = useState('');
  const [standingSaveStatus, setStandingSaveStatus] = useState<string | null>(null);
  const [standingSaveIsError, setStandingSaveIsError] = useState(false);
  const standingSectionRef = useRef<HTMLDivElement>(null);
  const [filesDirInput, setFilesDirInput] = useState('');
  const [currentFilesDir, setCurrentFilesDir] = useState<string | null>(null);
  // Auto-draft suggestions: the "Suggest for me" button calls the backend
  // generator, which returns 5-10 candidate instructions based on the
  // user's real patterns. Each row has a checkbox (default checked) and
  // an inline editable text. The Save all checked button joins the
  // checked rows with newlines and PATCHes the settings store.
  const [suggestions, setSuggestions] = useState<{ text: string; checked: boolean }[]>([]);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestError, setSuggestError] = useState<string | null>(null);
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

  // Gemini Enterprise provider state
  interface GeminiStatus {
    loading: boolean;
    available: boolean;
    email: string | null;
  }
  const [geminiStatus, setGeminiStatus] = useState<GeminiStatus>({ loading: true, available: false, email: null });
  const [defaultProvider, setDefaultProvider] = useState<'claude' | 'gemini'>('claude');

  // Connection-status state for Gmail, Calendar, Drive, and Slack.
  // All four are fetched in parallel on mount so every dot appears in
  // the same render tick rather than 1-2 seconds apart.
  interface ConnectionDot {
    loading: boolean;
    connected: boolean;
    label: string;
  }
  const [connectionStatus, setConnectionStatus] = useState<Record<string, ConnectionDot>>({
    Gmail: { loading: true, connected: false, label: '' },
    Calendar: { loading: true, connected: false, label: '' },
    Drive: { loading: true, connected: false, label: '' },
    Slack: { loading: true, connected: false, label: '' },
  });

  // Push notification state
  const [settingsPushEnabled, setSettingsPushEnabled] = useState(false);
  const [pushToggling, setPushToggling] = useState(false);
  const pushSupported = isPushSupported();

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

  // ADHD mode
  const [adhdEnabled, setAdhdEnabled] = useState(false);
  const [adhdCheckInSeconds, setAdhdCheckInSeconds] = useState(30);
  const [adhdFocusMode, setAdhdFocusMode] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [activeSection, setActiveSection] = useState('section-connections');
  const [expandedConnection, setExpandedConnection] = useState<string | null>(null);


  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await api.get<SettingsData>('/settings');
        if (data.accent_color) setAccentColor(data.accent_color as AccentColor);
        if (data.os_name) setOsName(data.os_name);
        if (typeof data.files_dir === 'string' || data.files_dir === null || data.files_dir === undefined) {
          const fd = (data.files_dir as string | null | undefined) ?? null;
          setCurrentFilesDir(fd);
          setFilesDirInput(fd ?? '');
        }
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
        // Load the default chat model from the saved default_model field
        if ((data as any).default_model) {
          const raw = (data as any).default_model.replace(/^@/, '');
          setDefaultChatModel(raw);
        } else if (data.provider) {
          // Fall back to provider if default_model is not set
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
        if ((data as any).briefing_enabled !== undefined) {
          setBriefingEnabled((data as any).briefing_enabled);
        }
        if ((data as any).chat_memory_enabled !== undefined) {
          setChatMemoryEnabled((data as any).chat_memory_enabled);
        }
        if (typeof (data as any).standing_instructions === 'string') {
          setStandingInstructions((data as any).standing_instructions);
        }
        if ((data as any).use_ostk_terms !== undefined) setUseOstkTerms((data as any).use_ostk_terms);
        if (data.mcp_servers) setMcpServers(data.mcp_servers);
        const prefRaw = (data as any).chat_backend_preference;
        if (prefRaw === 'auto' || prefRaw === 'claude_code' || prefRaw === 'anthropic_api') {
          setChatBackendPreference(prefRaw);
        }
        const providerRaw = (data as any).default_provider;
        if (providerRaw === 'claude' || providerRaw === 'gemini') {
          setDefaultProvider(providerRaw);
        }
        // ADHD mode
        const adhd = (data as any).adhd_mode;
        if (adhd) {
          if (adhd.enabled !== undefined) setAdhdEnabled(adhd.enabled);
          if (adhd.check_in_seconds !== undefined) setAdhdCheckInSeconds(adhd.check_in_seconds);
          if (adhd.focus_mode !== undefined) setAdhdFocusMode(adhd.focus_mode);
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
    // Load the current Anthropic model list from the server. The server
    // is the single source of truth so one edit updates every client.
    api.get<{ models?: { id: string; label: string; tier: string }[]; default?: string }>(
      '/models/anthropic',
    )
      .then((data) => {
        if (data.models && data.models.length > 0) {
          setAnthropicModels(data.models);
        }
      })
      .catch(() => {
        // Server unreachable. Keep the built-in fallback list defined above.
      });
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

    // Fetch Gemini Enterprise availability
    api.get<{ available: boolean; email: string | null }>('/gemini/status')
      .then((data) => setGeminiStatus({ loading: false, available: !!data.available, email: data.email ?? null }))
      .catch(() => setGeminiStatus({ loading: false, available: false, email: null }));

    // Kick off all four connection status fetches in parallel so every
    // dot renders in the same tick. Each call is cached server-side with
    // a short TTL so repeat visits return in under one millisecond.
    void (async () => {
      type GmailStatus = { authenticated: boolean; email: string | null };
      type CalStatus = { authenticated: boolean; email: string | null };
      type DriveStatus = { authenticated: boolean; email: string | null };
      type SlackStat = { connected: boolean; team_name: string };
      const [gmail, cal, drive, slack] = await Promise.all([
        api.get<GmailStatus>('/gmail/auth/status').catch(() => ({ authenticated: false, email: null })),
        api.get<CalStatus>('/calendar/auth/status').catch(() => ({ authenticated: false, email: null })),
        api.get<DriveStatus>('/drive/auth/status').catch(() => ({ authenticated: false, email: null })),
        api.get<SlackStat>('/slack/status').catch(() => ({ connected: false, team_name: '' })),
      ]);
      setConnectionStatus({
        Gmail: { loading: false, connected: !!gmail.authenticated, label: gmail.email || '' },
        Calendar: { loading: false, connected: !!cal.authenticated, label: cal.email || '' },
        Drive: { loading: false, connected: !!drive.authenticated, label: drive.email || '' },
        Slack: { loading: false, connected: !!slack.connected, label: slack.team_name || '' },
      });
    })();
    api.get<{ configured?: boolean; remote_url?: string | null; last_synced?: string | null }>('/sync/status')
      .then((data) => {
        setSyncConfigured(data.configured ?? false);
        setSyncRepoUrl(data.remote_url ?? null);
        setSyncLastSynced(data.last_synced ?? null);
      })
      .catch(() => {});
    // Check push subscription state
    if (pushSupported) {
      isSubscribed().then(setSettingsPushEnabled).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handlePushToggle = async () => {
    setPushToggling(true);
    try {
      if (settingsPushEnabled) {
        await pushUnsubscribe();
        setSettingsPushEnabled(false);
      } else {
        const ok = await pushSubscribe();
        setSettingsPushEnabled(ok);
      }
    } catch {
      // ignore
    } finally {
      setPushToggling(false);
    }
  };

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
    { label: 'Go to Agents', keys: '\u23184' },
    { label: 'Go to Files', keys: '\u23185' },
    { label: 'Go to Transcripts', keys: '\u23186' },
    { label: 'Go to Settings', keys: '\u23187' },
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

  // Escape closes the browse directory modal.
  useEffect(() => {
    if (!showBrowse) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowBrowse(false);
        setBrowseSearch('');
        setExpandedEntry(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showBrowse]);

  const isServerAdded = (entryName: string) =>
    mcpServers.some((s) => s.name.toLowerCase() === entryName.toLowerCase());

  const filteredDirectory = MCP_DIRECTORY.filter((entry) => {
    const q = browseSearch.toLowerCase();
    return entry.name.toLowerCase().includes(q) || entry.description.toLowerCase().includes(q);
  });

  const handleChangeFilesDir = async () => {
    const next = filesDirInput.trim();
    if (next === (currentFilesDir ?? '')) return;
    const ok = await confirm({
      title: 'Change files folder?',
      message: 'Changing this will point torios at the new folder. Files in your current folder stay where they are.',
      confirmLabel: 'Continue',
    });
    if (!ok) return;
    try {
      await api.put('/settings', { files_dir: next || null });
      setCurrentFilesDir(next || null);
    } catch (e) {
      console.error('Failed to update files_dir', e);
    }
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

  const handleDefaultProviderChange = (value: 'claude' | 'gemini') => {
    setDefaultProvider(value);
    api.patch('/settings', { default_provider: value }).catch(() => {});
  };

  const handleRecheckClaudeStatus = () => {
    setRecheckingClaude(true);
    api.get<{ claude_code_available?: boolean }>('/settings/chat-backend-status')
      .then((data) => setClaudeCodeReady(!!data.claude_code_available))
      .catch(() => setClaudeCodeReady(false))
      .finally(() => setRecheckingClaude(false));
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
          setTimeout(() => setKeySaveStatus(null), 4000);
        })
        .catch(() => {
          setKeySaveStatus('Error saving');
          setTimeout(() => setKeySaveStatus(null), 4000);
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

  const handleBriefingToggle = () => {
    const next = !briefingEnabled;
    setBriefingEnabled(next);
    api.patch('/settings', { briefing_enabled: next }).catch(() => {});
  };

  const handleChatMemoryToggle = () => {
    const next = !chatMemoryEnabled;
    setChatMemoryEnabled(next);
    api.patch('/settings', { chat_memory_enabled: next }).catch(() => {});
  };

  const handleAdhdToggle = () => {
    const next = !adhdEnabled;
    setAdhdEnabled(next);
    api.patch('/adhd/config', { enabled: next }).catch(() => {});
  };

  const handleAdhdIntervalChange = (seconds: number) => {
    const clamped = Math.max(10, Math.min(120, seconds));
    setAdhdCheckInSeconds(clamped);
    api.patch('/adhd/config', { check_in_seconds: clamped }).catch(() => {});
  };

  const handleAdhdFocusModeToggle = () => {
    const next = !adhdFocusMode;
    setAdhdFocusMode(next);
    api.patch('/adhd/config', { focus_mode: next }).catch(() => {});
  };

  const handleSaveStandingInstructions = async () => {
    try {
      await api.patch('/settings', { standing_instructions: standingInstructions });
      setStandingSaveIsError(false);
      setStandingSaveStatus('Saved');
    } catch {
      setStandingSaveIsError(true);
      setStandingSaveStatus('Could not save. Check your connection and try again.');
    } finally {
      setTimeout(() => setStandingSaveStatus(null), 3000);
    }
  };

  // Ask the backend to draft standing instructions from the user's own
  // patterns so she never has to stare at a blank textarea.
  const handleSuggestStandingInstructions = async () => {
    setSuggestLoading(true);
    setSuggestError(null);
    try {
      const resp = await api.post<{ suggestions: string[] }>('/settings/standing-instructions/suggest', {});
      const list = Array.isArray(resp?.suggestions) ? resp.suggestions : [];
      setSuggestions(list.map((text) => ({ text, checked: true })));
      if (list.length === 0) {
        setSuggestError('No suggestions yet. Try writing a few messages first, then try again.');
      }
    } catch {
      setSuggestError('Could not get suggestions. Check your connection and try again.');
    } finally {
      setSuggestLoading(false);
    }
  };

  // Join the checked suggestions with newlines, append to any existing
  // instructions the user already has, and PATCH the settings store.
  const handleSaveCheckedSuggestions = async () => {
    const joined = suggestions
      .filter((s) => s.checked && s.text.trim().length > 0)
      .map((s) => s.text.trim())
      .join('\n');
    if (!joined) {
      setSuggestError('Pick at least one suggestion to save.');
      return;
    }
    const merged = standingInstructions.trim()
      ? `${standingInstructions.trim()}\n${joined}`
      : joined;
    try {
      await api.patch('/settings', { standing_instructions: merged });
      setStandingInstructions(merged);
      setSuggestions([]);
      setSuggestError(null);
      setStandingSaveIsError(false);
      setStandingSaveStatus('Saved');
      setTimeout(() => setStandingSaveStatus(null), 3000);
    } catch {
      setSuggestError('Could not save. Check your connection and try again.');
    }
  };

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const hash = window.location.hash;
    if (hash === '#standing-instructions' || hash === '#standing-instructions-suggest') {
      setActiveSection('section-instructions');
      if (hash === '#standing-instructions-suggest') {
        window.setTimeout(() => handleSuggestStandingInstructions(), 0);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    'bg-slate-900/40 border border-slate-800 p-4 sm:p-6 rounded-xl hover:border-slate-700 transition-colors';

  const navItems = [
    { id: 'section-connections', label: 'Connections', icon: 'hub' },
    { id: 'section-preferences', label: 'Preferences', icon: 'palette' },
  ];

  // No IntersectionObserver needed: tabs show one section at a time.

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Settings" />

      {/* Mobile horizontal nav */}
      <div className="lg:hidden overflow-x-auto flex gap-2 px-4 py-2 border-b border-slate-800 sticky top-16 bg-slate-950 z-10">
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveSection(item.id)}
            className={`whitespace-nowrap px-3 py-1.5 rounded-full text-xs font-medium flex-shrink-0 transition-colors ${
              activeSection === item.id ? 'bg-slate-700 text-white' : 'bg-slate-800/60 text-slate-400 hover:text-white'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="flex pt-16 sm:pt-20">
        {/* Left nav rail — sticky on desktop */}
        <nav className="hidden lg:flex flex-col sticky top-20 self-start w-52 shrink-0 pl-4 py-6 space-y-0.5">
          {navItems.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveSection(item.id)}
              className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-sm text-left transition-colors ${
                activeSection === item.id
                  ? 'bg-slate-800 text-white font-medium'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              }`}
            >
              <Icon name={item.icon} size={15} className="shrink-0" />
              {item.label}
            </button>
          ))}
        </nav>

        {/* Main content */}
        <div className="flex-1 min-w-0 px-4 pb-8 sm:px-6 lg:pr-8 space-y-10">
          <PageHeader title="Settings" />

          {/* ── Instructions ─────────────────────── */}
          <div id="section-instructions" className={activeSection !== 'section-preferences' ? 'hidden' : ''}>
          <div
            ref={standingSectionRef}
            id="standing-instructions"
            className={cardClass}
            data-testid="standing-instructions-section"
          >
          <h2 className="text-lg font-semibold mb-2">Standing instructions</h2>
          <p className="text-sm text-slate-400 mb-4">
            Write instructions once and every chat, agent run, and task will follow them. Examples: your preferred tone, what apps to prefer, how you want code explained.
          </p>
          <div className="mb-3">
            <button
              type="button"
              onClick={handleSuggestStandingInstructions}
              disabled={suggestLoading}
              data-testid="standing-instructions-suggest"
              className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-60 rounded-lg text-white text-sm font-medium transition-colors"
            >
              {suggestLoading ? 'Thinking...' : 'Suggest for me'}
            </button>
            <span className="ml-3 text-xs text-slate-500">
              Draft from your recent chats, corrections, and connected apps.
            </span>
          </div>
          {suggestError && (
            <div
              data-testid="standing-instructions-suggest-error"
              role="alert"
              className="mb-3 text-sm text-red-400"
            >
              {suggestError}
            </div>
          )}
          {suggestions.length > 0 && (
            <div
              data-testid="standing-instructions-suggestions"
              className="mb-4 p-3 bg-slate-800/60 border border-slate-700 rounded-lg space-y-2"
            >
              <p className="text-sm text-slate-300 mb-2">
                Uncheck or edit any you do not want, then save.
              </p>
              {suggestions.map((s, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2"
                  data-testid={`standing-instructions-suggestion-row-${i}`}
                >
                  <input
                    type="checkbox"
                    checked={s.checked}
                    onChange={(e) => {
                      const next = [...suggestions];
                      next[i] = { ...next[i], checked: e.target.checked };
                      setSuggestions(next);
                    }}
                    data-testid={`standing-instructions-suggestion-check-${i}`}
                    className="mt-1"
                  />
                  <input
                    type="text"
                    value={s.text}
                    onChange={(e) => {
                      const next = [...suggestions];
                      next[i] = { ...next[i], text: e.target.value };
                      setSuggestions(next);
                    }}
                    data-testid={`standing-instructions-suggestion-text-${i}`}
                    className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
                  />
                </div>
              ))}
              <div className="flex items-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={handleSaveCheckedSuggestions}
                  data-testid="standing-instructions-save-checked"
                  className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-white text-sm font-medium transition-colors"
                >
                  Save all checked
                </button>
                <button
                  type="button"
                  onClick={() => { setSuggestions([]); setSuggestError(null); }}
                  data-testid="standing-instructions-dismiss-suggestions"
                  className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-white text-sm font-medium transition-colors"
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}
          <textarea
            value={standingInstructions}
            onChange={(e) => setStandingInstructions(e.target.value)}
            rows={6}
            data-testid="standing-instructions-textarea"
            placeholder="For example: always explain things in plain language, prefer Google Calendar over iCal, keep replies short unless I ask for detail."
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
          <div className="flex items-center gap-3 mt-3">
            <button
              onClick={handleSaveStandingInstructions}
              data-testid="standing-instructions-save"
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-white text-sm font-medium transition-colors"
            >
              Save
            </button>
            {standingSaveStatus && (
              <span
                data-testid="standing-instructions-status"
                role={standingSaveIsError ? 'alert' : 'status'}
                className={`text-sm ${standingSaveIsError ? 'text-red-400' : 'text-green-400'}`}
              >
                {standingSaveStatus}
              </span>
            )}
          </div>
          </div>
          </div>



          {/* ── 2. Appearance ───────────────────────── */}
          <div id="section-appearance" className={`grid grid-cols-1 md:grid-cols-2 gap-6 items-start${activeSection !== 'section-preferences' ? ' hidden' : ''}`}>
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-5">Appearance</h2>

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
                onKeyDown={(e) => { if (e.key === "Enter") handleOsNameBlur(); }}
                className="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>

            {/* Compact Mode */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">Compact Mode</label>
              <p className="text-xs text-slate-500 mb-2">Tighter spacing and smaller text throughout the app.</p>
              <div className="flex gap-2">
                <button
                  onClick={() => setCompactMode(false)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    !compactMode
                      ? 'accent-bg !text-white'
                      : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  Normal
                </button>
                <button
                  onClick={() => setCompactMode(true)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    compactMode
                      ? 'accent-bg !text-white'
                      : 'bg-slate-800 text-slate-400 hover:text-white'
                  }`}
                >
                  Compact
                </button>
              </div>
            </div>

            {/* Greeting Style */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">Home Greeting</label>
              <div className="flex gap-2">
                {(['time', 'quote', 'none'] as const).map((style) => (
                  <button
                    key={style}
                    onClick={() => setGreetingStyle(style)}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      greetingStyle === style
                        ? 'accent-bg !text-white'
                        : 'bg-slate-800 text-slate-400 hover:text-white'
                    }`}
                  >
                    {style === 'time' ? 'Time of day' : style === 'quote' ? 'Quote' : 'None'}
                  </button>
                ))}
              </div>
            </div>

          </div>

          {/* System Features */}
          <div className={cardClass}>
          <h2 className="text-lg font-semibold mb-5">System Features</h2>
          <p className="text-xs text-slate-500 mb-3">Toggle to show or hide in the sidebar. Drag items in the sidebar itself to reorder.</p>
          <div className="space-y-1.5">
            {features.map((f: { label: string; enabled: boolean }, index: number) => (
              <div
                key={f.label}
                className="flex items-center gap-3 bg-slate-800/50 border border-slate-700/50 rounded-lg px-3 py-2.5"
              >
                <Icon name={featureIcons[f.label] || 'extension'} className="text-slate-400" size={18} />
                <span className="flex-1 text-sm text-slate-300">{featureDisplayNames[f.label] || f.label}</span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={f.enabled}
                  aria-label={featureDisplayNames[f.label] || f.label}
                  onClick={() => handleFeatureToggle(index)}
                  className={`relative inline-flex items-center w-10 h-5 rounded-full transition-colors shrink-0 ${f.enabled ? 'bg-blue-500' : 'bg-slate-600'}`}
                >
                  <span className={`inline-block w-4 h-4 rounded-full bg-white shadow-sm transform transition-transform ${f.enabled ? 'translate-x-[20px]' : 'translate-x-0.5'}`} />
                </button>
              </div>
            ))}
          </div>

          {/* Budget Caps toggle */}
          <div className="mt-6 pt-6 border-t border-slate-800">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-200">Show budget caps</span>
              <button
                data-testid="budget-caps-toggle"
                onClick={() => setShowBudgetCaps(!showBudgetCaps)}
                className={`relative w-11 h-6 rounded-full transition-colors ${showBudgetCaps ? 'bg-blue-500' : 'bg-slate-700'}`}
                aria-pressed={showBudgetCaps}
                aria-label="Toggle budget caps visibility"
              >
                <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${showBudgetCaps ? 'translate-x-5' : ''}`} />
              </button>
            </div>
            <p className="text-xs text-slate-500 mt-2">Shows budget cap columns in Usage. Off by default since caps are not real spend.</p>
          </div>

          {/* Power user mode toggle */}
          <div className="mt-4 pt-4 border-t border-slate-800">
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
          </div>

          {/* ── AI Provider and Chat Settings (shown in Connections tab) ────────────────────────── */}
          <div id="section-ai-chat" className={activeSection !== 'section-connections' ? 'hidden' : 'space-y-6'}>
          <div className={cardClass} data-testid="api-key-setup-section">
            <h2 className="text-lg font-semibold mb-5">AI Provider</h2>

            {/* Provider for API Key setup */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">Set Up Provider</label>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
                  <div className="mb-3 px-4 py-3 bg-white border border-green-300 rounded-lg flex items-center gap-2">
                    <Icon name="check_circle" size={18} className="text-green-600" />
                    <span className="text-sm text-green-700 font-medium">Connected with Google</span>
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
                  disabled={!apiKeys[selectedProvider]?.trim() || keySaveStatus === 'Saved to keychain'}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap flex items-center gap-1.5 ${
                    keySaveStatus === 'Saved to keychain'
                      ? 'bg-emerald-600 text-white'
                      : keySaveStatus === 'Error saving'
                        ? 'bg-red-600 text-white'
                        : 'bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white'
                  }`}
                >
                  {keySaveStatus === 'Saved to keychain' && <Icon name="check_circle" size={16} />}
                  {keySaveStatus === 'Error saving' && <Icon name="error" size={16} />}
                  {keySaveStatus || 'Save to Keychain'}
                </button>
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Keys are stored securely in your system keychain, not in a file.
              </p>
            </div>

            {/* Model Selector: only shown when Anthropic is the chat provider.
                Google Gemini picks its own model elsewhere, so surfacing the
                Claude model list while Gemini is selected is confusing. */}
            {selectedProvider === 'Anthropic' && (
              <div data-testid="anthropic-model-dropdown">
                <label className="text-sm text-slate-400 mb-2 block">Model</label>
                <select
                  value={selectedModel}
                  onChange={(e) => handleModelChange(e.target.value)}
                  className="w-full bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors appearance-none cursor-pointer"
                >
                  {anthropicModels.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

          </div>
          </div>

          {/* ── 3. Notifications & Focus ────────────────────── */}
          <div id="section-notifications-focus" className={activeSection !== 'section-notifications-focus' ? 'hidden' : 'space-y-6'}>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Notifications</p>
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
            {pushSupported && (
              <div className="mt-5 pt-4 border-t border-slate-800">
                <div className="flex items-center justify-between">
                  <div className="pr-3">
                    <p className="text-sm text-slate-300">Push notifications</p>
                    <p className="text-xs text-slate-500">Get alerts even when the browser tab is closed</p>
                  </div>
                  <button
                    type="button"
                    data-testid="push-toggle"
                    onClick={handlePushToggle}
                    disabled={pushToggling}
                    className={`w-10 h-6 rounded-full relative transition-colors flex-shrink-0 ${
                      settingsPushEnabled ? 'accent-bg' : 'bg-slate-700'
                    } ${pushToggling ? 'opacity-50' : ''}`}
                  >
                    <span
                      className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        settingsPushEnabled ? 'left-5' : 'left-1'
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}
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
                  <p className="text-sm text-slate-300">Pick the right agent automatically</p>
                  <p className="text-xs text-slate-500">
                    When you ask a question, myOS chooses the best built-in or saved agent for the job.
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
                  <p className="text-sm text-slate-300">Briefing</p>
                  <p className="text-xs text-slate-500">
                    Show a short summary of your day on the dashboard. Available at any hour.
                  </p>
                </div>
                <button
                  data-testid="briefing-toggle"
                  onClick={handleBriefingToggle}
                  className={`w-10 h-6 rounded-full relative transition-colors flex-shrink-0 ${
                    briefingEnabled ? 'accent-bg' : 'bg-slate-700'
                  }`}
                >
                  <span
                    className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                      briefingEnabled ? 'left-5' : 'left-1'
                    }`}
                  />
                </button>
              </div>
              <div className="flex items-center justify-between mt-4">
                <div className="pr-3">
                  <p className="text-sm text-slate-300">Chat Memory</p>
                  <p className="text-xs text-slate-500">
                    Let the AI remember what you talked about in your previous chat tab.
                  </p>
                </div>
                <button
                  data-testid="chat-memory-toggle"
                  onClick={handleChatMemoryToggle}
                  className={`w-10 h-6 rounded-full relative transition-colors flex-shrink-0 ${
                    chatMemoryEnabled ? 'accent-bg' : 'bg-slate-700'
                  }`}
                >
                  <span
                    className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                      chatMemoryEnabled ? 'left-5' : 'left-1'
                    }`}
                  />
                </button>
              </div>
            </div>
          </div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Focus mode</p>
          <div className={cardClass}>
            <div className="flex items-center gap-3 mb-2">
              <h2 className="text-lg font-semibold">ADHD Mode</h2>
              <button
                data-testid="adhd-toggle"
                onClick={handleAdhdToggle}
                className={`w-10 h-6 rounded-full relative transition-colors ${
                  adhdEnabled ? 'accent-bg' : 'bg-slate-700'
                }`}
              >
                <span
                  className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                    adhdEnabled ? 'left-5' : 'left-1'
                  }`}
                />
              </button>
            </div>
            <p className="text-sm text-slate-400 mb-5">
              Designed to keep you in flow. Get regular check-ins while agents work, see where you left off when you come back, and get one clear recommendation instead of a list of choices.
            </p>

            <div className={`space-y-5 ${adhdEnabled ? '' : 'opacity-40 pointer-events-none'}`}>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <p className="text-sm text-slate-300">Check-in notifications</p>
                    <p className="text-xs text-slate-500">
                      How often to show you what your agents are doing
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <input
                    type="range"
                    min={10}
                    max={120}
                    step={5}
                    value={adhdCheckInSeconds}
                    onChange={(e) => handleAdhdIntervalChange(Number(e.target.value))}
                    className="flex-1 accent-blue-500"
                    data-testid="adhd-interval-slider"
                  />
                  <span className="text-sm text-slate-300 w-16 text-right font-mono">
                    {adhdCheckInSeconds}s
                  </span>
                </div>
              </div>

              <div className="pt-4 border-t border-slate-800">
                <div className="flex items-center justify-between">
                  <div className="pr-3">
                    <p className="text-sm text-slate-300">Welcome back summary</p>
                    <p className="text-xs text-slate-500">
                      When you return after being away for 5+ minutes, show you exactly where you left off
                    </p>
                  </div>
                  <div className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" title="Always on when ADHD mode is active" />
                </div>
              </div>

              <div className="pt-4 border-t border-slate-800">
                <div className="flex items-center justify-between">
                  <div className="pr-3">
                    <p className="text-sm text-slate-300">Reduce choices</p>
                    <p className="text-xs text-slate-500">
                      Show one recommendation instead of a list of options. Less deciding, more doing.
                    </p>
                  </div>
                  <button
                    data-testid="adhd-focus-toggle"
                    onClick={handleAdhdFocusModeToggle}
                    className={`w-10 h-6 rounded-full relative transition-colors flex-shrink-0 ${
                      adhdFocusMode ? 'accent-bg' : 'bg-slate-700'
                    }`}
                  >
                    <span
                      className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        adhdFocusMode ? 'left-5' : 'left-1'
                      }`}
                    />
                  </button>
                </div>
              </div>
            </div>
          </div>
          </div>

          <div className={activeSection !== 'section-ai-chat' ? 'hidden' : 'space-y-6'}>
          <div className={cardClass} data-testid="chat-backend-section">
          <h2 className="text-lg font-semibold mb-1">Chat backend</h2>
          <p className="text-sm text-slate-400 mb-4">
            Pick which sign-in powers your chat. Your Claude subscription costs nothing extra if you already pay for Pro or Max. Using your Anthropic key charges per message.
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

          <div className="mt-4 pt-4 border-t border-slate-800">
            <div className="flex items-center gap-3 flex-wrap">
              <div
                data-testid="claude-code-ready-indicator"
                className="flex items-center gap-2 text-xs"
              >
                {claudeCodeReady === null ? (
                  <>
                    <span className="w-2 h-2 rounded-full bg-slate-500" />
                    <span className="text-slate-500">Checking your Claude sign-in...</span>
                  </>
                ) : claudeCodeReady ? (
                  <>
                    <span className="w-2 h-2 rounded-full bg-green-500" />
                    <span className="text-green-400" data-testid="claude-auth-status-signed-in">Claude subscription is ready</span>
                  </>
                ) : (
                  <>
                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                    <span className="text-amber-400" data-testid="claude-auth-status-not-signed-in">Claude subscription not signed in</span>
                  </>
                )}
              </div>
              <button
                data-testid="claude-recheck-button"
                onClick={handleRecheckClaudeStatus}
                disabled={recheckingClaude}
                className="text-xs px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-slate-300 hover:border-slate-500 transition-colors disabled:opacity-50"
              >
                {recheckingClaude ? 'Checking...' : 'Re-check'}
              </button>
            </div>
            {claudeCodeReady === false && (
              <div className="mt-3 p-3 rounded-lg bg-slate-800/60 border border-slate-700 text-sm text-slate-300" data-testid="claude-login-instructions">
                <p className="font-medium text-white mb-1">To sign in with Claude Pro or Max</p>
                <p>Open your Terminal app and run:</p>
                <code className="block mt-1.5 px-2 py-1 bg-slate-900 rounded text-green-400 font-mono text-xs select-all">claude login</code>
                <p className="mt-2 text-slate-400 text-xs">Then click Re-check above to confirm it worked.</p>
              </div>
            )}
          </div>
          </div>
          </div>

          {/* ── 4. AI Provider (shown with AI & Chat tab) ── */}
          <div id="section-ai-provider" className={`space-y-6${activeSection !== 'section-ai-chat' ? ' hidden' : ''}`}>
          <div className={cardClass} data-testid="ai-provider-section">
            <h2 className="text-lg font-semibold mb-1">AI Provider</h2>
            <p className="text-sm text-slate-400 mb-4">
              Choose which AI answers your messages. Claude is always available. Connect your Google account to use Gemini Enterprise.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {/* Claude card */}
              <button
                data-testid="provider-card-claude"
                onClick={() => handleDefaultProviderChange('claude')}
                className={`flex items-start gap-3 p-3.5 rounded-lg border text-left transition-colors ${
                  defaultProvider === 'claude'
                    ? 'accent-border accent-highlight'
                    : 'border-slate-700 bg-slate-800/50 hover:border-slate-600'
                }`}
              >
                <span className="mt-1 w-2.5 h-2.5 rounded-full bg-emerald-400 claude-provider-dot flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200">Claude</p>
                  <p className="text-xs text-slate-500 mt-0.5">Always connected</p>
                  {defaultProvider === 'claude' && (
                    <p className="text-xs accent-text mt-1 font-medium">Default</p>
                  )}
                </div>
              </button>

              {/* Gemini Enterprise card */}
              <button
                data-testid="provider-card-gemini"
                onClick={() => geminiStatus.available && handleDefaultProviderChange('gemini')}
                disabled={!geminiStatus.available}
                className={`flex items-start gap-3 p-3.5 rounded-lg border text-left transition-colors ${
                  defaultProvider === 'gemini' && geminiStatus.available
                    ? 'accent-border accent-highlight'
                    : geminiStatus.available
                    ? 'border-slate-700 bg-slate-800/50 hover:border-slate-600'
                    : 'border-slate-800 bg-slate-900/30 opacity-60 cursor-not-allowed'
                }`}
              >
                <span
                  data-testid="gemini-status-dot"
                  className={`mt-1 w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                    geminiStatus.loading
                      ? 'bg-slate-600'
                      : geminiStatus.available
                      ? 'bg-emerald-400'
                      : 'bg-slate-600'
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200">Gemini Enterprise</p>
                  {geminiStatus.loading ? (
                    <p className="text-xs text-slate-500 mt-0.5">Checking...</p>
                  ) : geminiStatus.available ? (
                    <>
                      {geminiStatus.email && (
                        <p className="text-xs text-slate-400 mt-0.5 truncate">{geminiStatus.email}</p>
                      )}
                      <p className="text-xs text-slate-500 mt-0.5">Google Workspace · Slack · Jira · Confluence</p>
                      {defaultProvider === 'gemini' && (
                        <p className="text-xs accent-text mt-1 font-medium">Default</p>
                      )}
                    </>
                  ) : (
                    <p className="text-xs text-slate-500 mt-0.5">
                      Run <code className="text-green-400 font-mono">gcloud auth application-default login</code> or the Gemini CLI to connect
                    </p>
                  )}
                </div>
              </button>
            </div>
          </div>
          </div>

          {/* ── Connections Tab ──────────────────────── */}
          <div id="section-connections" className={`space-y-6${activeSection !== 'section-connections' ? ' hidden' : ''}`}>
          <div>
            <h2 className="text-2xl font-bold mb-2">Connections</h2>
            <p className="text-sm text-slate-400 mb-6">Sign in to the apps myOS works with.</p>
          </div>
          <div className="space-y-4">
            {/* Google pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'google' ? null : 'google')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-google"
            >
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                connectionStatus.Drive.loading
                  ? 'bg-slate-600'
                  : connectionStatus.Drive.connected
                  ? 'bg-emerald-400'
                  : 'bg-slate-600'
              }`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200">Google</p>
                <p className="text-xs text-slate-400 truncate">{connectionStatus.Drive.connected ? connectionStatus.Drive.label || 'Connected' : 'Sign in to use Gmail, Calendar, and Drive'}</p>
              </div>
              <Icon name={expandedConnection === 'google' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'google' && (
              <div className={cardClass} data-testid="google-connect-section">
                <div className="flex items-center gap-2 mb-3">
                  <Icon name="travel_explore" size={18} className="text-blue-400" />
                  <h2 className="text-base font-semibold">Google</h2>
                </div>
                <p className="text-xs text-slate-500 mb-3">Gmail, Calendar, and Drive</p>
                {connectionStatus.Drive.connected ? (
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
                      <p className="text-sm text-slate-200 font-medium">
                        {connectionStatus.Drive.label || 'Connected'}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await api.post('/drive/auth/revoke', {});
                          setConnectionStatus(prev => ({
                            ...prev,
                            Drive: { loading: false, connected: false, label: '' }
                          }));
                        } catch (err) {
                          console.error('Failed to disconnect Google:', err);
                        }
                      }}
                      className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors"
                    >
                      <Icon name="link_off" size={15} />
                      Disconnect
                    </button>
                  </div>
                ) : googleOAuthAvailable ? (
                  <button
                    onClick={async () => {
                      try {
                        const res = await api.get<{ url: string }>('/drive/auth/url');
                        window.location.href = res.url;
                      } catch (err) {
                        console.error('Google sign-in failed to start:', err);
                      }
                    }}
                    className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
                    data-testid="google-connect-btn"
                  >
                    <Icon name="login" size={16} />
                    Connect Google
                  </button>
                ) : (
                  <p className="text-sm text-slate-400">
                    Google sign-in is not configured for this instance.
                  </p>
                )}
              </div>
            )}

            {/* Slack pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'slack' ? null : 'slack')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-slack"
            >
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                connectionStatus.Slack.loading
                  ? 'bg-slate-600'
                  : connectionStatus.Slack.connected
                  ? 'bg-emerald-400'
                  : 'bg-slate-600'
              }`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200">Slack</p>
                <p className="text-xs text-slate-400 truncate">{connectionStatus.Slack.connected ? connectionStatus.Slack.label || 'Connected' : 'Sign in to use slash commands'}</p>
              </div>
              <Icon name={expandedConnection === 'slack' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'slack' && (
              <div className={cardClass} data-testid="slack-connect-section">
                <div className="flex items-center gap-2 mb-4">
                  <Icon name="forum" size={18} className="text-purple-400" />
                  <h2 className="text-base font-semibold">Slack</h2>
                </div>
                <SlackConnect />
              </div>
            )}

            {/* iMessage pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'imessage' ? null : 'imessage')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-imessage"
            >
              <span className="w-2.5 h-2.5 rounded-full bg-slate-600 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200">iMessage</p>
                <p className="text-xs text-slate-400">Set up iMessage</p>
              </div>
              <Icon name={expandedConnection === 'imessage' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'imessage' && (
              <div className={cardClass} data-testid="imessage-connect-section">
                <div className="flex items-center gap-2 mb-4">
                  <Icon name="chat_bubble" size={18} className="text-green-400" />
                  <h2 className="text-base font-semibold">iMessage</h2>
                </div>
                <p className="text-sm text-slate-400 mb-3">
                  Read and reply to iMessages from within myOS. Requires macOS.
                </p>
                <a
                  href="/imessage"
                  className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-500 rounded-lg text-sm font-medium text-white transition-colors"
                  data-testid="imessage-setup-link"
                >
                  <Icon name="open_in_new" size={16} />
                  Set up iMessage
                </a>
              </div>
            )}

            {/* GitHub pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'github' ? null : 'github')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-github"
            >
              <span className="w-2.5 h-2.5 rounded-full bg-slate-600 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200">GitHub</p>
                <p className="text-xs text-slate-400">Sign in to access your repos</p>
              </div>
              <Icon name={expandedConnection === 'github' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'github' && (
              <div className={cardClass} data-testid="github-connect-section">
                <div className="flex items-center gap-2 mb-4">
                  <Icon name="code" size={18} className="text-slate-300" />
                  <h2 className="text-base font-semibold">GitHub</h2>
                </div>
                <GithubSetupCard
                  darkMode={true}
                  inputCls="bg-slate-800 border-slate-700 text-white"
                  subtextCls="text-slate-400"
                />
              </div>
            )}

            {/* Atlassian pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'atlassian' ? null : 'atlassian')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-800/50 border border-slate-700/50 rounded-lg hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-atlassian"
            >
              <span className="w-2.5 h-2.5 rounded-full bg-slate-600 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200">Atlassian</p>
                <p className="text-xs text-slate-400">Connect Jira & Confluence</p>
              </div>
              <Icon name={expandedConnection === 'atlassian' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'atlassian' && (
              <div className={cardClass} data-testid="atlassian-connect-section">
                <div className="flex items-center gap-2 mb-4">
                  <Icon name="bug_report" size={18} className="text-blue-400" />
                  <h2 className="text-base font-semibold">Jira & Confluence</h2>
                </div>
                <AtlassianSetupCard
                  darkMode={true}
                  inputCls="bg-slate-800 border-slate-700 text-white"
                  subtextCls="text-slate-400"
                />
              </div>
            )}

            {/* Custom tack commands */}
            <div className={cardClass} data-testid="custom-verbs-section">
              <CustomVerbs />
            </div>
          </div>

          {/* Divider */}
          <div className="h-px bg-slate-700/50 my-6" />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-stretch">
          <div className={cardClass}>
          <div className="flex items-center gap-2 mb-5">
            <h2 className="text-lg font-semibold">Connected Tools</h2>
            <span className="text-xs text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">MCP</span>
            <div className="group relative ml-1">
              <Icon name="help_outline" size={18} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                Connect external tool servers (like Stitch, Gmail, Calendar). The chat panel can use their tools the same way it uses built-in tools.
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
                Managed automatically (configured in your profile)
              </p>
              <div className="space-y-2">
                {ostkMcpServers.map((server) => (
                  <div key={server.name} className="flex items-center gap-3 px-3 py-2.5 bg-slate-800/50 rounded-lg border border-emerald-900/40">
                    <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" title="Managed automatically" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-slate-200 font-medium">{server.name}</p>
                      <p className="text-xs text-slate-500 truncate font-mono">{server.command}</p>
                    </div>
                    <span className="text-xs text-emerald-400/70 flex-shrink-0">auto</span>
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
            <div className="flex flex-wrap gap-2">
              <input
                type="text"
                value={newMcpName}
                onChange={e => setNewMcpName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAddMcpServer()}
                placeholder="Server name (e.g. Stitch)"
                className="w-36 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
              <input
                type="text"
                value={newMcpUrl}
                onChange={e => setNewMcpUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAddMcpServer()}
                placeholder="Paste your server URL after running setup"
                className="flex-1 min-w-[200px] bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
              <input
                type="password"
                value={newMcpToken}
                onChange={e => setNewMcpToken(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAddMcpServer()}
                placeholder="Auth token (optional)"
                className="w-44 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
              <button
                onClick={handleAddMcpServer}
                disabled={!newMcpName.trim() || !newMcpUrl.trim()}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-white text-sm font-medium transition-colors whitespace-nowrap"
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
                    These tools run on your computer. Follow the setup instructions for each one, then paste the connection address here.
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
                              <code className="text-xs text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-slate-900/80 px-2 py-1 rounded font-mono block break-all">{entry.npmPackage}</code>
                            </div>
                            {/* Setup command */}
                            <div>
                              <p className="text-xs text-slate-400 mb-1">Setup command</p>
                              <div className="flex items-center gap-2">
                                <code className="text-xs text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-slate-900/80 px-2 py-1 rounded font-mono flex-1 break-all">{entry.setupCommand}</code>
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
                              <div className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400/80 bg-amber-50 dark:bg-amber-900/20 px-2 py-1.5 rounded">
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

        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-5">
            <h2 className="text-lg font-semibold">Sync</h2>
            <div className="group relative ml-1">
              <Icon name="help_outline" size={18} className="text-slate-500 hover:text-slate-300 cursor-help" />
              <div className="absolute bottom-full left-0 mb-2 w-72 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-[60]">
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
                  className="flex-1 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
                />
                <button
                  onClick={handleSetupSync}
                  disabled={syncLoading || !syncRepoInput.trim()}
                  data-testid="sync-setup-button"
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-white text-sm font-medium transition-colors whitespace-nowrap"
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
          </div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Data</p>
          {activeSection === 'section-connections' && (
          <div className={cardClass} data-testid="files-location-section">
          <h2 className="text-lg font-semibold mb-2">Files location</h2>
          <p className="text-sm text-slate-400 mb-3">
            This is the folder on your computer where torios saves your
            files, like briefs and roadmaps. The default is a hidden
            folder in your home directory.
          </p>
          <div className="flex gap-2 items-center">
            <input
              type="text"
              value={filesDirInput}
              onChange={(e) => setFilesDirInput(e.target.value)}
              placeholder={DEFAULT_FILES_DIR_HINT}
              data-testid="files-dir-input"
              className="flex-1 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={handleChangeFilesDir}
              data-testid="files-dir-change"
              className="accent-bg !text-white px-4 py-2 rounded-lg text-sm font-medium"
            >
              Change
            </button>
          </div>
          </div>
          )}
          {activeSection === 'section-connections' && (
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
          )}
          {activeSection === 'section-connections' && (
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
          )}
          </div>

          {/* ── Team Admin (gated) ──────────────────── */}
          {instanceMode === 'team' && (
          <div id="section-team-admin" className={activeSection !== 'section-team-admin' ? 'hidden' : ''}>
          <div className={cardClass}>
            <div className="flex items-center gap-2 mb-3">
              <Icon name="admin_panel_settings" size={22} className="text-indigo-400" />
              <h2 className="text-lg font-semibold">Team Admin</h2>
            </div>
            <p className="text-sm text-slate-400 mb-4">
              Manage your team, policies, security, and audit trail.
            </p>
            <a
              href="/admin"
              className="inline-flex items-center gap-2 px-4 py-2 team-bg hover:opacity-90 text-white font-semibold text-sm rounded-lg transition-opacity"
            >
              <Icon name="open_in_new" size={16} />
              Open admin settings
            </a>
          </div>
          </div>
          )}

          {/* ── Preferences Tab ────────────────────────── */}
          <div id="section-preferences" className={activeSection !== 'section-preferences' ? 'hidden' : 'space-y-6'}>
          <h2 className="text-2xl font-bold mb-6">Preferences</h2>
          </div>

          {/* ── Shortcuts ────────────────────────── */}
          <div id="section-shortcuts" className={activeSection !== 'section-preferences' ? 'hidden' : ''}>
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
        </div>
      </div>
      <ConfirmModal {...confirmProps} />
    </div>
  );
}
