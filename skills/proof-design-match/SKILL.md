---
name: proof-design-match
description: >-
  Checks that a built app screen matches its design and flags UI elements that are
  new, missing, moved or resized. Compares an app screenshot with a design export
  (a Figma or other frame exported as PNG, at any whole scale) and writes a diff
  image and a percent-changed score against a budget, with regions such as the
  status bar ignored. Compares the screen's element tree (an Android uiautomator
  dump, or element JSON from an iOS accessibility dump) with the last build and
  with an optional design spec. Offline, no design-tool account, results in the
  ledger. Use when the user says "does the build match the design", "design QA",
  "compare with Figma", "what changed on this screen", "new elements",
  "missing buttons", "pixel perfect check", or "did the redesign land".
argument-hint: "[compare | elements | record] --name <screen>"
---

# proof-design-match

A redesign lands in pieces: a button that moved twelve pixels, a label nobody
removed, a new element no designer signed off. This skill puts each screen next to
its design and next to the last build, and records what differs with the files as
proof.

What the user typed after `/proof-design-match`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/design.py" compare --screen shots/login.png --design figma/login@3x.png --name login
python3 "${CLAUDE_SKILL_DIR}/design.py" elements --current trees/login.xml --previous trees/login-old.xml
python3 "${CLAUDE_SKILL_DIR}/design.py" record --name login --screen shots/login.png --design figma/login@3x.png \
    --current trees/login.xml --previous trees/login-old.xml --spec spec/login.json
python3 "${CLAUDE_SKILL_DIR}/design.py" status
```

## Step 1: export the design

Export each frame as PNG from the design tool at the device's scale, so one design
pixel lines up with one screen pixel: in Figma, select the frame, add an export at
`3x` for a 3x phone (most current iPhones and many Android phones) or `2x`, and
export. Any whole scale works, because the tool resamples the design to the
screenshot's size, but the frame must have the same shape as the screen. Crop the
screenshot to the frame if the design leaves out the system bars.

## Step 2: capture the build

- Screenshot: `adb exec-out screencap -p > shots/login.png` on Android,
  `xcrun simctl io booted screenshot shots/login.png` on an iOS simulator.
- Element tree on Android: `adb shell uiautomator dump /sdcard/ui.xml` then
  `adb pull /sdcard/ui.xml trees/login.xml`.
- Element tree on iOS: `idb ui describe-all > trees/login.json`, or save the element
  list your device tool (such as a mobile automation server) returns. The tool reads
  the keys described below.

Keep the previous build's tree for the next run, so it can tell what is new.

## Step 3: compare and record

`compare` prints how many pixels differ, the design's scale and the changed region.
`--ignore x,y,w,h` (repeatable, screenshot pixels) leaves out the status bar, a
clock or live data. `--threshold` (default 0.1) is how far a colour channel must
move to count, `--max-diff` (default 2) the percent of pixels allowed to differ.

`elements` matches elements by id, then by label and type (repeated rows match in
order), and lists what is missing, new, and moved or resized beyond `--tolerance`
pixels (default 4) or `--tolerance-pct` of its size (default 2). `--json` prints
the full result.

`record --name login` writes both layers for one screen: coverage checks
`login-screen` and `login-elements`, a `design-diff-pct-login` score, the files
copied into evidence, and findings verified by them:

| Finding | Severity |
| --- | --- |
| Screen differs from the design beyond the budget | medium |
| Element in the last build or the design spec but not on screen | medium |
| Element on screen but not in the last build or the design spec | low |
| Element moved or resized beyond tolerance | low |

Recording a screen again replaces its findings and score; other screens are kept.

## Element JSON

A list, or an object with an `elements` list, of:

```json
[{"id": "email", "label": "Email", "type": "TextField", "x": 20, "y": 300, "w": 335, "h": 44}]
```

`id` or `label` is required; elements with neither (layout containers) are skipped.
The iOS accessibility keys `AXUniqueId` / `identifier`, `AXLabel` / `text`,
`AXRole` / `role` and a `frame` of `{x, y, width, height}` are read too. A design
spec uses the same shape: the elements the design expects on that screen.

## Honest limits

A pixel diff says where the build and the design disagree, not which one is right;
a designer still reviews the diff image. Fonts render differently from the design
tool, so expect small differences around text and set the budget for that. No
design-tool API is called: you export the frames. Element trees show what the
accessibility layer exposes, so an element drawn without an id or label is
invisible to the element check.

## Output / next

`proof/coverage/proof-design-match.json`, `proof/scores/proof-design-match.json`,
findings under `proof/findings/proof-design-match/`, and the diff images,
screenshots, design exports and trees under `proof/evidence/proof-design-match/`.
Then `/proof-report`.
