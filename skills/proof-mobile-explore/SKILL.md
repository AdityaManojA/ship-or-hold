---
name: proof-mobile-explore
description: >-
  Exploratory testing of a real iOS or Android app by an AI agent, with evidence.
  Writes a brief of goals (sign up, check out, ask the assistant a question), the
  agent drives the app on the user's own emulator, simulator or device with
  whatever driver the session has (mobile MCP tools, or Midscene, Mobilerun or
  Maestro on the PATH) and saves a screenshot per step, then the tool reads the
  session and the device logs and records every crash, ANR, dead end and blocked
  goal as a verified finding. The tool never drives the device itself. Use when
  the user says "explore my app", "test the app on my phone", "let an agent try
  the app", "find crashes in the app", "monkey test", "agentic app testing", or
  "try to break the mobile app".
argument-hint: "[app id and goals, or blank to set them up]"
---

# proof-mobile-explore

Scripted flows only test the paths someone thought to write down. An agent that
uses the app the way a person would, toward a goal, finds the screens that lead
nowhere, the button that freezes the app and the crash three taps off the happy
path. This skill plans that exploration, then judges it from what was saved: the
agent's step-by-step screenshots and the device's own crash logs.

What the user typed after `/proof-mobile-explore`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/mexplore.py" plan --app com.example.shop --goal "sign up" --goal "check out" --platform android
python3 "${CLAUDE_SKILL_DIR}/mexplore.py" logs saved/logcat.txt
python3 "${CLAUDE_SKILL_DIR}/mexplore.py" record --session saved/session.json --logcat saved/logcat.txt --crash-report saved/App.ips
python3 "${CLAUDE_SKILL_DIR}/mexplore.py" status
```

## Before you start

Only explore an app the user builds, on an emulator, simulator or device they
control. The agent must not sign in to real third-party accounts, touch other
apps' data or make a purchase that charges a real payment method. Use a test
account and a debug or staging build.

## Step 1: plan

```bash
python3 "${CLAUDE_SKILL_DIR}/mexplore.py" plan --app com.example.shop --goal "sign up" --goal "check out"
```

Writes `proof/evidence/proof-mobile-explore/brief.md`: the goals, the rules, and
which command-line drivers are installed (adb, xcrun, maestro, midscene,
mobilerun). Each goal becomes a coverage check. Choose goals a real user has: the
main task, the paid path, the AI feature, settings, an error case.

## Step 2: explore

Run the brief. When the session has mobile MCP tools loaded (listing elements,
tapping, typing, screenshots), use those; otherwise use a driver from the list.
For each goal, work toward it as a user would, take a screenshot after every
step, and stop when it is reached, after three steps with no progress (dead_end),
when the app closes or freezes (crash), or when something outside the app stops
you (blocked). Then save the device log: `adb logcat -d > logcat.txt` on Android,
or the `.ips` crash reports on iOS.

## The session file

The agent writes one JSON file; screenshot paths are relative to it:

```json
{"app": "com.example.shop", "platform": "android", "driver": "mobile-mcp",
 "goals": [
   {"goal": "sign up", "outcome": "reached",
    "steps": [{"action": "tap Sign up", "screenshot": "shots/01.png", "note": ""}]},
   {"goal": "check out", "outcome": "dead_end", "note": "Pay button does nothing",
    "steps": [{"action": "tap Pay", "screenshot": "shots/07.png", "note": "no response"}]}
 ]}
```

`outcome` is `reached`, `dead_end`, `crash` or `blocked`. `record` rejects a
session without goals or with an unknown outcome.

## Step 3: check the logs

```bash
python3 "${CLAUDE_SKILL_DIR}/mexplore.py" logs saved/logcat.txt
```

Lists every `FATAL EXCEPTION` with its process and exception, every `ANR in`,
and native signals such as SIGSEGV and SIGABRT. A file ending in `.ips` is read
as an iOS crash report.

## Step 4: record

```bash
python3 "${CLAUDE_SKILL_DIR}/mexplore.py" record --session saved/session.json --logcat saved/logcat.txt
```

Copies the session, its screenshots and the logs into
`proof/evidence/proof-mobile-explore/`, marks every goal covered, and writes a
verified finding for each goal that was not reached (crash high, dead end medium,
blocked low, evidence: the last screenshot) and for each crash or ANR in the logs
(high, evidence: the log). `--crash-report` can be given more than once. Each
finding's repro points at the session file, so a person can follow the steps.

## Honest limits

An agent is not a user. It misses things a person would notice (a confusing
label, an ugly screen) and can report a dead end that is really its own
misreading of the screen, so look at the screenshots before acting on a dead
end. Exploration is not exhaustive: a goal that was reached once can still fail
on another device, OS version or network. The log parsing covers common Android
and iOS crash formats, not every vendor's. The tool does not drive the device;
the quality of the run depends on the driver the session has.

## Output / next

`proof/coverage/proof-mobile-explore.json`, findings under
`proof/findings/proof-mobile-explore/`, the brief, session, screenshots and logs
under `proof/evidence/proof-mobile-explore/`. Then `/proof-report`.
