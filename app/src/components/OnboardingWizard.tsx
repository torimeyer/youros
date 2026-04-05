import { useState } from 'react'
import { useAppStore, PROVIDER_TO_MODEL } from '../stores/app'
import Icon from './Icon'
import { api } from '../lib/api'

const STEPS = ['Welcome', 'You', 'Name', 'Theme', 'AI', 'Ready'] as const

const PROVIDER_KEY_FIELD: Record<string, string> = {
  'Anthropic': 'anthropic_api_key',
  'Google Gemini': 'gemini_api_key',
  'OpenAI': 'openai_api_key',
}

export default function OnboardingWizard() {
  const [stepIndex, setStepIndex] = useState(0)
  const step = STEPS[stepIndex]

  // Store bindings
  const osName = useAppStore((s) => s.osName)
  const setOsName = useAppStore((s) => s.setOsName)
  const darkMode = useAppStore((s) => s.darkMode)
  const toggleDarkMode = useAppStore((s) => s.toggleDarkMode)
  const setOnboarded = useAppStore((s) => s.setOnboarded)
  const setDefaultChatModel = useAppStore((s) => s.setDefaultChatModel)

  // Local state
  const [userName, setUserName] = useState('')
  const [selectedProvider, setSelectedProvider] = useState('Anthropic')
  const [apiKey, setApiKey] = useState('')
  const [apiKeyVisible, setApiKeyVisible] = useState(false)

  const providers = [
    { name: 'Anthropic', label: 'Anthropic (Claude)' },
    { name: 'Google Gemini', label: 'Google (Gemini)' },
    { name: 'OpenAI', label: 'OpenAI (GPT)' },
  ]

  const next = () => setStepIndex((i) => Math.min(i + 1, STEPS.length - 1))
  const back = () => setStepIndex((i) => Math.max(i - 1, 0))

  const finish = () => {
    // Persist settings to backend (best-effort)
    const keyField = PROVIDER_KEY_FIELD[selectedProvider] || 'anthropic_api_key'
    api.patch('/settings', {
      os_name: osName,
      user_name: userName,
      dark_mode: darkMode,
      provider: selectedProvider,
      [keyField]: apiKey || undefined,
    }).catch(() => {})
    setOnboarded(true)
  }

  const skip = () => next()

  const handleDarkModeChoice = (wantDark: boolean) => {
    if (wantDark !== darkMode) {
      toggleDarkMode()
    }
  }

  const handleProviderSelect = (name: string) => {
    setSelectedProvider(name)
    const chatModel = PROVIDER_TO_MODEL[name] ?? 'claude'
    setDefaultChatModel(chatModel)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950 text-white"
      data-testid="onboarding-wizard"
    >
      <div className="w-full max-w-lg px-8">
        {/* Progress dots */}
        <div className="flex justify-center gap-2 mb-10" data-testid="progress-dots">
          {STEPS.map((s, i) => (
            <div
              key={s}
              className={`w-2.5 h-2.5 rounded-full transition-colors ${
                i === stepIndex ? 'bg-blue-500' : i < stepIndex ? 'bg-blue-400/50' : 'bg-slate-700'
              }`}
            />
          ))}
        </div>

        {/* Step content */}
        <div className="min-h-[320px]">
          {step === 'Welcome' && <WelcomeStep />}
          {step === 'You' && (
            <YouStep userName={userName} setUserName={setUserName} />
          )}
          {step === 'Name' && (
            <NameStep osName={osName} setOsName={setOsName} userName={userName} />
          )}
          {step === 'Theme' && (
            <ThemeStep darkMode={darkMode} onChoose={handleDarkModeChoice} />
          )}
          {step === 'AI' && (
            <AIStep
              providers={providers}
              selectedProvider={selectedProvider}
              onSelectProvider={handleProviderSelect}
              apiKey={apiKey}
              onApiKeyChange={setApiKey}
              apiKeyVisible={apiKeyVisible}
              onToggleApiKeyVisible={() => setApiKeyVisible((v) => !v)}
            />
          )}
          {step === 'Ready' && (
            <ReadyStep
              userName={userName}
              osName={osName}
              darkMode={darkMode}
              provider={selectedProvider}
              hasApiKey={apiKey.length > 0}
            />
          )}
        </div>

        {/* Navigation buttons */}
        <div className="flex items-center justify-between mt-8">
          <div>
            {stepIndex > 0 && step !== 'Ready' && (
              <button
                onClick={back}
                className="px-4 py-2 text-sm text-slate-400 hover:text-white transition-colors"
                data-testid="back-button"
              >
                Back
              </button>
            )}
          </div>

          <div className="flex items-center gap-3">
            {step !== 'Welcome' && step !== 'Ready' && (
              <button
                onClick={skip}
                className="px-4 py-2 text-sm text-slate-500 hover:text-slate-300 transition-colors"
                data-testid="skip-button"
              >
                Skip
              </button>
            )}

            {step === 'Ready' ? (
              <button
                onClick={finish}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
                data-testid="finish-button"
              >
                Get started
              </button>
            ) : (
              <button
                onClick={next}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
                data-testid="next-button"
              >
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ---- Step Components ---- */

function WelcomeStep() {
  return (
    <div className="text-center" data-testid="step-welcome">
      <div className="mb-6">
        <Icon name="rocket_launch" size={48} className="text-blue-400" />
      </div>
      <h1 className="text-3xl font-bold mb-4">Welcome!</h1>
      <p className="text-slate-400 text-lg leading-relaxed">
        Let's set up your personal OS. This will only take a minute, and you can
        change everything later in settings.
      </p>
    </div>
  )
}

function YouStep({
  userName,
  setUserName,
}: {
  userName: string
  setUserName: (name: string) => void
}) {
  return (
    <div data-testid="step-you">
      <h2 className="text-2xl font-bold mb-2">What's your name?</h2>
      <p className="text-slate-400 mb-6">
        Your AI assistant will use this to personalize your experience.
      </p>
      <input
        type="text"
        value={userName}
        onChange={(e) => setUserName(e.target.value)}
        placeholder="e.g. Madison"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-3 text-lg text-white focus:outline-none focus:border-blue-500 transition-colors"
        data-testid="user-name-input"
        autoFocus
      />
    </div>
  )
}

function NameStep({
  osName,
  setOsName,
  userName,
}: {
  osName: string
  setOsName: (name: string) => void
  userName: string
}) {
  const example = userName ? `${userName}OS` : 'MyOS'
  return (
    <div data-testid="step-name">
      <h2 className="text-2xl font-bold mb-2">Name your OS</h2>
      <p className="text-slate-400 mb-6">
        Give your personal OS a name. This shows up in the sidebar and title bar.
      </p>
      <input
        type="text"
        value={osName}
        onChange={(e) => setOsName(e.target.value)}
        placeholder={`e.g. ${example}`}
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-3 text-lg text-white focus:outline-none focus:border-blue-500 transition-colors"
        data-testid="os-name-input"
        autoFocus
      />
    </div>
  )
}

function ThemeStep({
  darkMode,
  onChoose,
}: {
  darkMode: boolean
  onChoose: (wantDark: boolean) => void
}) {
  return (
    <div data-testid="step-theme">
      <h2 className="text-2xl font-bold mb-2">Pick your theme</h2>
      <p className="text-slate-400 mb-6">
        Choose how you want things to look. You can change this anytime.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <button
          onClick={() => onChoose(false)}
          className={`p-4 rounded-xl border-2 transition-all ${
            !darkMode
              ? 'border-blue-500 bg-slate-800/60'
              : 'border-slate-700 bg-slate-800/30 hover:border-slate-600'
          }`}
          data-testid="theme-light"
        >
          {/* Mini dashboard preview - light */}
          <div className="w-full rounded-lg bg-gray-100 p-3 mb-3 overflow-hidden">
            <div className="flex gap-2">
              <div className="w-10 bg-white rounded p-1.5 flex flex-col gap-1">
                <div className="w-full h-1 bg-gray-300 rounded" />
                <div className="w-full h-1 bg-gray-300 rounded" />
                <div className="w-full h-1 bg-gray-300 rounded" />
                <div className="w-full h-1 bg-gray-300 rounded" />
              </div>
              <div className="flex-1 flex flex-col gap-1.5">
                <div className="h-2 bg-gray-400 rounded w-3/4" />
                <div className="flex gap-1.5">
                  <div className="flex-1 h-8 bg-white rounded border border-gray-200" />
                  <div className="flex-1 h-8 bg-white rounded border border-gray-200" />
                </div>
                <div className="flex gap-1.5">
                  <div className="flex-1 h-6 bg-white rounded border border-gray-200" />
                  <div className="flex-1 h-6 bg-white rounded border border-gray-200" />
                  <div className="flex-1 h-6 bg-white rounded border border-gray-200" />
                </div>
              </div>
            </div>
          </div>
          <p className="text-sm font-medium">Light</p>
        </button>
        <button
          onClick={() => onChoose(true)}
          className={`p-4 rounded-xl border-2 transition-all ${
            darkMode
              ? 'border-blue-500 bg-slate-800/60'
              : 'border-slate-700 bg-slate-800/30 hover:border-slate-600'
          }`}
          data-testid="theme-dark"
        >
          {/* Mini dashboard preview - dark */}
          <div className="w-full rounded-lg bg-slate-950 p-3 mb-3 overflow-hidden">
            <div className="flex gap-2">
              <div className="w-10 bg-slate-900 rounded p-1.5 flex flex-col gap-1">
                <div className="w-full h-1 bg-slate-700 rounded" />
                <div className="w-full h-1 bg-slate-700 rounded" />
                <div className="w-full h-1 bg-slate-700 rounded" />
                <div className="w-full h-1 bg-slate-700 rounded" />
              </div>
              <div className="flex-1 flex flex-col gap-1.5">
                <div className="h-2 bg-slate-500 rounded w-3/4" />
                <div className="flex gap-1.5">
                  <div className="flex-1 h-8 bg-slate-800 rounded border border-slate-700" />
                  <div className="flex-1 h-8 bg-slate-800 rounded border border-slate-700" />
                </div>
                <div className="flex gap-1.5">
                  <div className="flex-1 h-6 bg-slate-800 rounded border border-slate-700" />
                  <div className="flex-1 h-6 bg-slate-800 rounded border border-slate-700" />
                  <div className="flex-1 h-6 bg-slate-800 rounded border border-slate-700" />
                </div>
              </div>
            </div>
          </div>
          <p className="text-sm font-medium">Dark</p>
        </button>
      </div>
    </div>
  )
}

function AIStep({
  providers,
  selectedProvider,
  onSelectProvider,
  apiKey,
  onApiKeyChange,
  apiKeyVisible,
  onToggleApiKeyVisible,
}: {
  providers: { name: string; label: string }[]
  selectedProvider: string
  onSelectProvider: (name: string) => void
  apiKey: string
  onApiKeyChange: (key: string) => void
  apiKeyVisible: boolean
  onToggleApiKeyVisible: () => void
}) {
  return (
    <div data-testid="step-ai">
      <h2 className="text-2xl font-bold mb-2">Connect your AI</h2>
      <p className="text-slate-400 mb-6">
        Pick an AI provider and paste your API key. You can explore everything
        without one. Chat and agents need a key to work.
      </p>

      <div className="space-y-3 mb-6">
        {providers.map((p) => (
          <button
            key={p.name}
            onClick={() => onSelectProvider(p.name)}
            className={`w-full p-4 rounded-lg border text-left transition-colors ${
              selectedProvider === p.name
                ? 'border-blue-500 bg-blue-500/10'
                : 'border-slate-700 bg-slate-800/50 hover:border-slate-600'
            }`}
            data-testid={`provider-${p.name}`}
          >
            <p className="text-sm font-medium">{p.label}</p>
          </button>
        ))}
      </div>

      <div>
        <label className="text-sm text-slate-400 mb-2 block">
          API Key (optional)
        </label>
        <div className="relative">
          <input
            type={apiKeyVisible ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            placeholder="Paste your API key here"
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-3 pr-10 text-sm text-white focus:outline-none focus:border-blue-500 transition-colors"
            data-testid="api-key-input"
          />
          <button
            onClick={onToggleApiKeyVisible}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white transition-colors"
            data-testid="toggle-api-key-visibility"
          >
            <Icon
              name={apiKeyVisible ? 'visibility_off' : 'visibility'}
              size={18}
            />
          </button>
        </div>
      </div>
    </div>
  )
}

function ReadyStep({
  userName,
  osName,
  darkMode,
  provider,
  hasApiKey,
}: {
  userName: string
  osName: string
  darkMode: boolean
  provider: string
  hasApiKey: boolean
}) {
  return (
    <div className="text-center" data-testid="step-ready">
      <div className="mb-6">
        <Icon name="check_circle" size={48} className="text-green-400" />
      </div>
      <h2 className="text-2xl font-bold mb-4">You're all set!</h2>
      <p className="text-slate-400 mb-8">
        Here's a summary of what you chose. You can change any of this later in
        Settings.
      </p>

      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-6 text-left space-y-4">
        {userName && (
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-400">Your Name</span>
            <span className="text-sm font-medium">{userName}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-400">OS Name</span>
          <span className="text-sm font-medium" data-testid="summary-os-name">
            {osName || 'YourOS'}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-400">Theme</span>
          <span className="text-sm font-medium" data-testid="summary-theme">
            {darkMode ? 'Dark' : 'Light'}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-400">AI Provider</span>
          <span className="text-sm font-medium" data-testid="summary-provider">
            {provider}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-400">API Key</span>
          <span className="text-sm font-medium" data-testid="summary-api-key">
            {hasApiKey ? 'Saved' : 'Not set (add later in Settings)'}
          </span>
        </div>
      </div>
    </div>
  )
}
