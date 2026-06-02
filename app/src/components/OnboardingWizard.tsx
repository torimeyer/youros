import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { useAppStore, TEAM_MODE_VISIBLE } from '../stores/app'
import Icon from './Icon'
import { api, ApiError } from '../lib/api'
import { reportError } from '../lib/reportError'
import { AGENT_MARKETPLACE, PERSONA_ICONS, type MarketplaceCategory } from '../data/agentMarketplace'
import { GoogleAccountSetupCard } from './GoogleWorkspaceSetupCard'
import {
  OrgNameStep,
  AdminEmailStep,
  InviteTeamStep,
  GuardrailsStep,
  TeamReadyStep,
  finishTeamOnboarding,
  type TeamOnboardingData,
} from './TeamOnboardingSteps'

const PERSONAL_STEPS = ['Fork', 'Welcome', 'You', 'Name', 'Profile', 'Theme', 'Tracking', 'Connect', 'Ready'] as const
const PERSONAL_STEPS_NO_FORK = ['Welcome', 'You', 'Name', 'Profile', 'Theme', 'Tracking', 'Connect', 'Ready'] as const

const TEAM_STEPS = ['Fork', 'OrgName', 'AdminEmail', 'InviteTeam', 'Guardrails', 'Theme', 'Connect', 'TeamReady'] as const
type OnboardingMode = 'undecided' | 'personal' | 'team'

const PROVIDER_SECRET_NAME: Record<string, string> = {
  'Anthropic': 'ANTHROPIC_API_KEY',
  'Google Gemini': 'GEMINI_API_KEY',
}

export default function OnboardingWizard() {
  const [stepIndex, setStepIndex] = useState(0)
  const [onboardingMode, setOnboardingMode] = useState<OnboardingMode>(TEAM_MODE_VISIBLE ? 'undecided' : 'personal')
  const STEPS = onboardingMode === 'team' ? TEAM_STEPS : (TEAM_MODE_VISIBLE ? PERSONAL_STEPS : PERSONAL_STEPS_NO_FORK)
  const step = STEPS[stepIndex]

  // Store bindings
  const osName = useAppStore((s) => s.osName)
  const setOsName = useAppStore((s) => s.setOsName)
  const instanceName = useAppStore((s) => s.instanceName)
  const darkMode = useAppStore((s) => s.darkMode)
  const toggleDarkMode = useAppStore((s) => s.toggleDarkMode)
  const setOnboarded = useAppStore((s) => s.setOnboarded)
  const setDefaultChatModel = useAppStore((s) => s.setDefaultChatModel)
  const setCustomAgentTemplates = useAppStore((s) => s.setCustomAgentTemplates)
  const setInstanceMode = useAppStore((s) => s.setInstanceMode)
  const setOrgName = useAppStore((s) => s.setOrgName)
  const setAgentsLastViewed = useAppStore((s) => s.setAgentsLastViewed)

  // Local state
  const [userName, setUserName] = useState('')
  const [selectedProvider, setSelectedProvider] = useState('Anthropic')
  const [apiKey, setApiKey] = useState('')
  const [keySaved, setKeySaved] = useState(false)
  const [detectedProvider, setDetectedProvider] = useState<string | null>(null)
  const [detectLoading, setDetectLoading] = useState(true)
  const [detectError, setDetectError] = useState(false)
  const [selectedPersonaId, setSelectedPersonaId] = useState<string | null>(null)
  const [otherSelected, setOtherSelected] = useState(false)
  const [oauthError, setOauthError] = useState<string | null>(null)
  const [trackingOption, setTrackingOption] = useState<'everywhere' | 'repo' | 'myos-only' | null>(null)
  const [trackingRepoPath, setTrackingRepoPath] = useState('')
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  const [mounted, setMounted] = useState(false)

  // Reset osName to empty on wizard mount so a new user always starts with
  // an empty "Name your OS" field. This prevents stale values from
  // localStorage or prior test runs (e.g. e2e_browser.sh writes
  // "e2e-browser-os" via the Settings page and e2e_smoke.sh writes
  // "e2e-test-os" via PATCH) from pre-filling the field. The wizard only
  // mounts when onboarded=false, so the user has not confirmed a name yet
  // and nothing of theirs is being clobbered.
  useEffect(() => {
    if (osName !== '') {
      setOsName('')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    setDetectLoading(true)
    setDetectError(false)
    api.get<{ claude_code?: boolean; anthropic_key?: boolean; gemini_key?: boolean; vertex_ai?: boolean; bedrock?: boolean }>('/providers/detect')
      .then((data) => {
        if (data.claude_code) setDetectedProvider('Claude Code')
        else if (data.anthropic_key) setDetectedProvider('Anthropic')
        else if (data.gemini_key) setDetectedProvider('Gemini')
        else if (data.vertex_ai) setDetectedProvider('Vertex AI')
        else if (data.bedrock) setDetectedProvider('AWS Bedrock')
      })
      .catch((e) => {
        reportError('provider detection failed', e)
        setDetectError(true)
      })
      .finally(() => setDetectLoading(false))
  }, [])

  // Restore step after any OAuth redirect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)

    const errorMsg = params.get('error') || params.get('auth_error')
    if (errorMsg) {
      setOauthError(`Connection failed: ${errorMsg}. Try again.`)
      api.patch('/settings', { onboarding_step: null }).catch(() => {})
      window.history.replaceState({}, '', '/')
      return
    }

    const oauthReturnKeys = ['connected', 'auth_success', 'atlassian_connected', 'oauth_connected']
    const isOAuthReturn = oauthReturnKeys.some(k => params.has(k))
    if (isOAuthReturn) {
      api.get<{ onboarding_step?: number | null }>('/settings').then((data) => {
        const savedStep = data.onboarding_step
        if (savedStep != null) {
          const idx = typeof savedStep === 'number' ? savedStep : parseInt(String(savedStep), 10)
          if (!Number.isNaN(idx) && idx >= 0) {
            setStepIndex(idx)
          }
          api.patch('/settings', { onboarding_step: null }).catch(() => {})
        }
        window.history.replaceState({}, '', '/')
      }).catch(() => {
        window.history.replaceState({}, '', '/')
      })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Restore step from localStorage on mount (→1518)
  useEffect(() => {
    try {
      const raw = localStorage.getItem('myos.onboarding.state')
      if (raw) {
        const saved = JSON.parse(raw) as { stepName?: string; mode?: string }
        if (saved.mode === 'team') {
          setOnboardingMode('team')
          setInstanceMode('team')
        } else if (saved.mode === 'personal') {
          setOnboardingMode('personal')
        }
        const steps = saved.mode === 'team' ? TEAM_STEPS : PERSONAL_STEPS_NO_FORK
        const idx = (steps as readonly string[]).indexOf(saved.stepName ?? '')
        if (idx > 0) setStepIndex(idx)
      }
    } catch { /* corrupted entry — ignore */ }
    setMounted(true)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Persist current step to localStorage on every transition (→1518)
  useEffect(() => {
    if (!mounted) return
    const state = JSON.stringify({ stepName: step, mode: onboardingMode === 'undecided' ? 'personal' : onboardingMode })
    localStorage.setItem('myos.onboarding.state', state)
  }, [stepIndex, step, onboardingMode, mounted])

  // Profile (HUMANFILE) step state
  const [profileRole, setProfileRole] = useState('')
  const [profileStyle, setProfileStyle] = useState<'brief' | 'detailed' | ''>('')

  // Team onboarding state
  const [teamOrgName, setTeamOrgName] = useState('')
  const [teamAdminEmail, setTeamAdminEmail] = useState('')
  const [teamInviteEmails, setTeamInviteEmails] = useState<string[]>([])
  const [teamIsolationLevel, setTeamIsolationLevel] = useState<'open' | 'governed' | 'sealed'>('governed')

  const handleForkChoice = (mode: 'personal' | 'team') => {
    setOnboardingMode(mode)
    if (mode === 'team') {
      setInstanceMode('team')
    }
    // Move to next step (index stays at 0, but STEPS array changes for team)
    setStepIndex(1)
  }

  const next = () => setStepIndex((i) => Math.min(i + 1, STEPS.length - 1))
  const back = () => setStepIndex((i) => Math.max(i - 1, 0))

  const handlePersonaPick = (category: MarketplaceCategory) => {
    setSelectedPersonaId(category.id)
    setProfileRole(category.category)
    setOtherSelected(false)
    setCustomAgentTemplates([])
  }

  const handleOtherPick = () => {
    setSelectedPersonaId(null)
    setOtherSelected(true)
    setProfileRole('')
  }

  const handleProfileNext = () => {
    if (selectedPersonaId) {
      api.post('/agents/pm-templates/install-persona', { persona_id: selectedPersonaId }).catch((e) => reportError('persona install failed', e))
    }
    next()
  }

  const dismissWizard = () => {
    localStorage.setItem('myos.onboarding.dismissed', 'true')
    try { window.history.replaceState({}, '', '/dashboard') } catch { /* test env */ }
    setOnboarded(true)
  }

  // Always land the user on the homepage ("/") after onboarding, regardless
  // of the URL they arrived at (deep-link, /settings, /onboarding, etc.).
  // The wizard renders outside of BrowserRouter (see App.tsx), so we cannot
  // use useNavigate here. Rewrite the URL via history API before flipping
  // onboarded=true so when App.tsx swaps the wizard out for BrowserRouter,
  // the Router reads "/" and mounts the Dashboard.
  const goHome = () => {
    try {
      window.history.replaceState({}, '', '/')
    } catch {
      // history API unavailable (non-browser test env). Tests assert
      // onboarded=true directly, so swallow and move on.
    }
  }

  const finish = async () => {
    if (onboardingMode === 'team') {
      // Team finish: create org, invite members, set policies
      const data: TeamOnboardingData = {
        orgName: teamOrgName,
        adminEmail: teamAdminEmail,
        inviteEmails: teamInviteEmails,
        isolationLevel: teamIsolationLevel,
      }
      await finishTeamOnboarding(data)
      setOrgName(teamOrgName)
      setInstanceMode('team')
      const settings: Record<string, unknown> = {
        dark_mode: pickedDarkRef.current,
        provider: selectedProvider,
        instance_mode: 'team',
      }
      // Sync the store with what the user picked in the wizard
      if (pickedDarkRef.current !== darkMode) toggleDarkMode()
      api.patch('/settings', settings).catch((e) => reportError('settings patch failed', e))
      // Reset the "Finished" baseline so agents from before onboarding
      // do not immediately show a stale count in the sidebar.
      setAgentsLastViewed(new Date().toISOString())
      localStorage.removeItem('myos.onboarding.state')
      // Navigate home BEFORE flipping onboarded. Once onboarded=true the
      // wizard unmounts and BrowserRouter mounts at whatever URL is current.
      goHome()
      setOnboarded(true)
      return
    }
    // Personal finish
    const settings: Record<string, unknown> = {
      os_name: osName,
      instance_name: instanceName || 'yourOS',
      user_name: userName,
      dark_mode: pickedDarkRef.current,
      provider: selectedProvider,
      instance_mode: 'personal',
    }
    if (selectedPersonaId) settings.persona = selectedPersonaId
    if (profileRole) settings.user_role = profileRole
    if (profileStyle) settings.communication_style = profileStyle
    // Sync the store with what the user picked in the wizard
    if (pickedDarkRef.current !== darkMode) toggleDarkMode()
    api.patch('/settings', settings).catch((e) => reportError('settings patch failed', e))
    // Reset the "Finished" baseline so agents from before onboarding
    // do not immediately show a stale count in the sidebar.
    setAgentsLastViewed(new Date().toISOString())
    if (trackingOption) {
      const trackBody: Record<string, unknown> = { scope: trackingOption }
      if (trackingOption === 'repo' && trackingRepoPath) trackBody.path = trackingRepoPath
      await api.post('/onboarding/enable-myos-hooks', trackBody).catch((e) => reportError('tracking setup failed', e))
    }
    // Navigate home BEFORE flipping onboarded (see goHome comment above).
    localStorage.removeItem('myos.onboarding.state')
    goHome()
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
        .catch((e) => reportError('save key failed', e))
    }
  }

  const skip = () => next()

  // Stable ref holds the latest advance logic. Registered on window once (empty-deps effect)
  // so Enter fires globally regardless of which element has focus.
  const advanceOnEnterRef = useRef<(e: KeyboardEvent) => void>(() => {})
  advanceOnEnterRef.current = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' || e.isComposing) return
    const target = e.target as HTMLElement
    const tag = target.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON' || target.contentEditable === 'true') return
    // Fork step: no action (user must click a card)
    if (step === 'Fork') return
    // TeamReady and Ready both finish.
    if (step === 'TeamReady') { finish(); return }
    if (step === 'Ready') { finish(); return }
    // Profile fires persona install on advance.
    if (step === 'Profile') { handleProfileNext(); return }
    next()
  }
  useEffect(() => {
    const handler = (e: KeyboardEvent) => advanceOnEnterRef.current(e)
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Theme: purely local state. Zero store interaction during the wizard.
  // The store is synced ONLY when the user finishes onboarding.
  const themeIdx = (STEPS as readonly string[]).indexOf('Theme')
  const [pickedDark, setPickedDark] = useState(darkMode)
  const effectiveDark = themeIdx >= 0 && stepIndex >= themeIdx ? pickedDark : false
  const pickedDarkRef = useRef(darkMode)

  const handleDarkModeChoice = (wantDark: boolean) => {
    setPickedDark(wantDark)
    pickedDarkRef.current = wantDark
  }

  // Keep DOM in sync with the wizard's theme choice
  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', effectiveDark ? 'dark' : 'light')
    document.body.style.backgroundColor = effectiveDark ? '#020617' : '#f8fafc'
    return () => { document.body.style.backgroundColor = '' }
  }, [effectiveDark])

  // Dark-mode-aware style helpers (use effectiveDark, not darkMode)
  const inputCls = effectiveDark
    ? 'bg-slate-800 border-slate-700 text-white'
    : 'bg-white border-gray-300 text-slate-900'
  const subtextCls = effectiveDark ? 'text-slate-400' : 'text-slate-500'
  const cardCls = effectiveDark
    ? 'bg-slate-900/60 border-slate-800'
    : 'bg-white border-gray-200 shadow-sm'
  const dotInactiveCls = effectiveDark ? 'bg-slate-700' : 'bg-gray-300'
  const navBtnCls = effectiveDark
    ? 'text-slate-400 hover:text-white'
    : 'text-slate-500 hover:text-slate-900'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto py-8"
      style={{
        backgroundColor: effectiveDark ? '#020617' : '#f8fafc',
        backgroundImage: effectiveDark ? 'none' : 'linear-gradient(to bottom, #f8fafc 0%, #ffffff 100%)',
        color: effectiveDark ? '#ffffff' : '#0f172a',
        transition: 'background-color 0.3s, color 0.3s',
      }}
      data-testid="onboarding-wizard"
    >
      {/* Exit confirm dialog (→1519) */}
      {showExitConfirm && (
        <div className="fixed inset-0 z-60 flex items-center justify-center bg-black/50" data-testid="exit-confirm-dialog">
          <div className={`rounded-xl p-6 max-w-sm w-full mx-4 shadow-xl ${effectiveDark ? 'bg-slate-900 border border-slate-700' : 'bg-white border border-gray-200'}`}>
            <h3 className="text-base font-semibold mb-2">Exit setup?</h3>
            <p className={`text-sm mb-4 ${subtextCls}`}>You can resume from Settings anytime.</p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowExitConfirm(false)}
                className={`px-4 py-2 text-sm rounded-lg border transition-colors ${effectiveDark ? 'border-slate-600 hover:border-slate-400' : 'border-gray-300 hover:border-gray-500'}`}
                data-testid="exit-cancel-btn"
              >
                Keep going
              </button>
              <button
                onClick={dismissWizard}
                className="px-4 py-2 text-sm rounded-lg bg-red-600 hover:bg-red-500 text-white transition-colors"
                data-testid="exit-confirm-btn"
              >
                Exit setup
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Close button (→1519) */}
      <button
        onClick={() => setShowExitConfirm(true)}
        className={`fixed top-4 right-4 z-50 p-1.5 rounded-full transition-colors ${effectiveDark ? 'text-slate-500 hover:text-white hover:bg-slate-800' : 'text-gray-400 hover:text-gray-700 hover:bg-gray-100'}`}
        data-testid="onboarding-close-btn"
        aria-label="Exit setup"
      >
        <Icon name="close" size={20} />
      </button>

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

        {/* OAuth error banner */}
        {oauthError && (
          <div
            className="mb-4 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm flex items-center justify-between"
            data-testid="oauth-error-banner"
          >
            <span>{oauthError}</span>
            <button onClick={() => setOauthError(null)} className="ml-3 text-red-400 hover:text-red-300 text-xs underline">Dismiss</button>
          </div>
        )}

        {/* Step content */}
        <div className="min-h-[320px]">
          {step === 'Fork' && <ForkStep onChoose={handleForkChoice} subtextCls={subtextCls} cardCls={cardCls} darkMode={effectiveDark} />}
          {step === 'Welcome' && <WelcomeStep />}
          {step === 'You' && (
            <YouStep
              userName={userName}
              setUserName={setUserName}
              onNext={next}
              inputCls={inputCls}
              subtextCls={subtextCls}
            />
          )}
          {step === 'Name' && (
            <NameStep
              osName={osName}
              setOsName={setOsName}
              onNext={next}
              userName={userName}
              inputCls={inputCls}
              subtextCls={subtextCls}
            />
          )}
          {step === 'Profile' && (
            <div data-testid="step-profile">
              <h2 className="text-2xl font-bold mb-2">Tell {osName || 'your OS'} about you</h2>
              <p className={`mb-2 ${subtextCls}`}>
                This creates your profile so {osName} knows how to help you. Think of it as a quick intro so your AI knows who it's working for.
              </p>
              <p className={`mb-6 text-xs ${subtextCls}`}>
                Your profile stays on your machine. It's never shared or uploaded.
              </p>
              <div className="space-y-4">
                <div>
                  <label className={`block text-sm font-medium mb-2 ${subtextCls}`}>What best describes you?</label>
                  <div className="space-y-1.5">
                    {AGENT_MARKETPLACE.map((cat) => {
                      const isPicked = selectedPersonaId === cat.id
                      return (
                        <button
                          key={cat.id}
                          onClick={() => handlePersonaPick(cat)}
                          data-testid={`persona-card-${cat.id}`}
                          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg border text-left transition-colors ${
                            isPicked
                              ? 'bg-blue-500/20 border-blue-500'
                              : `${cardCls} ${effectiveDark ? 'hover:border-slate-600' : 'hover:border-gray-400'}`
                          }`}
                        >
                          <div className={`w-7 h-7 rounded-md flex items-center justify-center shrink-0 ${
                            isPicked ? 'bg-blue-500/30 text-blue-300' : effectiveDark ? 'bg-slate-800 text-slate-400' : 'bg-gray-100 text-slate-500'
                          }`}>
                            <Icon name={PERSONA_ICONS[cat.id] || 'person'} size={16} />
                          </div>
                          <span className="text-sm font-medium">{cat.category}</span>
                          {isPicked && <Icon name="check_circle" className="text-blue-400 ml-auto" size={16} />}
                        </button>
                      )
                    })}
                    <button
                      onClick={handleOtherPick}
                      data-testid="persona-card-other"
                      className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg border text-left transition-colors ${
                        otherSelected
                          ? 'bg-blue-500/20 border-blue-500'
                          : `${cardCls} ${effectiveDark ? 'hover:border-slate-600' : 'hover:border-gray-400'}`
                      }`}
                    >
                      <div className={`w-7 h-7 rounded-md flex items-center justify-center shrink-0 ${
                        otherSelected ? 'bg-blue-500/30 text-blue-300' : effectiveDark ? 'bg-slate-800 text-slate-400' : 'bg-gray-100 text-slate-500'
                      }`}>
                        <Icon name="edit" size={16} />
                      </div>
                      <span className="text-sm font-medium">Other</span>
                      {otherSelected && <Icon name="check_circle" className="text-blue-400 ml-auto" size={16} />}
                    </button>
                    {otherSelected && (
                      <input
                        type="text"
                        value={profileRole}
                        onChange={(e) => setProfileRole(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') next() }}
                        placeholder="e.g. Founder, Student, Designer"
                        data-testid="other-role-input"
                        className={`w-full border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
                        autoFocus
                      />
                    )}
                  </div>
                </div>
                <div>
                  <label className={`block text-sm font-medium mb-1 ${subtextCls}`}>How should {osName} communicate with you?</label>
                  <div className="grid grid-cols-2 gap-3">
                    {([
                      { id: 'brief', label: 'Keep it short', desc: 'Quick answers, no fluff' },
                      { id: 'detailed', label: 'Give me detail', desc: 'Thorough explanations' },
                    ] as const).map((opt) => (
                      <button
                        key={opt.id}
                        onClick={() => setProfileStyle(opt.id)}
                        className={`text-left p-3 rounded-lg border transition-colors ${
                          profileStyle === opt.id
                            ? 'border-blue-500 bg-blue-500/10'
                            : `border-slate-300 dark:border-slate-700 ${inputCls}`
                        }`}
                      >
                        <p className="text-sm font-medium">{opt.label}</p>
                        <p className={`text-xs mt-0.5 ${subtextCls}`}>{opt.desc}</p>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
          {step === 'Theme' && (
            <ThemeStep darkMode={pickedDark} onChoose={handleDarkModeChoice} subtextCls={subtextCls} />
          )}
          {step === 'Tracking' && (
            <TrackingStep
              selectedOption={trackingOption}
              onSelect={setTrackingOption}
              repoPath={trackingRepoPath}
              onRepoPath={setTrackingRepoPath}
              subtextCls={subtextCls}
              cardCls={cardCls}
            />
          )}
          {step === 'Connect' && (
            <ConnectStep
              selectedProvider={selectedProvider}
              onSelectProvider={handleProviderSelect}
              apiKey={apiKey}
              onApiKeyChange={setApiKey}
              onSaveKey={handleSaveKey}
              onNext={next}
              keySaved={keySaved}
              darkMode={effectiveDark}
              inputCls={inputCls}
              subtextCls={subtextCls}
              stepIndex={stepIndex}
              detectedProvider={detectedProvider}
              detectLoading={detectLoading}
              detectError={detectError}
            />
          )}
          {step === 'Ready' && (
            <ReadyStep
              userName={userName}
              osName={osName}
              darkMode={effectiveDark}
              provider={selectedProvider}
              subtextCls={subtextCls}
              cardCls={cardCls}
              detectedProvider={detectedProvider}
            />
          )}
          {/* Team steps */}
          {step === 'OrgName' && (
            <OrgNameStep orgName={teamOrgName} setOrgName={setTeamOrgName} onNext={next} inputCls={inputCls} subtextCls={subtextCls} />
          )}
          {step === 'AdminEmail' && (
            <AdminEmailStep adminEmail={teamAdminEmail} setAdminEmail={setTeamAdminEmail} onNext={next} inputCls={inputCls} subtextCls={subtextCls} />
          )}
          {step === 'InviteTeam' && (
            <InviteTeamStep inviteEmails={teamInviteEmails} setInviteEmails={setTeamInviteEmails} inputCls={inputCls} subtextCls={subtextCls} darkMode={effectiveDark} />
          )}
          {step === 'Guardrails' && (
            <GuardrailsStep isolationLevel={teamIsolationLevel} setIsolationLevel={setTeamIsolationLevel} subtextCls={subtextCls} darkMode={effectiveDark} />
          )}
          {step === 'TeamReady' && (
            <TeamReadyStep
              orgName={teamOrgName}
              adminEmail={teamAdminEmail}
              inviteCount={teamInviteEmails.length}
              isolationLevel={teamIsolationLevel}
              subtextCls={subtextCls}
              cardCls={cardCls}
            />
          )}
        </div>

        {/* Navigation buttons */}
        <div className="flex items-center justify-between mt-8">
          <div>
            {stepIndex > 0 && step !== 'Ready' && step !== 'TeamReady' && step !== 'Fork' && (
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
            {step !== 'Welcome' && step !== 'Ready' && step !== 'Fork' && step !== 'TeamReady' && (
              <button
                onClick={skip}
                className={`px-4 py-2 text-sm transition-colors ${navBtnCls}`}
                data-testid="skip-button"
              >
                Skip
              </button>
            )}

            {step === 'Fork' ? null : step === 'TeamReady' ? (
              <button
                onClick={finish}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
                data-testid="finish-button"
              >
                Get started
              </button>
            ) : step === 'Ready' ? (
              <button
                onClick={finish}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
                data-testid="finish-button"
              >
                Get started
              </button>
            ) : (
              <button
                onClick={step === 'Profile' ? handleProfileNext : next}
                className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-semibold text-white transition-colors"
                data-testid="next-button"
              >
                Next
              </button>
            )}
          </div>
        </div>
      </div>
      {/* Privacy footer: fixed to bottom of viewport, visible on every step */}
      <div className="fixed bottom-0 left-0 right-0 text-center text-xs text-slate-500 dark:text-slate-400 py-2 pointer-events-none">
        <span className="pointer-events-auto">
          Your data stays on your device. Read our{' '}
          <a
            href="/privacy"
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:opacity-80"
            data-testid="onboarding-privacy-link"
          >
            privacy policy
          </a>
          .
        </span>
      </div>
    </div>
  )
}

/* ---- Step Components ---- */

function ForkStep({
  onChoose,
  subtextCls,
  cardCls,
  darkMode,
}: {
  onChoose: (mode: 'personal' | 'team') => void
  subtextCls: string
  cardCls: string
  darkMode: boolean
}) {
  return (
    <div className="text-center" data-testid="step-fork">
      <div className="mb-6">
        <Icon name="rocket_launch" size={48} className="text-blue-400" />
      </div>
      <h1 className="text-3xl font-bold mb-2">Let's get started</h1>
      <p className={`${subtextCls} text-lg mb-8`}>Who is this for?</p>
      <div className="grid grid-cols-1 gap-4">
        <button
          onClick={() => onChoose('personal')}
          className={`flex items-center gap-4 p-5 rounded-xl border ${cardCls} hover:border-blue-500 hover:bg-blue-500/10 text-left transition-all`}
          data-testid="fork-personal"
        >
          <div className="w-12 h-12 rounded-xl bg-pink-500/20 text-pink-400 flex items-center justify-center shrink-0">
            <Icon name="person" size={28} />
          </div>
          <div>
            <p className={`font-bold text-lg ${darkMode ? 'text-white' : 'text-slate-900'}`}>Just me</p>
            <p className={`text-sm ${subtextCls}`}>A personal OS for managing your needles, agents, and tools.</p>
          </div>
        </button>
        <button
          onClick={() => onChoose('team')}
          className={`flex items-center gap-4 p-5 rounded-xl border ${cardCls} hover:border-indigo-500 hover:bg-indigo-500/10 text-left transition-all`}
          data-testid="fork-team"
        >
          <div className="w-12 h-12 rounded-xl bg-indigo-500/20 text-indigo-400 flex items-center justify-center shrink-0">
            <Icon name="groups" size={28} />
          </div>
          <div>
            <p className={`font-bold text-lg ${darkMode ? 'text-white' : 'text-slate-900'}`}>My team</p>
            <p className={`text-sm ${subtextCls}`}>A shared workspace with governance, policies, and shared agents.</p>
          </div>
        </button>
      </div>
    </div>
  )
}

type StarterPackItem = {
  kind: 'skill' | 'agent'
  id: string
  name: string
  description: string
  default_selected: boolean
}

// Maps persona IDs (from agentMarketplace) to the intent key used by /onboarding/intent
const PERSONA_TO_INTENT: Record<string, string> = {
  everyone: 'general',
  pm: 'work_role',
  engineer: 'coding',
  sales: 'sales',
  writer: 'writing',
  home: 'personal',
  student: 'research',
  marketing: 'marketing',
  founder: 'founder',
  support: 'support',
  designer: 'designer',
}

export function CustomizeStep({
  selectedPersonaId,
  subtextCls,
  cardCls,
}: {
  selectedPersonaId: string | null
  subtextCls: string
  cardCls: string
}) {
  const [starterPack, setStarterPack] = useState<StarterPackItem[]>([])
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    const intentId = selectedPersonaId ? PERSONA_TO_INTENT[selectedPersonaId] : null
    if (!intentId) {
      setStarterPack([])
      setChecked(new Set())
      return
    }
    setLoading(true)
    setError(null)
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout> | undefined
    const timeoutPromise = new Promise<never>((_, reject) => {
      // Safety net for a fully-wedged backend only. Must stay ABOVE api.ts
      // REQUEST_TIMEOUT_MS (30s) so the request's own ApiTimeoutError fires first
      // with a specific message; 30s here collided with it (simultaneous reject).
      timeoutId = setTimeout(() => reject(new Error('timeout')), 35_000)
    })
    Promise.race([
      api.post<{ starter_pack: StarterPackItem[] }>('/onboarding/intent', { intent: intentId }),
      timeoutPromise,
    ])
      .then((resp) => {
        if (cancelled) return
        setStarterPack(resp.starter_pack)
        setChecked(new Set(resp.starter_pack.filter((i) => i.default_selected).map((i) => i.id)))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setStarterPack([])
        const detail = err instanceof ApiError
          ? (typeof err.response.data.detail === 'string' ? err.response.data.detail : '')
          : ''
        setError(detail || "Couldn't load your starter agents. Please try again.")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
      clearTimeout(timeoutId)
    }
  }, [selectedPersonaId, retryCount])

  const toggleItem = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div data-testid="step-customize">
      <h2 className="text-2xl font-bold mb-2">Your starter agents</h2>
      <p className={`mb-5 ${subtextCls}`}>
        These are suggested based on your profile. Uncheck anything you don't want.
      </p>
      {loading && (
        <p className={`text-sm ${subtextCls}`} data-testid="customize-loading">Loading...</p>
      )}
      {!loading && error && (
        <div data-testid="customize-load-error" className={`text-sm ${subtextCls}`}>
          <p className="mb-2">{error}</p>
          <button
            data-testid="customize-load-retry"
            onClick={() => setRetryCount((c) => c + 1)}
            className="px-3 py-1.5 text-xs rounded border border-current hover:opacity-80 transition-opacity"
          >
            Try again
          </button>
        </div>
      )}
      {!loading && !error && starterPack.length > 0 && (
        <div className="space-y-1.5">
          {starterPack.map((item) => (
            <label
              key={item.id}
              className={`flex items-start gap-3 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                checked.has(item.id) ? 'bg-blue-500/10 border-blue-500/50' : cardCls
              }`}
              data-testid={`pack-item-${item.id}`}
            >
              <input
                type="checkbox"
                checked={checked.has(item.id)}
                onChange={() => toggleItem(item.id)}
                className="mt-0.5 shrink-0"
                data-testid={`pack-checkbox-${item.id}`}
              />
              <div className="min-w-0">
                <p className="text-sm font-medium">{item.name}</p>
                <p className={`text-xs ${subtextCls}`}>{item.description}</p>
              </div>
            </label>
          ))}
        </div>
      )}
      {starterPack.length === 0 && !selectedPersonaId && (
        <p className={`text-sm ${subtextCls}`} data-testid="customize-no-persona">
          Pick a profile on the previous step to see suggested agents here.
        </p>
      )}
    </div>
  )
}

function WelcomeStep() {
  return (
    <div className="text-center" data-testid="step-welcome">
      <div className="mb-6">
        <img src="/youros-logo-hero.png" alt="yourOS" className="mx-auto h-auto w-[368px]" />
      </div>
      <p className="text-xl italic font-medium text-center tracking-wide whitespace-nowrap text-slate-700 dark:text-slate-300">an operating system for how <span className="font-bold bg-gradient-to-r from-pink-500 via-purple-500 to-orange-500 bg-clip-text text-transparent">you</span> operate.</p>
      <p className="text-[28px] italic font-bold text-center mb-6 bg-gradient-to-r from-pink-500 via-purple-500 to-orange-500 bg-clip-text text-transparent">this is your OS.</p>
      <p className="text-[15px] italic text-slate-500 dark:text-slate-400 leading-relaxed mt-2">
        Let's get you set up. It takes about two minutes, and you can change anything later.
      </p>
    </div>
  )
}

function YouStep({
  userName,
  setUserName,
  onNext,
  inputCls,
  subtextCls,
}: {
  userName: string
  setUserName: (name: string) => void
  onNext: () => void
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
        onKeyDown={(e) => { if (e.key === "Enter") onNext(); }}
        placeholder="Your name"
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
  onNext,
  userName,
  inputCls,
  subtextCls,
}: {
  osName: string
  setOsName: (name: string) => void
  onNext: () => void
  userName: string
  inputCls: string
  subtextCls: string
}) {
  const example = userName ? `${userName}OS` : 'YourOS'
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
        onKeyDown={(e) => { if (e.key === "Enter") onNext(); }}
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
          className="p-4 rounded-xl border-2 transition-all"
          style={{
            borderColor: !darkMode ? '#3b82f6' : '#334155',
            backgroundColor: !darkMode ? 'rgba(59,130,246,0.1)' : 'rgba(30,41,59,0.3)',
          }}
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
          className="p-4 rounded-xl border-2 transition-all"
          style={{
            borderColor: darkMode ? '#3b82f6' : '#334155',
            backgroundColor: darkMode ? 'rgba(59,130,246,0.1)' : 'rgba(30,41,59,0.3)',
          }}
          data-testid="theme-dark"
        >
          {/* Mini dashboard preview - dark. Inline hex so the [data-theme="light"]
              overrides in index.css never force these swatches to light colors. */}
          <div
            className="w-full rounded-lg p-3 mb-3 overflow-hidden"
            style={{ backgroundColor: '#020617' }}
            data-testid="theme-dark-preview"
          >
            <div className="flex gap-2">
              <div className="w-10 rounded p-1.5 flex flex-col gap-1" style={{ backgroundColor: '#0f172a' }}>
                <div className="w-full h-1 rounded" style={{ backgroundColor: '#334155' }} />
                <div className="w-full h-1 rounded" style={{ backgroundColor: '#334155' }} />
                <div className="w-full h-1 rounded" style={{ backgroundColor: '#334155' }} />
                <div className="w-full h-1 rounded" style={{ backgroundColor: '#334155' }} />
              </div>
              <div className="flex-1 flex flex-col gap-1.5">
                <div className="h-2 rounded w-3/4" style={{ backgroundColor: '#64748b' }} />
                <div className="flex gap-1.5">
                  <div className="flex-1 h-8 rounded border" style={{ backgroundColor: '#1e293b', borderColor: '#334155' }} />
                  <div className="flex-1 h-8 rounded border" style={{ backgroundColor: '#1e293b', borderColor: '#334155' }} />
                </div>
                <div className="flex gap-1.5">
                  <div className="flex-1 h-6 rounded border" style={{ backgroundColor: '#1e293b', borderColor: '#334155' }} />
                  <div className="flex-1 h-6 rounded border" style={{ backgroundColor: '#1e293b', borderColor: '#334155' }} />
                  <div className="flex-1 h-6 rounded border" style={{ backgroundColor: '#1e293b', borderColor: '#334155' }} />
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

function TrackingStep({
  selectedOption,
  onSelect,
  repoPath,
  onRepoPath,
  subtextCls,
  cardCls,
}: {
  selectedOption: 'everywhere' | 'repo' | 'myos-only' | null
  onSelect: (opt: 'everywhere' | 'repo' | 'myos-only') => void
  repoPath: string
  onRepoPath: (path: string) => void
  subtextCls: string
  cardCls: string
}) {
  const options = [
    {
      id: 'everywhere' as const,
      label: 'Track everything: yourOS is my main dashboard.',
      subtitle: "Every Claude Code conversation on this computer shows up in yourOS, no matter which folder you're in.",
    },
    {
      id: 'repo' as const,
      label: 'Track one specific project.',
      subtitle: 'Only conversations inside the project you choose show up in yourOS. Other projects stay untouched.',
    },
    {
      id: 'myos-only' as const,
      label: 'I just want to try yourOS without touching anything else.',
      subtitle: 'Only conversations inside the yourOS folder show up. Nothing else on your computer changes.',
    },
  ]

  return (
    <div data-testid="step-tracking">
      <h2 className="text-2xl font-bold mb-2">How should yourOS track your work?</h2>
      <p className={`mb-6 ${subtextCls}`}>
        Choose how yourOS connects to Claude Code on this computer.
      </p>
      <div className="space-y-3">
        {options.map((opt) => (
          <button
            key={opt.id}
            onClick={() => onSelect(opt.id)}
            data-testid={`tracking-option-${opt.id}`}
            className={`w-full text-left p-4 rounded-xl border transition-all ${
              selectedOption === opt.id
                ? 'border-blue-500 bg-blue-500/10'
                : `${cardCls} hover:border-blue-400`
            }`}
          >
            <p className="text-sm font-medium">{opt.label}</p>
            <p className={`text-xs mt-1 ${subtextCls}`}>{opt.subtitle}</p>
          </button>
        ))}
      </div>
      {selectedOption === 'repo' && (
        <div className="mt-4" data-testid="tracking-folder-picker">
          <p className={`text-xs mb-2 ${subtextCls}`}>Paste the full path to your project folder:</p>
          <input
            type="text"
            data-testid="tracking-folder-input"
            value={repoPath}
            onChange={(e) => onRepoPath(e.target.value)}
            placeholder="/Users/you/work/myproject"
            className="text-sm w-full px-3 py-2 rounded-lg border border-slate-600 bg-transparent"
          />
        </div>
      )}
    </div>
  )
}

function ConnectStep({
  selectedProvider,
  onSelectProvider,
  apiKey,
  onApiKeyChange,
  onSaveKey,
  onNext,
  keySaved,
  darkMode,
  inputCls,
  subtextCls,
  stepIndex,
  detectedProvider,
  detectLoading,
  detectError,
}: {
  selectedProvider: string
  onSelectProvider: (name: string) => void
  apiKey: string
  onApiKeyChange: (key: string) => void
  onSaveKey: () => void
  onNext: () => void
  keySaved: boolean
  darkMode: boolean
  inputCls: string
  subtextCls: string
  stepIndex: number
  detectedProvider?: string | null
  detectLoading?: boolean
  detectError?: boolean
}) {
  const [googleOAuthAvailable, setGoogleOAuthAvailable] = useState(false)
  const [googleConnected, setGoogleConnected] = useState(false)
  const [atlassianConnected, setAtlassianConnected] = useState(false)
  const [geminiAdvancedOpen, setGeminiAdvancedOpen] = useState(false)

  useEffect(() => {
    api.get<{ google_oauth_available?: boolean; google_connected?: boolean }>('/secrets/key-status')
      .then((data) => {
        setGoogleOAuthAvailable(data.google_oauth_available ?? false)
        setGoogleConnected(data.google_connected ?? false)
      })
      .catch((e) => reportError('key status check failed', e))
  }, [])

  useEffect(() => {
    api.get<{ connected?: boolean }>('/atlassian/status')
      .then((data) => setAtlassianConnected(data.connected ?? false))
      .catch(() => {})
  }, [])

  const providers = [
    { name: 'Anthropic', label: 'Anthropic (Claude)' },
    { name: 'Google Gemini', label: 'Google (Gemini)' },
  ]

  const sectionDivider = `text-xs font-semibold uppercase tracking-wider mb-3 pb-1 border-b ${darkMode ? 'text-slate-500 border-slate-800' : 'text-slate-400 border-gray-200'}`

  return (
    <div data-testid="step-connect">
      <h2 className="text-2xl font-bold mb-2">Connect your providers</h2>
      {detectedProvider && (
        <div
          className="inline-flex items-center gap-1.5 mb-4 px-3 py-1.5 rounded-full text-sm font-medium bg-green-500/15 text-green-400 border border-green-500/30"
          data-testid="already-connected-badge"
        >
          <span>Already connected: {detectedProvider}</span>
        </div>
      )}
      <p className={`${subtextCls} mb-6`}>
        Pick a provider and sign in or paste an API key. You can change this
        anytime in Settings.
      </p>

      {/* ── Which AI runs your chat ───────────────────────── */}
      <div className="mb-5">
        <p className={sectionDivider}>Which AI runs your chat</p>

        {/* Provider selector — keep both tabs for switching */}
        <div className="grid grid-cols-2 gap-3 mb-3">
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

        {selectedProvider === 'Anthropic' && detectedProvider && (
          <div
            data-testid="anthropic-connected-status"
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-green-500/10 border border-green-500/30 text-green-400 text-sm"
          >
            <Icon name="check_circle" size={16} />
            <span>Connected via {detectedProvider}. No key needed.</span>
          </div>
        )}
        {selectedProvider === 'Anthropic' && detectLoading && !detectedProvider && (
          <p
            data-testid="anthropic-detect-loading"
            className={`text-xs ${subtextCls}`}
          >
            Checking for a detected provider...
          </p>
        )}
        {selectedProvider === 'Anthropic' && detectError && !detectedProvider && !detectLoading && (
          <p
            data-testid="anthropic-detect-error"
            className="text-xs text-red-400"
          >
            Could not check for a connected provider. You can paste a key below or skip and connect later.
          </p>
        )}
        {selectedProvider === 'Anthropic' && !detectLoading && !detectedProvider && (
          <>
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
            <div className="flex gap-2">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { onSaveKey(); onNext(); } }}
                placeholder="Paste API key (sk-ant-xxxx...)"
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
            <p className={`text-xs mt-2 ${subtextCls}`}>
              You can skip this step and connect later in Settings.
            </p>
          </>
        )}

        {/* Gemini: free AI Studio key as lead, Cloud setup behind Advanced toggle */}
        {selectedProvider === 'Google Gemini' && (
          <div className="mt-3">
            <p className={`text-xs mb-3 ${subtextCls}`}>
              A paid Gemini app subscription (Gemini Advanced) doesn't include API access, so it can't be used here. The free key below works with the same Google account.
            </p>
            <p className={`text-xs font-medium mb-1 ${subtextCls}`}>Paste AI Studio key (for personal use)</p>
            <div className="flex gap-2">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { onSaveKey(); onNext(); } }}
                placeholder="Paste API key (AIzaSy...)"
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
            <p className={`text-xs mt-2 ${subtextCls}`}>
              You can skip this step and connect later in Settings.
            </p>
            {googleOAuthAvailable ? (
              <button
                onClick={() => {
                  api.patch('/settings', { onboarding_step: stepIndex }).catch(() => {})
                  window.open('/api/auth/google', '_self')
                }}
                className={`w-full mt-3 mb-3 px-4 py-2.5 border rounded-lg text-sm font-medium transition-colors flex items-center gap-2 ${
                  darkMode
                    ? 'bg-slate-800 border-slate-700 text-white hover:border-blue-500'
                    : 'bg-white border-gray-300 text-slate-900 hover:border-blue-500'
                }`}
                data-testid="connect-google"
              >
                <Icon name="login" size={18} />
                Connect via Google Cloud (Vertex AI)
              </button>
            ) : (
              <p className={`text-sm mt-3 mb-3 ${subtextCls}`}>
                Google sign-in is not set up yet. Paste a Gemini API key above, or switch to Anthropic.
              </p>
            )}
            <button
              data-testid="gemini-advanced-toggle"
              onClick={() => setGeminiAdvancedOpen((v) => !v)}
              className={`text-xs underline ${subtextCls} hover:opacity-80`}
            >
              {geminiAdvancedOpen ? 'Hide Google Cloud setup' : 'Advanced: set up through Google Cloud'}
            </button>
            {geminiAdvancedOpen && (
              <div
                className={`mt-2 mb-3 p-3 rounded-lg text-xs space-y-2 border bg-gradient-to-r from-blue-500/10 to-cyan-500/10 border-blue-500/30 ${
                  darkMode ? 'text-slate-200' : 'text-slate-700'
                }`}
                data-testid="gemini-key-help"
              >
                <p>
                  Use the same{' '}
                  <a
                    href="https://console.cloud.google.com"
                    target="_blank"
                    rel="noreferrer"
                    className={`underline ${darkMode ? 'text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
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
                      className={`underline ${darkMode ? 'text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
                    >
                      "Generative Language API"
                    </a>{' '}
                    in the API library. It takes about 30 seconds.
                  </li>
                  <li>Open Credentials and click Create credentials, API key.</li>
                  <li>
                    Open the key you just created, scroll to "API restrictions", and select "Generative Language API" from the list. (This option only shows up after you complete step 1 above.)
                  </li>
                </ol>
                <p>
                  Only using Gemini for chat? Grab a free key at{' '}
                  <a
                    href="https://aistudio.google.com/apikey"
                    target="_blank"
                    rel="noreferrer"
                    className={`underline ${darkMode ? 'text-blue-300 hover:text-blue-200' : 'text-blue-600 hover:text-blue-700'}`}
                  >
                    Google AI Studio
                  </a>{' '}
                  instead. It's one click and ties to your personal Google account.
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Connect your Google apps (optional) ─────────────── */}
      {(googleOAuthAvailable || googleConnected) && (
        <div className="mb-5">
          <p className={sectionDivider}>Connect your Google apps (optional)</p>
          {googleConnected ? (
            <div
              data-testid="google-already-connected"
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-green-500/10 border border-green-500/30 text-green-400 text-sm"
            >
              <Icon name="check_circle" size={16} />
              <span>Already connected</span>
            </div>
          ) : (
            <>
              <p className={`text-xs mt-1 mb-3 ${subtextCls}`}>
                This connects Drive, Calendar, and Gmail. It does not change which AI runs your chat.
              </p>
              <GoogleAccountSetupCard darkMode={darkMode} subtextCls={subtextCls} stepIndex={stepIndex} />
            </>
          )}
        </div>
      )}

      {/* ── Confluence ────────────────────────────────────── */}
      <div className="mb-2">
        <p className={sectionDivider}>Confluence</p>
        {atlassianConnected ? (
          <div
            data-testid="atlassian-already-connected"
            className="inline-flex items-center gap-1.5 mt-2 px-3 py-1.5 rounded-full text-sm font-medium bg-green-500/15 text-green-400 border border-green-500/30"
          >
            <span>Already connected: Jira</span>
          </div>
        ) : (
          <AtlassianSetupCard darkMode={darkMode} inputCls={inputCls} subtextCls={subtextCls} stepIndex={stepIndex} />
        )}
      </div>

      {/* ── GitHub ────────────────────────────────────────── */}
      <GithubSetupCard darkMode={darkMode} inputCls={inputCls} subtextCls={subtextCls} stepIndex={stepIndex} sectionDivider={sectionDivider} />
    </div>
  )
}

export function AtlassianSetupCard({
  darkMode,
  inputCls,
  subtextCls,
  stepIndex,
}: {
  darkMode: boolean
  inputCls: string
  subtextCls: string
  stepIndex?: number
}) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [site, setSite] = useState('')
  const [email, setEmail] = useState('')
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<'idle' | 'connecting' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [oauthAvailable, setOauthAvailable] = useState(false)
  const [forceTokenForm, setForceTokenForm] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('atlassian_connected') === 'true') {
      window.history.replaceState({}, '', '/')
    }
    api.get<{ connected: boolean }>('/atlassian/status')
      .then((data) => setConnected(data.connected))
      .catch(() => setConnected(false))
  }, [])

  const handleExpand = () => {
    setExpanded(true)
    api.get<{ site?: string; email?: string; oauth_available?: boolean }>('/atlassian/defaults')
      .then((data) => {
        if (data.site) setSite(data.site)
        if (data.oauth_available) setOauthAvailable(true)
      })
      .catch((e) => reportError('load atlassian defaults failed', e))
  }

  const handleConnect = () => {
    setStatus('connecting')
    setError(null)
    api.post('/atlassian/connect', { site, email, api_token: token })
      .then(() => setStatus('done'))
      .catch((e: Error) => {
        setStatus('error')
        setError(e?.message ?? 'Connection failed')
      })
  }

  if (connected === null || connected === true) return null

  const cardBase = `mt-4 p-3 rounded-lg border ${darkMode ? 'border-slate-700' : 'border-gray-200'}`

  if (!expanded) {
    return (
      <div data-testid="onboarding-atlassian-card" className={cardBase}>
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">Connect Jira & Confluence (optional)</p>
          <button
            onClick={handleExpand}
            className="text-xs text-blue-500 hover:text-blue-400"
            data-testid="onboarding-atlassian-setup"
          >
            Set up
          </button>
        </div>
      </div>
    )
  }

  if (oauthAvailable && !forceTokenForm) {
    return (
      <div data-testid="onboarding-atlassian-card" className={cardBase}>
        <p className="text-sm font-semibold mb-3">Connect Jira & Confluence (optional)</p>
        <p className={`text-xs mb-3 ${subtextCls}`}>
          One click. Atlassian will ask for your permission, then bring you back here.
        </p>
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              if (stepIndex !== undefined) api.patch('/settings', { onboarding_step: stepIndex }).catch(() => {})
              window.location.href = '/api/atlassian/auth'
            }}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
            data-testid="onboarding-atlassian-oauth"
          >
            Connect Atlassian
          </button>
          <button
            onClick={() => setForceTokenForm(true)}
            className={`text-xs underline ${subtextCls} hover:opacity-80`}
            data-testid="onboarding-atlassian-use-token"
          >
            Use a token instead
          </button>
          <button
            onClick={() => setExpanded(false)}
            className={`text-sm ml-auto ${subtextCls} hover:opacity-80`}
            data-testid="onboarding-atlassian-skip"
          >
            Skip for now
          </button>
        </div>
      </div>
    )
  }

  return (
    <div data-testid="onboarding-atlassian-card" className={cardBase}>
      <p className="text-sm font-semibold mb-3">Connect Jira & Confluence (optional)</p>
      <div className="space-y-2">
        <input
          type="text"
          value={site}
          onChange={(e) => setSite(e.target.value)}
          placeholder="https://company.atlassian.net"
          className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
          data-testid="onboarding-atlassian-site"
        />
        <input
          type="text"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
          data-testid="onboarding-atlassian-email"
        />
        <div>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="API token"
            className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
            data-testid="onboarding-atlassian-token"
          />
          <p className={`text-xs mt-1 ${subtextCls}`}>
            <a
              href="https://id.atlassian.com/manage-profile/security/api-tokens"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              Get a token at id.atlassian.com
            </a>
          </p>
        </div>
        {error && <p className="text-xs text-red-500">{error}</p>}
        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={handleConnect}
            disabled={status === 'connecting' || status === 'done'}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
            data-testid="onboarding-atlassian-connect"
          >
            {status === 'done' ? 'Connected!' : status === 'connecting' ? 'Connecting...' : 'Connect Jira & Confluence'}
          </button>
          <button
            onClick={() => setExpanded(false)}
            className={`text-sm ${subtextCls} hover:opacity-80`}
            data-testid="onboarding-atlassian-skip"
          >
            Skip for now
          </button>
        </div>
      </div>
    </div>
  )
}

export function GithubSetupCard({
  darkMode,
  inputCls,
  subtextCls,
  stepIndex,
  sectionDivider,
}: {
  darkMode: boolean
  inputCls: string
  subtextCls: string
  stepIndex?: number
  sectionDivider?: string
}) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [ownerRepo, setOwnerRepo] = useState('')
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<'idle' | 'connecting' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [oauthAvailable, setOauthAvailable] = useState(false)
  const [forceTokenForm, setForceTokenForm] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('oauth_connected') === 'true') {
      window.history.replaceState({}, '', '/')
    }
    api.get<{ connected: boolean }>('/github/status')
      .then((data) => setConnected(data.connected))
      .catch(() => setConnected(false))
    api.get<{ oauth_available?: boolean }>('/github/defaults')
      .then((data) => setOauthAvailable(Boolean(data.oauth_available)))
      .catch(() => setOauthAvailable(false))
  }, [])

  const handleConnect = () => {
    setStatus('connecting')
    setError(null)
    api.post('/github/connect', { repo: ownerRepo, token })
      .then(() => { setStatus('done'); setConnected(true) })
      .catch((e: Error) => {
        setStatus('error')
        setError(e?.message ?? 'Connection failed')
      })
  }

  if (connected === null || connected === true) return null

  const cardBase = `mt-4 p-3 rounded-lg border ${darkMode ? 'border-slate-700' : 'border-gray-200'}`
  const header = sectionDivider ? <p className={sectionDivider}>GitHub</p> : null

  if (!expanded) {
    return (
      <>
        {header}
      <div data-testid="onboarding-github-card" className={cardBase}>
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">Connect GitHub (optional)</p>
          <button
            onClick={() => setExpanded(true)}
            className="text-xs text-blue-500 hover:text-blue-400"
            data-testid="onboarding-github-setup"
          >
            Set up
          </button>
        </div>
      </div>
      </>
    )
  }

  if (oauthAvailable && !forceTokenForm) {
    return (
      <>
        {header}
      <div data-testid="onboarding-github-card" className={cardBase}>
        <p className="text-sm font-semibold mb-3">Connect GitHub (optional)</p>
        <p className={`text-xs mb-3 ${subtextCls}`}>
          One click. GitHub will ask for your permission, then you'll pick which repo to track.
        </p>
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              if (stepIndex !== undefined) api.patch('/settings', { onboarding_step: stepIndex }).catch(() => {})
              window.location.href = '/api/github/auth'
            }}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition-colors"
            data-testid="onboarding-github-oauth"
          >
            Connect GitHub
          </button>
          <button
            onClick={() => setForceTokenForm(true)}
            className={`text-xs underline ${subtextCls} hover:opacity-80`}
            data-testid="onboarding-github-use-token"
          >
            Use a token instead
          </button>
          <button
            onClick={() => setExpanded(false)}
            className={`text-sm ml-auto ${subtextCls} hover:opacity-80`}
            data-testid="onboarding-github-skip"
          >
            Skip for now
          </button>
        </div>
      </div>
      </>
    )
  }

  return (
    <>
      {header}
    <div data-testid="onboarding-github-card" className={cardBase}>
      <p className="text-sm font-semibold mb-3">Connect GitHub (optional)</p>
      <div className="space-y-2">
        <p className={`text-xs ${subtextCls}`}>
          <a
            href="https://github.com/settings/tokens/new?scopes=repo,read:user&description=yourOS"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            Create a GitHub token
          </a>
        </p>
        <input
          type="text"
          value={ownerRepo}
          onChange={(e) => setOwnerRepo(e.target.value)}
          placeholder="owner/repo (e.g. acme/website)"
          className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
          data-testid="onboarding-github-repo"
        />
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Personal access token"
          className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 transition-colors ${inputCls}`}
          data-testid="onboarding-github-token"
        />
        {error && <p className="text-xs text-red-500">{error}</p>}
        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={handleConnect}
            disabled={status === 'connecting' || status === 'done'}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
            data-testid="onboarding-github-connect"
          >
            {status === 'done' ? 'Connected!' : status === 'connecting' ? 'Connecting...' : 'Connect GitHub'}
          </button>
          <button
            onClick={() => setExpanded(false)}
            className={`text-sm ${subtextCls} hover:opacity-80`}
            data-testid="onboarding-github-skip"
          >
            Skip for now
          </button>
        </div>
      </div>
    </div>
    </>
  )
}

function ReadyStep({
  userName,
  osName,
  darkMode,
  provider,
  subtextCls,
  cardCls,
  detectedProvider,
}: {
  userName: string
  osName: string
  darkMode: boolean
  provider: string
  subtextCls: string
  cardCls: string
  detectedProvider?: string | null
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
            {osName || 'yourOS'}
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
        {detectedProvider && (
          <div className="flex items-center justify-between">
            <span className={`text-sm ${subtextCls}`}>Connected via</span>
            <span className="text-sm font-medium" data-testid="summary-connected-via">{detectedProvider}</span>
          </div>
        )}
      </div>
    </div>
  )
}


