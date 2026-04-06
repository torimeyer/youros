import { useState, useEffect } from 'react'
import { useAppStore } from '../stores/app'
import Icon from './Icon'
import { api } from '../lib/api'

const STEPS = ['Welcome', 'You', 'Name', 'Theme', 'Connect', 'Dream', 'Ready'] as const

const PROVIDER_SECRET_NAME: Record<string, string> = {
  'Anthropic': 'ANTHROPIC_API_KEY',
  'Google Gemini': 'GEMINI_API_KEY',
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
  const [keySaved, setKeySaved] = useState(false)

  // Dream step state
  const [dreamText, setDreamText] = useState('')
  const [doneLooksLike, setDoneLooksLike] = useState('')
  const [dreamResult, setDreamResult] = useState<{
    goal: { title: string; description: string }
    tasks: { title: string; priority: string }[]
  } | null>(null)
  const [dreamLoading, setDreamLoading] = useState(false)
  const [dreamPhase, setDreamPhase] = useState<'ask' | 'show'>('ask')

  const next = () => setStepIndex((i) => Math.min(i + 1, STEPS.length - 1))
  const back = () => setStepIndex((i) => Math.max(i - 1, 0))

  const finish = () => {
    const settings: Record<string, unknown> = {
      os_name: osName,
      user_name: userName,
      dark_mode: darkMode,
      provider: selectedProvider,
    }
    api.patch('/settings', settings).catch(() => {})
    setOnboarded(true)
  }

  const handleProviderSelect = (name: string) => {
    setSelectedProvider(name)
    setApiKey('')
    setKeySaved(false)
    const chatModel = name === 'Google Gemini' ? 'gemini' : 'claude'
    setDefaultChatModel(chatModel)
  }

  const handleSaveKey = () => {
    const secretName = PROVIDER_SECRET_NAME[selectedProvider]
    if (secretName && apiKey) {
      api.post('/secrets', { key: secretName, value: apiKey })
        .then(() => setKeySaved(true))
        .catch(() => {})
    }
  }

  const skip = () => next()

  const handleDream = async () => {
    setDreamLoading(true)
    try {
      const result = await api.post<{
        goal: { title: string; description: string }
        tasks: { title: string; priority: string }[]
      }>('/onboarding/dream', {
        dreading: dreamText,
        done_looks_like: doneLooksLike || undefined,
      })
      setDreamResult(result)
      setDreamPhase('show')
    } catch {
      // If the API fails, just move on
      next()
    } finally {
      setDreamLoading(false)
    }
  }

  const handleDarkModeChoice = (wantDark: boolean) => {
    if (wantDark !== darkMode) {
      toggleDarkMode()
    }
  }

  // Dark-mode-aware style helpers
  const inputCls = darkMode
    ? 'bg-slate-800 border-slate-700 text-white'
    : 'bg-white border-gray-300 text-slate-900'
  const subtextCls = darkMode ? 'text-slate-400' : 'text-slate-500'
  const cardCls = darkMode
    ? 'bg-slate-900/60 border-slate-800'
    : 'bg-white border-gray-200 shadow-sm'
  const dotInactiveCls = darkMode ? 'bg-slate-700' : 'bg-gray-300'
  const navBtnCls = darkMode
    ? 'text-slate-400 hover:text-white'
    : 'text-slate-500 hover:text-slate-900'

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center transition-colors duration-300 ${
        darkMode ? 'bg-slate-950 text-white' : 'bg-gray-50 text-slate-900'
      }`}
      data-testid="onboarding-wizard"
    >
      <div className="w-full max-w-lg px-8">
        {/* Progress dots */}
        <div className="flex justify-center gap-2 mb-10" data-testid="progress-dots">
          {STEPS.map((s, i) => (
            <div
              key={s}
              className={`w-2.5 h-2.5 rounded-full transition-colors ${
                i === stepIndex ? 'bg-blue-500' : i < stepIndex ? 'bg-blue-400/50' : dotInactiveCls
              }`}
            />
          ))}
        </div>

        {/* Step content */}
        <div className="min-h-[320px]">
          {step === 'Welcome' && <WelcomeStep subtextCls={subtextCls} />}
          {step === 'You' && (
            <YouStep
              userName={userName}
              setUserName={setUserName}
              inputCls={inputCls}
              subtextCls={subtextCls}
            />
          )}
          {step === 'Name' && (
            <NameStep
              osName={osName}
              setOsName={setOsName}
              userName={userName}
              inputCls={inputCls}
              subtextCls={subtextCls}
            />
          )}
          {step === 'Theme' && (
            <ThemeStep
              darkMode={darkMode}
              onChoose={handleDarkModeChoice}
              subtextCls={subtextCls}
            />
          )}
          {step === 'Connect' && (
            <ConnectStep
              selectedProvider={selectedProvider}
              onSelectProvider={handleProviderSelect}
              apiKey={apiKey}
              onApiKeyChange={setApiKey}
              onSaveKey={handleSaveKey}
              keySaved={keySaved}
              darkMode={darkMode}
              inputCls={inputCls}
              subtextCls={subtextCls}
            />
          )}
          {step === 'Dream' && (
            <DreamStep
              osName={osName}
              dreamText={dreamText}
              setDreamText={setDreamText}
              doneLooksLike={doneLooksLike}
              setDoneLooksLike={setDoneLooksLike}
              dreamResult={dreamResult}
              dreamLoading={dreamLoading}
              dreamPhase={dreamPhase}
              onSubmit={handleDream}
              darkMode={darkMode}
              inputCls={inputCls}
              subtextCls={subtextCls}
              cardCls={cardCls}
            />
          )}
          {step === 'Ready' && (
            <ReadyStep
              userName={userName}
              osName={osName}
              darkMode={darkMode}
              provider={selectedProvider}
              subtextCls={subtextCls}
              cardCls={cardCls}
            />
          )}
        </div>

        {/* Navigation buttons */}
        <div className="flex items-center justify-between mt-8">
          <div>
            {stepIndex > 0 && step !== 'Ready' && (
              <button
                onClick={back}
                className={`px-4 py-2 text-sm transition-colors ${navBtnCls}`}
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
                className={`px-4 py-2 text-sm transition-colors ${navBtnCls}`}
                data-testid="skip-button"
              >
                Skip
              </button>
            )}

            {step === 'Ready' ? (
              <button
                onClick={finish}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
                data-testid="finish-button"
              >
                Get started
              </button>
            ) : (
              <button
                onClick={next}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
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

function WelcomeStep({ subtextCls }: { subtextCls: string }) {
  return (
    <div className="text-center" data-testid="step-welcome">
      <div className="mb-6">
        <Icon name="rocket_launch" size={48} className="text-blue-400" />
      </div>
      <h1 className="text-3xl font-bold mb-4">Welcome!</h1>
      <p className={`${subtextCls} text-lg leading-relaxed`}>
        Let's set up your personal OS. This will only take a minute, and you can
        change everything later in settings.
      </p>
    </div>
  )
}

function YouStep({
  userName,
  setUserName,
  inputCls,
  subtextCls,
}: {
  userName: string
  setUserName: (name: string) => void
  inputCls: string
  subtextCls: string
}) {
  return (
    <div data-testid="step-you">
      <h2 className="text-2xl font-bold mb-2">What's your name?</h2>
      <p className={`${subtextCls} mb-6`}>
        Your AI assistant will use this to personalize your experience.
      </p>
      <input
        type="text"
        value={userName}
        onChange={(e) => setUserName(e.target.value)}
        placeholder="e.g. Madison"
        className={`w-full border rounded-lg px-4 py-3 text-lg focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
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
  inputCls,
  subtextCls,
}: {
  osName: string
  setOsName: (name: string) => void
  userName: string
  inputCls: string
  subtextCls: string
}) {
  const example = userName ? `${userName}OS` : 'MyOS'
  return (
    <div data-testid="step-name">
      <h2 className="text-2xl font-bold mb-2">Name your OS</h2>
      <p className={`${subtextCls} mb-6`}>
        Give your personal OS a name. This shows up in the sidebar and title bar.
      </p>
      <input
        type="text"
        value={osName}
        onChange={(e) => setOsName(e.target.value)}
        placeholder={`e.g. ${example}`}
        className={`w-full border rounded-lg px-4 py-3 text-lg focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
        data-testid="os-name-input"
        autoFocus
      />
    </div>
  )
}

function ThemeStep({
  darkMode,
  onChoose,
  subtextCls,
}: {
  darkMode: boolean
  onChoose: (wantDark: boolean) => void
  subtextCls: string
}) {
  return (
    <div data-testid="step-theme">
      <h2 className="text-2xl font-bold mb-2">Pick your theme</h2>
      <p className={`${subtextCls} mb-6`}>
        Choose how you want things to look. You can change this anytime.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <button
          onClick={() => onChoose(false)}
          className={`p-4 rounded-xl border-2 transition-all ${
            !darkMode
              ? 'border-blue-500 bg-blue-500/10'
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
              ? 'border-blue-500 bg-blue-500/10'
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

function ConnectStep({
  selectedProvider,
  onSelectProvider,
  apiKey,
  onApiKeyChange,
  onSaveKey,
  keySaved,
  darkMode,
  inputCls,
  subtextCls,
}: {
  selectedProvider: string
  onSelectProvider: (name: string) => void
  apiKey: string
  onApiKeyChange: (key: string) => void
  onSaveKey: () => void
  keySaved: boolean
  darkMode: boolean
  inputCls: string
  subtextCls: string
}) {
  const [googleOAuthAvailable, setGoogleOAuthAvailable] = useState(false)

  useEffect(() => {
    api.get<{ google_oauth_available?: boolean }>('/secrets/key-status')
      .then((data) => setGoogleOAuthAvailable(data.google_oauth_available ?? false))
      .catch(() => {})
  }, [])

  const providers = [
    { name: 'Anthropic', label: 'Anthropic (Claude)' },
    { name: 'Google Gemini', label: 'Google (Gemini)' },
  ]

  return (
    <div data-testid="step-connect">
      <h2 className="text-2xl font-bold mb-2">Connect your AI</h2>
      <p className={`${subtextCls} mb-6`}>
        Pick a provider and sign in or paste an API key. You can change this
        anytime in Settings.
      </p>

      {/* Provider selector */}
      <div className="grid grid-cols-2 gap-3 mb-5">
        {providers.map((p) => {
          const isSelected = selectedProvider === p.name
          return (
            <button
              key={p.name}
              onClick={() => onSelectProvider(p.name)}
              className={`p-3 rounded-lg border-2 text-center transition-colors ${
                isSelected
                  ? 'border-blue-500 bg-blue-500/10'
                  : darkMode
                    ? 'border-slate-700 bg-slate-800/50 hover:border-slate-600'
                    : 'border-gray-200 bg-white hover:border-gray-300'
              }`}
              data-testid={`provider-${p.name}`}
            >
              <p className="text-sm font-medium">{p.label}</p>
              {isSelected && (
                <span className="text-xs text-blue-400 font-medium">Selected</span>
              )}
            </button>
          )
        })}
      </div>

      {/* Connect option */}
      {selectedProvider === 'Google Gemini' ? (
        googleOAuthAvailable ? (
          <button
            onClick={() => window.open('/api/auth/google', '_self')}
            className={`w-full mb-3 px-4 py-2.5 border rounded-lg text-sm font-medium transition-colors flex items-center gap-2 ${
              darkMode
                ? 'bg-slate-800 border-slate-700 text-white hover:border-blue-500'
                : 'bg-white border-gray-300 text-slate-900 hover:border-blue-500'
            }`}
            data-testid="connect-google"
          >
            <Icon name="login" size={18} />
            Sign in with Google
          </button>
        ) : (
          <p className={`text-sm mb-3 ${subtextCls}`}>
            Google sign-in is not set up yet. Paste a Gemini API key below, or switch to Anthropic.
          </p>
        )
      ) : (
        <button
          onClick={() => window.open('https://console.anthropic.com/settings/keys', '_blank')}
          className={`w-full mb-3 px-4 py-2.5 border rounded-lg text-sm font-medium transition-colors flex items-center gap-2 ${
            darkMode
              ? 'bg-slate-800 border-slate-700 text-white hover:border-blue-500'
              : 'bg-white border-gray-300 text-slate-900 hover:border-blue-500'
          }`}
          data-testid="connect-anthropic"
        >
          <Icon name="open_in_new" size={18} />
          Sign in to Anthropic to get a key
        </button>
      )}

      {/* API key paste */}
      <div className="flex gap-2">
        <input
          type="password"
          value={apiKey}
          onChange={(e) => onApiKeyChange(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSaveKey()}
          placeholder={selectedProvider === 'Anthropic' ? 'Paste API key (sk-ant-xxxx...)' : 'Paste API key (AIzaSy...)'}
          className={`flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
          data-testid="api-key-input"
        />
        <button
          onClick={onSaveKey}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors whitespace-nowrap"
          data-testid="save-key-button"
        >
          {keySaved ? 'Saved!' : 'Save'}
        </button>
      </div>

      <p className={`text-xs mt-3 ${subtextCls}`}>
        You can skip this step and connect later in Settings.
      </p>
    </div>
  )
}

function DreamStep({
  osName,
  dreamText,
  setDreamText,
  doneLooksLike,
  setDoneLooksLike,
  dreamResult,
  dreamLoading,
  dreamPhase,
  onSubmit,
  darkMode,
  inputCls,
  subtextCls,
  cardCls,
}: {
  osName: string
  dreamText: string
  setDreamText: (v: string) => void
  doneLooksLike: string
  setDoneLooksLike: (v: string) => void
  dreamResult: {
    goal: { title: string; description: string }
    tasks: { title: string; priority: string }[]
  } | null
  dreamLoading: boolean
  dreamPhase: 'ask' | 'show'
  onSubmit: () => void
  darkMode: boolean
  inputCls: string
  subtextCls: string
  cardCls: string
}) {
  const [visibleCount, setVisibleCount] = useState(0)

  useEffect(() => {
    if (dreamPhase !== 'show' || !dreamResult) return
    setVisibleCount(0)
    const total = dreamResult.tasks.length
    let current = 0
    const timer = setInterval(() => {
      current++
      setVisibleCount(current)
      if (current >= total) clearInterval(timer)
    }, 200)
    return () => clearInterval(timer)
  }, [dreamPhase, dreamResult])

  if (dreamPhase === 'show' && dreamResult) {
    const priorityColor = (p: string) => {
      switch (p) {
        case 'P1': return 'bg-red-500/20 text-red-400'
        case 'P2': return 'bg-yellow-500/20 text-yellow-400'
        case 'P3': return 'bg-blue-500/20 text-blue-400'
        default: return 'bg-slate-500/20 text-slate-400'
      }
    }

    return (
      <div data-testid="step-dream">
        <div data-testid="dream-phase-show">
          <h2 className="text-2xl font-bold mb-2" data-testid="dream-goal-title">
            {dreamResult.goal.title}
          </h2>
          <p className={`${subtextCls} mb-6`} data-testid="dream-goal-description">
            {dreamResult.goal.description}
          </p>

          <div className={`border rounded-xl p-4 space-y-2 ${cardCls}`} data-testid="dream-tasks">
            {dreamResult.tasks.map((task, i) => (
              <div
                key={i}
                className={`flex items-center gap-3 py-2 transition-all duration-300 ${
                  i < visibleCount ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'
                }`}
                data-testid="dream-task"
              >
                <div
                  className={`w-4 h-4 rounded border-2 flex-shrink-0 ${
                    darkMode ? 'border-slate-600' : 'border-gray-300'
                  }`}
                />
                <span className="text-sm flex-1">{task.title}</span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full font-medium ${priorityColor(task.priority)}`}
                >
                  {task.priority}
                </span>
              </div>
            ))}
          </div>

          <p className={`text-sm mt-4 ${subtextCls}`}>
            These are waiting for you on your dashboard.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div data-testid="step-dream">
      <div data-testid="dream-phase-ask">
        <h2 className="text-2xl font-bold mb-2">Let's get something off your plate</h2>
        <p className={`${subtextCls} mb-6`}>
          Tell {osName} something you've been putting off. We'll turn it into a plan.
        </p>

        <textarea
          value={dreamText}
          onChange={(e) => setDreamText(e.target.value)}
          placeholder="e.g. I need to do my taxes but I have no idea where to start"
          className={`w-full border rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-blue-500 transition-colors resize-none ${inputCls}`}
          rows={3}
          data-testid="dream-text-input"
          autoFocus
        />

        <label className={`block text-sm mt-4 mb-2 ${subtextCls}`}>
          What would done look like? (optional)
        </label>
        <input
          type="text"
          value={doneLooksLike}
          onChange={(e) => setDoneLooksLike(e.target.value)}
          placeholder="e.g. Taxes filed, no penalties"
          className={`w-full border rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
          data-testid="dream-done-input"
        />

        <button
          onClick={onSubmit}
          disabled={!dreamText.trim() || dreamLoading}
          className={`mt-6 w-full px-6 py-2.5 rounded-lg text-sm font-medium text-white transition-colors ${
            !dreamText.trim() || dreamLoading
              ? 'bg-blue-600/50 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-500'
          }`}
          data-testid="dream-submit"
        >
          {dreamLoading ? 'Thinking...' : 'Make it happen'}
        </button>
      </div>
    </div>
  )
}

function ReadyStep({
  userName,
  osName,
  darkMode,
  provider,
  subtextCls,
  cardCls,
}: {
  userName: string
  osName: string
  darkMode: boolean
  provider: string
  subtextCls: string
  cardCls: string
}) {
  return (
    <div className="text-center" data-testid="step-ready">
      <div className="mb-6">
        <Icon name="check_circle" size={48} className="text-green-400" />
      </div>
      <h2 className="text-2xl font-bold mb-4">You're all set!</h2>
      <p className={`${subtextCls} mb-8`}>
        Here's a summary of what you chose. You can change any of this later in
        Settings.
      </p>

      <div className={`border rounded-xl p-6 text-left space-y-4 ${cardCls}`}>
        {userName && (
          <div className="flex items-center justify-between">
            <span className={`text-sm ${subtextCls}`}>Your Name</span>
            <span className="text-sm font-medium">{userName}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <span className={`text-sm ${subtextCls}`}>OS Name</span>
          <span className="text-sm font-medium" data-testid="summary-os-name">
            {osName || 'myOS'}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className={`text-sm ${subtextCls}`}>Theme</span>
          <span className="text-sm font-medium" data-testid="summary-theme">
            {darkMode ? 'Dark' : 'Light'}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className={`text-sm ${subtextCls}`}>AI Provider</span>
          <span className="text-sm font-medium" data-testid="summary-provider">
            {provider}
          </span>
        </div>
      </div>
    </div>
  )
}
