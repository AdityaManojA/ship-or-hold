---
name: proof-compat
description: >-
  Cross-browser and device compatibility testing as an evidence matrix. Rows are
  the flows or pages that matter (sign in, checkout, the home page), columns are
  browser and viewport combinations (Chrome, Firefox, Safari and Edge on desktop,
  Chrome on Android, Safari on iOS, tablets), and every cell needs its own
  screenshot or capture before it counts. A failing cell becomes a finding tied to
  that environment; the grid shows at a glance what was checked where. Use when the
  user says "cross-browser", "browser compatibility", "does it work in Safari",
  "test on mobile", "test on iPhone", "device testing", "compat matrix", "check
  every browser", or "responsive check".
argument-hint: "[a preset (web, mobile, full) or a target to focus on, or blank]"
---

# proof-compat

Check that the flows and pages that matter work in each browser and on each
device size you care about, and leave one piece of evidence per cell. The matrix
makes the gaps visible: a cell with no evidence is not run, never a quiet pass.

What the user typed after `/proof-compat`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/matrix.py" template --out matrix.json --preset web   # web, mobile or full
python3 "${CLAUDE_SKILL_DIR}/matrix.py" plan matrix.json
python3 "${CLAUDE_SKILL_DIR}/matrix.py" result <target> <env> pass|fail --evidence <path under proof/>
python3 "${CLAUDE_SKILL_DIR}/matrix.py" skip <target> <env> --note "why"
python3 "${CLAUDE_SKILL_DIR}/matrix.py" grid
python3 "${CLAUDE_SKILL_DIR}/matrix.py" status
```

Every command takes `--dir` (default `proof`) and `--help`.

## Step 1: know the target

If `proof/target.json` exists, read it. Only drive a live, non-local URL when the
target is marked authorised. A local dev server (localhost / 127.0.0.1) is always
fine. This skill does not need any other skill to have run first.

## Step 2: write the matrix

Start from a preset and edit it, or copy `${CLAUDE_SKILL_DIR}/matrix.example.json`:

- `web`: Chrome, Firefox, Safari and Edge on desktop at 1440x900, plus Chrome on
  Android at 412x915 and Safari on iOS at 390x844.
- `mobile`: phones from 360 to 412 wide and two tablets.
- `full`: both.

Each target has an `id`, a `title` and an optional `critical` flag. Each
environment has an `id`, `browser`, `version` (a note on which version you mean),
`viewport` (like `390x844`) and `os`. Keep the matrix to what your users actually
use; analytics beat guesses. Then:

```bash
python3 "${CLAUDE_SKILL_DIR}/matrix.py" plan matrix.json
```

This rejects missing fields, bad viewports and duplicate ids, saves the matrix to
`proof/compat/matrix.json`, and creates one coverage check per cell, with an id
like `checkout@safari-ios` and a title like "checkout on Safari iOS 390x844".

## Step 3: drive each cell

Pick the strongest option you have for each environment and say which you used:

1. Real devices and real browsers the user has. Strongest evidence.
2. A cloud device farm the user already has an account with. Never sign up for
   one on their behalf.
3. Browser automation (for example Playwright) when it is installed; check with
   `/proof-doctor --for proof-web-e2e` (the same browser tools). Its Chromium, Firefox and WebKit builds
   cover the three engines, with device emulation for phone sizes.
4. The built-in browser tools with viewport emulation (resize to the cell's
   viewport, then navigate, read_page, find, computer). This only checks layout
   at that width in one engine; label it so in the evidence.

For each cell, walk the target and save its evidence to
`proof/evidence/proof-compat/<target>@<env>.png` (or `.txt` for a page capture).

The rule is one cell, one piece of evidence. A screenshot taken in Chrome does
not prove Safari, and a 1440 wide capture does not prove a phone. The tool refuses
to reuse one evidence file for two cells.

## Step 4: record each cell

```bash
# it worked:
python3 "${CLAUDE_SKILL_DIR}/matrix.py" result checkout safari-ios pass \
  --evidence evidence/proof-compat/checkout@safari-ios.png

# it broke (writes a finding in category compat:<env>; --verify promotes it):
python3 "${CLAUDE_SKILL_DIR}/matrix.py" result checkout safari-ios fail \
  --evidence evidence/proof-compat/checkout@safari-ios.png \
  --title "Pay button hidden under the on-screen keyboard" --severity high \
  --repro "iPhone 390x844, open /cart, tap the card field; Pay is off screen" --verify

# not supported in this release:
python3 "${CLAUDE_SKILL_DIR}/matrix.py" skip checkout edge-desktop --note "Edge not in scope for v1"
```

A pass needs an evidence file that exists under `proof/` and is not empty. A fail
needs `--title`; severity defaults to high for a critical target and medium
otherwise. A skip needs `--note`.

## Step 5: read the grid

```bash
python3 "${CLAUDE_SKILL_DIR}/matrix.py" grid
```

Prints the matrix with `ok` (pass with evidence), `!!` (failed, a finding), `--`
(not applicable) and `..` (not run), and writes `proof/compat/grid.md` as a
markdown table. A cell with a finding stays `!!` until the finding is fixed and
rejected or the cell is re-run after a fix and the old finding is ruled out.

## Honest limits

An emulated viewport is not a real device: it does not reproduce touch, the
on-screen keyboard, safe areas, real font rendering or a phone's speed. A browser
version is a moment in time; the next release can change the result. The matrix
covers only the targets and environments in it; an empty row or column is a gap,
not a pass.

## Output / next

`proof/compat/matrix.json`, `proof/compat/grid.md`,
`proof/coverage/proof-compat.json`, findings under `proof/findings/proof-compat/`,
evidence under `proof/evidence/proof-compat/`. `/proof-report` reads the coverage
and findings; `/proof-web-e2e` covers the flows in depth on one browser.
