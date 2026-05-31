import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../lib/api'
import Icon from './Icon'

interface Contact {
  name: string
  email: string
}

interface TimeSuggestion {
  start: string
  end: string
  busy_count: number
}

interface NewEventModalProps {
  onClose: () => void
  onCreated: () => void
}

function formatSuggestionLabel(s: TimeSuggestion): string {
  const start = new Date(s.start)
  const end = new Date(s.end)
  const day = start.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
  const t1 = start.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const t2 = end.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const suffix = s.busy_count === 0 ? '' : ` · ${s.busy_count} conflict${s.busy_count !== 1 ? 's' : ''}`
  return `${day} · ${t1}–${t2}${suffix}`
}

function toLocalISOString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export default function NewEventModal({ onClose, onCreated }: NewEventModalProps) {
  const [title, setTitle] = useState('')
  const [startVal, setStartVal] = useState(() => {
    const d = new Date(); d.setMinutes(0, 0, 0); d.setHours(d.getHours() + 1)
    return toLocalISOString(d)
  })
  const [endVal, setEndVal] = useState(() => {
    const d = new Date(); d.setMinutes(0, 0, 0); d.setHours(d.getHours() + 2)
    return toLocalISOString(d)
  })
  const [allDay, setAllDay] = useState(false)
  const [attendees, setAttendees] = useState<string[]>([])
  const [emailInput, setEmailInput] = useState('')
  const [contacts, setContacts] = useState<Contact[]>([])
  const [suggestions, setSuggestions] = useState<TimeSuggestion[]>([])
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const contactDebounce = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchContacts = useCallback((q: string) => {
    if (contactDebounce.current) clearTimeout(contactDebounce.current)
    if (q.trim().length < 1) { setContacts([]); return }
    contactDebounce.current = setTimeout(async () => {
      try {
        const data = await api.get<{ contacts: Contact[] }>(`/calendar/contacts?q=${encodeURIComponent(q.trim())}`)
        setContacts(data.contacts || [])
      } catch { setContacts([]) }
    }, 250)
  }, [])

  const addAttendee = (email: string) => {
    const trimmed = email.trim().toLowerCase()
    if (!trimmed || !trimmed.includes('@')) return
    if (attendees.includes(trimmed)) return
    setAttendees(prev => [...prev, trimmed])
    setEmailInput('')
    setContacts([])
  }

  const removeAttendee = (email: string) => {
    setAttendees(prev => prev.filter(a => a !== email))
  }

  const fetchSuggestions = async () => {
    if (attendees.length === 0) return
    setLoadingSuggestions(true)
    setSuggestions([])
    try {
      const startDate = new Date(startVal)
      const endDate = new Date(endVal)
      const timeMin = startDate.toISOString()
      const timeMax = new Date(endDate.getTime() + 24 * 3600 * 1000).toISOString()
      const durationMs = endDate.getTime() - startDate.getTime()
      const durationMinutes = Math.max(30, Math.round(durationMs / 60000))
      const data = await api.post<{ suggestions: TimeSuggestion[] }>('/calendar/freebusy', {
        attendees,
        time_min: timeMin,
        time_max: timeMax,
        duration_minutes: durationMinutes,
      })
      setSuggestions(data.suggestions || [])
    } catch { /* no-op */ }
    setLoadingSuggestions(false)
  }

  const applySuggestion = (s: TimeSuggestion) => {
    setStartVal(toLocalISOString(new Date(s.start)))
    setEndVal(toLocalISOString(new Date(s.end)))
    setSuggestions([])
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) { setError('Event title is required.'); return }
    setSaving(true); setError(null)
    try {
      await api.post('/calendar/events', {
        summary: title.trim(),
        start: allDay ? startVal.slice(0, 10) : startVal,
        end: allDay ? endVal.slice(0, 10) : endVal,
        all_day: allDay,
        attendees,
      })
      onCreated()
      onClose()
    } catch (err: unknown) {
      const e = err as { detail?: { needs_reauth?: boolean; message?: string } | string; message?: string }
      const detail = e?.detail
      if (detail && typeof detail === 'object' && detail.needs_reauth) {
        setError('Calendar write access is missing. Reconnect your Google account in Settings.')
      } else {
        setError('Something went wrong. Please try again.')
      }
    }
    setSaving(false)
  }

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const inputClass = 'w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2 text-sm text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/40'

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="New event">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={onClose} />
      <form
        onSubmit={handleSubmit}
        className="relative w-full max-w-md bg-white dark:bg-slate-900 rounded-2xl shadow-xl border border-slate-200 dark:border-slate-800 p-5 space-y-4"
        data-testid="new-event-modal"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">New event</h2>
          <button type="button" onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800">
            <Icon name="close" size={18} />
          </button>
        </div>

        {/* Title */}
        <div>
          <input
            type="text"
            placeholder="Event title"
            value={title}
            onChange={e => setTitle(e.target.value)}
            className={inputClass}
            autoFocus
            data-testid="new-event-title"
          />
        </div>

        {/* All-day toggle */}
        <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={allDay}
            onChange={e => setAllDay(e.target.checked)}
            className="rounded"
            data-testid="new-event-allday"
          />
          All day
        </label>

        {/* Date/time */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Start</label>
            <input
              type={allDay ? 'date' : 'datetime-local'}
              value={allDay ? startVal.slice(0, 10) : startVal}
              onChange={e => setStartVal(e.target.value)}
              className={inputClass}
              data-testid="new-event-start"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">End</label>
            <input
              type={allDay ? 'date' : 'datetime-local'}
              value={allDay ? endVal.slice(0, 10) : endVal}
              onChange={e => setEndVal(e.target.value)}
              className={inputClass}
              data-testid="new-event-end"
            />
          </div>
        </div>

        {/* Attendees */}
        <div>
          <label className="block text-xs text-slate-500 mb-1">Invite people (optional)</label>
          {attendees.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2" data-testid="attendee-chips">
              {attendees.map(email => (
                <span
                  key={email}
                  className="inline-flex items-center gap-1 text-xs bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 px-2 py-0.5 rounded-full"
                >
                  {email}
                  <button
                    type="button"
                    onClick={() => removeAttendee(email)}
                    className="text-blue-500 hover:text-blue-700"
                    aria-label={`Remove ${email}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="relative">
            <input
              type="email"
              placeholder="Add email and press Enter"
              value={emailInput}
              onChange={e => { setEmailInput(e.target.value); fetchContacts(e.target.value) }}
              onKeyDown={e => {
                if (e.key === 'Enter') { e.preventDefault(); addAttendee(emailInput) }
                if (e.key === ',' || e.key === ' ') { e.preventDefault(); addAttendee(emailInput) }
              }}
              className={inputClass}
              data-testid="attendee-input"
            />
            {contacts.length > 0 && (
              <ul
                className="absolute z-10 mt-1 w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl shadow-lg text-sm max-h-40 overflow-y-auto"
                data-testid="contact-suggestions"
              >
                {contacts.map(c => (
                  <li key={c.email}>
                    <button
                      type="button"
                      onClick={() => addAttendee(c.email)}
                      className="w-full text-left px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-800"
                    >
                      <span className="font-medium">{c.name || c.email}</span>
                      {c.name && <span className="text-slate-500 ml-1 text-xs">{c.email}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {attendees.length > 0 && (
            <button
              type="button"
              onClick={fetchSuggestions}
              disabled={loadingSuggestions}
              className="mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
              data-testid="find-times-button"
            >
              {loadingSuggestions ? 'Finding times…' : 'Find a time that works for everyone'}
            </button>
          )}
        </div>

        {/* Time suggestions */}
        {suggestions.length > 0 && (
          <div data-testid="time-suggestions">
            <p className="text-xs text-slate-500 mb-1.5">Suggested times</p>
            <ul className="space-y-1">
              {suggestions.map((s, i) => (
                <li key={i}>
                  <button
                    type="button"
                    onClick={() => applySuggestion(s)}
                    className={`w-full text-left px-3 py-1.5 rounded-xl text-xs border transition-colors ${
                      s.busy_count === 0
                        ? 'border-green-200 bg-green-50 dark:bg-green-900/20 dark:border-green-800 text-green-700 dark:text-green-300 hover:bg-green-100'
                        : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300'
                    }`}
                  >
                    {formatSuggestionLabel(s)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400" role="alert" data-testid="new-event-error">{error}</p>
        )}

        <div className="flex gap-2 justify-end pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving || !title.trim()}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-xl transition-colors disabled:opacity-50 font-medium"
            data-testid="new-event-save"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  )
}
