import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'
import { installVitePreloadRecovery } from './lib/vitePreloadRecovery'

// After a dev-server or PM2 restart, an already-open tab holds stale
// dynamic-import chunk hashes. Vite fires 'vite:preloadError' when those
// fetches fail. One auto-reload recovers the tab silently. A sessionStorage
// flag prevents the reload from looping if the server is still misconfigured.
installVitePreloadRecovery()

// Reveal icon font glyphs only after Material Symbols has loaded,
// preventing a flash of icon-name text (e.g. "home", "search") on refresh.
if (document.fonts?.ready) {
  document.fonts.ready.then(() => {
    document.documentElement.classList.add('icons-loaded')
  })
} else {
  // Fallback for environments without the Font Loading API
  document.documentElement.classList.add('icons-loaded')
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)

// Register the service worker for push notifications and offline support.
// Production only. In dev, the service worker caches index.html and JS
// bundles with a cache-first strategy, so code changes made between
// sessions never appear until the user hard-refreshes. Skipping
// registration in dev avoids this entirely.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Registration can fail in environments that restrict workers.
    })
  })
} else if (!import.meta.env.PROD && 'serviceWorker' in navigator) {
  // Dev: actively remove any service worker left over from a past
  // production build. Without this, the old worker keeps serving cached
  // index.html and JS bundles, so code changes never appear no matter how
  // many times you refresh. Unregistering it here means one reload after
  // this loads gets you the live dev build, with no DevTools digging.
  navigator.serviceWorker.getRegistrations().then((regs) => {
    regs.forEach((reg) => reg.unregister())
  }).catch(() => {
    // getRegistrations can be unavailable in restricted environments.
  })
}
