---
name: proof-mobile-e2e
description: >-
  End-to-end testing of a mobile app's user flows, with evidence for each. Plans the
  critical flows (sign in, the main task, checkout), generates Maestro flow files to
  run on a device or emulator, and records each flow as a pass backed by a
  screenshot or a failing finding. Falls back to manual-guided steps when Maestro is
  not installed. Use when the user says "test the mobile app", "test the app on a
  phone", "android e2e", "ios flow test", "does the app work on a device", or
  "mobile end to end".
argument-hint: "[a flow to focus on, or blank for all]"
---

# proof-mobile-e2e

Walk the app on a real device or an emulator, flow by flow, and leave a screenshot
behind each pass and a repro behind each failure, the same discipline as the web
end-to-end skill.

What the user typed after `/proof-mobile-e2e`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" plan flows.json
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" maestro sign-in --out proof/mobile/sign-in.yaml --app com.example.app
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" result sign-in pass --evidence evidence/proof-mobile-e2e/sign-in.png
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" status
```

## Step 1: plan the flows

Write a flow spec (an id, title, whether it is `critical`, the steps, and the
expected end state), then:

```bash
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" plan flows.json
```

## Step 2: generate and run

If Maestro is installed (check with `/proof-doctor --for proof-mobile-e2e`):

```bash
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" maestro sign-in --out proof/mobile/sign-in.yaml --app com.example.app
maestro test proof/mobile/sign-in.yaml
```

Refine the generated `tapOn` selectors to match the real UI. The flow ends with a
screenshot saved as evidence. Without Maestro, drive the flow by hand on the device
or emulator and save a screenshot to `proof/evidence/proof-mobile-e2e/<flow>.png`.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" result sign-in pass --evidence evidence/proof-mobile-e2e/sign-in.png
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" result checkout fail --evidence <path> --title "Pay button does nothing on Android 13" --severity high --repro "..." --verify
python3 "${CLAUDE_SKILL_DIR}/flowgen.py" skip widget --note "no home-screen widget in this build"
```

A pass needs an evidence file; a failure writes a finding. Reproduce a suspected
bug once more before recording it.

## Honest limits

A flow proves that path on the one device or emulator and OS version you ran it on.
Mobile behaviour varies a lot across devices, OS versions and screen sizes, so a
pass on one is not a pass on all. Generated Maestro selectors are a starting point
and usually need adjusting to the real view hierarchy.

## Output / next

`proof/mobile/flows.json`, generated flows under `proof/mobile/`,
`proof/coverage/proof-mobile-e2e.json`, findings and evidence under their folders.
Then `/proof-mobile-audit` and `/proof-report`.
