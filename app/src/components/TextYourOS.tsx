import { useState, useEffect } from 'react'
import { api } from '../lib/api'

interface TextBridgeStatus {
  enabled: boolean
  trusted_contacts: string[]
  confirm_commands: string | null
}

export default function TextYourOS() {
  const [status, setStatus] = useState<TextBridgeStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [newContact, setNewContact] = useState('')

  useEffect(() => {
    api.get<TextBridgeStatus>('/text-bridge/status')
      .then(setStatus)
      .catch(() => setStatus({ enabled: false, trusted_contacts: [], confirm_commands: null }))
      .finally(() => setLoading(false))
  }, [])

  const patch = async (updates: Partial<TextBridgeStatus>) => {
    setSaving(true)
    try {
      const updated = await api.patch<TextBridgeStatus>('/text-bridge/config', updates)
      setStatus(updated)
    } catch {
      // ignore
    } finally {
      setSaving(false)
    }
  }

  const toggleEnabled = () => {
    if (!status) return
    patch({ enabled: !status.enabled })
  }

  const addContact = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = newContact.trim()
    if (!trimmed || !status) return
    const contacts = [...(status.trusted_contacts ?? []), trimmed]
    await patch({ trusted_contacts: contacts })
    setNewContact('')
  }

  const removeContact = (contact: string) => {
    if (!status) return
    patch({ trusted_contacts: status.trusted_contacts.filter(c => c !== contact) })
  }

  const toggleConfirm = () => {
    if (!status) return
    const next = status.confirm_commands === 'always' ? 'never' : 'always'
    patch({ confirm_commands: next })
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <div className="w-2.5 h-2.5 rounded-full bg-slate-600 animate-pulse" />
        Checking…
      </div>
    )
  }

  return (
    <div data-testid="text-youros-card" className="space-y-4">
      {/* Live / off toggle */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-slate-800 dark:text-slate-200">
            Text yourOS from your phone
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            {status?.enabled ? 'Listening for messages from trusted contacts' : 'Texting is off'}
          </p>
        </div>
        <button
          onClick={toggleEnabled}
          disabled={saving}
          data-testid="text-youros-toggle"
          className={`relative w-10 h-6 rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
            status?.enabled ? 'bg-green-500' : 'bg-slate-300 dark:bg-slate-600'
          }`}
          aria-label={status?.enabled ? 'Turn off' : 'Turn on'}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
              status?.enabled ? 'translate-x-4' : 'translate-x-0'
            }`}
          />
        </button>
      </div>

      {/* Trusted contacts */}
      <div>
        <p className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-2">
          Trusted contacts
        </p>
        {status?.trusted_contacts?.length === 0 && (
          <p className="text-xs text-slate-400 dark:text-slate-500 mb-2">
            No contacts yet. Add a phone number or email so yourOS knows who to listen to.
          </p>
        )}
        <ul className="space-y-1 mb-2">
          {status?.trusted_contacts?.map(c => (
            <li key={c} className="flex items-center gap-2">
              <span className="text-sm text-slate-700 dark:text-slate-300 flex-1 truncate">{c}</span>
              <button
                onClick={() => removeContact(c)}
                data-testid={`remove-contact-${c}`}
                className="text-xs text-slate-400 hover:text-red-500 transition-colors"
                aria-label={`Remove ${c}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
        <form onSubmit={addContact} className="flex gap-2">
          <input
            type="text"
            value={newContact}
            onChange={e => setNewContact(e.target.value)}
            placeholder="+1 555 000 0000 or email"
            className="flex-1 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-green-500/50"
            data-testid="text-youros-contact-input"
          />
          <button
            type="submit"
            disabled={!newContact.trim() || saving}
            className="px-3 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-50 rounded-lg text-sm font-medium text-white transition-colors"
            data-testid="text-youros-add-contact"
          >
            Add
          </button>
        </form>
      </div>

      {/* Confirm before acting toggle */}
      <div className="flex items-center justify-between border-t border-slate-200 dark:border-slate-700 pt-3">
        <div>
          <p className="text-sm text-slate-700 dark:text-slate-300">Ask before running anything</p>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            When on, yourOS will ask you to confirm before starting an agent or running a command.
          </p>
        </div>
        <button
          onClick={toggleConfirm}
          disabled={saving}
          data-testid="text-youros-confirm-toggle"
          className={`relative w-10 h-6 rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
            status?.confirm_commands === 'always' ? 'bg-green-500' : 'bg-slate-300 dark:bg-slate-600'
          }`}
          aria-label={status?.confirm_commands === 'always' ? 'Turn off confirmations' : 'Turn on confirmations'}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
              status?.confirm_commands === 'always' ? 'translate-x-4' : 'translate-x-0'
            }`}
          />
        </button>
      </div>
    </div>
  )
}
