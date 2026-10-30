---
name: proof-store-ready
description: >-
  Catches App Store and Google Play rejection risks before you submit, from the
  project and a few saved files, offline: no in-app account deletion, third-party
  login on iOS without Sign in with Apple, an Android target SDK below Play's
  floor, a missing or incomplete iOS privacy manifest, analytics, crash and ads
  SDKs that the privacy declarations do not mention, version and build numbers,
  export compliance, the App Store icon (1024 px, no alpha) and Android adaptive
  icon, and screenshot sizes. Works for Expo and React Native and for native
  projects. Optional, not part of a full check. Use when the user says "is the app
  ready for the App Store", "will Apple reject this", "Play Store submission",
  "store readiness", "app review checklist", "why was my app rejected", or "before
  we submit the app".
argument-hint: "[project folder, blank for the current one]"
---

# proof-store-ready

Most store rejections are for things visible in the project before anyone builds
it: the app lets people sign up but never lets them delete the account, it offers
Google login on iOS without Sign in with Apple, it targets an Android version Play
no longer accepts, or it ships an analytics SDK the privacy labels never mention.
This skill reads the project and points at each risk with a file, a line and the
guideline it breaks.

It is optional and not part of a full check. It pairs with `/proof-react-native`
(permissions, secrets and storage in the code), `/proof-mobile-audit` (the built
APK or IPA) and `/proof-privacy` (the web side).

What the user typed after `/proof-store-ready`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/store.py" scan .
python3 "${CLAUDE_SKILL_DIR}/store.py" scan . --expo-config expo-config.json --data-safety data-safety.csv --assets store/
python3 "${CLAUDE_SKILL_DIR}/store.py" scan . --json
python3 "${CLAUDE_SKILL_DIR}/store.py" record . --expo-config expo-config.json --assets store/
python3 "${CLAUDE_SKILL_DIR}/store.py" status
```

## Step 1: gather the inputs

All optional; each one missing marks its check not applicable, never passed.

- **The resolved Expo config.** `app.config.js` is code, so save what it produces:
  `npx expo config --json > expo-config.json`, then pass `--expo-config`.
- **A privacy manifest** outside the project: `--privacy path/PrivacyInfo.xcprivacy`.
  One inside `ios/` or in `ios.privacyManifests` is found by itself.
- **The Play Data safety answers**: export the CSV from Play Console (App content,
  Data safety) and pass `--data-safety`.
- **Store screenshots**: a folder of the PNG or JPEG files you will upload, `--assets`.
- **The Play target SDK floor**: `--min-target-sdk` (default 35). Play raises it
  each year; set it to the current requirement.

## Step 2: what it checks

| Check | Flags | Severity | Rule |
| --- | --- | --- | --- |
| account-deletion | Sign-up or login with no delete-account path in the app | high | App Store 5.1.1(v), Play account deletion |
| sign-in-with-apple | Google, Facebook or other login on iOS without Sign in with Apple | high | App Store 4.8 |
| target-sdk | targetSdkVersion below the Play floor | high | Play target API level |
| privacy-manifest | No manifest while required-reason APIs are used (AsyncStorage counts as UserDefaults), or a used category not declared | medium | Apple required reason APIs |
| tracking-sdks | PostHog, Sentry, Firebase, Amplitude, Mixpanel, Segment, AppsFlyer, AdMob or Facebook SDK not reflected in the manifest's collected data or the Play Data safety export | medium | App Privacy, Play Data safety |
| versioning | No version, or a build number or versionCode that is not a number | low | |
| export-compliance | The encryption question left unanswered | low | |
| icons | App Store icon not 1024 x 1024 or with an alpha channel; no Android adaptive icon | medium | |
| screenshots | No iPhone shot at 1320x2868, 1290x2796 or 1284x2778; no 13-inch iPad shot when tablets are supported; fewer than two Play shots between 320 and 3840 px within 2:1 | low | |
| permissions | Not checked here: `/proof-react-native` matches permissions to the code | n/a | |

For Expo, the target SDK comes from `expo-build-properties`; without it, Expo's
SDK sets it, and the tool notes to confirm it in the EAS build log.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/store.py" record . --expo-config expo-config.json --assets store/
```

One coverage check per row, one verified finding per problem with its file, line,
fix and guideline, and the scan saved as `evidence/proof-store-ready/store-scan.json`.
Recording again drops findings that are fixed and keeps any you marked a false
positive.

## What a person still has to do

- Review notes and a working demo account for any login-gated app.
- The age rating questionnaire and content rights (music, images, trademarks).
- In-app purchase rules if the app sells digital goods or subscriptions.
- App Privacy answers in App Store Connect and the Play Data safety form filled in
  to match the code, not only checked against it.
- New personal Play developer accounts: a closed test with testers for 14 days
  before production access.
- A last look at the current guideline text, because the rules change.

## Honest limits

These are patterns read from the project, not App Review. A delete-account path is
recognised by its words and calls, so one behind an unusual name can be missed,
and a match does not prove deletion really removes the data. Store rules and the
Play target SDK move every year; the guideline references say where to check.
Dynamic `app.config.js` values are only seen through `--expo-config`.

## Output / next

`proof/coverage/proof-store-ready.json`, findings under
`proof/findings/proof-store-ready/`, the scan under
`proof/evidence/proof-store-ready/`. Then `/proof-report`.
