// Key stored in sessionStorage to mark that we have already auto-reloaded
// once for stale dynamic-import chunk hashes this session. Prevents an
// infinite reload loop when the server is still serving stale hashes after
// the first recovery attempt.
const RELOAD_FLAG = '_vitePreloadReloaded'

/**
 * Installs a one-shot handler for the `vite:preloadError` window event.
 *
 * Vite emits that event whenever a lazy dynamic import fails to fetch its
 * chunk (the content hash changed after the tab was opened, e.g. after a
 * dev-server or PM2 restart). The standard recovery is a full page reload,
 * which forces the browser to re-fetch the latest index.html and all updated
 * chunk URLs.
 *
 * A sessionStorage flag prevents a second reload in the same browser session,
 * guarding against an infinite reload loop.
 *
 * @param reload  Optional override for window.location.reload. Pass a spy
 *                function in tests to avoid actually navigating.
 */
export function installVitePreloadRecovery(
  reload: () => void = () => window.location.reload()
): void {
  // If we already reloaded once this session, do not register the listener
  // at all. sessionStorage persists through a page reload but clears when
  // the tab is closed, so a genuine re-open always starts fresh.
  if (sessionStorage.getItem(RELOAD_FLAG)) return

  window.addEventListener(
    'vite:preloadError',
    () => {
      // Secondary check: guards the unlikely case where two dispatches hit
      // before { once: true } removes the listener.
      if (sessionStorage.getItem(RELOAD_FLAG)) return
      sessionStorage.setItem(RELOAD_FLAG, '1')
      reload()
    },
    { once: true }
  )
}
