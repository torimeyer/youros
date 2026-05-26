import { useState, useEffect } from 'react'
import Icon from '../../components/Icon'
import { api } from '../../lib/api'
import { useAppStore } from '../../stores/app'

interface SSOConfig {
  configured: boolean
  provider?: string
  issuer_url?: string
}

interface SigningKeyStatus {
  exists: boolean
  fingerprint: string | null
  generated_at: string | null
}

export default function AdminSecurity() {
  const darkMode = useAppStore((s) => s.darkMode)

  // SSO state
  const [sso, setSSO] = useState<SSOConfig>({ configured: false })
  const [ssoProvider, setSSOProvider] = useState('okta')
  const [ssoIssuer, setSSOIssuer] = useState('')
  const [ssoClientId, setSSOClientId] = useState('')
  const [ssoClientSecret, setSSOClientSecret] = useState('')
  const [ssoSaving, setSSOSaving] = useState(false)

  // API keys state
  const [apiKeyProviders, setApiKeyProviders] = useState<string[]>([])
  const [apiKeyProvider, setApiKeyProvider] = useState('anthropic')
  const [apiKeyValue, setApiKeyValue] = useState('')
  const [apiKeySaving, setApiKeySaving] = useState(false)

  // Signing key state
  const [signingKey, setSigningKey] = useState<SigningKeyStatus>({ exists: false, fingerprint: null, generated_at: null })
  const [signingBusy, setSigningBusy] = useState(false)
  const [revokeConfirm, setRevokeConfirm] = useState(false)

  const [loading, setLoading] = useState(true)

  const cardCls = `rounded-xl border p-6 ${darkMode ? 'bg-white dark:bg-slate-900/50 border-slate-200 dark:border-slate-800' : 'bg-white border-slate-200'}`
  const labelCls = darkMode ? 'text-slate-600 dark:text-slate-400' : 'text-slate-500'
  const valueCls = darkMode ? 'text-white' : 'text-slate-900'
  const headingCls = darkMode ? 'text-slate-800 dark:text-slate-200' : 'text-slate-800'
  const inputCls = darkMode ? 'bg-slate-100 dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-white' : 'bg-white border-slate-300 text-slate-900'

  useEffect(() => {
    Promise.all([
      api.get<SSOConfig>('/enterprise/sso'),
      api.get<{ providers: string[] }>('/enterprise/api-keys'),
      api.get<SigningKeyStatus>('/org/signing-key'),
    ])
      .then(([ssoRes, keysRes, sigRes]) => {
        setSSO(ssoRes)
        setApiKeyProviders(keysRes.providers || [])
        setSigningKey(sigRes)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleSaveSSO = async () => {
    setSSOSaving(true)
    try {
      await api.post('/enterprise/sso', {
        provider: ssoProvider,
        issuer_url: ssoIssuer,
        client_id: ssoClientId,
        client_secret: ssoClientSecret,
      })
      setSSO({ configured: true, provider: ssoProvider, issuer_url: ssoIssuer })
      setSSOClientId('')
      setSSOClientSecret('')
    } catch {
      // ignore
    } finally {
      setSSOSaving(false)
    }
  }

  const handleRemoveSSO = async () => {
    try {
      await api.delete('/enterprise/sso')
      setSSO({ configured: false })
    } catch {
      // ignore
    }
  }

  const handleAddApiKey = async () => {
    if (!apiKeyValue.trim()) return
    setApiKeySaving(true)
    try {
      await api.post('/enterprise/api-keys', { provider: apiKeyProvider, key: apiKeyValue })
      setApiKeyValue('')
      const res = await api.get<{ providers: string[] }>('/enterprise/api-keys')
      setApiKeyProviders(res.providers || [])
    } catch {
      // ignore
    } finally {
      setApiKeySaving(false)
    }
  }

  const handleRemoveApiKey = async (provider: string) => {
    try {
      await api.delete(`/enterprise/api-keys/${encodeURIComponent(provider)}`)
      const res = await api.get<{ providers: string[] }>('/enterprise/api-keys')
      setApiKeyProviders(res.providers || [])
    } catch {
      // ignore
    }
  }

  const handleGenerateKey = async () => {
    setSigningBusy(true)
    try {
      const res = await api.post<SigningKeyStatus>('/org/signing-key/generate', {})
      setSigningKey({ exists: true, fingerprint: res.fingerprint, generated_at: res.generated_at })
    } catch {
      // ignore
    } finally {
      setSigningBusy(false)
    }
  }

  const handleRevokeKey = async () => {
    setSigningBusy(true)
    try {
      await api.post('/org/signing-key/revoke', {})
      setSigningKey({ exists: false, fingerprint: null, generated_at: null })
      setRevokeConfirm(false)
    } catch {
      // ignore
    } finally {
      setSigningBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Icon name="hourglass_empty" className="animate-spin text-slate-500 text-2xl" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* SSO */}
      <div className={cardCls}>
        <div className="flex items-center justify-between mb-4">
          <h2 className={`text-base font-semibold ${headingCls}`}>Single sign-on (SSO)</h2>
          {sso.configured && (
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-green-500/20 text-green-600 dark:text-green-400">
              Active
            </span>
          )}
        </div>

        {sso.configured ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className={`text-sm ${labelCls}`}>Provider</span>
              <span className={`text-sm font-medium ${valueCls}`}>{sso.provider}</span>
            </div>
            {sso.issuer_url && (
              <div className="flex items-center justify-between">
                <span className={`text-sm ${labelCls}`}>Issuer URL</span>
                <span className={`text-sm font-mono ${valueCls}`}>{sso.issuer_url}</span>
              </div>
            )}
            <button
              onClick={handleRemoveSSO}
              className="text-sm text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition-colors"
            >
              Remove SSO configuration
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className={`text-sm ${labelCls}`}>
              Connect an identity provider so your team can sign in with their company credentials.
            </p>
            <div>
              <label className={`block text-sm font-medium mb-1 ${labelCls}`}>Provider</label>
              <select
                value={ssoProvider}
                onChange={(e) => setSSOProvider(e.target.value)}
                className={`w-full border rounded-lg px-3 py-2 text-sm ${inputCls}`}
              >
                <option value="okta">Okta</option>
                <option value="azure">Azure AD</option>
                <option value="google">Google Workspace</option>
                <option value="auth0">Auth0</option>
              </select>
            </div>
            <div>
              <label className={`block text-sm font-medium mb-1 ${labelCls}`}>Issuer URL</label>
              <input
                type="url"
                value={ssoIssuer}
                onChange={(e) => setSSOIssuer(e.target.value)}
                placeholder="https://your-org.okta.com"
                className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={`block text-sm font-medium mb-1 ${labelCls}`}>Client ID</label>
                <input
                  type="text"
                  value={ssoClientId}
                  onChange={(e) => setSSOClientId(e.target.value)}
                  className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
                />
              </div>
              <div>
                <label className={`block text-sm font-medium mb-1 ${labelCls}`}>Client secret</label>
                <input
                  type="password"
                  value={ssoClientSecret}
                  onChange={(e) => setSSOClientSecret(e.target.value)}
                  className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
                />
              </div>
            </div>
            <button
              onClick={handleSaveSSO}
              disabled={ssoSaving || !ssoIssuer || !ssoClientId || !ssoClientSecret}
              className="px-4 py-2 team-bg hover:opacity-90 disabled:opacity-50 text-white font-semibold text-sm rounded-lg transition-opacity"
            >
              {ssoSaving ? 'Saving...' : 'Save SSO configuration'}
            </button>
          </div>
        )}
      </div>

      {/* API Keys */}
      <div className={cardCls}>
        <h2 className={`text-base font-semibold mb-2 ${headingCls}`}>Org API keys</h2>
        <p className={`text-sm mb-4 ${labelCls}`}>
          Set org-level API keys so your team can use AI features without managing their own keys.
          Key values are never shown after saving.
        </p>

        {apiKeyProviders.length > 0 && (
          <div className="mb-4 space-y-2">
            {apiKeyProviders.map((p) => (
              <div key={p} className={`flex items-center justify-between px-3 py-2 rounded-lg ${darkMode ? 'bg-slate-100 dark:bg-slate-800/50' : 'bg-slate-50'}`}>
                <div className="flex items-center gap-2">
                  <Icon name="vpn_key" className="text-green-600 dark:text-green-400" size={16} />
                  <span className={`text-sm font-medium ${valueCls}`}>{p.charAt(0).toUpperCase() + p.slice(1)}</span>
                </div>
                <button
                  onClick={() => handleRemoveApiKey(p)}
                  className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition-colors"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex gap-2">
          <select
            value={apiKeyProvider}
            onChange={(e) => setApiKeyProvider(e.target.value)}
            className={`border rounded-lg px-2 py-2 text-sm ${inputCls}`}
          >
            <option value="anthropic">Anthropic</option>
            <option value="gemini">Google Gemini</option>
          </select>
          <input
            type="password"
            value={apiKeyValue}
            onChange={(e) => setApiKeyValue(e.target.value)}
            placeholder="Paste API key"
            className={`flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500 ${inputCls}`}
          />
          <button
            onClick={handleAddApiKey}
            disabled={apiKeySaving || !apiKeyValue.trim()}
            className="px-4 py-2 team-bg hover:opacity-90 disabled:opacity-50 text-white font-semibold text-sm rounded-lg transition-opacity"
          >
            {apiKeySaving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Org Signing Key */}
      <div className={cardCls} data-testid="signing-key-card">
        <div className="flex items-center justify-between mb-4">
          <h2 className={`text-base font-semibold ${headingCls}`}>Org signing key</h2>
          <span
            data-testid="signing-key-status-badge"
            className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
              signingKey.exists
                ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                : 'bg-slate-500/20 text-slate-600 dark:text-slate-400'
            }`}
          >
            {signingKey.exists ? 'Active' : 'Not set'}
          </span>
        </div>

        <p className={`text-sm mb-4 ${labelCls}`}>
          A signing key lets your org put a tamper-evident stamp on catalog items.
          When a key is active, every item you publish gets signed automatically.
          Anyone on your team can verify that an item came from your org and has not been changed.
        </p>

        {signingKey.exists && signingKey.fingerprint && (
          <div className="mb-4 space-y-2">
            <div className="flex items-start justify-between gap-4">
              <span className={`text-sm ${labelCls}`}>Fingerprint</span>
              <span className={`text-xs font-mono break-all ${valueCls}`} data-testid="signing-key-fingerprint">
                {signingKey.fingerprint}
              </span>
            </div>
            {signingKey.generated_at && (
              <div className="flex items-center justify-between">
                <span className={`text-sm ${labelCls}`}>Generated</span>
                <span className={`text-sm ${valueCls}`}>
                  {new Date(signingKey.generated_at).toLocaleDateString()}
                </span>
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handleGenerateKey}
            disabled={signingBusy}
            data-testid="generate-signing-key-btn"
            className="px-4 py-2 team-bg hover:opacity-90 disabled:opacity-50 text-white font-semibold text-sm rounded-lg transition-opacity"
          >
            {signingBusy ? 'Working...' : signingKey.exists ? 'Rotate key' : 'Generate key'}
          </button>

          {signingKey.exists && !revokeConfirm && (
            <button
              onClick={() => setRevokeConfirm(true)}
              data-testid="revoke-signing-key-btn"
              className="text-sm text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition-colors"
            >
              Revoke key
            </button>
          )}

          {revokeConfirm && (
            <div className="flex items-center gap-2">
              <span className={`text-sm ${labelCls}`}>Remove the key? Signed items can no longer be verified.</span>
              <button
                onClick={handleRevokeKey}
                disabled={signingBusy}
                data-testid="revoke-confirm-btn"
                className="text-sm text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition-colors font-semibold"
              >
                Yes, remove
              </button>
              <button
                onClick={() => setRevokeConfirm(false)}
                className={`text-sm ${labelCls} hover:text-slate-700 dark:hover:text-slate-300 transition-colors`}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
