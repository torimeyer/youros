import { useState, useEffect, useRef } from 'react';
import { NavLink } from 'react-router-dom';
import { useAppStore, PROVIDER_TO_MODEL, type AccentColor } from '../stores/app';
import Icon from '../components/Icon';
import PageShell from '../components/PageShell';
import TopNavTabs from '../components/TopNavTabs';
import ConfirmModal from '../components/ConfirmModal';
import { useConfirm } from '../hooks/useConfirm';
import { api } from '../lib/api';
import { reportError } from '../lib/reportError';
import { isPushSupported, isSubscribed, subscribe as pushSubscribe, unsubscribe as pushUnsubscribe } from '../lib/pushNotifications';
import SlackConnect from '../components/SlackConnect';
import { GithubSetupCard } from '../components/OnboardingWizard';
import AtlassianConnect from '../components/AtlassianConnect';
import CustomVerbs from '../components/CustomVerbs';
import ChannelRoutingPanel from '../components/ChannelRoutingPanel';
import { parseMemoryProvenance } from '../lib/parseMemoryProvenance';


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
  shortcuts?: Record<string, string>;
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
  'Transcripts': 'History',
  'Cost Tracking': 'Usage',
};


function Toggle({ checked, onChange, testId, disabled, label }: { checked: boolean; onChange: () => void; testId?: string; disabled?: boolean; label?: string; }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-pressed={checked}
      aria-label={label}
      data-testid={testId}
      disabled={disabled}
      onClick={onChange}
      className={`relative w-11 h-6 rounded-full transition-colors flex-shrink-0 ${checked ? 'bg-blue-500' : 'bg-slate-200 dark:bg-slate-700'} ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
    >
      <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-transform ${checked ? 'translate-x-5' : ''}`} />
    </button>
  );
}

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
  } = useAppStore();

  const { confirm, confirmProps } = useConfirm();

  const [selectedProvider, setSelectedProvider] = useState('Anthropic');
  // Chat backend preference: "auto" picks the subscription when ready,
  // otherwise falls back to the Anthropic key. Users can force either
  // pathway from the Settings page.
  const [chatBackendPreference, setChatBackendPreference] = useState<'auto' | 'claude_code' | 'anthropic_api'>('auto');
  const [useGeminiCli, setUseGeminiCli] = useState(false);
  const [claudeCodeReady, setClaudeCodeReady] = useState<boolean | null>(null);
  const [geminiCliReady, setGeminiCliReady] = useState<boolean | null>(null);
  const [recheckingClaude, setRecheckingClaude] = useState(false);
  const [recheckingGemini, setRecheckingGemini] = useState(false);
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
  const [chatReceiptsGateEnabled, setChatReceiptsGateEnabled] = useState(true);
  const [memoryContent, setMemoryContent] = useState('');
  const [memorySaveStatus, setMemorySaveStatus] = useState<string | null>(null);
  const [memoryOverflow, setMemoryOverflow] = useState<{ overflowed: boolean; reason: string; kb: number; lines: number; total_kb: number; hard_cap: boolean } | null>(null);
  const [suggestTopicsLoading, setSuggestTopicsLoading] = useState(false);
  const [suggestedTopics, setSuggestedTopics] = useState<{ topic: string; bullets: string[] }[] | null>(null);
  const [standingInstructions, setStandingInstructions] = useState('');
  const [standingSaveStatus, setStandingSaveStatus] = useState<string | null>(null);
  const [standingSaveIsError, setStandingSaveIsError] = useState(false);
  const standingSectionRef = useRef<HTMLDivElement>(null);
  // Auto-draft suggestions: the "Suggest for me" button calls the backend
  // generator, which returns 5-10 candidate instructions based on the
  // user's real patterns. Each row has a checkbox (default checked) and
  // an inline editable text. The Save all checked button joins the
  // checked rows with newlines and PATCHes the settings store.
  const [suggestions, setSuggestions] = useState<{ text: string; checked: boolean }[]>([]);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const [editingShortcut, setEditingShortcut] = useState<string | null>(null);
  const [customShortcuts, setCustomShortcuts] = useState<Record<string, string>>({});
  const [keySaveStatus, setKeySaveStatus] = useState<string | null>(null);
  const [googleOAuthAvailable, setGoogleOAuthAvailable] = useState(false);
  const [googleConnected, setGoogleConnected] = useState(false);
  const [keyAvailable, setKeyAvailable] = useState<Record<string, boolean>>({ Anthropic: false, 'Google Gemini': false });
  const [keySource, setKeySource] = useState<Record<string, string>>({});
  const [geminiAdvancedOpen, setGeminiAdvancedOpen] = useState(false);
  const [keyStatusLoading, setKeyStatusLoading] = useState(true);
  // Gemini Enterprise provider state
  interface GeminiStatus {
    loading: boolean;
    available: boolean;
    email: string | null;
    api_error: string | null;
  }
  const [geminiStatus, setGeminiStatus] = useState<GeminiStatus>({ loading: true, available: false, email: null, api_error: null });
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
    iMessage: { loading: true, connected: false, label: '' },
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

  // ADHD mode
  const [adhdEnabled, setAdhdEnabled] = useState(false);
  const [adhdCheckInSeconds, setAdhdCheckInSeconds] = useState(30);
  const [adhdFocusMode, setAdhdFocusMode] = useState(false);

  const [activeSection, setActiveSection] = useState('section-connections');
  const [expandedConnection, setExpandedConnection] = useState<string | null>(null);
  const [filesDir, setFilesDir] = useState<string>('');
  const [projectsDir, setProjectsDir] = useState<string>('');
  const [plansBecomesSpecs, setPlansBecomesSpecs] = useState<boolean>(true);
  const [inboundImessageRoutingEnabled, setInboundImessageRoutingEnabled] = useState<boolean>(false);
  const [defaultConfluenceSpace, setDefaultConfluenceSpace] = useState('');
  const [wipeDataError, setWipeDataError] = useState<string | null>(null);


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
        if ((data as any).files_dir) setFilesDir((data as any).files_dir);
        if ((data as any).projects_dir) setProjectsDir((data as any).projects_dir);
        if ((data as any).plans_become_specs !== undefined) setPlansBecomesSpecs(!!(data as any).plans_become_specs);
        if ((data as any).inbound_imessage_routing_enabled !== undefined) setInboundImessageRoutingEnabled(!!(data as any).inbound_imessage_routing_enabled);
        if ((data as any).shortcuts) setCustomShortcuts((data as any).shortcuts);
        if ((data as any).auto_template_matching !== undefined) {
          setAutoTemplateMatching((data as any).auto_template_matching);
        }
        if ((data as any).briefing_enabled !== undefined) {
          setBriefingEnabled((data as any).briefing_enabled);
        }
        if ((data as any).chat_memory_enabled !== undefined) {
          setChatMemoryEnabled((data as any).chat_memory_enabled);
        }
        if ((data as any).chat_receipts_gate_enabled !== undefined) {
          setChatReceiptsGateEnabled((data as any).chat_receipts_gate_enabled);
        }
        if ((data as any).use_gemini_cli !== undefined) {
          setUseGeminiCli((data as any).use_gemini_cli);
        }
        if (typeof (data as any).standing_instructions === 'string') {
          setStandingInstructions((data as any).standing_instructions);
        }
        if ((data as any).use_ostk_terms !== undefined) setUseOstkTerms((data as any).use_ostk_terms);
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
        if (typeof (data as any).default_confluence_space === 'string') {
          setDefaultConfluenceSpace((data as any).default_confluence_space);
        }
      } catch {
        // API not available, use defaults
      }
    };
    fetchSettings();
    // Load per-user memory file and overflow status for the Memory editor.
    api.get<{ content: string }>('/memory')
      .then((d) => setMemoryContent(d.content ?? ''))
      .catch(() => {});
    api.get<{ overflowed: boolean; reason: string; kb: number; lines: number; total_kb: number; hard_cap: boolean }>('/memory/user/overflow-status')
      .then((d) => setMemoryOverflow(d))
      .catch(() => {});
    // Check whether the local local subscription programs are ready.
    api.get<{ claude_code_available?: boolean; gemini_cli_available?: boolean }>('/settings/chat-backend-status')
      .then((data) => {
        setClaudeCodeReady(!!data.claude_code_available);
        setGeminiCliReady(!!data.gemini_cli_available);
      })
      .catch(() => {
        setClaudeCodeReady(false);
        setGeminiCliReady(false);
      });
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
        setKeyStatusLoading(false);
      })
      .catch(() => { setKeyStatusLoading(false); });
    // Fetch Gemini Enterprise availability
    api.get<{ available: boolean; email: string | null; api_error?: string | null }>('/gemini/status')
      .then((data) => setGeminiStatus({ loading: false, available: !!data.available, email: data.email ?? null, api_error: data.api_error ?? null }))
      .catch(() => setGeminiStatus({ loading: false, available: false, email: null, api_error: null }));

    // Kick off all four connection status fetches in parallel so every
    // dot renders in the same tick. Each call is cached server-side with
    // a short TTL so repeat visits return in under one millisecond.
    void (async () => {
      type GmailStatus = { authenticated: boolean; email: string | null };
      type CalStatus = { authenticated: boolean; email: string | null };
      type DriveStatus = { authenticated: boolean; email: string | null };
      type SlackStat = { connected: boolean; team_name: string };
      type iMessageStat = { available: boolean; reason: string };
      const [gmail, cal, drive, slack, imsg] = await Promise.all([
        api.get<GmailStatus>('/gmail/auth/status').catch(() => ({ authenticated: false, email: null })),
        api.get<CalStatus>('/calendar/auth/status').catch(() => ({ authenticated: false, email: null })),
        api.get<DriveStatus>('/drive/auth/status').catch(() => ({ authenticated: false, email: null })),
        api.get<SlackStat>('/slack/status').catch(() => ({ connected: false, team_name: '' })),
        api.get<iMessageStat>('/imessage/status').catch(() => ({ available: false, reason: '' })),
      ]);
      setConnectionStatus({
        Gmail: { loading: false, connected: !!gmail.authenticated, label: gmail.email || '' },
        Calendar: { loading: false, connected: !!cal.authenticated, label: cal.email || '' },
        Drive: { loading: false, connected: !!drive.authenticated, label: drive.email || '' },
        Slack: { loading: false, connected: !!slack.connected, label: slack.team_name || '' },
        iMessage: { loading: false, connected: !!imsg.available, label: '' },
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



  const providers = [
    { name: 'Anthropic', model: 'Claude' },
    { name: 'Google Gemini', model: 'Gemini' },
  ];

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
    api.get<{ claude_code_available?: boolean; gemini_cli_available?: boolean }>('/settings/chat-backend-status')
      .then((data) => {
        setClaudeCodeReady(!!data.claude_code_available);
        if (data.gemini_cli_available !== undefined) setGeminiCliReady(!!data.gemini_cli_available);
      })
      .catch(() => setClaudeCodeReady(false))
      .finally(() => setRecheckingClaude(false));
  };

  const handleRecheckGeminiStatus = () => {
    setRecheckingGemini(true);
    api.get<{ claude_code_available?: boolean; gemini_cli_available?: boolean }>('/settings/chat-backend-status')
      .then((data) => {
        if (data.claude_code_available !== undefined) setClaudeCodeReady(!!data.claude_code_available);
        setGeminiCliReady(!!data.gemini_cli_available);
      })
      .catch(() => setGeminiCliReady(false))
      .finally(() => setRecheckingGemini(false));
  };

  const handleUseGeminiCliToggle = (value: boolean) => {
    setUseGeminiCli(value);
    api.patch('/settings', { use_gemini_cli: value }).catch(() => {});
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

  const handleShortcutEdit = (label: string, keys: string) => {
    const updated = { ...customShortcuts, [label]: keys };
    setCustomShortcuts(updated);
    setEditingShortcut(null);
    api.patch('/settings', { shortcuts: updated }).catch(() => {});
  };

  const handleShortcutReset = (label: string) => {
    const updated = { ...customShortcuts };
    delete updated[label];
    setCustomShortcuts(updated);
    api.patch('/settings', { shortcuts: updated }).catch(() => {});
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

  const handleReceiptsGateToggle = () => {
    const next = !chatReceiptsGateEnabled;
    setChatReceiptsGateEnabled(next);
    api.patch('/settings', { chat_receipts_gate_enabled: next }).catch(() => {});
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

  const handleSaveMemory = async () => {
    try {
      await api.put('/memory', { content: memoryContent });
      setMemorySaveStatus('Saved');
    } catch {
      setMemorySaveStatus('Could not save. Check your connection and try again.');
    } finally {
      setTimeout(() => setMemorySaveStatus(null), 3000);
    }
  };

  const handleSuggestTopics = async () => {
    setSuggestTopicsLoading(true);
    setSuggestedTopics(null);
    try {
      const res = await api.post<{ topics: { topic: string; bullets: string[] }[] }>(
        '/memory/user/suggest-topics',
        { content: memoryContent }
      );
      setSuggestedTopics(res.topics ?? []);
    } catch {
      setSuggestedTopics([]);
    } finally {
      setSuggestTopicsLoading(false);
    }
  };

  const handleApplySplit = async (bullet: string, topic: string) => {
    try {
      await api.post('/memory/user/split-topic', { bullet_text: bullet, topic_name: topic });
      // Reload memory content and overflow status after split.
      const [mem, status] = await Promise.all([
        api.get<{ content: string }>('/memory'),
        api.get<{ overflowed: boolean; reason: string; kb: number; lines: number; total_kb: number; hard_cap: boolean }>('/memory/user/overflow-status'),
      ]);
      setMemoryContent(mem.content ?? '');
      setMemoryOverflow(status);
      // Remove the applied bullet from suggestions.
      setSuggestedTopics(prev =>
        prev?.map(t => t.topic === topic ? { ...t, bullets: t.bullets.filter(b => b !== bullet) } : t)
            .filter(t => t.bullets.length > 0) ?? null
      );
    } catch {
      // Silent — bullet may have already been moved.
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
    if (hash === '#memory') {
      setActiveSection('section-preferences');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const handleDeleteAllData = async () => {
    const ok = await confirm({
      title: 'Delete all your data?',
      message: 'This permanently deletes all your tasks, chats, and agent history. Your settings are kept so the app still works after.',
      confirmLabel: 'Delete everything',
      danger: true,
    });
    if (!ok) return;
    setWipeDataError(null);
    try {
      await api.delete('/settings/data');
      window.location.reload();
    } catch {
      setWipeDataError('Something went wrong. Please try again.');
    }
  };

  const cardClass =
    'bg-white dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 p-4 sm:p-6 rounded-xl hover:border-slate-200 dark:hover:border-slate-700 transition-colors';

  const navItems = [
    { id: 'section-connections', label: 'Connections', icon: 'hub' },
    { id: 'section-preferences', label: 'Preferences', icon: 'palette' },
  ];

  // No IntersectionObserver needed: tabs show one section at a time.

  return (
    <PageShell title="Settings">

      <TopNavTabs
        tabs={navItems.map((n) => ({ key: n.id, label: n.label }))}
        active={activeSection}
        onChange={setActiveSection}
      />

      {/* Main content */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">

          {/* ── How torios works ─────────────────── */}
          <div className={`lg:col-span-2 ${activeSection !== 'section-preferences' ? 'hidden' : ''}`}>
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-4">How torios works</h2>
            <div className="space-y-4">
              {/* Plans → specs toggle */}
              <div className="flex items-center justify-between">
                <div className="pr-3">
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Turn plans into specs automatically</p>
                  <p className="text-xs text-slate-500">When on, approved plans become tracked spec documents instead of one-off files.</p>
                </div>
                <Toggle checked={plansBecomesSpecs} onChange={() => {
                  const next = !plansBecomesSpecs;
                  setPlansBecomesSpecs(next);
                  api.patch('/settings', { plans_become_specs: next }).catch(() => {});
                }} testId="plans-become-specs-toggle" />
              </div>
              {/* iMessage routing toggle */}
              <div className="flex items-center justify-between pt-4 border-t border-slate-200 dark:border-slate-800">
                <div className="pr-3">
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Act on incoming messages</p>
                  <p className="text-xs text-slate-500">When on, torios reads new iMessages and can kick off tasks from them automatically.</p>
                </div>
                <Toggle checked={inboundImessageRoutingEnabled} onChange={() => {
                  const next = !inboundImessageRoutingEnabled;
                  setInboundImessageRoutingEnabled(next);
                  api.patch('/settings', { inbound_imessage_routing_enabled: next }).catch(() => {});
                }} testId="inbound-imessage-toggle" />
              </div>
              {/* Text-to-agent (formerly "Channel Routing Rules") */}
              <div className="pt-4 border-t border-slate-200 dark:border-slate-800" data-testid="channel-routing-rules-section">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200 mb-1">Text-to-agent</p>
                <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">
                  Controls how messages sent to your phone get turned into agent actions. When the iMessage poller is active, incoming texts are read and parsed into one of these commands:
                </p>
                <ul className="text-xs text-slate-500 dark:text-slate-400 space-y-1 mb-3 list-none">
                  <li><span className="font-mono text-slate-700 dark:text-slate-300">spawn &lt;name&gt; to &lt;task&gt;</span> — starts a new agent with that task</li>
                  <li><span className="font-mono text-slate-700 dark:text-slate-300">nudge &lt;agent&gt; &lt;message&gt;</span> — sends a follow-up message to a running agent</li>
                  <li><span className="font-mono text-slate-700 dark:text-slate-300">status</span> — returns a summary of what agents are currently running</li>
                </ul>
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  The live poller that reads new iMessages is off by default. Set the environment variable <span className="font-mono">CHANNEL_ROUTING_LIVE_POLLER_ENABLED=1</span> to turn it on.
                </p>
              </div>
              {/* Files location */}
              <div className="pt-4 border-t border-slate-200 dark:border-slate-800">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200 mb-1">Where files are saved</p>
                <p className="text-xs text-slate-500 mb-2">torios saves documents, exports, and attachments here.</p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={filesDir}
                    onChange={(e) => setFilesDir(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') api.patch('/settings', { files_dir: filesDir }).catch(() => {}); }}
                    placeholder="~/.myos/files"
                    data-testid="files-dir-input"
                    className="flex-1 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white font-mono focus:outline-none focus:border-blue-500 transition-colors"
                  />
                  <button
                    onClick={() => api.patch('/settings', { files_dir: filesDir }).catch(() => {})}
                    data-testid="files-dir-save"
                    className="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    Save
                  </button>
                </div>
              </div>
              {/* Projects location */}
              <div className="pt-4 border-t border-slate-200 dark:border-slate-800">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200 mb-1">Where projects are saved</p>
                <p className="text-xs text-slate-500 mb-2">torios looks here for your project docs and recent files.</p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={projectsDir}
                    onChange={(e) => setProjectsDir(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') api.patch('/settings', { projects_dir: projectsDir }).catch(() => {}); }}
                    placeholder="~/.myos/projects"
                    data-testid="projects-dir-input"
                    className="flex-1 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white font-mono focus:outline-none focus:border-blue-500 transition-colors"
                  />
                  <button
                    onClick={() => api.patch('/settings', { projects_dir: projectsDir }).catch(() => {})}
                    data-testid="projects-dir-save"
                    className="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    Save
                  </button>
                </div>
              </div>
            </div>
          </div>
          </div>

          {/* ── AI behavior ──────────────────────── */}
          <div id="section-instructions" className={`lg:col-span-2 ${activeSection !== 'section-preferences' ? 'hidden' : ''}`}>
          <div
            ref={standingSectionRef}
            id="standing-instructions"
            className={cardClass}
            data-testid="standing-instructions-section"
          >
          <h2 className="text-lg font-semibold mb-4">AI behavior</h2>
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Your instructions</p>
          <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
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
              className="mb-3 text-sm text-red-600 dark:text-red-400"
            >
              {suggestError}
            </div>
          )}
          {suggestions.length > 0 && (
            <div
              data-testid="standing-instructions-suggestions"
              className="mb-4 p-3 bg-slate-50/60 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 rounded-lg space-y-2"
            >
              <p className="text-sm text-slate-700 dark:text-slate-300 mb-2">
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
                    className="flex-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded px-2 py-1 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500"
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
                  className="px-3 py-1.5 bg-slate-200 dark:bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-900 dark:text-white text-sm font-medium transition-colors"
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
            className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
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
                className={`text-sm ${standingSaveIsError ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'}`}
              >
                {standingSaveStatus}
              </span>
            )}
          </div>

          {/* Rules link (E) */}
          <div className="mt-4 pt-4 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between">
            <div>
              <p className="text-sm text-slate-700 dark:text-slate-300">Rules</p>
              <p className="text-xs text-slate-500">The rules your agents always follow.</p>
            </div>
            <NavLink
              to="/settings/rules"
              data-testid="settings-rules-link"
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-200 dark:bg-slate-700 hover:bg-slate-600 text-slate-800 dark:text-slate-200 transition-colors"
            >
              Open
            </NavLink>
          </div>

          {/* What it's learned (D) */}
          <div className="mt-6 pt-6 border-t border-slate-200 dark:border-slate-800">
            <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">What it's learned</p>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">Things you've told me to remember. Edit or remove anytime.</p>
            {memoryOverflow?.hard_cap && (
              <div data-testid="memory-hard-cap-banner" className="mb-4 rounded-lg bg-red-900/40 border border-red-700 px-4 py-3 text-sm text-red-200">
                Your memory file is very large ({memoryOverflow.total_kb.toFixed(0)} KB). Remove anything outdated.
              </div>
            )}
            {(() => {
              const bullets = parseMemoryProvenance(memoryContent);
              if (bullets.length > 0) {
                return (
                  <ul data-testid="memory-bullet-list" className="mb-4 space-y-2">
                    {bullets.map((bullet, i) => {
                      const label = bullet.added
                        ? `added ${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(bullet.added)}`
                        : 'edited manually';
                      return (
                        <li key={i} className="flex items-baseline gap-3">
                          <span className="text-sm text-slate-800 dark:text-slate-200 flex-1">{bullet.text}</span>
                          <span data-testid={`memory-provenance-${i}`} className="text-xs text-slate-500 shrink-0">{label}</span>
                        </li>
                      );
                    })}
                  </ul>
                );
              }
              return null;
            })()}
            <textarea
              data-testid="memory-editor"
              value={memoryContent}
              onChange={(e) => setMemoryContent(e.target.value)}
              placeholder="Things you tell me to remember will show up here."
              rows={8}
              className="w-full rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-sm text-slate-800 dark:text-slate-200 px-3 py-2 font-mono resize-y focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <div className="mt-3 flex items-center gap-3">
              <button data-testid="memory-save-button" onClick={handleSaveMemory} className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors">Save</button>
              {memorySaveStatus && <span className="text-xs text-slate-600 dark:text-slate-400">{memorySaveStatus}</span>}
            </div>
          </div>
          </div>
          </div>



          {/* ── 2. Appearance ───────────────────────── */}
          <div id="section-appearance" className={`space-y-6${activeSection !== 'section-preferences' ? ' hidden' : ''}`}>
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-5">Appearance</h2>

            {/* Accent Color */}
            <div className="mb-5">
              <label className="text-sm text-slate-600 dark:text-slate-400 mb-2 block">Accent Color</label>
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
              <label className="text-sm text-slate-600 dark:text-slate-400 mb-2 block">OS Identifier</label>
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
              <label className="text-sm text-slate-600 dark:text-slate-400 mb-2 block">Compact Mode</label>
              <p className="text-xs text-slate-500 mb-2">Tighter spacing and smaller text throughout the app.</p>
              <div className="flex gap-2">
                <button
                  onClick={() => setCompactMode(false)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    !compactMode
                      ? 'accent-bg !text-white'
                      : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:text-white'
                  }`}
                >
                  Normal
                </button>
                <button
                  onClick={() => setCompactMode(true)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    compactMode
                      ? 'accent-bg !text-white'
                      : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:text-white'
                  }`}
                >
                  Compact
                </button>
              </div>
            </div>

            {/* Greeting Style */}
            <div className="mb-5">
              <label className="text-sm text-slate-600 dark:text-slate-400 mb-2 block">Home Greeting</label>
              <div className="flex gap-2">
                {(['time', 'quote', 'none'] as const).map((style) => (
                  <button
                    key={style}
                    onClick={() => setGreetingStyle(style)}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      greetingStyle === style
                        ? 'accent-bg !text-white'
                        : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:text-white'
                    }`}
                  >
                    {style === 'time' ? 'Time of day' : style === 'quote' ? 'Quote' : 'None'}
                  </button>
                ))}
              </div>
            </div>

          </div>

          {/* Features (F) */}
          <div className={`lg:col-span-2 ${activeSection !== 'section-preferences' ? 'hidden' : ''}`}>
          <div className={cardClass}>
          <h2 className="text-lg font-semibold mb-5">Features</h2>
          <p className="text-xs text-slate-500 mb-3">Toggle to show or hide in the sidebar. Drag items in the sidebar itself to reorder.</p>
          <div className="space-y-1.5">
            {features.map((f: { label: string; enabled: boolean }, index: number) => (
              <div
                key={f.label}
                className="flex items-center gap-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg px-3 py-2.5"
              >
                <Icon name={featureIcons[f.label] || 'extension'} className="text-slate-600 dark:text-slate-400" size={18} />
                <span className="flex-1 text-sm text-slate-700 dark:text-slate-300">{featureDisplayNames[f.label] || f.label}</span>
                <Toggle checked={f.enabled} onChange={() => handleFeatureToggle(index)} label={featureDisplayNames[f.label] || f.label} />
              </div>
            ))}
          </div>

          </div>
          </div>
          </div>

          {/* ── Power user (G) ────────────────────── */}
          <div className={activeSection !== 'section-preferences' ? 'hidden' : ''}>
          <div className={cardClass}>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold">Power user mode</h2>
                <div className="group relative">
                  <Icon name="help_outline" size={16} className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 cursor-help" />
                  <div className="absolute left-0 top-full mt-1 w-80 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-3 text-xs text-slate-700 dark:text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                    <p className="font-semibold text-slate-900 dark:text-white mb-1">What this unlocks</p>
                    <p className="mb-2"><strong>Delegate tab:</strong> see suggested tasks an agent could pick up and hand them off with one click.</p>
                    <p className="mb-2"><strong>Shared Workspace tab:</strong> a message board where multiple agents leave each other notes mid-task, so they can build on each other's findings.</p>
                    <p className="mb-2"><strong>ostk browser:</strong> read-only view of the underlying kernel files (decisions, tasks history, audit log) for verifying what actually happened.</p>
                    <p>Both are useful when you run multiple agents in parallel. Most people will not need them.</p>
                  </div>
                </div>
              </div>
              <Toggle checked={powerUserMode} onChange={() => setPowerUserMode(!powerUserMode)} testId="power-user-toggle" />
            </div>
            <p className="text-xs text-slate-500">Shows advanced agent tabs (Delegate, Shared Workspace) and the ostk browser in the sidebar.</p>
          </div>
          </div>

          {/* ── AI Provider and Chat Settings (shown in Connections tab) ────────────────────────── */}
          <div id="section-ai-chat" className={activeSection !== 'section-connections' ? 'hidden' : 'space-y-6'}>
          <div className={cardClass} data-testid="api-key-setup-section">
            <h2 className="text-lg font-semibold mb-5">AI Provider</h2>

            {/* Provider for API Key setup */}
            <div className="mb-5">
              <label className="text-sm text-slate-600 dark:text-slate-400 mb-2 block">Set Up Provider</label>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {providers.map((p) => (
                  <div
                    key={p.name}
                    onClick={() => handleProviderSelect(p.name)}
                    className={`p-3 rounded-lg border text-center cursor-pointer transition-colors ${
                      selectedProvider === p.name
                        ? 'accent-border accent-highlight'
                        : 'border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800/50 hover:border-slate-600'
                    }`}
                  >
                    <p className="text-sm font-medium">{p.name}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Connection */}
            <div className="mb-5">
              <label className="text-sm text-slate-600 dark:text-slate-400 mb-3 block">Connect {selectedProvider}</label>

              {/* Status indicator */}
              {keyStatusLoading ? (
                <div data-testid="key-status-skeleton" className="h-8 bg-slate-50/60 dark:bg-slate-800/60 rounded-lg animate-pulse mb-3" />
              ) : keyAvailable[selectedProvider] ? (
                <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-white border border-green-300 rounded-lg">
                  <Icon name="check_circle" size={16} className="text-green-600" />
                  <span className="text-sm text-green-700">
                    Key available (stored in {keySource[selectedProvider] === 'keychain' ? 'system keychain' : keySource[selectedProvider] === 'env' ? 'environment' : 'settings'})
                  </span>
                </div>
              ) : (
                <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-amber-900/20 border border-amber-800/30 rounded-lg">
                  <Icon name="warning" size={16} className="text-amber-600 dark:text-amber-400" />
                  <span className="text-sm text-amber-600 dark:text-amber-400">No key found. Paste one below to save it securely.</span>
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
                    className="w-full mb-3 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-medium text-slate-900 dark:text-white hover:border-blue-500 transition-colors flex items-center gap-2"
                  >
                    <Icon name="login" size={18} />
                    Sign in with Google
                  </button>
                ) : (
                  <p className="text-sm text-slate-600 dark:text-slate-400 mb-3">
                    Google sign-in is not set up yet. Paste a Gemini API key below.
                  </p>
                )
              ) : (
                <button
                  onClick={() => window.open('https://console.anthropic.com/settings/keys', '_blank')}
                  className="w-full mb-3 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-medium text-slate-900 dark:text-white hover:border-blue-500 transition-colors flex items-center gap-2"
                >
                  <Icon name="open_in_new" size={18} />
                  Get a key from Anthropic
                </button>
              )}

              {/* Gemini: subscription note + Advanced toggle for Cloud setup */}
              {selectedProvider === 'Google Gemini' && (
                <>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
                    A paid Gemini app subscription (Gemini Advanced) doesn't include API access, so it can't be used here. The free key below works with the same Google account.
                  </p>
                  <div className="mb-3">
                    <button
                      data-testid="gemini-advanced-toggle"
                      onClick={() => setGeminiAdvancedOpen((v) => !v)}
                      className="text-xs text-slate-500 dark:text-slate-400 underline hover:opacity-80"
                    >
                      {geminiAdvancedOpen ? 'Hide Google Cloud setup' : 'Advanced: set up through Google Cloud'}
                    </button>
                    {geminiAdvancedOpen && (
                      <div
                        className={`mt-2 p-3 rounded-lg text-xs space-y-2 border bg-gradient-to-r from-blue-500/10 to-cyan-500/10 border-blue-500/30 ${
                          darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-700'
                        }`}
                        data-testid="gemini-key-help"
                      >
                        <p>
                          Use the same{' '}
                          <a
                            href="https://console.cloud.google.com"
                            target="_blank"
                            rel="noreferrer"
                            className={`underline ${darkMode ? 'text-blue-700 dark:text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
                          >
                            Google Cloud project
                          </a>{' '}
                          you set up for Drive, Calendar, or Gmail. Three steps:
                        </p>
                        <ol className="list-decimal ml-5 space-y-1">
                          <li>
                            Enable{' '}
                            <a
                              href="https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com"
                              target="_blank"
                              rel="noreferrer"
                              className={`underline ${darkMode ? 'text-blue-700 dark:text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
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
                          Only using Gemini for chat? Grab a free key at{' '}
                          <a
                            href="https://aistudio.google.com/apikey"
                            target="_blank"
                            rel="noreferrer"
                            className={`underline ${darkMode ? 'text-blue-700 dark:text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
                          >
                            Google AI Studio
                          </a>{' '}
                          instead. It's one click and ties to your personal Google account.
                        </p>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Gemini CLI Toggle */}
              {selectedProvider === 'Google Gemini' && (
                <div className="mb-6 pt-4 border-t border-slate-200 dark:border-slate-800">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-800 dark:text-slate-200">Use Gemini CLI</span>
                    </div>
                    <Toggle checked={useGeminiCli} onChange={() => handleUseGeminiCliToggle(!useGeminiCli)} testId="gemini-cli-toggle" />
                  </div>
                  <p className="text-xs text-slate-500 mb-4">
                    Routes chat through your local <code>gemini</code> command. Uses your Google One AI Ultra subscription instead of API credits.
                  </p>

                  <div className="flex items-center gap-3 flex-wrap">
                    <div
                      data-testid="gemini-cli-ready-indicator"
                      className="flex items-center gap-2 text-xs"
                    >
                      {geminiCliReady === null ? (
                        <>
                          <span className="w-2 h-2 rounded-full bg-slate-500" />
                          <span className="text-slate-500">Checking Gemini CLI...</span>
                        </>
                      ) : geminiCliReady ? (
                        <>
                          <span className="w-2 h-2 rounded-full bg-green-500" />
                          <span className="text-green-600 dark:text-green-400">Gemini CLI is ready</span>
                        </>
                      ) : (
                        <>
                          <span className="w-2 h-2 rounded-full bg-red-500" />
                          <span className="text-red-600 dark:text-red-400">Gemini CLI not found or not signed in</span>
                        </>
                      )}
                    </div>
                    <button
                      onClick={handleRecheckGeminiStatus}
                      disabled={recheckingGemini}
                      className="text-[10px] text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors uppercase font-bold tracking-tight disabled:opacity-50"
                    >
                      {recheckingGemini ? 'Checking...' : 'Recheck'}
                    </button>
                  </div>
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
                    className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 pr-10 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-colors"
                  />
                  <button
                    onClick={() => setApiKeyVisible(!apiKeyVisible)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-600 dark:text-slate-400 hover:text-white transition-colors"
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
                <label className="text-sm text-slate-600 dark:text-slate-400 mb-2 block">Model</label>
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

          <div className={activeSection !== 'section-connections' ? 'hidden' : 'space-y-6'}>
          <div className={cardClass} data-testid="chat-backend-section">
          <h2 className="text-lg font-semibold mb-1">AI backend</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
            Pick which sign-in powers every AI feature in yourOS: chat, tasks, specs, and more. Your Claude subscription costs nothing extra if you already pay for Pro or Max. Using your Anthropic key charges per message.
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
                    : 'border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800/50 hover:border-slate-600'
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
                <span className="text-sm text-slate-800 dark:text-slate-200">{opt.label}</span>
              </label>
            ))}
          </div>

          <div className="mt-4 pt-4 border-t border-slate-200 dark:border-slate-800">
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
                    <span className="text-green-600 dark:text-green-400" data-testid="claude-auth-status-signed-in">Claude subscription is ready</span>
                  </>
                ) : (
                  <>
                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                    <span className="text-amber-600 dark:text-amber-400" data-testid="claude-auth-status-not-signed-in">Claude subscription not signed in</span>
                  </>
                )}
              </div>
              <button
                data-testid="claude-recheck-button"
                onClick={handleRecheckClaudeStatus}
                disabled={recheckingClaude}
                className="text-xs px-2.5 py-1 rounded bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:border-slate-500 transition-colors disabled:opacity-50"
              >
                {recheckingClaude ? 'Checking...' : 'Re-check'}
              </button>
            </div>
            {claudeCodeReady === false && (
              <div className="mt-3 p-3 rounded-lg bg-slate-50/60 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 text-sm text-slate-700 dark:text-slate-300" data-testid="claude-login-instructions">
                <p className="font-medium text-slate-900 dark:text-white mb-1">To sign in with Claude Pro or Max</p>
                <p>Open your Terminal app and run:</p>
                <code className="block mt-1.5 px-2 py-1 bg-white dark:bg-slate-900 rounded text-green-600 dark:text-green-400 font-mono text-xs select-all">claude login</code>
                <p className="mt-2 text-slate-600 dark:text-slate-400 text-xs">Then click Re-check above to confirm it worked.</p>
              </div>
            )}
          </div>
          </div>

          {/* ── 4. AI Provider (shown with AI & Chat tab) ── */}
          <div id="section-ai-provider" className="space-y-6">
          <div className={cardClass} data-testid="ai-provider-section">
            <h2 className="text-lg font-semibold mb-1">AI Provider</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
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
                    : 'border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800/50 hover:border-slate-600'
                }`}
              >
                <span className="mt-1 w-2.5 h-2.5 rounded-full bg-emerald-400 claude-provider-dot flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Claude</p>
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
                    ? 'border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800/50 hover:border-slate-600'
                    : 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/30 opacity-60 cursor-not-allowed'
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
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Gemini Enterprise</p>
                  {geminiStatus.loading ? (
                    <p className="text-xs text-slate-500 mt-0.5">Checking...</p>
                  ) : geminiStatus.available ? (
                    <>
                      {geminiStatus.email && (
                        <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 truncate">{geminiStatus.email}</p>
                      )}
                      <p className="text-xs text-slate-500 mt-0.5">Google Workspace · Slack · Jira · Confluence</p>
                      {defaultProvider === 'gemini' && (
                        <p className="text-xs accent-text mt-1 font-medium">Default</p>
                      )}
                    </>
                  ) : (
                    <>
                      <p className="text-xs text-slate-500 mt-0.5">
                        Run <code className="text-green-600 dark:text-green-400 font-mono">gcloud auth application-default login</code> or the Gemini CLI to connect
                      </p>
                      {geminiStatus.api_error && (
                        <p
                          data-testid="gemini-api-error"
                          aria-label="Gemini API error"
                          className="text-xs text-red-600 dark:text-red-400 mt-1 break-words"
                        >
                          {geminiStatus.api_error}
                        </p>
                      )}
                    </>
                  )}
                </div>
              </button>
            </div>
          </div>
          </div>
          </div>

          {/* ── Connections Tab ──────────────────────── */}
          <div id="section-connections" className={`lg:col-span-2 space-y-6${activeSection !== 'section-connections' ? ' hidden' : ''}`}>
          <div className="space-y-4">
            {/* Google pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'google' ? null : 'google')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-google"
            >
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                connectionStatus.Drive.loading
                  ? 'bg-slate-600 animate-pulse'
                  : connectionStatus.Drive.connected
                  ? 'bg-emerald-400'
                  : 'bg-slate-600'
              }`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Google</p>
                <p className="text-xs text-slate-600 dark:text-slate-400 truncate">{connectionStatus.Drive.connected ? connectionStatus.Drive.label || 'Connected' : 'Sign in to use Gmail, Calendar, and Drive'}</p>
              </div>
              <Icon name={expandedConnection === 'google' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-600 dark:text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'google' && (
              <div className={cardClass} data-testid="google-connect-section">
                <div className="flex items-center gap-2 mb-3">
                  <Icon name="travel_explore" size={18} className="text-blue-600 dark:text-blue-400" />
                  <h2 className="text-base font-semibold">Google</h2>
                </div>
                <p className="text-xs text-slate-600 dark:text-slate-400 mb-3">Gmail, Calendar, and Drive</p>
                {connectionStatus.Drive.connected ? (
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
                      <p className="text-sm text-slate-800 dark:text-slate-200 font-medium">
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
                          reportError('Failed to disconnect Google', err);
                        }
                      }}
                      className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-lg text-sm text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-colors"
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
                        reportError('Google sign-in failed to start', err);
                      }
                    }}
                    className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
                    data-testid="google-connect-btn"
                  >
                    <Icon name="login" size={16} />
                    Connect Google
                  </button>
                ) : (
                  <p className="text-sm text-slate-600 dark:text-slate-400">
                    Google sign-in is not configured for this instance.
                  </p>
                )}
              </div>
            )}

            {/* Slack pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'slack' ? null : 'slack')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-slack"
            >
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                connectionStatus.Slack.loading
                  ? 'bg-slate-600 animate-pulse'
                  : connectionStatus.Slack.connected
                  ? 'bg-emerald-400'
                  : 'bg-slate-600'
              }`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Slack</p>
                <p className="text-xs text-slate-600 dark:text-slate-400 truncate">{connectionStatus.Slack.connected ? connectionStatus.Slack.label || 'Connected' : 'Sign in to use slash commands'}</p>
              </div>
              <Icon name={expandedConnection === 'slack' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-600 dark:text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'slack' && (
              <div className={cardClass} data-testid="slack-connect-section">
                <div className="flex items-center gap-2 mb-4">
                  <Icon name="forum" size={18} className="text-purple-600 dark:text-purple-400" />
                  <h2 className="text-base font-semibold">Slack</h2>
                </div>
                <SlackConnect />
              </div>
            )}

            {/* iMessage pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'imessage' ? null : 'imessage')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-imessage"
            >
              <span
                data-testid="imessage-status-dot"
                className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                  connectionStatus.iMessage.loading
                    ? 'bg-slate-600 animate-pulse'
                    : connectionStatus.iMessage.connected
                    ? 'bg-emerald-400'
                    : 'bg-slate-600'
                }`}
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">iMessage</p>
                <p className="text-xs text-slate-600 dark:text-slate-400">{connectionStatus.iMessage.connected ? 'Connected' : 'Set up iMessage'}</p>
              </div>
              <Icon name={expandedConnection === 'imessage' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-600 dark:text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'imessage' && (
              <div className={cardClass} data-testid="imessage-connect-section">
                <div className="flex items-center gap-2 mb-4">
                  <Icon name="chat_bubble" size={18} className="text-green-600 dark:text-green-400" />
                  <h2 className="text-base font-semibold">iMessage</h2>
                </div>
                <p className="text-sm text-slate-600 dark:text-slate-400 mb-3">
                  Read and reply to iMessages from within yourOS. Requires macOS.
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
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-github"
            >
              <span className="w-2.5 h-2.5 rounded-full bg-slate-600 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">GitHub</p>
                <p className="text-xs text-slate-600 dark:text-slate-400">Sign in to access your repos</p>
              </div>
              <Icon name={expandedConnection === 'github' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-600 dark:text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'github' && (
              <div className={cardClass} data-testid="github-connect-section">
                <div className="flex items-center gap-2 mb-4">
                  <Icon name="code" size={18} className="text-slate-700 dark:text-slate-300" />
                  <h2 className="text-base font-semibold">GitHub</h2>
                </div>
                <GithubSetupCard
                  darkMode={true}
                  inputCls="bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-900 dark:text-white"
                  subtextCls="text-slate-600 dark:text-slate-400"
                />
              </div>
            )}

            {/* Atlassian pill */}
            <button
              onClick={() => setExpandedConnection(expandedConnection === 'atlassian' ? null : 'atlassian')}
              className="w-full flex items-center gap-3 px-4 py-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/70 transition-colors text-left"
              data-testid="pill-atlassian"
            >
              <span className="w-2.5 h-2.5 rounded-full bg-slate-600 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Atlassian</p>
                <p className="text-xs text-slate-600 dark:text-slate-400">Connect Jira & Confluence</p>
              </div>
              <Icon name={expandedConnection === 'atlassian' ? 'expand_less' : 'expand_more'} size={18} className="text-slate-600 dark:text-slate-400 flex-shrink-0" />
            </button>
            {expandedConnection === 'atlassian' && (
              <>
                <div className={cardClass} data-testid="atlassian-connect-section">
                  <div className="flex items-center gap-2 mb-4">
                    <Icon name="link" size={18} className="text-blue-600 dark:text-blue-400" />
                    <h2 className="text-base font-semibold text-slate-800 dark:text-slate-200">Jira & Confluence</h2>
                  </div>
                  {/* UAT item 6: AtlassianConnect always renders a connect path
                      (OAuth button + token form) and never blanks while
                      /atlassian/status is loading. The old AtlassianSetupCard
                      returned null until connected===false resolved, which left
                      the panel empty below the header. */}
                  <AtlassianConnect />
                </div>
                <div className={cardClass} data-testid="atlassian-preferences-section">
                  <h2 className="text-base font-semibold mb-3 text-slate-800 dark:text-slate-200">Confluence preferences</h2>
                  <div>
                    <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1" htmlFor="default-confluence-space">
                      Default Confluence space
                    </label>
                    <div className="flex gap-2">
                      <input
                        id="default-confluence-space"
                        type="text"
                        value={defaultConfluenceSpace}
                        onChange={(e) => setDefaultConfluenceSpace(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') api.patch('/settings', { default_confluence_space: defaultConfluenceSpace.trim() }).catch(() => {}); }}
                        placeholder="e.g. IAM"
                        data-testid="default-confluence-space-input"
                        className="flex-1 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white placeholder:text-slate-500 outline-none focus:border-blue-500/50"
                      />
                      <button
                        onClick={() => api.patch('/settings', { default_confluence_space: defaultConfluenceSpace.trim() }).catch(() => {})}
                        data-testid="confluence-space-save"
                        className="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
                      >
                        Save
                      </button>
                    </div>
                    <p className="text-xs text-slate-500 mt-1">
                      Paste a space shortcut (like IAM) or a full Confluence URL. The widget will show pages from this space only.
                    </p>
                  </div>
                </div>
              </>
            )}

            {/* Custom tack commands */}
            <div className={cardClass} data-testid="custom-verbs-section">
              <CustomVerbs />
            </div>

            {/* Channel routing (Wave 6, →1872) */}
            <div className={cardClass} data-testid="channel-routing-section">
              <ChannelRoutingPanel />
            </div>
          </div>

          {/* Divider */}
          <div className="h-px bg-slate-200 dark:bg-slate-700/50 my-6" />

        <div className={cardClass}>
          <div className="flex items-center gap-2 mb-5">
            <h2 className="text-lg font-semibold">Sync</h2>
            <div className="group relative ml-1">
              <Icon name="help_outline" size={18} className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 cursor-help" />
              <div className="absolute bottom-full left-0 mb-2 w-72 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-3 text-xs text-slate-700 dark:text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-[60]">
                Keep your settings the same across all your devices using a private git repo you own.
              </div>
            </div>
          </div>

          {syncConfigured ? (
            <div>
              <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-emerald-50 border border-emerald-300 dark:bg-emerald-900/20 dark:border-emerald-800/30 rounded-lg">
                <Icon name="check_circle" size={16} className="text-emerald-600 dark:text-emerald-400" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-emerald-700 dark:text-emerald-300 font-medium">Sync is on</p>
                  <p className="text-xs text-slate-600 dark:text-slate-400 truncate">{syncRepoUrl}</p>
                </div>
              </div>
              {syncLastSynced && (
                <p className="text-xs text-slate-500 mb-4">
                  Last synced: {new Date(syncLastSynced).toLocaleString()}
                </p>
              )}
              {syncStatus && (
                <p className="text-sm text-slate-700 dark:text-slate-300 mb-3">{syncStatus}</p>
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
                  className="flex items-center gap-2 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-sm text-slate-600 dark:text-slate-400 hover:text-red-600 dark:hover:text-red-400 hover:border-red-800 transition-colors"
                >
                  <Icon name="link_off" size={18} />
                  Disconnect
                </button>
              </div>
            </div>
          ) : (
            <div>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
                Enter the URL of a private git repo you own. yourOS will use it to keep your settings the same across all your devices.
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
                <p className="text-sm text-red-600 dark:text-red-400 mt-2">{syncStatus}</p>
              )}
            </div>
          )}
          </div>
          </div>

          {/* ── Team Admin (gated) ──────────────────── */}
          {instanceMode === 'team' && (
          <div id="section-team-admin" className={`lg:col-span-2${activeSection !== 'section-team-admin' ? ' hidden' : ''}`}>
          <div className={cardClass}>
            <div className="flex items-center gap-2 mb-3">
              <Icon name="admin_panel_settings" size={22} className="text-indigo-600 dark:text-indigo-400" />
              <h2 className="text-lg font-semibold">Team Admin</h2>
            </div>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
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

          {/* ── Preferences Tab extra cards ────────────────────────── */}
          {/* Notifications */}
          <div id="section-notifications" className={activeSection !== 'section-preferences' ? 'hidden' : ''}>
            <div className={cardClass}>
              <h2 className="text-lg font-semibold mb-5">Notifications</h2>
              {pushSupported && (
                <div className="flex items-center justify-between py-2">
                  <div className="pr-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">Desktop notifications</p>
                    <p className="text-xs text-slate-500">Get alerts even when the browser tab is closed</p>
                  </div>
                  <Toggle checked={settingsPushEnabled} onChange={handlePushToggle} testId="push-toggle" disabled={pushToggling} />
                </div>
              )}
              <div className="mt-4 pt-4 border-t border-slate-200 dark:border-slate-800">
                <p className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-3">What triggers notifications</p>
                <div className="space-y-3">
                  {notifications.map((n, index) => (
                    <div key={n.label} className="flex items-center justify-between py-1">
                      <span className="text-sm text-slate-700 dark:text-slate-300">{n.label}</span>
                      <Toggle checked={n.enabled} onChange={() => handleNotificationToggle(index)} />
                    </div>
                  ))}
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between">
                <div>
                  <p className="text-sm text-slate-700 dark:text-slate-300">Quiet Hours</p>
                  <p className="text-xs text-slate-500">10pm – 7am</p>
                </div>
                <Toggle checked={quietHours} onChange={handleQuietHoursToggle} testId="quiet-hours-toggle" />
              </div>
            </div>
          </div>

          {/* AI behavior */}
          <div className={`${activeSection !== 'section-preferences' ? 'hidden' : ''}`}>
            <div className={cardClass}>
              <h2 className="text-lg font-semibold mb-5">AI behavior</h2>
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="pr-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">Pick the right agent automatically</p>
                    <p className="text-xs text-slate-500">When you ask a question, yourOS chooses the best built-in or saved agent for the job.</p>
                  </div>
                  <Toggle checked={autoTemplateMatching} onChange={handleAutoTemplateMatchingToggle} testId="auto-template-toggle" />
                </div>
                <div className="flex items-center justify-between pt-3 border-t border-slate-200 dark:border-slate-800">
                  <div className="pr-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">Daily briefing</p>
                    <p className="text-xs text-slate-500">Show a short summary of your day on the dashboard.</p>
                  </div>
                  <Toggle checked={briefingEnabled} onChange={handleBriefingToggle} testId="briefing-toggle" />
                </div>
                <div className="flex items-center justify-between pt-3 border-t border-slate-200 dark:border-slate-800">
                  <div className="pr-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">Chat memory</p>
                    <p className="text-xs text-slate-500">Let the AI remember what you talked about in your previous chat.</p>
                  </div>
                  <Toggle checked={chatMemoryEnabled} onChange={handleChatMemoryToggle} testId="chat-memory-toggle" />
                </div>
                <div className="flex items-center justify-between pt-3 border-t border-slate-200 dark:border-slate-800">
                  <div className="pr-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">Warn when the model claims done without evidence</p>
                    <p className="text-xs text-slate-500">Shows a pill on messages that say "done" or "fixed" without a commit hash, test output, or file reference.</p>
                  </div>
                  <Toggle checked={chatReceiptsGateEnabled} onChange={handleReceiptsGateToggle} testId="receipts-gate-toggle" />
                </div>
              </div>
            </div>
          </div>

          {/* Memory editor: content merged into AI behavior section above */}
          <div className="hidden">
            <div className={cardClass}>
              <h2 className="text-lg font-semibold mb-1">Memory</h2>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
                Things you tell me to remember show up here. You can edit or remove them anytime.
              </p>
              {memoryOverflow?.hard_cap && (
                <div data-testid="memory-hard-cap-banner" className="mb-4 rounded-lg bg-red-900/40 border border-red-700 px-4 py-3 text-sm text-red-200">
                  Your memory file is very large ({memoryOverflow.total_kb.toFixed(0)} KB). Go through it and remove anything outdated.
                </div>
              )}
              {!memoryOverflow?.hard_cap && memoryOverflow?.overflowed && (
                <div data-testid="memory-overflow-banner" className="mb-4 rounded-lg bg-amber-900/30 border border-amber-700 px-4 py-3">
                  <p className="text-sm text-amber-200 mb-2">
                    Your memory file is getting large ({memoryOverflow.reason === 'kb' ? `${memoryOverflow.kb.toFixed(0)} KB` : `${memoryOverflow.lines} lines`}). Want to organize it into topic files?
                  </p>
                  <button
                    data-testid="suggest-topics-button"
                    onClick={handleSuggestTopics}
                    disabled={suggestTopicsLoading}
                    className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-xs font-medium transition-colors"
                  >
                    {suggestTopicsLoading ? 'Thinking…' : 'Suggest topics'}
                  </button>
                  {suggestedTopics !== null && suggestedTopics.length === 0 && (
                    <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">No groupings suggested. Your memory looks well-organized already.</p>
                  )}
                  {suggestedTopics && suggestedTopics.length > 0 && (
                    <div data-testid="suggested-topics-list" className="mt-3 space-y-3">
                      {suggestedTopics.map(({ topic, bullets }) => (
                        <div key={topic}>
                          <p className="text-xs font-semibold text-amber-100 mb-1">{topic}</p>
                          <ul className="space-y-1">
                            {bullets.map((bullet) => (
                              <li key={bullet} className="flex items-center gap-2">
                                <span className="text-xs text-slate-700 dark:text-slate-300 flex-1">{bullet}</span>
                                <button
                                  data-testid={`apply-split-${topic}`}
                                  onClick={() => handleApplySplit(bullet, topic)}
                                  className="shrink-0 px-2 py-0.5 rounded bg-amber-700 hover:bg-amber-600 text-white text-xs transition-colors"
                                >
                                  Move
                                </button>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {(() => {
                const bullets = parseMemoryProvenance(memoryContent);
                if (bullets.length > 0) {
                  return (
                    <ul data-testid="memory-bullet-list" className="mb-4 space-y-2">
                      {bullets.map((bullet, i) => {
                        const label = bullet.added
                          ? `added ${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(bullet.added)}`
                          : 'edited manually';
                        return (
                          <li key={i} className="flex items-baseline gap-3">
                            <span className="text-sm text-slate-800 dark:text-slate-200 flex-1">{bullet.text}</span>
                            <span
                              data-testid={`memory-provenance-${i}`}
                              className="text-xs text-slate-500 shrink-0"
                            >
                              {label}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  );
                }
                return null;
              })()}
              <textarea
                data-testid="memory-editor"
                value={memoryContent}
                onChange={(e) => setMemoryContent(e.target.value)}
                placeholder="Things you tell me to remember will show up here."
                rows={10}
                className="w-full rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-sm text-slate-800 dark:text-slate-200 px-3 py-2 font-mono resize-y focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
              <div className="mt-3 flex items-center gap-3">
                <button
                  data-testid="memory-save-button"
                  onClick={handleSaveMemory}
                  className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors"
                >
                  Save
                </button>
                {memorySaveStatus && (
                  <span className="text-xs text-slate-600 dark:text-slate-400">{memorySaveStatus}</span>
                )}
              </div>
            </div>
          </div>

          {/* Focus */}
          <div className={`${activeSection !== 'section-preferences' ? 'hidden' : ''}`}>
            <div className={cardClass}>
              <div className="flex items-center gap-3 mb-2">
                <h2 className="text-lg font-semibold">ADHD mode</h2>
                <Toggle checked={adhdEnabled} onChange={handleAdhdToggle} testId="adhd-toggle" />
              </div>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-5">Get regular check-ins while agents work, see where you left off when you come back, and get one clear recommendation instead of a list.</p>
              <div className={`space-y-5 ${adhdEnabled ? '' : 'opacity-40 pointer-events-none'}`}>
                <div>
                  <p className="text-sm text-slate-700 dark:text-slate-300 mb-1">Check-in interval</p>
                  <p className="text-xs text-slate-500 mb-3">How often to show you what your agents are doing</p>
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
                    <span className="text-sm text-slate-700 dark:text-slate-300 w-16 text-right font-mono">{adhdCheckInSeconds}s</span>
                  </div>
                </div>
                <div className="pt-4 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between">
                  <div className="pr-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">Welcome back summary</p>
                    <p className="text-xs text-slate-500">When you return after 5+ minutes, show you where you left off</p>
                  </div>
                  <div className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" title="Always on when focus mode is active" />
                </div>
                <div className="pt-4 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between">
                  <div className="pr-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">Reduce choices</p>
                    <p className="text-xs text-slate-500">Show one recommendation instead of a list. Less deciding, more doing.</p>
                  </div>
                  <Toggle checked={adhdFocusMode} onChange={handleAdhdFocusModeToggle} testId="adhd-focus-toggle" />
                </div>
              </div>
            </div>
          </div>

          {/* ── Shortcuts (I) ────────────────────── */}
          <div id="section-shortcuts" className={`${activeSection !== 'section-preferences' ? 'hidden' : ''}` }>
            <div className={cardClass}>
              <h2 className="text-lg font-semibold mb-4">Shortcuts</h2>
              {Object.keys(customShortcuts).length === 0 ? (
                <div className="text-center py-6">
                  <p className="text-sm text-slate-500 mb-3">No custom shortcuts yet.</p>
                  <button
                    type="button"
                    onClick={() => {
                      const label = prompt('Shortcut name (e.g. "Open Tasks")');
                      if (!label) return;
                      const next = { ...customShortcuts, [label]: '' };
                      setCustomShortcuts(next);
                      setEditingShortcut(label);
                    }}
                    className="px-3 py-1.5 text-sm font-medium rounded-lg bg-slate-200 dark:bg-slate-700 hover:bg-slate-300 dark:hover:bg-slate-600 text-slate-800 dark:text-slate-200 transition-colors"
                  >
                    Add shortcut
                  </button>
                </div>
              ) : (
                <div className="space-y-1">
                  {Object.entries(customShortcuts).map(([label, keys]) => {
                    const isEditing = editingShortcut === label;
                    return (
                      <div key={label} className="flex items-center justify-between py-2">
                        <span className="text-sm text-slate-700 dark:text-slate-300">{label}</span>
                        <div className="flex items-center gap-2">
                          <button type="button" onClick={() => handleShortcutReset(label)} className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors" title="Remove">×</button>
                          {isEditing ? (
                            <kbd className="px-2.5 py-1 bg-slate-200 dark:bg-slate-700 border border-blue-500 rounded-md text-xs text-blue-700 dark:text-blue-300 font-mono min-w-[72px] text-center" onKeyDown={(e) => { e.preventDefault(); if (e.key === 'Escape') { setEditingShortcut(null); return; } const parts: string[] = []; if (e.metaKey) parts.push('⌘'); if (e.ctrlKey) parts.push('⌃'); if (e.altKey) parts.push('⌥'); if (e.shiftKey) parts.push('⇧'); const k = e.key; if (!['Meta','Control','Alt','Shift'].includes(k)) { parts.push(k.length === 1 ? k.toUpperCase() : k); } if (parts.length > 1 || (parts.length === 1 && !['⌘','⌃','⌥','⇧'].includes(parts[0]))) { handleShortcutEdit(label, parts.join('')); } }} tabIndex={0} autoFocus onBlur={() => setEditingShortcut(null)}>Press keys…</kbd>
                          ) : (
                            <kbd className="px-2.5 py-1 rounded-md text-xs font-mono cursor-pointer bg-slate-100 dark:bg-slate-800 border border-blue-500/50 text-blue-700 dark:text-blue-300" onClick={() => setEditingShortcut(label)} title="Click to edit">{keys || '…'}</kbd>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  <button type="button" onClick={() => { const label = prompt('Shortcut name'); if (!label) return; const next = { ...customShortcuts, [label]: '' }; setCustomShortcuts(next); setEditingShortcut(label); }} className="mt-2 px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-200 dark:bg-slate-700 hover:bg-slate-600 text-slate-800 dark:text-slate-200 transition-colors">Add shortcut</button>
                </div>
              )}
            </div>
          </div>

          {/* ── Take the tour (J - Help dissolved, Tour standalone) ── */}
          <div className={activeSection !== 'section-preferences' ? 'hidden' : ''}>
            <div className={cardClass}>
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold mb-1">Take the tour</h2>
                  <p className="text-xs text-slate-500">Walk through what yourOS can do, step by step.</p>
                </div>
                <button
                  data-testid="settings-tour-button"
                  onClick={() => useAppStore.getState().setShowTour(true)}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg bg-slate-200 dark:bg-slate-700 hover:bg-slate-600 text-slate-800 dark:text-slate-200 transition-colors"
                >
                  Start
                </button>
              </div>
            </div>
          </div>

          {/* ── Danger Zone ────────────────────────── */}
          <div className={activeSection !== 'section-preferences' ? 'hidden' : ''}>
            <div className={cardClass}>
              <h2 className="text-lg font-semibold mb-2 text-red-600 dark:text-red-400">Danger zone</h2>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-4">
                Permanently delete all your tasks, chats, and agent history. Your settings are kept so the app still works after.
              </p>
              {wipeDataError && (
                <p className="text-sm text-red-600 dark:text-red-400 mb-3">{wipeDataError}</p>
              )}
              <button
                onClick={handleDeleteAllData}
                data-testid="delete-all-data-button"
                className="flex items-center gap-2 px-4 py-2.5 bg-red-900/30 border border-red-800 rounded-lg text-sm text-red-600 dark:text-red-400 hover:bg-red-900/50 hover:text-red-700 dark:hover:text-red-300 transition-colors"
              >
                <Icon name="delete_forever" size={18} />
                Delete all data
              </button>
            </div>
          </div>
      </div>
      <ConfirmModal {...confirmProps} />
    </PageShell>
  );
}
