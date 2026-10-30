---
name: proof-visual
description: >-
  Visual regression testing backed by evidence. Saves a screenshot of each page or
  state as a baseline, compares later screenshots against it pixel by pixel, writes
  a diff image (changed pixels in red over a faded copy of the page) with the
  bounding box of what moved, scores the percent of changed pixels against a budget,
  and records a verified finding when a page changed more than allowed. Intended
  changes are approved into a new baseline. Use when the user says "visual
  regression", "did the layout change", "screenshot diff", "compare screenshots",
  "pixel diff", "did my CSS break anything", "check the UI didn't move", "visual
  test", or "approve the new design".
argument-hint: "[baseline | compare | approve] [page name] [screenshot.png]"
---

# proof-visual

Catch layout changes nobody meant to make. Save a screenshot of each important page
as a baseline, take the same screenshot after a change, and compare. Every
comparison becomes a score (the percent of pixels that changed, against a budget)
and a coverage check; a page that changed more than the budget becomes a finding
backed by a diff image you can open and look at. Use it before a release, after a
CSS or dependency upgrade, or whenever a change "should not touch" the UI.

What the user typed after `/proof-visual`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/visual.py" baseline <name> <image.png>
python3 "${CLAUDE_SKILL_DIR}/visual.py" compare <name> <image.png> [--threshold 0.1] [--max-diff 0.5]
python3 "${CLAUDE_SKILL_DIR}/visual.py" approve <name> <image.png>
python3 "${CLAUDE_SKILL_DIR}/visual.py" status
```

`<name>` is a short id for the page or state: lowercase letters, digits and
hyphens, such as `home`, `pricing` or `checkout-step-2`. The tool reads 8-bit RGB
and RGBA PNGs itself, with nothing to install. A palette, 16-bit or interlaced PNG
is refused with exit code 2 and a message to save it as 8-bit RGB(A).

`--threshold` is how far a colour channel must move (0 to 1) before a pixel counts
as changed; 0.1 ignores faint anti-aliasing noise. `--max-diff` is the percent of
changed pixels allowed before it is a finding. `compare` exits 1 when over budget,
so a CI step can stop on it.

## Step 1: know the target

If `proof/target.json` exists, read it. Only screenshot a live, non-local URL when
the target is marked authorised. A local dev server is always fine. Nothing else
needs to have run first.

## Step 2: take the screenshots

Pick the pages and states that matter (home, pricing, sign in, a filled cart, an
error state). For each, take a screenshot with a fixed viewport:

- With a browser automation library installed, use the starter scripts from
  `/proof-web-e2e` (they save full-page PNGs) and keep the viewport fixed.
- Without one, use the built-in browser tools: set the window size, navigate,
  wait for the page to settle, and save the screenshot as a PNG.

Before each shot, freeze what changes on its own: hide or stub dates, clocks,
counters, ads, carousels, avatars and random content, and turn off animations
(for example a stylesheet with `* { animation: none !important; transition: none !important; }`).

## Step 3: save baselines

On the build you trust:

```bash
python3 "${CLAUDE_SKILL_DIR}/visual.py" baseline home shots/home.png
```

The PNG is copied to `proof/visual/baseline/home.png`. The tool refuses to
overwrite an existing, different baseline; that is what `approve` is for.

## Step 4: compare after a change

Take the same screenshot, same viewport, same device, same browser, then:

```bash
python3 "${CLAUDE_SKILL_DIR}/visual.py" compare home shots/home.png --max-diff 0.5
```

It prints the changed-pixel percent and the bounding box of the changed region,
writes `proof/evidence/proof-visual/home-diff.png` and a copy of the new
screenshot, records the score `visual_home` and marks the `home` check covered.
Over budget, it writes a medium finding (category `visual`) with the diff image as
evidence and verifies it by that file. A screenshot of a different size counts as
100% changed and says so.

Open the diff image and look at it before you report anything. Red speckle along
text edges is rendering noise; a red block is a real change.

## Step 5: approve intended changes

When the change is the new design, not a bug:

```bash
python3 "${CLAUDE_SKILL_DIR}/visual.py" approve home shots/home.png
```

This replaces the baseline, removes the earlier finding and score for that name
(they described the old baseline), and sets its check back to not run. Compare
again to record a fresh pass. Approve only with the user's say-so: approving a bug
hides it.

## Step 6: check where things stand

```bash
python3 "${CLAUDE_SKILL_DIR}/visual.py" status
```

Lists every baseline with its last score and any findings.

## What it writes

- `proof/visual/baseline/<name>.png`: the approved baselines.
- `proof/evidence/proof-visual/<name>-diff.png` and `<name>.png`: the diff image
  and the screenshot that was compared.
- `proof/scores/proof-visual.json`: one `visual_<name>` metric per name, in `%`,
  budget `--max-diff`, op `<=`.
- `proof/coverage/proof-visual.json`: one check per baseline name.
- `proof/findings/proof-visual/visual-<name>.json`: one verified finding per page
  over budget.

## Honest limits

Pixel diffs are blunt. Anti-aliasing, font rendering, sub-pixel layout, animations
and dynamic content (dates, ads, avatars, random picks) all show up as changes, so
hide or freeze them and raise `--threshold` only as far as the noise needs.
Baseline and new screenshot must come from the same viewport, device scale,
browser and operating system, or almost everything will differ. The tool counts
pixels; it does not know which change matters to a user, so a small change in a
price can pass the budget and a harmless shift of a whole page can fail it. A pass
means the pixels match the baseline, not that the baseline is right.

## Output / next

Files: `proof/visual/baseline/`, `proof/evidence/proof-visual/`,
`proof/scores/proof-visual.json`, `proof/coverage/proof-visual.json` and
`proof/findings/proof-visual/`. `/proof-report` reads the scores, coverage and
findings into the verdict; `/proof-web-e2e` is the usual source of the screenshots,
and `/proof-a11y` is worth running on any page whose look changed.
