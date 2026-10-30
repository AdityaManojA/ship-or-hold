---
name: proof-mobile-a11y
description: >-
  Accessibility checks for a native iOS or Android app from captured UI trees,
  offline. Flags controls a screen reader cannot name, touch targets under 48 x 48
  dp (Android) or 44 x 44 pt (iOS), labels that say nothing or repeat ("button",
  "img_3", three controls all called "Edit"), and, from a second capture at the
  largest font size, text that is truncated, runs off the screen or overlaps.
  Records each problem per screen as a verified finding with the tree as evidence,
  and lists the checks only a person can do (a TalkBack or VoiceOver walk-through,
  focus order, gestures). Use when the user says "mobile accessibility", "app
  accessibility", "TalkBack", "VoiceOver", "is my app accessible", "touch target
  size", "dynamic type", "large text breaks my app", or "a11y check for the app".
argument-hint: "[a saved UI tree, or blank to capture one]"
---

# proof-mobile-a11y

Most accessibility problems in a native app are visible in its UI tree: an icon
button with no label, so a screen reader says only "button"; a close icon 24 points
wide that a shaky finger misses; a price that turns into "$1..." when someone uses
large text. This skill reads the tree of each screen and records those problems
with the tree as proof.

What the user typed after `/proof-mobile-a11y`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/ma11y.py" check saved/home.xml
python3 "${CLAUDE_SKILL_DIR}/ma11y.py" check saved/home.xml --large saved/home-large.xml --dpi 440
python3 "${CLAUDE_SKILL_DIR}/ma11y.py" check saved/home.json --platform ios --json
python3 "${CLAUDE_SKILL_DIR}/ma11y.py" record saved/home.xml --large saved/home-large.xml --screen home
python3 "${CLAUDE_SKILL_DIR}/ma11y.py" status
```

## Step 1: capture each screen

Open the app on an emulator, simulator or device and go to the screen to check.

- **Android:** `adb shell uiautomator dump /sdcard/home.xml` then
  `adb pull /sdcard/home.xml saved/home.xml`. Note the screen density from
  `adb shell wm density` and pass it as `--dpi` (sizes in the dump are pixels; the
  tool converts them to dp).
- **iOS simulator:** `idb ui describe-all --udid <id> > saved/home.json`. The output
  is read as is (AXLabel, AXFrame or frame, type, role). Sizes are in points.
- **Any platform through an agent tool:** a mobile automation MCP server that lists
  screen elements can save them in the generic shape below.

Generic JSON, one screen, sizes in points (iOS) or dp (Android):

```json
{"platform": "ios", "screen": {"w": 390, "h": 844},
 "elements": [{"id": "send", "label": "Send", "type": "Button", "traits": ["button"],
               "clickable": true, "x": 300, "y": 780, "w": 60, "h": 44, "text": ""}]}
```

A bare list of elements works too. `screen` is optional; without it the largest
element's extent is used.

## Step 2: capture the same screen at the largest font size

- **Android:** `adb shell settings put system font_scale 2.0`, reopen the screen,
  dump again to `saved/home-large.xml`, then `adb shell settings put system font_scale 1.0`.
- **iOS simulator:** Settings, Accessibility, Display and Text Size, Larger Text,
  turn on Larger Accessibility Sizes and drag to the end (or use the Environment
  Overrides panel in Xcode), then `idb ui describe-all` again.

Pass it with `--large`. Without it the large-text check stays not run.

## Step 3: check and record

```bash
python3 "${CLAUDE_SKILL_DIR}/ma11y.py" check saved/home.xml --large saved/home-large.xml
python3 "${CLAUDE_SKILL_DIR}/ma11y.py" record saved/home.xml --large saved/home-large.xml --screen home
```

| Check | Flags | Severity |
| --- | --- | --- |
| Unlabelled controls | A button, switch, field or other clickable element with no label, text or labelled child | high |
| Touch targets | A control smaller than 48 x 48 dp (Android) or 44 x 44 pt (iOS) | medium |
| Unclear names | Labels like "button", "icon", "img_3" or a file name, and several controls with the same label | low |
| Large text | Text ending in an ellipsis, running off the screen, or overlapping other text at the largest size | medium |

`record` adds four coverage checks per screen (`home-unlabeled`, `home-targets`,
and so on), one verified finding per check that has problems, with the element ids
and positions in the description, and copies the trees to the evidence folder.
Re-recording a screen replaces its findings and drops the ones that now pass; a
finding you marked a false positive is left alone. Record every screen that
matters: sign-in, the main task, settings, checkout, error states.

## What a person still has to check

- A full walk-through with TalkBack (Android) and VoiceOver (iOS) turned on: can
  someone finish the main task without looking?
- Focus order: does the reader move through the screen in a sensible order, and
  does focus land somewhere useful after a dialog opens or closes?
- Custom gestures: is every swipe, long press or drag also possible with a button
  or an accessibility action?
- Colour contrast: run `/proof-a11y`'s contrast checker on the app's colour pairs.
- Motion and timing: reduced-motion support, and no time limits a user cannot extend.
- Announcements: errors and loading states are spoken, not only shown.

## Honest limits

A UI tree shows what the accessibility layer is told, not how it sounds: a label can
exist and still be wrong. The tree is one screen at one moment, so states that only
appear after a tap need their own capture. Android touch targets are measured on the
visible node; a hit area enlarged in code (touch delegates, padding outside the
bounds) will be reported as small and should be marked a false positive after a
check. Overlap is measured between text elements only. Cross-platform toolkits that
draw their own UI (some game engines, canvas-based apps) may expose a thin or empty
tree, which this cannot judge. Results from Xcode's accessibility audit or Android's
Accessibility Scanner are useful second opinions; record what they find as findings
by hand.

## Output / next

`proof/coverage/proof-mobile-a11y.json` (four checks per screen), findings under
`proof/findings/proof-mobile-a11y/`, the saved trees under
`proof/evidence/proof-mobile-a11y/`. Then `/proof-report`.
