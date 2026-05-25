import { useEffect } from 'react'
import TopBar from '../components/TopBar'
import Icon from '../components/Icon'
import releaseNotes from '../data/releaseNotes'
import { useAppStore } from '../stores/app'

export default function Releases() {
  const setWhatsNewLastSeen = useAppStore((s) => s.setWhatsNewLastSeen)

  // Mark everything as seen the moment the page mounts. This mirrors the
  // old drawer behavior so the sidebar badge clears once the user has
  // actually looked at the release notes.
  useEffect(() => {
    if (releaseNotes.length > 0) {
      setWhatsNewLastSeen(releaseNotes[0].date)
    }
  }, [setWhatsNewLastSeen])

  return (
    <div className="min-h-dvh bg-white dark:bg-slate-950 text-white">
      <TopBar title="What's New" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        <div className="max-w-6xl mx-auto">
          <div className="flex items-center gap-3 mb-2">
            <Icon name="auto_awesome" className="text-yellow-600 dark:text-yellow-400" size={28} />
            <h1 className="text-2xl sm:text-3xl font-bold text-white">What's New in myOS</h1>
          </div>
          <p className="text-slate-600 dark:text-slate-400 mb-10">
            Everything that has shipped recently, in plain language.
          </p>

          {releaseNotes.length === 0 && (
            <div className="bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl p-8 text-center text-slate-600 dark:text-slate-400">
              No release notes yet. Check back soon.
            </div>
          )}

          <div className="space-y-12">
            {releaseNotes.map((group) => (
              <section key={group.date} data-testid={`release-group-${group.date}`}>
                <h2 className="text-sm font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide mb-4">
                  {group.label}
                </h2>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  {group.entries.map((entry) => (
                    <div
                      key={entry.title}
                      className="bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl p-5"
                    >
                      <p className="text-base font-semibold text-white mb-2">
                        {entry.title}
                      </p>
                      <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
                        {entry.description}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
