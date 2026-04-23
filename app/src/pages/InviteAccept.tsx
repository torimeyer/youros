import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Icon from '../components/Icon'
import { useAppStore } from '../stores/app'

/**
 * Invite acceptance page.
 *
 * When someone navigates to /invite/:token, this page redirects them
 * to the backend endpoint that validates the token, sets a session
 * cookie, and redirects back to the app. The backend handles all the
 * logic. This page only shows a brief loading state.
 */
export default function InviteAccept() {
  const { token } = useParams<{ token: string }>()
  const navigate = useNavigate()
  const darkMode = useAppStore((s) => s.darkMode)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!token) {
      setError('No invite token found.')
      return
    }

    // Redirect to backend accept endpoint. The backend will set the
    // session cookie and redirect back to /?invite_accepted=true.
    window.location.href = `/api/enterprise/invite/${token}`
  }, [token])

  const labelClass = darkMode ? 'text-slate-400' : 'text-slate-500'

  if (error) {
    return (
      <div className="min-h-dvh flex items-center justify-center">
        <div className="text-center space-y-4">
          <Icon name="error_outline" className="text-red-400 text-4xl" />
          <p className="text-red-400">{error}</p>
          <button
            onClick={() => navigate('/')}
            className="text-sm text-purple-400 hover:text-purple-300"
          >
            Go to home
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-dvh flex items-center justify-center">
      <div className="text-center space-y-3">
        <Icon name="hourglass_empty" className={`animate-spin text-2xl ${labelClass}`} />
        <p className={`text-sm ${labelClass}`}>Accepting your invite...</p>
      </div>
    </div>
  )
}
