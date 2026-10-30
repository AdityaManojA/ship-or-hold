---
name: proof-react-native
description: >-
  A static, offline audit of a React Native or Expo app's own source and config,
  for problems that ship to users: secrets compiled into the bundle (app config
  extra, EXPO_PUBLIC_ variables, keys in the source), tokens and passwords kept in
  plain AsyncStorage, cleartext traffic, permissions that do not match the code or
  lack an iOS usage description, debug leftovers in release builds, deep-link
  handlers that open whatever they are given, over-the-air update settings,
  Android backups and expo-doctor failures. Read-only; it never builds or runs the
  app. Optional, not part of a full check. Use when the user says "audit my React
  Native app", "check my Expo app", "is my Expo config safe", "are my tokens stored
  safely", "check app permissions", "react native security", or "is the app ready
  for the app store".
argument-hint: "[project folder, blank for the current one]"
---

# proof-react-native

Most problems in a React Native app are visible before it is ever built: a key in
`extra` or an `EXPO_PUBLIC_` variable is compiled into the bundle for anyone to
read, a refresh token sits in AsyncStorage as plain text, an API is called over
http, the camera is used with no iOS usage description, and App Review rejects the
build. This skill reads the project and points at each one with a file and line.

It is optional and not part of a full check. It pairs with `/proof-mobile-audit`
(the built APK or IPA), `/proof-mobile-e2e` (flows on a device),
`/proof-mobile-a11y` (accessibility from UI trees) and `/proof-ai` with
`plan --mobile` (an assistant inside the app).

What the user typed after `/proof-react-native`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/rncheck.py" scan .
python3 "${CLAUDE_SKILL_DIR}/rncheck.py" scan . --expo-config expo-config.json --doctor doctor.txt
python3 "${CLAUDE_SKILL_DIR}/rncheck.py" scan . --json
python3 "${CLAUDE_SKILL_DIR}/rncheck.py" record . --expo-config expo-config.json --doctor doctor.txt
python3 "${CLAUDE_SKILL_DIR}/rncheck.py" status
```

`--show N` sets how many hits `scan` prints per check.

## Step 1: save the resolved config (recommended)

`app.config.js` and `app.config.ts` are code, so the tool can only read their
obvious literals. Save what Expo actually resolves, from the project folder:

```bash
npx expo config --json > expo-config.json
npx expo-doctor > doctor.txt
```

Pass them with `--expo-config` and `--doctor`. Without `--expo-config` the tool
reads `app.json`, or the literals in `app.config.*`, and says when dynamic values
were not evaluated. Without `--doctor` the version check is marked not applicable.

## Step 2: scan

```bash
python3 "${CLAUDE_SKILL_DIR}/rncheck.py" scan . --expo-config expo-config.json --doctor doctor.txt
```

It reads `package.json`, the app config, `eas.json`, `.env*` files, the native
`android/` and `ios/` folders when they exist, and the JS/TS source (skipping
`node_modules`, `.expo`, build output and tests).

| Check | What it flags | Severity |
| --- | --- | --- |
| secrets-in-bundle | Secret-named `EXPO_PUBLIC_` variables, secret-looking values in `extra`, `.env` or the source (live Stripe keys, OpenAI and Anthropic keys, AWS ids, private keys, a Supabase `service_role` JWT) | high |
| token-storage | `AsyncStorage.setItem` with a token, auth, session or password key | high |
| cleartext | Android cleartext traffic, iOS arbitrary loads, plain `http://` URLs (local addresses excepted) | medium, high for API URLs |
| permissions | A capability used with no iOS usage description (high), a permission the code never uses (low), a usage description that does not say why (low) | high / low |
| debug-in-release | `console.log` in app code (low), a debuggable manifest or a development client in a store profile (medium) | low / medium |
| deep-links | A deep-link handler that opens a URL taken from its input | medium |
| updates | `expo-updates` with no `runtimeVersion`, or updates over http | medium |
| android-backup | Android backup left on while the app stores data on the device | low |
| versions | Each failed `expo-doctor` check | medium |
| tests | Whether test files and jest exist (a note, never a finding) | none |

Secrets are always masked: at most the first four characters are shown.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/rncheck.py" record . --expo-config expo-config.json --doctor doctor.txt
```

One coverage check per row above, marked not applicable with a reason when it does
not apply (no deep links, no `expo-updates`, no doctor output). Every hit becomes a
verified finding with its file and line, backed by the saved scan. A hit that is
intended (a public, restricted key, say) can be rejected as a false positive with a
reason.

## What a person still has to check

- That keys the tool cannot recognise are not secrets: read `extra` and every
  `EXPO_PUBLIC_` variable yourself.
- How the backend authorises calls; a key being public is only safe if the server
  limits what it can do.
- Runtime behaviour on a device, which `/proof-mobile-e2e` and
  `/proof-mobile-explore` cover, and the built binary, which
  `/proof-mobile-audit` covers.

## Honest limits

Pattern matching on source, not a type-aware analysis: a token passed through a
helper before reaching AsyncStorage is missed, and a log behind a `__DEV__` check is
still counted. `app.config.*` is not executed, so pass `--expo-config` for the real
values. Native code you wrote in `android/` and `ios/` beyond the manifest and
`Info.plist` is not reviewed. Dependency versions are only judged through
`expo-doctor`; known vulnerabilities are `/proof-sec-deps`.

## Output / next

`proof/coverage/proof-react-native.json`, findings under
`proof/findings/proof-react-native/`, the scan under
`proof/evidence/proof-react-native/rn-scan.json`. Then `/proof-report`.
