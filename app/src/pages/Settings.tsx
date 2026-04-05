import { useState, useEffect, useRef } from 'react';
import { useAppStore, PROVIDER_TO_MODEL, type AccentColor } from '../stores/app';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { api } from '../lib/api';

interface SettingsData {
  dark_mode?: boolean;
  accent_color?: string;
  os_name?: string;
  features?: Record<string, boolean>;
  provider?: string;
  anthropic_api_key?: string;
  model?: string;
  notifications?: Record<string, boolean>;
  quiet_hours?: boolean;
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
  } = useAppStore();

  const [selectedProvider, setSelectedProvider] = useState('Anthropic');
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({ Anthropic: '', 'Google Gemini': '', OpenAI: '' });
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [selectedModel, setSelectedModel] = useState('claude-opus-4-20250514');
  const [notifications, setNotifications] = useState([
    { label: 'Agent Complete', enabled: true },
    { label: 'Agent Needs Input', enabled: true },
    { label: 'Agent Failed', enabled: true },
    { label: 'Approval Needed', enabled: true },
  ]);
  const [quietHours, setQuietHours] = useState(true);
  const [showAllKeys, setShowAllKeys] = useState(false);
  const [keySaveStatus, setKeySaveStatus] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await api.get<SettingsData>('/settings');
        if (data.accent_color) setAccentColor(data.accent_color as AccentColor);
        if (data.os_name) setOsName(data.os_name);
        if (data.dark_mode !== undefined && data.dark_mode !== darkMode) {
          toggleDarkMode();
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
          const chatModel = PROVIDER_TO_MODEL[data.provider] ?? 'claude';
          setDefaultChatModel(chatModel);
        }
        setApiKeys(prev => ({
          ...prev,
          Anthropic: data.anthropic_api_key || '',
          'Google Gemini': (data as any).gemini_api_key || '',
          OpenAI: (data as any).openai_api_key || '',
        }));
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
      } catch {
        // API not available, use defaults
      }
    };
    fetchSettings();
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
    { label: 'Go to Home', keys: '\u23180' },
    { label: 'Go to Tasks', keys: '\u23181' },
    { label: 'Go to Agents', keys: '\u23182' },
    { label: 'Go to Projects', keys: '\u23183' },
    { label: 'Go to Files', keys: '\u23184' },
    { label: 'Go to Transcripts', keys: '\u23185' },
    { label: 'Go to Settings', keys: '\u2318,' },
    { label: 'New Note', keys: '\u2318\u21e7N' },
  ];

  const providers = [
    { name: 'Anthropic', model: 'Claude' },
    { name: 'Google Gemini', model: 'Gemini' },
    { name: 'OpenAI', model: 'GPT' },
  ];

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
    const chatModel = PROVIDER_TO_MODEL[name] ?? 'claude';
    setDefaultChatModel(chatModel);
    api.patch('/settings', { provider: name }).catch(() => {});
  };

  const PROVIDER_KEY_FIELD: Record<string, string> = {
    Anthropic: 'anthropic_api_key',
    'Google Gemini': 'gemini_api_key',
    OpenAI: 'openai_api_key',
  };

  const handleApiKeySave = () => {
    const field = PROVIDER_KEY_FIELD[selectedProvider];
    if (field) {
      api.patch('/settings', { [field]: apiKeys[selectedProvider] })
        .then(() => {
          setKeySaveStatus('Saved!');
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
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'youros-settings.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      // handle error silently
    }
  };

  const cardClass =
    'bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors';

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Settings" />

      <div className="pt-16 p-8 space-y-6">
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
            <div>
              <label className="text-sm text-slate-400 mb-2 block">OS Identifier</label>
              <input
                type="text"
                value={osName}
                onChange={(e) => setOsName(e.target.value)}
                onBlur={handleOsNameBlur}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
              />
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
        </div>

        {/* Row 3: AI Provider + Notifications */}
        <div className="grid grid-cols-2 gap-6">
          {/* AI Provider */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-5">AI Provider</h2>

            {/* Provider Cards */}
            <div className="grid grid-cols-3 gap-3 mb-5">
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

            {/* API Key */}
            <div className="mb-5">
              <label className="text-sm text-slate-400 mb-2 block">{selectedProvider} API Key</label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type={apiKeyVisible ? 'text' : 'password'}
                    value={apiKeys[selectedProvider] || ''}
                    onChange={(e) => setApiKeys(prev => ({ ...prev, [selectedProvider]: e.target.value }))}
                    onKeyDown={(e) => e.key === 'Enter' && handleApiKeySave()}
                    placeholder={selectedProvider === 'Anthropic' ? 'sk-ant-xxxx...' : selectedProvider === 'Google Gemini' ? 'AIzaSy...' : 'sk-xxxx...'}
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
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                >
                  {keySaveStatus || 'Save Key'}
                </button>
              </div>
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
          </div>
        </div>

        {/* Row 4: Data Management */}
        <div className={cardClass}>
          <h2 className="text-lg font-semibold mb-2">Data Management</h2>
          <p className="text-sm text-slate-500 mb-4">
            Export your settings to a file for backup, or import a previously exported file to restore them.
          </p>
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
          </div>
        </div>
      </div>
    </div>
  );
}
