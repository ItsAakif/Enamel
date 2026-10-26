# PWA and offline behaviour

Enamel ships a web app manifest and a minimal service worker that cache the
**application shell only**. No API response and no patient data is ever written
to Cache Storage.

## Files

| File | Purpose |
| --- | --- |
| `artifacts/enamel-landing/public/manifest.webmanifest` | Installable manifest (`start_url: /portal`, standalone). |
| `artifacts/enamel-landing/public/sw.js` | App-shell service worker (`enamel-shell-v1`). |
| `artifacts/enamel-landing/src/lib/pwa.ts` | Registration, cache clearing, `useOnlineStatus`. |
| `artifacts/enamel-landing/index.html` | `<link rel="manifest">` + `theme-color`. |
| `artifacts/enamel-landing/src/main.tsx` | `registerServiceWorker()` on boot. |

## What the service worker caches

- `/index.html` (the SPA shell), `/manifest.webmanifest`, `/favicon.svg` — precached on install.
- Navigation responses are cached under the stable key `/index.html`
  (network-first: the live response is returned, a copy is stored for offline).
- `/assets/*` (content-hashed production bundles) — cache-first, since the name
  changes whenever the content does.

## What it never caches

- `/api/*` — every JSON response, including patient/clinical data.
- `/media/*` — uploaded documents and imaging.
- Any non-`GET` request, any cross-origin request.
- Development module paths (`/src/*`, `/@vite/*`) fall through to the network so
  hot reload keeps working.

The rule lives in one place in `public/sw.js` (`isCacheableShellRequest`) and is
covered by an E2E assertion in `e2e/portal.spec.ts` that enumerates Cache Storage
and fails if any entry points at `/api/`.

## Offline UX

- `useOnlineStatus()` drives a persistent banner (`data-testid="offline-banner"`)
  in `PortalLayout`. While offline the cached shell still loads, but the banner
  states that live actions are unavailable — no stale clinical data is presented
  as current.
- Pages render their normal loading/error/empty states; a failed request shows
  the API error, never fabricated data.

## Cache clearing

`clearAppCaches()` deletes every `enamel-shell-*` cache and messages the active
worker to drop its caches too. It is called on **portal logout** so a shared
device cannot serve a previously cached shell to the next patient.

## Verification

- `pnpm --filter @workspace/enamel-landing run build` — the manifest and `sw.js`
  are copied to `dist/public`.
- `pnpm run e2e -- e2e/portal.spec.ts` — asserts the manifest is served, the
  service worker registers, and Cache Storage contains no `/api/` entries.
