---
name: proof-resilience
description: >-
  Checks how a web or mobile app behaves when the network or the backend
  misbehaves, with evidence for every result: offline, a slow network, server
  errors, requests that never answer, a cold start of a sleeping backend, a fast
  double tap on submit, retry storms, one API failing while the rest works, and a
  session that expires mid-task (plus backgrounding and network switches on a
  phone). Gives the way to cause each one on your own app and what a pass looks
  like, records each result with a screenshot or capture, and reads HAR files for
  duplicate writes, retry storms, very slow requests and a slow first request.
  Optional, not part of a full check. Use when the user says "what happens when
  the network drops", "test offline", "resilience test", "does it handle server
  errors", "double submit", "chaos test my app", "slow network test", or "cold
  start".
argument-hint: "[a scenario to focus on, or blank for all]"
---

# proof-resilience

Most apps are tested on a fast network against a backend that always answers.
Real users are on a train, on hotel Wi-Fi, or hitting a free-tier host that went
to sleep. This skill walks through the ways the network and the backend fail, has
you cause each one on your own app, and records what the app did, backed by a
screenshot, a screen recording or a HAR capture.

It is optional and never part of `/proof-full-check` or
`/proof-full-check-no-seo`. Run it on its own when you want it.

What the user typed after `/proof-resilience`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/resilience.py" plan                 # the scenario checklist
python3 "${CLAUDE_SKILL_DIR}/resilience.py" plan --mobile        # plus backgrounding and network switches
python3 "${CLAUDE_SKILL_DIR}/resilience.py" har capture.har      # read a HAR
python3 "${CLAUDE_SKILL_DIR}/resilience.py" record-har capture.har --scenario double-submit
python3 "${CLAUDE_SKILL_DIR}/resilience.py" result offline pass --evidence evidence/proof-resilience/offline.png
python3 "${CLAUDE_SKILL_DIR}/resilience.py" status
```

## Only your own app

Break only an app you own or are allowed to test, and prefer a local or staging
copy. Every scenario is caused on your side (your browser, your emulator, your
proxy, your staging backend); nothing here sends traffic to anyone else.

## Step 1: the plan

```bash
python3 "${CLAUDE_SKILL_DIR}/resilience.py" plan --mobile
```

Each scenario prints how to cause it and what a pass looks like:

| Scenario | Cause it | Pass |
| --- | --- | --- |
| offline | DevTools Offline, `context.set_offline(True)`, airplane mode | A clear message; work kept or queued |
| slow-network | DevTools slow 3G, `adb emu network speed gsm`, iOS Network Link Conditioner | Loading states, no double submit |
| server-error | Playwright `route.fulfill(status=500)`, a proxy | A friendly error with retry |
| timeout | A route that never answers | The app gives up and says so |
| cold-start | Let a sleeping host idle, then open the app with a HAR running | A loading state, no failure |
| double-submit | Tap submit, pay or send twice fast | One action only |
| retry-storm | Fail an endpoint with 503 | Few retries, spaced out |
| partial-failure | Abort one API, keep the rest | Only that part shows an error |
| token-expiry-midway | Expire the session during a task | Sign in again without losing work |
| background-resume (mobile) | Background or lock the phone mid-request | Finishes or retries cleanly |
| network-switch (mobile) | `adb shell svc wifi disable` mid-request | Recovers, no duplicate action |

Useful ways to cause them:

- **Browser:** DevTools > Network: Offline, throttling presets, request blocking.
- **Playwright:** `route.abort()`, `route.fulfill(status=500)`, a route handler that
  waits before fulfilling, `context.set_offline(True)`.
- **Android emulator:** `adb emu network delay gprs`, `adb emu network speed gsm`,
  `adb shell svc wifi disable` and `adb shell svc data disable`.
- **iOS:** the Network Link Conditioner (developer settings on device, or the
  Additional Tools for Xcode on a Mac).
- **A proxy** in front of a staging backend, for delays and error codes.

## Step 2: capture and read the network

Record the browser's Network panel during a scenario and save it as a HAR
(right-click > Save all as HAR), or use Playwright's `record_har_path`. Then:

```bash
python3 "${CLAUDE_SKILL_DIR}/resilience.py" har capture.har --scenario double-submit
```

It reports duplicate writes (the same POST, PUT, PATCH or DELETE with the same
body within `--window` seconds, default 2), retry storms (more than
`--max-retries` requests to one endpoint in 10 seconds after an error, default 5),
requests slower than `--slow-ms` (default 10000), and the first request's time
against the median, which shows a cold start. `--json` prints the raw result.

## Step 3: record

From a HAR, which writes verified findings with the HAR as evidence:

```bash
python3 "${CLAUDE_SKILL_DIR}/resilience.py" record-har capture.har --scenario retry-storm
```

From what you saw on screen, with a screenshot or recording as evidence:

```bash
python3 "${CLAUDE_SKILL_DIR}/resilience.py" result server-error fail \
  --evidence evidence/proof-resilience/500.png \
  --title "A 500 on /chat leaves the send button spinning forever" --severity high --verify
python3 "${CLAUDE_SKILL_DIR}/resilience.py" result offline pass --evidence evidence/proof-resilience/offline.png
python3 "${CLAUDE_SKILL_DIR}/resilience.py" na token-expiry-midway --note "the app has no sign-in"
```

A pass needs an evidence file; a failure needs `--title` and becomes a finding
(`--severity`, `--repro` and `--fix` are optional, `--verify` promotes it once the
evidence is saved).

## Honest limits

This records what happened in the runs you did; it does not prove the app handles
every failure. The HAR checks see only what the capture saw, a duplicate write in
the HAR may still be deduplicated by the server (check the data), and a slow
request can be the test machine. Simulated failures are not identical to a real
outage, so repeat the important ones on a real device and network.

## Output / next

`proof/coverage/proof-resilience.json`, findings under
`proof/findings/proof-resilience/`, HARs and screenshots under
`proof/evidence/proof-resilience/`. Then `/proof-report`.
