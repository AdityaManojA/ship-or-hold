---
name: proof-pwa
description: >-
  Checks that a web app works as an installable Progressive Web App, from files
  saved off the running site: the page links a manifest, the manifest has the
  fields an install needs, icons exist at 192 and 512 px with one maskable and
  match their declared sizes, a service worker is registered and handles fetches,
  there is an offline fallback, the origin is HTTPS, and the iOS home-screen and
  theme meta are set. Computes a score against a budget and records findings in
  the ledger. Offline, read-only, optional (not part of a full check). Use when
  the user says "is my PWA installable", "PWA check", "add to home screen", "does
  it work offline", "check the manifest", "service worker check", or "test the
  PWA".
argument-hint: "[--html page.html --manifest manifest.json --sw sw.js]"
---

# proof-pwa

A PWA either installs and keeps working with no network, or it quietly behaves
like a bookmark. The difference is a handful of things the browser checks: a
linked manifest with the right fields, real icons, a service worker that answers
fetches, an offline page and a secure origin. This skill reads those from saved
files and records each gap with the file as proof.

It is optional and not part of `/proof-full-check`. Run it on its own when the
product is meant to be installed.

What the user typed after `/proof-pwa`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/pwa.py" check --html saved/home.html --manifest saved/manifest.json --sw saved/sw.js
python3 "${CLAUDE_SKILL_DIR}/pwa.py" check --html saved/home.html --manifest saved/manifest.json --icons-dir frontend/public --json
python3 "${CLAUDE_SKILL_DIR}/pwa.py" record --html saved/home.html --manifest saved/manifest.json --sw saved/sw.js \
  --headers saved/sw-headers.txt --script saved/main.js --icons-dir frontend/public --url https://app.example --budget 80
python3 "${CLAUDE_SKILL_DIR}/pwa.py" status
```

## Step 1: capture the served files

Capture what the browser actually receives, from a site you own or are
authorised to test:

- The page: `curl -s https://app.example/ -o saved/home.html` (or save the page
  source from the browser).
- The manifest: open the `href` of `<link rel="manifest">` and save it, for
  example `curl -s https://app.example/manifest.webmanifest -o saved/manifest.json`.
  Frameworks that generate it at build time (Next.js `app/manifest.ts`, Vite PWA
  plugins) only produce the real file when the app is built and served, so fetch
  the served file, not the source.
- The service worker: the script passed to `navigator.serviceWorker.register`,
  for example `curl -s https://app.example/sw.js -o saved/sw.js`, and its headers
  with `curl -sI https://app.example/sw.js > saved/sw-headers.txt`.
- When the registration lives in a bundled script, save that script and pass it
  with `--script` (repeatable). The browser's Application panel (Service Workers,
  Manifest) shows every URL.
- `--icons-dir` points at the folder icons are served from (often `public/`), so
  each icon is confirmed to exist and its real PNG size is read.

`--url` (or the first URL in `proof/target.json`) decides the HTTPS check;
`localhost` counts as secure, as browsers treat it.

## Step 2: check

`check` prints every check as passing, failing with the reasons, or not checked
because its input was not given. Nothing is assumed: a missing input never passes.

| Check | Looks at | Severity |
| --- | --- | --- |
| manifest-linked | `<link rel="manifest">` in the page | high |
| manifest-fields | name or short_name, id, start_url, scope, display (standalone, fullscreen or minimal-ui), theme_color, background_color | medium |
| icons | a 192x192 and a 512x512 icon, one maskable, files present at their declared sizes | medium |
| service-worker | a `register` call in the page or scripts, a fetch handler, served as JavaScript, not cached for days | high |
| offline | the service worker answers from the cache (caches.match, a precache, an offline page) | medium |
| https | the origin is HTTPS or localhost | high |
| ios | a 180x180 apple-touch-icon and the mobile-web-app-capable meta | low |
| meta | viewport with width=device-width, and theme-color | low |

## Step 3: record

`record` takes the same inputs, copies them into evidence, marks each check
covered or not applicable (with the reason), writes one verified finding per
failing check, and a `pwa-score` from 0 to 100: the weighted share of the checks
that could be run and passed, against `--budget` (default 80). Running it again
drops findings that now pass and leaves anything you marked a false positive.

## What a person still has to check

- Install it: Android Chrome shows the install prompt or menu item, and iOS
  Safari's Share, Add to Home Screen gives the right name and icon.
- Launch from the home-screen icon: it opens `start_url` in its own window, with
  no browser bar, and the splash and theme colours look right.
- Turn on airplane mode and reload or open the app: the offline page appears, not
  the browser's error page, and nothing that needs the network pretends to work.
- Ship a change to the service worker: the app picks up the new version (an update
  prompt, or on the next launch) and does not stay stuck on the old one.

## Honest limits

This reads static files. It finds a fetch handler or a cache call by pattern, not
by running the worker, so a worker built by a tool in an unusual way can be
misread either way; the airplane-mode test above is the real proof. Install
criteria change between browsers and versions. It does not run Lighthouse.

## Output / next

`proof/coverage/proof-pwa.json`, findings under `proof/findings/proof-pwa/`,
`proof/scores/proof-pwa.json`, the saved files under `proof/evidence/proof-pwa/`.
Then `/proof-report`.
