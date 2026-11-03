---
name: proof-mobile
description: >-
  A targeted check for an iOS or Android app, native or React Native / Expo: the user flows on a device, the build, the React Native config, App Store and Google Play rejection risks, phone accessibility, plus the API, sign-in, LLM, on-device model and voice skills only when the app has them. Use when the user says "check my app", "test my mobile app", "iOS / Android check", "Expo check", "is the app ready for the store", or "app store review".
argument-hint: "[--add proof-x] [--skip proof-x]"
---

# proof-mobile

For a phone app, the questions are different: does it work on a device, will a store reviewer reject it, and is it usable with a screen reader. Web-only checks (browser speed, cookie banners, SEO) never run here.

What the user typed after `/proof-mobile`: `$ARGUMENTS`

## What it runs

| skill | checks |
| --- | --- |
| `/proof-mobile-e2e` | user flows with Maestro, a screenshot per result |
| `/proof-mobile-audit` | the built APK/IPA: permissions, debug flags, cleartext, secrets |
| `/proof-store-ready` | account deletion, Sign in with Apple, target SDK, privacy manifest |
| `/proof-mobile-a11y` | labels, touch targets, large text |
| `/proof-sec-secrets`, `/proof-sec-deps` | leaked keys and vulnerable packages |

Only when the review finds it:

| found | adds |
| --- | --- |
| React Native or Expo | `/proof-react-native` |
| an API backend | `/proof-api` |
| sign-in | `/proof-auth` |
| an LLM | `/proof-ai` |
| a model on the device | `/proof-llm-device` |
| voice | `/proof-voice` |

Common additions: `/proof-mobile-explore` (an agent explores the app), `/proof-resilience`, `/proof-design-match`, `/proof-finance`.

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first (what is
   under test, and whether you are authorised to test it).
2. **Plan.** Review the app, then plan this profile. Show the user the review and the
   list before running anything:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile mobile --project .
   ```

   `--add proof-x` adds a skill the profile leaves out; `--skip proof-x` drops one.
3. **Run.** `check.py next` names the next skill; follow that skill's own steps, then
   ask again. `check.py status` shows progress and flags verified high or critical
   findings as they appear. Proof only reads and records: no skill edits the project.
4. **Finish.** `/proof-report`. Then offer the board in one line and run `/proof-board` and `/proof-audit-trail` only on a yes.

## Example

For an Expo app with sign-in that talks to its own API, the plan holds 9 skills:

```
/proof-mobile-e2e  mobile user flows
/proof-api  API contract and awkward inputs
/proof-sec-secrets  committed secrets
/proof-sec-deps  vulnerable dependencies
/proof-auth  sign-in, sessions, reset, logout, rate limits
/proof-mobile-audit  mobile build audit
/proof-react-native  React Native and Expo config and code
/proof-store-ready  App Store and Google Play rejection risks
/proof-mobile-a11y  phone accessibility from UI trees
```

## Not the right fit?

A web app or PWA? `/proof-webapp`. Only the API behind the app? `/proof-backend`. Launching? `/proof-startup`.

## Honest limits

Flows, exploration and phone accessibility need your own device or emulator; the tools never drive a device you did not set up. Without a build file, the build audit is not run. Ship means every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json`, `required_skills` in `proof/target.json`, and
each skill's own evidence. Then `/proof-report`.
