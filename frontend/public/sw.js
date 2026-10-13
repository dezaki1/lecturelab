// LectureLab service worker: makes the app installable and keeps the app shell available when
// the PC is briefly unreachable. Data (/api) always comes from the network, never the cache.
const SHELL = 'lecturelab-shell-v1'

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)
  if (req.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return
  if (req.mode === 'navigate') {
    // Network first so a new build shows up at once; the cached shell only as a fallback.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) caches.open(SHELL).then((c) => c.put('/', res.clone()))
          return res
        })
        .catch(() => caches.match('/').then((r) => r || Response.error())),
    )
  } else if (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/icons/')) {
    // Hashed build files never change: cache first.
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) caches.open(SHELL).then((c) => c.put(req, res.clone()))
            return res
          }),
      ),
    )
  }
})
