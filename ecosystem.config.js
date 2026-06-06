const path = require('path');

const APP_DIR = path.join(__dirname, 'app');

// Frontend only — the backend is managed by launchd (com.myos.backend).
// On macOS after install.sh, launchd owns backend restarts (KeepAlive=true,
// ThrottleInterval=1s) and the kill-only watchdog handles deadlocked event
// loops. PM2 handles the Vite dev server only.
module.exports = {
  apps: [
    {
      name: 'youros-frontend',
      cwd: APP_DIR,
      script: 'node',
      args: [path.join(APP_DIR, 'node_modules', '.bin', 'vite')],
      env: {
        NODE_ENV: 'development'
      },
      exp_backoff_restart_delay: 100
    }
  ]
};
