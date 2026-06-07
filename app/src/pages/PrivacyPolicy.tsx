import PageShell from '../components/PageShell'

interface Section {
  id: string
  heading: string
  body: React.ReactNode
}

export default function PrivacyPolicy() {
  const sections: Section[] = [
    {
      id: 'what-we-store',
      heading: 'What we store on your machine',
      body: (
        <>
          <p className="text-sm text-slate-600 dark:text-slate-400 mb-3">
            Everything stays local. All data lives in{' '}
            <code className="text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1 rounded">~/.youros/</code> on your
            computer. Nothing is uploaded to a server run by this project.
          </p>
          <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-2 list-disc list-inside">
            <li>Your preferences: OS name, theme, chosen AI provider, communication style.</li>
            <li>Your tasks, notes, and agent transcripts.</li>
            <li>Your chat history with AI assistants.</li>
            <li>Files and documents you add to the workspace.</li>
            <li>
              API keys you provide. These are written to your local database and never transmitted
              by this app.
            </li>
            <li>
              Cached data from services you connect (Google Calendar, Gmail, Drive, Slack, GitHub).
              Caches live in subdirectories of{' '}
              <code className="text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1 rounded">~/.youros/</code> and are
              only populated when you explicitly connect a service.
            </li>
          </ul>
        </>
      ),
    },
    {
      id: 'third-party-calls',
      heading: 'When your data leaves your machine',
      body: (
        <>
          <p className="text-sm text-slate-600 dark:text-slate-400 mb-3">
            This app does not have a cloud backend. The only outbound calls happen when you use
            features that require third-party services:
          </p>
          <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-2 list-disc list-inside">
            <li>
              <strong className="text-slate-700 dark:text-slate-300">AI providers.</strong> When you send a message or
              run an agent, that content is sent to the AI provider you chose (Anthropic, Google,
              etc.). Those calls are subject to the provider's own privacy policy. This app never
              reads or logs the responses on your behalf.
            </li>
            <li>
              <strong className="text-slate-700 dark:text-slate-300">Connected services.</strong> If you link Google,
              Slack, or GitHub, the app calls those APIs directly from your machine using
              credentials you provided. We do not proxy or log those calls.
            </li>
          </ul>
          <p className="text-sm text-slate-600 dark:text-slate-400 mt-3">
            If you have not connected any services and have not configured an AI provider, no
            network calls are made to third parties.
          </p>
        </>
      ),
    },
    {
      id: 'telemetry',
      heading: 'What we do not collect',
      body: (
        <p className="text-sm text-slate-600 dark:text-slate-400">
          This app collects no usage analytics, no crash reports, and no telemetry of any kind.
          There is no tracking pixel, no session recording, and no automatic check-in on startup.
          If you are not connected to the internet, the app works exactly the same as when you are.
        </p>
      ),
    },
    {
      id: 'your-control',
      heading: 'Your control over your data',
      body: (
        <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-2 list-disc list-inside">
          <li>
            <strong className="text-slate-700 dark:text-slate-300">Delete everything.</strong> Remove{' '}
            <code className="text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1 rounded">~/.youros/</code> to wipe all
            local data. The app will start fresh on next launch.
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-300">Delete specific data.</strong> Remove individual
            files inside{' '}
            <code className="text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1 rounded">~/.youros/</code> (for
            example,{' '}
            <code className="text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1 rounded">chat_history.json</code>)
            to clear just that data.
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-300">Reset onboarding.</strong> Go to Settings to clear
            your profile and run the setup wizard again.
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-300">Revoke shared links.</strong> Go to Settings and
            find the Shares section. You can revoke any shared link at any time.
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-300">Disconnect services.</strong> Revoke OAuth tokens in
            your Google, Slack, or GitHub account settings. Deleting the token files inside{' '}
            <code className="text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1 rounded">~/.youros/</code> also severs
            the connection locally.
          </li>
        </ul>
      ),
    },
    {
      id: 'contact',
      heading: 'Questions',
      body: (
        <p className="text-sm text-slate-600 dark:text-slate-400">
          If you have a question about how your data is handled, file an issue in the project's
          GitHub repository. A human will respond.
        </p>
      ),
    },
  ]

  return (
    <PageShell title="Privacy">
      <main className="pt-24 pb-16 px-8">
        <p className="text-slate-600 dark:text-slate-400 text-sm mb-8">
          Plain-language summary of what this app stores, what it sends, and how you stay in
          control.
        </p>

        <div className="space-y-6">
          {sections.map((section) => (
            <div
              key={section.id}
              data-testid={`privacy-section-${section.id}`}
              className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-6"
            >
              <h2
                className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-4"
                data-testid={`privacy-heading-${section.id}`}
              >
                {section.heading}
              </h2>
              {section.body}
            </div>
          ))}
        </div>

        <p className="text-xs text-slate-600 mt-10">Last updated: April 2026</p>
      </main>
    </PageShell>
  )
}
