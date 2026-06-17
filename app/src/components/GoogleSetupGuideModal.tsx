import { useEffect } from 'react'
import Icon from './Icon'

// Shared Google setup guide modal. Used by the Drive, Gmail, and
// Calendar connect panels so a person opening any tab before completing
// Google Cloud setup has a single place to read the instructions.
// Previously this lived only inside Drive.tsx, so Gmail and Calendar
// users who hit the connect screen had no way to find the setup steps.
export default function GoogleSetupGuideModal({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      data-testid="google-setup-guide-modal"
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-2xl p-8 max-w-lg w-full max-h-[90vh] overflow-y-auto text-left"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <h2 className="text-xl font-bold">Set up your Google credentials</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <p className="text-sm text-slate-600 dark:text-slate-400 mb-5">
          yourOS needs a credentials file from Google so it can connect to your Drive, Calendar, and Gmail. You only have to do this once. Here is how.
        </p>

        <ol className="space-y-4 text-sm text-slate-700 dark:text-slate-300">
          <li>
            <div className="font-semibold text-slate-800 dark:text-slate-200 mb-1">1. Open Google Cloud Console</div>
            <p className="text-slate-600 dark:text-slate-400">
              Go to{' '}
              <a
                href="https://console.cloud.google.com"
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline"
              >
                console.cloud.google.com
              </a>{' '}
              and sign in with the Google account you want to connect. Create a new project if you do not already have one.
            </p>
          </li>

          <li>
            <div className="font-semibold text-slate-800 dark:text-slate-200 mb-1">2. Turn on the APIs you want</div>
            <p className="text-slate-600 dark:text-slate-400 mb-2">
              In the search bar, find and enable each of these:
            </p>
            <ul className="list-disc list-inside space-y-1 text-slate-600 dark:text-slate-400 ml-2">
              <li>Google Drive API</li>
              <li>Google Calendar API</li>
              <li>Gmail API</li>
            </ul>
            <p className="text-slate-500 text-xs mt-2">
              For each one, open it and click the blue "Enable" button. This takes about a minute.
            </p>
          </li>

          <li>
            <div className="font-semibold text-slate-800 dark:text-slate-200 mb-1">3. Set up the OAuth consent screen</div>
            <p className="text-slate-600 dark:text-slate-400 mb-2">
              Go to "APIs and Services" then "OAuth consent screen". Choose <strong>External</strong> and click Create. Fill in your app name and email, then click Save and Continue through each section.
            </p>
            <p className="text-slate-600 dark:text-slate-400 mb-2">
              On the "Test users" step, click "Add users" and add your own Google email address. This lets you sign in while the app is in testing mode.
            </p>
            <div className="mt-2 p-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/50 rounded-lg">
              <p className="text-xs text-amber-700 dark:text-amber-300 font-medium mb-1">Important: Publish to Production so it does not expire</p>
              <p className="text-xs text-amber-600 dark:text-amber-400">
                While in testing mode, Google disconnects your account every 7 days. To fix this, go back to the OAuth consent screen, click "Publish App", and confirm. You do not need Google's review for a personal app.
              </p>
            </div>
          </li>

          <li>
            <div className="font-semibold text-slate-800 dark:text-slate-200 mb-1">4. Create OAuth credentials</div>
            <p className="text-slate-600 dark:text-slate-400">
              Go to "APIs and Services" then "Credentials". Click "Create Credentials" and pick "OAuth client ID". Choose <strong>Desktop app</strong> as the type, give it any name, and click Create.
            </p>
          </li>

          <li>
            <div className="font-semibold text-slate-800 dark:text-slate-200 mb-1">5. Download and upload the JSON file</div>
            <p className="text-slate-600 dark:text-slate-400">
              After creating the client, click the download button next to it. You will get a small .json file. Upload it here using the area below to finish connecting.
            </p>
          </li>
        </ol>

        <div className="mt-6 p-3 bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg">
          <p className="text-xs text-slate-600 dark:text-slate-400">
            Your credentials file stays on your computer. yourOS never uploads it anywhere.
          </p>
        </div>

        <div className="mt-4 p-3 bg-slate-100 dark:bg-slate-800/30 border border-slate-200 dark:border-slate-700 rounded-lg">
          <p className="text-xs text-slate-700 dark:text-slate-300 font-medium mb-1">Want to use Gemini chat too?</p>
          <p className="text-xs text-slate-600 dark:text-slate-400">
            Recommended: reuse this same Google Cloud project. Three steps:
          </p>
          <ol className="text-xs text-slate-600 dark:text-slate-400 list-decimal ml-5 mt-1 space-y-1">
            <li>
              Enable{' '}
              <a
                href="https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com"
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline"
              >
                "Gemini API"
              </a>{' '}
              (formerly Generative Language API) in the API library. It takes about 30 seconds.
            </li>
            <li>Open Credentials and click Create credentials, API key.</li>
            <li>
              Edit the new key and restrict it to "Gemini API" under API restrictions. It only appears in the dropdown after step 1. Paste the key into Settings under AI Provider.
            </li>
          </ol>
          <div className="mt-2 text-[10px] text-slate-500 leading-normal">
            <strong>Privacy:</strong> Google uses data from the free Gemini API tier to improve its products. For strict privacy, use a paid tier or Vertex AI. See the{' '}
            <a
              href="https://ai.google.dev/gemini-api/terms#data-use-unpaid"
              target="_blank"
              rel="noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline"
            >
              official privacy notice
            </a>{' '}
            for details.
          </div>
          <p className="text-xs text-slate-500 mt-2">
            Only using Gemini chat and nothing else from Google? Grab a free key at{' '}
            <a
              href="https://aistudio.google.com/apikey"
              target="_blank"
              rel="noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline"
            >
              Google AI Studio
            </a>{' '}
            instead. It ties to your personal Google account and is one click.
          </p>
        </div>

        <button
          type="button"
          onClick={onClose}
          className="w-full mt-5 py-3 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-xl font-medium transition-colors"
        >
          Got it
        </button>
      </div>
    </div>
  )
}
