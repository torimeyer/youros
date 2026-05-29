/**
 * myOS Service Worker
 *
 * Handles two things:
 * 1. Push notifications: listens for push events and shows native OS notifications
 * 2. Offline mode: caches the app shell and recent API data so the app works offline
 *
 * Caching strategy:
 * - Navigation requests (HTML): network-first. This ensures the browser always
 *   gets the latest index.html after a deploy or restart. Falls back to cache
 *   when offline.
 * - Static assets (JS/CSS/images): cache-first. Vite hashes filenames, so a
 *   new deploy produces new URLs. Stale entries are harmless and get cleaned
 *   up on the next service worker activate.
 * - API GET requests: network-first with cache fallback for offline use.
 */

// Cache names. Bump the version suffix to bust stale caches on deploy.
var SHELL_CACHE = 'myos-shell-v2';
var DATA_CACHE = 'myos-data-v1';

// App shell files to cache on install.
var SHELL_FILES = [
  '/',
  '/index.html',
];

// API paths whose GET responses we cache for offline use.
var CACHEABLE_API_PATHS = [
  '/api/tasks',
  '/api/dashboard',
  '/api/settings',
  '/api/notifications',
  '/api/notifications/unread/count',
  '/api/agents',
  '/api/health',
];

// ---------------------------------------------------------------
// Install: pre-cache the app shell
// ---------------------------------------------------------------
self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(function (cache) {
      return cache.addAll(SHELL_FILES);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

// ---------------------------------------------------------------
// Activate: clean up old caches
// ---------------------------------------------------------------
self.addEventListener('activate', function (event) {
  var currentCaches = [SHELL_CACHE, DATA_CACHE];
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names.filter(function (name) {
          return currentCaches.indexOf(name) === -1;
        }).map(function (name) {
          return caches.delete(name);
        })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

// ---------------------------------------------------------------
// Fetch: network-first for navigation, cache-first for assets,
//        network-first for API
// ---------------------------------------------------------------
self.addEventListener('fetch', function (event) {
  var url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Skip WebSocket upgrade requests
  if (event.request.headers.get('Upgrade') === 'websocket') return;

  // API requests: network-first with cache fallback
  if (url.pathname.startsWith('/api/')) {
    if (event.request.method === 'GET' && isCacheableApiPath(url.pathname)) {
      event.respondWith(networkFirstThenCache(event.request));
    }
    return;
  }

  // Navigation requests (HTML pages): ALWAYS network-first.
  // This is the key fix. Without this, after a deploy or restart the
  // browser would serve the old cached index.html which references old
  // JS/CSS bundle URLs, so no code changes would appear until hard refresh.
  if (event.request.mode === 'navigate') {
    event.respondWith(networkFirstThenCache(event.request));
    return;
  }

  // Static assets (JS/CSS/images/fonts): cache-first.
  // Vite content-hashes filenames, so new deploys produce new URLs.
  event.respondWith(cacheFirstThenNetwork(event.request));
});

function isCacheableApiPath(pathname) {
  for (var i = 0; i < CACHEABLE_API_PATHS.length; i++) {
    if (pathname === CACHEABLE_API_PATHS[i]) return true;
  }
  return false;
}

function cacheFirstThenNetwork(request) {
  return caches.match(request).then(function (cached) {
    if (cached) return cached;
    return fetch(request).then(function (response) {
      if (response.ok) {
        var clone = response.clone();
        caches.open(SHELL_CACHE).then(function (cache) {
          cache.put(request, clone);
        });
      }
      return response;
    }).catch(function () {
      // If both cache and network fail for a navigation request,
      // serve the cached index.html (SPA fallback)
      if (request.mode === 'navigate') {
        return caches.match('/index.html');
      }
      return new Response('Offline', { status: 503, statusText: 'Offline' });
    });
  });
}

function networkFirstThenCache(request) {
  return fetch(request).then(function (response) {
    if (response.ok) {
      var clone = response.clone();
      // Use the right cache bucket depending on the request type
      var bucket = request.url.indexOf('/api/') !== -1 ? DATA_CACHE : SHELL_CACHE;
      caches.open(bucket).then(function (cache) {
        cache.put(request, clone);
      });
    }
    return response;
  }).catch(function () {
    return caches.match(request).then(function (cached) {
      if (cached) {
        var headers = new Headers(cached.headers);
        headers.set('X-MyOS-Cached', 'true');
        return new Response(cached.body, {
          status: cached.status,
          statusText: cached.statusText,
          headers: headers,
        });
      }
      // Offline SPA fallback for navigation
      if (request.mode === 'navigate') {
        return caches.match('/index.html');
      }
      return new Response(
        JSON.stringify({ error: 'offline', detail: 'No cached data available' }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      );
    });
  });
}

// ---------------------------------------------------------------
// Push: show native OS notification
// ---------------------------------------------------------------
self.addEventListener('push', function (event) {
  var data = { title: 'myOS', body: 'New notification', url: '/', tag: 'myos' };

  if (event.data) {
    try {
      data = Object.assign(data, event.data.json());
    } catch (e) {
      data.body = event.data.text();
    }
  }

  var options = {
    body: data.body,
    icon: '/favicon.svg',
    badge: '/favicon.svg',
    tag: data.tag || 'myos',
    renotify: false,
    // Keep web push unobtrusive: no audible alert and no re-alert on repeat
    // pushes. The in-app toast already surfaces the event visually.
    silent: true,
    data: { url: data.url || '/' },
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// ---------------------------------------------------------------
// Notification click: focus or open the app
// ---------------------------------------------------------------
self.addEventListener('notificationclick', function (event) {
  event.notification.close();

  var targetUrl = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        var client = clients[i];
        if (client.url.indexOf(self.location.origin) !== -1) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      return self.clients.openWindow(targetUrl);
    })
  );
});

// ---------------------------------------------------------------
// Message: allow the main thread to communicate with the SW
// ---------------------------------------------------------------
self.addEventListener('message', function (event) {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data && event.data.type === 'CACHE_URLS') {
    var urls = event.data.urls || [];
    caches.open(DATA_CACHE).then(function (cache) {
      urls.forEach(function (url) {
        fetch(url).then(function (resp) {
          if (resp.ok) cache.put(url, resp);
        }).catch(function () {});
      });
    });
  }
});
