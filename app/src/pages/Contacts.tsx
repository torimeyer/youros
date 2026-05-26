import { useState, useEffect, useMemo } from 'react'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { LoadingState, EmptyState, ErrorBanner } from '../components/ui'
import { api } from '../lib/api'

interface Contact {
  name: string
  phone_numbers: string[]
  emails: string[]
}

interface ContactsResponse {
  contacts: Contact[]
  count: number
}

export default function Contacts() {
  const [contacts, setContacts] = useState<Contact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    ;(async () => {
      try {
        const res = await api.get<ContactsResponse>('/contacts')
        setContacts(res.contacts || [])
      } catch (err: unknown) {
        setError((err as Error)?.message || 'Could not load contacts.')
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const filtered = useMemo(() => {
    if (!search.trim()) return contacts
    const q = search.toLowerCase()
    return contacts.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.phone_numbers.some((p) => p.toLowerCase().includes(q)) ||
        c.emails.some((e) => e.toLowerCase().includes(q))
    )
  }, [contacts, search])

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Contacts" />
      <div className="px-4 pb-4 sm:px-8 sm:pb-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">Contacts</h1>
            {!loading && !error && (
              <p className="text-sm text-slate-400 mt-0.5">
                {contacts.length} contact{contacts.length !== 1 ? 's' : ''}
              </p>
            )}
          </div>
        </div>

        {/* Privacy note */}
        <div
          data-testid="privacy-note"
          className="flex items-start gap-2 bg-slate-900/60 border border-slate-800 rounded-xl px-4 py-3 mb-4 text-sm text-slate-400"
        >
          <Icon name="lock" size={16} className="mt-0.5 shrink-0 text-slate-500" />
          <span>Loaded from macOS Contacts. Stays on this machine — never sent anywhere.</span>
        </div>

        {/* Search */}
        <div className="mb-4">
          <div className="relative">
            <Icon
              name="search"
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none"
            />
            <input
              type="text"
              placeholder="Search by name, phone, or email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                <Icon name="close" size={16} />
              </button>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="bg-slate-900/40 border border-slate-800 rounded-xl">
          {loading ? (
            <div className="p-6">
              <LoadingState variant="spinner" message="Loading contacts from macOS Contacts…" />
            </div>
          ) : error ? (
            <div className="p-4">
              <ErrorBanner message={error} />
            </div>
          ) : filtered.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon="person_search"
                title={search ? `No contacts matching "${search}"` : 'No contacts found.'}
              />
            </div>
          ) : (
            <div className="divide-y divide-slate-800/60">
              {filtered.map((contact, idx) => (
                <div key={idx} className="px-4 py-3 flex items-start gap-3">
                  <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center shrink-0 mt-0.5">
                    <span className="text-sm font-medium text-slate-300">
                      {(contact.name || '?').charAt(0).toUpperCase()}
                    </span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-white truncate">
                      {contact.name || <span className="text-slate-500 italic">No name</span>}
                    </p>
                    {contact.phone_numbers.map((p, i) => (
                      <p key={i} className="text-xs text-slate-400 mt-0.5">
                        {p}
                      </p>
                    ))}
                    {contact.emails.map((e, i) => (
                      <p key={i} className="text-xs text-slate-400 mt-0.5">
                        {e}
                      </p>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {search && !loading && filtered.length > 0 && (
          <p className="text-xs text-slate-500 mt-2 text-center">
            {filtered.length} of {contacts.length} contact{contacts.length !== 1 ? 's' : ''}
          </p>
        )}
      </div>
    </div>
  )
}
