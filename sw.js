/* Service worker for Dr. 張簡 En-中 Reference.
 *
 * The About screen has always claimed the app is offline-first; before this it
 * would show the load-failure screen with no network. The dictionary is a single
 * immutable 28MB file, which makes it a good fit for cache-first: fetch it once,
 * serve it from the cache forever, and only re-download when CACHE_VERSION moves.
 */

const CACHE_VERSION = 'drt-v3';
const SHELL = [
  './',
  './index.html',
  './style.css',
  './manifest.json',
  './apple-touch-icon.jpg',
  './favicon.ico',
];
const DICTIONARY = './dictionary.jsonl';

// Third-party calls that must never be served stale or cached.
const NETWORK_ONLY = [
  'translate.googleapis.com',
  'api.dictionaryapi.dev',
  'www.google.com',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_VERSION);
    // The shell must succeed; the dictionary is added separately so a failure
    // there doesn't abort the whole install.
    await cache.addAll(SHELL);
    try {
      await cache.add(DICTIONARY);
    } catch (err) {
      // Left uncached — the first online load will populate it via the fetch handler.
      console.warn('[sw] dictionary not precached:', err);
    }
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (NETWORK_ONLY.some((host) => url.hostname.endsWith(host))) return;

  // Cache-first for the dictionary: it is large and content-stable, so a
  // revalidation round trip on every launch would be pure cost.
  if (url.pathname.endsWith('dictionary.jsonl')) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_VERSION);
      const hit = await cache.match(req, { ignoreSearch: true });
      if (hit) return hit;
      const res = await fetch(req);
      if (res && res.ok) cache.put(req, res.clone());
      return res;
    })());
    return;
  }

  // Everything else: serve from cache immediately, refresh in the background.
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_VERSION);
    const hit = await cache.match(req, { ignoreSearch: true });

    const network = fetch(req).then((res) => {
      if (res && res.ok && (url.origin === self.location.origin || res.type === 'cors')) {
        cache.put(req, res.clone()).catch(() => {});
      }
      return res;
    }).catch(() => null);

    if (hit) return hit;

    const res = await network;
    if (res) return res;

    // Offline with nothing cached: for a navigation, fall back to the shell.
    if (req.mode === 'navigate') {
      const shell = await cache.match('./index.html');
      if (shell) return shell;
    }
    return new Response('Offline', { status: 503, statusText: 'Offline' });
  })());
});
