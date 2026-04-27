import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { onSpecsChange } from '../lib/sidebarBus'
import Icon from './Icon'
import { Button } from './ui'
import { useAppStore } from '../stores/app'
import { useNotificationStore } from '../stores/notifications'

// Canonical lifecycle end-states recognized by the backend's
// compute_spec_status. ``complete`` is the one we care about; legacy
// ``done`` stays in the set so an unreloaded backend still fires
// correctly during a session where the writer and reader migrated at
// different wall clocks.
const COMPLETE_STATUSES = new Set(['complete', 'done'])

interface Spec {
  path: string
  title?: string
  status?: string
  /** File mtime in ms (from the server). Used to decide whether a
   *  spec that is already complete at mount time is a "recent" win
   *  that should still open the modal, vs. a stale entry from a
   *  previous demo run that should stay silent. */
  updated_at_ms?: number
  acceptance_criteria?: Array<{ text: string; checked: boolean }>
}

/** Grace window for "recent" completions. If a spec is already
 *  complete at mount and its file was touched within this many
 *  milliseconds of now, fire the modal instead of silently seeding
 *  it. Covers the hard-refresh-right-after-Build-it case: the
 *  watcher re-mounts AFTER the ready -> complete transition already
 *  happened, so there is no in-session transition to observe, but
 *  the user still has not seen the celebration yet. Stale specs
 *  from previous demo runs have an mtime older than 60 seconds, so
 *  they stay silent. */
const RECENT_COMPLETION_WINDOW_MS = 60_000

interface SpecsResponse {
  docs: Spec[]
}

/**
 * Global release-notes watcher. Polls /specs every 2 seconds and pops a
 * modal once per spec the moment it transitions to ``complete``. Mounted
 * in Layout so it fires regardless of which page the user is on — Tori
 * was missing the notification because the per-page implementation only
 * ran while Specs.tsx was mounted, and the transition usually happens
 * while she's watching the chat or the agents list.
 *
 * Dedup is in-memory only (Set on a ref). A hard-refresh resets the
 * Set, which is exactly what we want for a fresh demo run — the
 * celebration should fire on every new demo, not be suppressed by a
 * localStorage flag from the previous run.
 */
const CELEBRATED_STORAGE_KEY = 'myos-ephemeral-celebrated-spec-paths'

function loadCelebratedFromStorage(): Set<string> {
  try {
    const raw = window.localStorage.getItem(CELEBRATED_STORAGE_KEY)
    if (raw) return new Set<string>(JSON.parse(raw))
  } catch {
    // Malformed cache — start empty.
  }
  return new Set<string>()
}

function saveCelebratedToStorage(set: Set<string>): void {
  try {
    window.localStorage.setItem(
      CELEBRATED_STORAGE_KEY,
      JSON.stringify(Array.from(set))
    )
  } catch {
    // Quota or disabled localStorage — in-memory still dedups.
  }
}

// Permanent (never cleared) dedup key for notification IDs the user has
// already seen. The ephemeral CELEBRATED_STORAGE_KEY is wiped on every
// onboarded=false reset so demos can re-celebrate the same spec, but
// notification IDs must survive that reset — otherwise the toast that
// was already dismissed comes back on the next page load.
const SEEN_NOTIFICATION_IDS_KEY = 'myos-permanent-seen-notification-ids'

function loadSeenNotificationIds(): Set<string> {
  try {
    const raw = window.localStorage.getItem(SEEN_NOTIFICATION_IDS_KEY)
    if (raw) return new Set<string>(JSON.parse(raw))
  } catch {
    // Malformed — start empty.
  }
  return new Set<string>()
}

function saveSeenNotificationIds(ids: Set<string>): void {
  try {
    window.localStorage.setItem(
      SEEN_NOTIFICATION_IDS_KEY,
      JSON.stringify(Array.from(ids))
    )
  } catch {
    // Quota or disabled localStorage — in-memory still dedups.
  }
}

export default function ReleaseNotesWatcher() {
  const [current, setCurrent] = useState<Spec | null>(null)
  const celebratedRef = useRef<Set<string>>(loadCelebratedFromStorage())
  const lastStatusByPathRef = useRef<Record<string, string>>({})
  const initializedRef = useRef(false)
  const setChatOpen = useAppStore((s) => s.setChatOpen)
  // Clear the persistent celebration set whenever the server reports
  // onboarded=false (the canonical "fresh demo" signal). This is how
  // a hard-refresh after a reset lets the same spec re-celebrate on
  // the next run without leaking celebrations across runs otherwise.
  const onboarded = useAppStore((s) => s.onboarded)
  useEffect(() => {
    if (!onboarded) {
      celebratedRef.current = new Set<string>()
      try { window.localStorage.removeItem(CELEBRATED_STORAGE_KEY) } catch {
        // ignore
      }
    }
  }, [onboarded])

  useEffect(() => {
    let cancelled = false

    const check = async () => {
      try {
        const data = await api.get<SpecsResponse>('/specs')
        if (cancelled) return
        const docs = data.docs || []

        const prevByPath = lastStatusByPathRef.current
        const nextByPath: Record<string, string> = {}
        const firstPoll = !initializedRef.current
        let toCelebrate: Spec | null = null

        const nowMs = Date.now()
        for (const d of docs) {
          const status = d.status || ''
          nextByPath[d.path] = status
          const isComplete = COMPLETE_STATUSES.has(status)
          if (!isComplete) continue
          if (celebratedRef.current.has(d.path)) continue
          if (firstPoll) {
            // Mount can land AFTER the transition already happened
            // (hard refresh right after Build-it lands, watcher
            // remount, etc). Two sub-cases:
            //   a) The spec completed seconds ago and the user has
            //      not seen the modal yet. We must still celebrate.
            //   b) The spec completed hours or days ago and is just
            //      sitting on disk from a previous run. We must
            //      stay silent so old specs do not re-celebrate on
            //      every login.
            // The server stamps each doc with its file mtime
            // (updated_at_ms). If that mtime is within the recent
            // window, treat it as case (a) and open the modal.
            // Otherwise seed the dedup set silently (case b).
            const updatedAt = d.updated_at_ms || 0
            const isRecent =
              updatedAt > 0 && nowMs - updatedAt <= RECENT_COMPLETION_WINDOW_MS
            if (isRecent && !toCelebrate) {
              toCelebrate = d
            } else {
              celebratedRef.current.add(d.path)
            }
          } else {
            const prev = prevByPath[d.path]
            if (prev && !COMPLETE_STATUSES.has(prev)) {
              // Clean transition observed this session.
              toCelebrate = d
            }
          }
        }

        lastStatusByPathRef.current = nextByPath
        if (firstPoll) {
          // Persist the seed so a remount inside the same
          // localStorage-era does not re-fire either.
          saveCelebratedToStorage(celebratedRef.current)
        }
        initializedRef.current = true

        if (toCelebrate) {
          celebratedRef.current.add(toCelebrate.path)
          saveCelebratedToStorage(celebratedRef.current)
          setCurrent(toCelebrate)
        }
      } catch {
        // Transient fetch failure is not fatal; next poll will retry.
      }
    }

    // Initial + 2s poll. Matches the cadence the Specs page uses
    // during an active build, so transitions land within 2s.
    check()
    const interval = window.setInterval(check, 2000)
    // Bus hook: any write to /specs/* anywhere in the app also nudges
    // us so we refetch within a single frame of the write that caused
    // the transition.
    const off = onSpecsChange(() => {
      check()
    })
    return () => {
      cancelled = true
      window.clearInterval(interval)
      off()
    }
  }, [])

  // Fallback surface: fire the modal from a persistent ``spec_complete``
  // notification too. The /api/specs polling path above assumes the
  // spec file is still on disk; when the demo flow cleans up the spec
  // after a successful build the docs list comes back empty and the
  // watcher has nothing to detect. The backend ``_fire_spec_complete_
  // notification`` writes a row to the persistent notifications store
  // on the same transition, which the TopBar poll hands to the
  // notifications store via ``addPersistentToast``. Reading it here
  // guarantees the modal fires on every Build-it landing, no matter
  // whether the spec file survives the build.
  const lastFeatureLive = useNotificationStore((s) => s.lastFeatureLive)
  // Persisted across page loads and onboarded=false resets. The ephemeral
  // celebratedRef is wiped on demo reset so the same spec can re-celebrate,
  // but a notification the user already dismissed must never re-appear —
  // that requires a key that outlives the reset.
  const seenNotificationIdsRef = useRef<Set<string>>(loadSeenNotificationIds())
  useEffect(() => {
    if (!lastFeatureLive) return
    if (seenNotificationIdsRef.current.has(lastFeatureLive.id)) return
    seenNotificationIdsRef.current.add(lastFeatureLive.id)
    saveSeenNotificationIds(seenNotificationIdsRef.current)
    // Use the spec path as the dedup key when we have it. This is the
    // same key the /api/specs polling path stores when it celebrates,
    // so whichever path fires first (polling at ~2s, notification at
    // ~10s) will block the other from opening the modal a second time.
    // Fall back to notification:id when specPath is absent (legacy
    // notifications without an expand= action_url).
    const dedupPath = lastFeatureLive.specPath || `notification:${lastFeatureLive.id}`
    if (celebratedRef.current.has(dedupPath)) return
    celebratedRef.current.add(dedupPath)
    // Bridge the two dedup namespaces: the polling path keys on the spec
    // file path (e.g. "docs/spec/foo.md") while the notification path
    // keys on "notification:<id>". Without this bridge, dismissing via
    // the notification path leaves the spec path absent from celebratedRef,
    // so a hard-refresh within the 60s grace window re-fires the modal via
    // the polling path even though the user already dismissed it.
    if (lastFeatureLive.action_url) {
      try {
        const u = new URL(lastFeatureLive.action_url, 'https://x')
        const specPath = u.searchParams.get('expand')
        if (specPath) celebratedRef.current.add(specPath)
      } catch {
        // malformed action_url — in-memory key still dedups this session
      }
    }
    saveCelebratedToStorage(celebratedRef.current)
    setCurrent({
      path: dedupPath,
      title: lastFeatureLive.title,
      status: 'complete',
      acceptance_criteria: lastFeatureLive.body
        ? [{ text: lastFeatureLive.body, checked: true }]
        : [],
    })
  }, [lastFeatureLive])

  // Escape closes the release notes modal.
  useEffect(() => {
    if (!current) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setCurrent(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current])

  if (!current) return null

  const title = current.title || 'Your feature is live'
  const acs = Array.isArray(current.acceptance_criteria)
    ? current.acceptance_criteria
    : []

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) setCurrent(null)
      }}
      data-testid="release-notes-modal"
      role="dialog"
      aria-modal="true"
      aria-label={`${title} is live`}
    >
      <div className="relative max-w-lg w-[92vw] bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl p-6">
        <button
          type="button"
          onClick={() => setCurrent(null)}
          className="absolute top-3 right-3 text-slate-400 hover:text-slate-200 transition-colors"
          aria-label="Close release notes"
        >
          <Icon name="close" size={18} />
        </button>

        <div className="flex items-center gap-2 text-xl mb-1">
          <span role="img" aria-label="Ship">🚢</span>
          <span className="font-semibold text-slate-100">{title}</span>
        </div>
        <div className="text-sm text-slate-400 mb-4">
          Your agents just shipped this. Here's what changed.
        </div>

        {acs.length > 0 && (
          <ul className="space-y-2 mb-5">
            {acs.map((ac, i) => (
              <li
                key={i}
                className="flex items-start gap-2 text-sm text-slate-300"
              >
                <Icon
                  name="check_circle"
                  className="text-green-400 mt-0.5 shrink-0"
                  size={16}
                />
                <span>{ac.text}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" onClick={() => setCurrent(null)}>
            Close
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              try {
                setChatOpen(true)
              } catch {
                // store not available — just close
              }
              setCurrent(null)
            }}
          >
            Try it now →
          </Button>
        </div>
      </div>
    </div>
  )
}
