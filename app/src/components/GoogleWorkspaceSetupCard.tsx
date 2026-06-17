import { useState, useEffect, useRef } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'
import { reportError } from '../lib/reportError'

interface DriveAuthStatus {
  authenticated: boolean
  email: string | null
  credentials_file_present: boolean
}

function googleAccountLabel(email: string | null): string {
  if (!email) return 'Google account'
  const lower = email.toLowerCase()
  if (lower.endsWith('@gmail.com') || lower.endsWith('@googlemail.com')) {
    return 'Google account'
  }
  return 'Google Workspace'
}

export function GoogleAccountSetupCard({
  darkMode,
  subtextCls,
  stepIndex,
}: {
  darkMode: boolean
  subtextCls: string
  stepIndex?: number
}) {
  const [status, setStatus] = useState<DriveAuthStatus | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const fetchStatus = () => {
    return api.get<DriveAuthStatus>('/drive/auth/status')
      .then((data) => {
        setStatus(data)
        return data
      })
      .catch(() => {
        setStatus({ authenticated: false, email: null, credentials_file_present: false })
        return null
      })
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('connected') === 'true' || params.get('auth_success') === 'true') {
      window.history.replaceState({}, '', '/')
    }
    fetchStatus()
  }, [])

  const startOAuth = () => {
    if (stepIndex !== undefined) api.patch('/settings', { onboarding_step: stepIndex }).catch(() => {})
    api.get<{ url: string }>('/drive/auth/url?return_to=%2F')
      .then((res) => { window.location.href = res.url })
      .catch((e) => reportError('Google account OAuth failed to start', e))
  }

  const uploadFile = async (file: File) => {
    setUploading(true)
    setUploadError(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const resp = await fetch('/api/drive/credentials', { method: 'POST', body: formData })
      const data = (await resp.json()) as { ok: boolean; error?: string }
      if (data.ok) {
        // Re-fetch status, then auto-continue to OAuth
        const updated = await fetchStatus()
        if (updated?.credentials_file_present) {
          startOAuth()
        }
      } else {
        setUploadError(data.error ?? 'Something went wrong. Please try again.')
      }
    } catch {
      setUploadError('Could not upload the file. Check your connection and try again.')
    } finally {
      setUploading(false)
    }
  }

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) uploadFile(file)
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) uploadFile(file)
  }

  const cardBase = `mt-2 p-3 rounded-lg border ${darkMode ? 'border-slate-700' : 'border-gray-200'}`

  if (status === null) {
    return (
      <div className={cardBase} data-testid="onboarding-google-workspace-card">
        <div className="flex items-center gap-2 text-sm">
          <div className="w-2.5 h-2.5 rounded-full bg-slate-600 animate-pulse" />
          <span className={subtextCls}>Checking Google account...</span>
        </div>
      </div>
    )
  }

  if (status.authenticated) {
    return (
      <div className={cardBase} data-testid="onboarding-google-workspace-card">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium">{googleAccountLabel(status.email)}</p>
              {status.email && <p className={`text-xs ${subtextCls}`}>{status.email}</p>}
            </div>
          </div>
          <span
            className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400"
            data-testid="google-workspace-connected-pill"
          >
            Connected
          </span>
        </div>
      </div>
    )
  }

  // Credentials file present but not yet signed in: show Connect button
  if (status.credentials_file_present) {
    return (
      <div className={cardBase} data-testid="onboarding-google-workspace-card">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">{googleAccountLabel(null)} (Drive, Calendar, Gmail)</p>
          <button
            onClick={startOAuth}
            className={`flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-xs font-medium transition-colors ${
              darkMode
                ? 'bg-slate-800 border-slate-700 text-slate-300 hover:border-blue-500'
                : 'bg-gray-50 border-gray-200 text-slate-600 hover:border-blue-500'
            }`}
            data-testid="connect-google-workspace"
          >
            <Icon name="folder_shared" size={14} />
            Connect
          </button>
        </div>
        <p className={`text-xs mt-1.5 ${subtextCls}`}>
          One click. Google will ask for permission, then bring you back here.
        </p>
      </div>
    )
  }

  // No credentials file: show upload area
  return (
    <div className={cardBase} data-testid="onboarding-google-workspace-card">
      <p className="text-sm font-medium mb-2">{googleAccountLabel(null)} (Drive, Calendar, Gmail)</p>
      <p className={`text-xs mb-3 ${subtextCls}`}>
        To connect Google, upload the credentials file you downloaded from Google Cloud Console.
        Your file stays on your computer and is never sent anywhere.
      </p>

      <div
        role="button"
        tabIndex={0}
        data-testid="google-credentials-upload"
        onClick={() => !uploading && inputRef.current?.click()}
        onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !uploading) inputRef.current?.click() }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`flex flex-col items-center justify-center gap-2 p-5 border-2 border-dashed rounded-xl cursor-pointer transition-colors ${
          dragging
            ? 'border-blue-400 bg-blue-500/10'
            : darkMode
            ? 'border-slate-600 hover:border-slate-400 bg-slate-800/40'
            : 'border-gray-300 hover:border-gray-400 bg-gray-50/40'
        }`}
      >
        {uploading ? (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <div className="w-4 h-4 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" />
            Saving...
          </div>
        ) : (
          <>
            <Icon name="upload_file" size={24} className={darkMode ? 'text-slate-400' : 'text-slate-500'} />
            <span className={`text-sm ${darkMode ? 'text-slate-300' : 'text-slate-600'}`}>
              Drop your credentials file here
            </span>
            <span className={`text-xs ${subtextCls}`}>or click to browse</span>
          </>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept=".json,application/json"
        className="sr-only"
        onChange={handleFileChange}
        aria-label="Upload Google credentials file"
      />

      {uploadError && (
        <p
          data-testid="credentials-upload-error"
          className="mt-2 text-xs text-red-600 dark:text-red-400"
        >
          {uploadError}
        </p>
      )}

      <p className={`text-xs mt-2 ${subtextCls}`}>
        Don't have this file yet?{' '}
        <a
          href="https://console.cloud.google.com"
          target="_blank"
          rel="noreferrer"
          className="underline text-blue-600 dark:text-blue-400"
        >
          Open Google Cloud Console
        </a>{' '}
        to create one. The guide above walks you through it.
      </p>
    </div>
  )
}

export { GoogleAccountSetupCard as GoogleWorkspaceSetupCard }
