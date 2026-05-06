import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { useAppStore, TEAM_MODE_VISIBLE } from '../stores/app'
import Icon from './Icon'
import { api } from '../lib/api'
import { AGENT_MARKETPLACE, PERSONA_ICONS, type MarketplaceCategory } from '../data/agentMarketplace'
import {
  OrgNameStep,
  AdminEmailStep,
  InviteTeamStep,
  GuardrailsStep,
  TeamReadyStep,
  finishTeamOnboarding,
  type TeamOnboardingData,
} from './TeamOnboardingSteps'

const PERSONAL_STEPS = ['Fork', 'Welcome', 'You', 'Name', 'Profile', 'Customize', 'Theme', 'EnhanceClaude', 'Connect', 'Ready'] as const
const PERSONAL_STEPS_NO_FORK = ['Welcome', 'You', 'Name', 'Profile', 'Customize', 'Theme', 'EnhanceClaude', 'Connect', 'Ready'] as const
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
  const [selectedPersonaId, setSelectedPersonaId] = useState<string | null>(null)
  const [otherSelected, setOtherSelected] = useState(false)

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
    api.get<{ claude_code?: boolean; anthropic_key?: boolean; gemini_key?: boolean; vertex_ai?: boolean; bedrock?: boolean }>('/providers/detect')
      .then((data) => {
        if (data.claude_code) setDetectedProvider('Claude Code')
        else if (data.anthropic_key) setDetectedProvider('Anthropic')
        else if (data.gemini_key) setDetectedProvider('Gemini')
        else if (data.vertex_ai) setDetectedProvider('Vertex AI')
        else if (data.bedrock) setDetectedProvider('AWS Bedrock')
      })
      .catch((e) => console.error('provider detection failed:', e))
  }, [])

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

  const connectIdx = (STEPS as readonly string[]).indexOf('Connect')
  const next = () => setStepIndex((i) => {
    let n = Math.min(i + 1, STEPS.length - 1)
    if (detectedProvider !== null && n === connectIdx) n = Math.min(n + 1, STEPS.length - 1)
    return n
  })
  const back = () => setStepIndex((i) => Math.max(i - 1, 0))

  const handlePersonaPick = (category: MarketplaceCategory) => {
    setSelectedPersonaId(category.id)
    setProfileRole(category.category)
    setOtherSelected(false)
    // Install only the templates for the persona the user picked.
    // Other persona templates stay in the marketplace and are not installed.
    // The backend install-persona endpoint updates the disk store so these
    // show up as installed=true. The Templates tab reads them back via
    // /agents/persona-templates so we do NOT seed customAgentTemplates here.
    // customAgentTemplates is reserved for user-created templates only, so
    // marketplace picks never show up with a "custom" badge.
    api.post('/agents/pm-templates/install-persona', { persona_id: category.id }).catch((e) => console.error('persona install failed:', e))
    // Clear any leftover marketplace entries that older builds saved into
    // customAgentTemplates so existing users stop seeing duplicate cards.
    setCustomAgentTemplates([])
  }

  const handleOtherPick = () => {
    setSelectedPersonaId(null)
    setOtherSelected(true)
    setProfileRole('')
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
      api.patch('/settings', settings).catch((e) => console.error('settings patch failed:', e))
      // Reset the "Finished" baseline so agents from before onboarding
      // do not immediately show a stale count in the sidebar.
      setAgentsLastViewed(new Date().toISOString())
      // Navigate home BEFORE flipping onboarded. Once onboarded=true the
      // wizard unmounts and BrowserRouter mounts at whatever URL is current.
      goHome()
      setOnboarded(true)
      return
    }
    // Personal finish
    const settings: Record<string, unknown> = {
      os_name: osName,
      instance_name: instanceName || 'myOS',
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
    api.patch('/settings', settings).catch((e) => console.error('settings patch failed:', e))
    // Reset the "Finished" baseline so agents from before onboarding
    // do not immediately show a stale count in the sidebar.
    setAgentsLastViewed(new Date().toISOString())
    // Navigate home BEFORE flipping onboarded (see goHome comment above).
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
        .catch((e) => console.error('save key failed:', e))
    }
  }

  const skip = () => next()

  // Global Enter handler: advance (or finish) when Enter is pressed on any non-input element.
  // Input/textarea elements handle Enter themselves so we skip them here to avoid double-fires.
  const handleGlobalKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Enter') return
    const tag = (e.target as HTMLElement).tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON') return
    // Fork step: no action (user must click a card)
    if (step === 'Fork') return
    // TeamReady and Ready both finish.
    if (step === 'TeamReady') { finish(); return }
    if (step === 'Ready') { finish(); return }
    next()
  }

  // Theme: purely local state. Zero store interaction during the wizard.
  // The store is synced ONLY when the user finishes onboarding.
  const themeIdx = (STEPS as readonly string[]).indexOf('Theme')
  const [pickedDark, setPickedDark] = useState(darkMode)
  const effectiveDark = themeIdx >= 0 && stepIndex >= themeIdx ? pickedDark : false
  const pickedDarkRef = useRef(false)

  const handleDarkModeChoice = (wantDark: boolean) => {
    setPickedDark(wantDark)
    pickedDarkRef.current = wantDark
  }

  // Keep DOM in sync with the wizard's theme choice
  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', effectiveDark ? 'dark' : 'light')
    document.body.style.backgroundColor = effectiveDark ? '#020617' : '#f9fafb'
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
        backgroundColor: effectiveDark ? '#020617' : '#f9fafb',
        color: effectiveDark ? '#ffffff' : '#0f172a',
        transition: 'background-color 0.3s, color 0.3s',
      }}
      data-testid="onboarding-wizard"
      onKeyDown={handleGlobalKeyDown}
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
          {step === 'Fork' && <ForkStep onChoose={handleForkChoice} subtextCls={subtextCls} cardCls={cardCls} darkMode={effectiveDark} />}
          {step === 'Welcome' && <WelcomeStep subtextCls={subtextCls} />}
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
              <h2 className="text-2xl font-bold mb-2">Tell {osName} about you</h2>
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
          {step === 'Customize' && (
            <CustomizeStep
              selectedPersonaId={selectedPersonaId}
              subtextCls={subtextCls}
              cardCls={cardCls}
            />
          )}
          {step === 'Theme' && (
            <ThemeStep darkMode={pickedDark} onChoose={handleDarkModeChoice} subtextCls={subtextCls} />
          )}
          {step === 'EnhanceClaude' && (
            <div data-testid="step-enhance-claude">
              <h2 className="text-2xl font-bold mb-2">Want myOS to enhance Claude Code?</h2>
              <p className={`mb-6 ${subtextCls}`}>
                myOS can plug into Claude Code so it knows what you're working on. This is optional and you can change it later in Settings.
              </p>
              <div className="space-y-3">
                <button
                  onClick={async () => {
                    try {
                      await api.post('/onboarding/enable-myos-hooks', {})
                    } catch (e) {
                      console.error('enable myos hooks failed:', e)
                    }
                    setStepIndex(i => Math.min(i + 1, STEPS.length - 1))
                  }}
                  className={`w-full flex items-center gap-4 p-5 rounded-xl border ${cardCls} hover:border-blue-500 hover:bg-blue-500/10 text-left transition-all`}
                >
                  <span className="font-medium">Yes, enhance Claude Code with myOS</span>
                </button>
                <button
                  onClick={() => setStepIndex(i => Math.min(i + 1, STEPS.length - 1))}
                  className={`w-full flex items-center gap-4 p-5 rounded-xl border ${cardCls} hover:border-slate-400 text-left transition-all`}
                >
                  <span className={`font-medium ${subtextCls}`}>No thanks, maybe later</span>
                </button>
              </div>
            </div>
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
            <OrgNameStep orgName={teamOrgName} setOrgName={setTeamOrgName} inputCls={inputCls} subtextCls={subtextCls} />
          )}
          {step === 'AdminEmail' && (
            <AdminEmailStep adminEmail={teamAdminEmail} setAdminEmail={setTeamAdminEmail} inputCls={inputCls} subtextCls={subtextCls} />
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
      <h1 className="text-3xl font-bold mb-2">Welcome!</h1>
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
            <p className={`text-sm ${subtextCls}`}>A personal OS for managing your tasks, agents, and tools.</p>
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
}

function CustomizeStep({
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
    if (!intentId) return
    setLoading(true)
    setError(null)
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout> | undefined
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => reject(new Error('timeout')), 10_000)
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
      .catch(() => {
        if (cancelled) return
        setStarterPack([])
        setError("Couldn't load suggestions. Skip this step or try again.")
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
      {!loading && !error && starterPack.length === 0 && !selectedPersonaId && (
        <p className={`text-sm ${subtextCls}`} data-testid="customize-no-persona">
          Pick a profile on the previous step to see suggested agents here.
        </p>
      )}
    </div>
  )
}

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
      <p className={`${subtextCls} text-sm leading-relaxed mt-4`} data-testid="onboarding-files-location-note">
        Your files live in ~/.myos/files. You can change this in Settings.
      </p>
      <p className={`${subtextCls} text-xs leading-relaxed mt-3`}>
        <a
          href="/privacy"
          target="_blank"
          rel="noopener noreferrer"
          className="underline opacity-60 hover:opacity-100"
          data-testid="onboarding-privacy-link"
        >
          Privacy policy
        </a>
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
}) {
  const [googleOAuthAvailable, setGoogleOAuthAvailable] = useState(false)

  useEffect(() => {
    api.get<{ google_oauth_available?: boolean }>('/secrets/key-status')
      .then((data) => setGoogleOAuthAvailable(data.google_oauth_available ?? false))
      .catch((e) => console.error('key status check failed:', e))
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

      {/* Google Workspace OAuth — secondary, only when server-side OAuth is configured */}
      {googleOAuthAvailable && (
        <div className={`mb-4 pb-4 border-b ${darkMode ? 'border-slate-700' : 'border-gray-200'}`}>
          <button
            onClick={() => { window.location.href = '/api/auth/google' }}
            className={`w-full px-4 py-2.5 border rounded-lg text-sm transition-colors flex items-center gap-2 ${
              darkMode
                ? 'bg-slate-800/50 border-slate-700 text-slate-300 hover:border-blue-500'
                : 'bg-gray-50 border-gray-200 text-slate-600 hover:border-blue-500'
            }`}
            data-testid="connect-google-workspace"
          >
            <Icon name="folder_shared" size={18} />
            Connect Google Workspace (Drive, Calendar, Gmail) — one click sign-in.
          </button>
        </div>
      )}

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

      {/* Gemini: recommend Cloud Console first, AI Studio as fallback */}
      {selectedProvider === 'Google Gemini' && (
        <div
          className={`mb-3 p-3 rounded-lg text-xs space-y-2 border bg-gradient-to-r from-blue-500/10 to-cyan-500/10 border-blue-500/30 ${
            darkMode ? 'text-slate-200' : 'text-slate-700'
          }`}
          data-testid="gemini-key-help"
        >
          <p>
            <span className={darkMode ? 'text-white font-medium' : 'text-slate-900 font-medium'}>Recommended.</span>{' '}
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
              Open the key you just created, scroll to "API restrictions", and select "Generative Language API" from the list. (This option only shows up after you complete step 1 above.)
            </li>
          </ol>
          <p>
            <span className={darkMode ? 'text-white font-medium' : 'text-slate-900 font-medium'}>Chat only.</span>{' '}
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

      {/* API key paste */}
      <div className="flex gap-2">
        <input
          type="password"
          value={apiKey}
          onChange={(e) => onApiKeyChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { onSaveKey(); onNext(); } }}
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

      <AtlassianSetupCard darkMode={darkMode} inputCls={inputCls} subtextCls={subtextCls} />
      <GithubSetupCard darkMode={darkMode} inputCls={inputCls} subtextCls={subtextCls} />
    </div>
  )
}

function AtlassianSetupCard({
  darkMode,
  inputCls,
  subtextCls,
}: {
  darkMode: boolean
  inputCls: string
  subtextCls: string
}) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [site, setSite] = useState('')
  const [email, setEmail] = useState('')
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<'idle' | 'connecting' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.get<{ connected: boolean }>('/atlassian/status')
      .then((data) => setConnected(data.connected))
      .catch(() => setConnected(false))
  }, [])

  const handleExpand = () => {
    setExpanded(true)
    api.get<{ site?: string; email?: string }>('/atlassian/defaults')
      .then((data) => {
        if (data.site) setSite(data.site)
      })
      .catch((e) => console.error('load atlassian defaults failed:', e))
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

function GithubSetupCard({
  darkMode,
  inputCls,
  subtextCls,
}: {
  darkMode: boolean
  inputCls: string
  subtextCls: string
}) {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [ownerRepo, setOwnerRepo] = useState('')
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<'idle' | 'connecting' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.get<{ connected: boolean }>('/github/status')
      .then((data) => setConnected(data.connected))
      .catch(() => setConnected(false))
  }, [])

  const handleConnect = () => {
    setStatus('connecting')
    setError(null)
    api.post('/github/connect', { repo: ownerRepo, token })
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
    )
  }

  return (
    <div data-testid="onboarding-github-card" className={cardBase}>
      <p className="text-sm font-semibold mb-3">Connect GitHub (optional)</p>
      <div className="space-y-2">
        <p className={`text-xs ${subtextCls}`}>
          <a
            href="https://github.com/settings/tokens/new?scopes=repo,read:user&description=myOS"
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


