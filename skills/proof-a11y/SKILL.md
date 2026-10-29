---
name: proof-a11y
description: >-
  An accessibility audit of a web app against WCAG 2.2 where every result is backed
  by evidence. The always-available tool is a WCAG contrast checker (pure arithmetic,
  nothing to install) that scores colour pairs against the AA and AAA bars, records
  each failing pair as a verified finding and writes a score the verdict reads. When
  the axe command-line tool or Lighthouse is installed, their rule engines run too and
  their reports go into the ledger as evidence. The rest is a manual checklist
  (keyboard focus order, visible focus, names and labels, alt text, headings,
  landmarks, form errors, motion, target size, zoom, a screen-reader pass) tracked as
  coverage, because no tool can judge those. Use when the user says "accessibility
  audit", "a11y check", "is this accessible", "WCAG", "check the contrast", "colour
  contrast", "screen reader test", "keyboard navigation", or "will this pass an
  accessibility review".
argument-hint: "[a URL, a colour pair, or a checklist item to focus on; blank for the full audit]"
---

# proof-a11y

Find the barriers that stop someone using the app, prove each one, and say plainly
which checks a tool did and which a person did. The contrast arithmetic always runs.
A rule engine runs when one is installed. The manual checklist is where most real
barriers are found, and it is recorded as coverage so the report shows what was
looked at, not only what was found. A finding counts only once it is verified.

What the user typed after `/proof-a11y`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/contrast.py" ratio --fg "#767676" --bg "#ffffff"      # one pair, both text sizes
python3 "${CLAUDE_SKILL_DIR}/contrast.py" ratio --fg "#777" --bg "#fff" --size 14pt --bold
python3 "${CLAUDE_SKILL_DIR}/contrast.py" check --pairs pairs.json --record        # every pair, into the ledger
python3 "${CLAUDE_SKILL_DIR}/contrast.py" checklist --record                      # the manual items, all not run
python3 "${CLAUDE_SKILL_DIR}/contrast.py" cover <id> --note "..." --evidence <file>
python3 "${CLAUDE_SKILL_DIR}/contrast.py" na <id> --note "..."
python3 "${CLAUDE_SKILL_DIR}/contrast.py" finding add <id> --title "..." --severity high --evidence <file>
python3 "${CLAUDE_SKILL_DIR}/contrast.py" finding verify <finding-id> --by evidence-file
```

The manual items, with a how-to and the WCAG criterion for each, are in
`${CLAUDE_SKILL_DIR}/checklist.md`. Read it before the manual pass.

## Step 1: know the target

If `proof/target.json` exists, read it. A local dev server (localhost, 127.0.0.1)
can always be audited. Point a rule engine at a live, non-local URL only when the
target is marked authorised. The contrast arithmetic touches nothing but the colours
you give it, so it needs no target at all. If there is no target yet and the user
wants a verdict, run `/proof-plan` first.

## Step 2: see which engines are installed

Run `/proof-doctor --for proof-a11y`. It reports whether the axe command-line tool
and Lighthouse are on PATH. Neither is required; the rest of this skill works with
Python alone.

## Step 3: contrast, always

Collect the colour pairs the app actually shows: body text, muted and helper text,
links, button labels, placeholder text, text on coloured banners, and each state
that changes a colour (hover, focus, disabled, error). Read them from the
stylesheet or design tokens; for text over an image, use the lightest and darkest
points of the image behind it. Write them as a pairs file:

```json
{
  "pairs": [
    {"id": "body", "fg": "#333333", "bg": "#ffffff", "size": "16px", "where": "body copy on white"},
    {"id": "helper", "fg": "#8a8a8a", "bg": "#ffffff", "size": "14px", "where": "helper text under form fields"},
    {"id": "hero", "fg": "#777777", "bg": "#ffffff", "size": "32px", "bold": true, "where": "hero heading"},
    {"id": "button", "fg": "#ffffff", "bg": "#2f6fed", "size": "16px", "bold": true, "where": "primary button label"}
  ]
}
```

Every pair needs `id`, `fg`, `bg`, `size` and `where`; `bold` defaults to false.
Size is `normal`, `large`, or the real font size (`14pt`, `24px`), and the tool
applies the WCAG rule itself: 18pt and up, or 14pt bold and up, is large text and
gets the 3:1 bar instead of 4.5:1. A missing size is an error, never a guess.

```bash
python3 "${CLAUDE_SKILL_DIR}/contrast.py" check --pairs pairs.json            # look first
python3 "${CLAUDE_SKILL_DIR}/contrast.py" check --pairs pairs.json --record   # then record
```

`--record` marks every pair covered (with its ratio in the note), writes one
verified finding per failing pair (severity medium, category `contrast`, the ratio
in the title, the fix in the fix field, a repro command that recomputes it), and
writes the score `contrast_pairs_passing`, which must equal the number of pairs.
The findings are medium, below the default `fail_on` of high, so it is the score
that makes a single failing pair turn the verdict to Not yet. Re-running `check
--record` with a changed pairs file replaces the findings it generated earlier; it
never leaves a stale one behind.

Use `ratio` for a quick look at one pair while fixing a colour. It records nothing.

## Step 4: the rule engine, when present

If the axe command-line tool is installed, run it against each main page of a
running instance and save its JSON report under the evidence folder (its save
option writes the file; `axe --help` lists it):

```bash
axe http://localhost:3000/ --save proof/evidence/proof-a11y/axe-home.json
```

If Lighthouse is installed instead:

```bash
lighthouse http://localhost:3000/ --only-categories=accessibility --output=json \
  --output-path=proof/evidence/proof-a11y/lighthouse-home.json --chrome-flags="--headless"
```

Then record the run and its results. One finding per rule, listing the affected
elements in the location, verified by the report file:

```bash
python3 "${CLAUDE_SKILL_DIR}/contrast.py" checklist --record
python3 "${CLAUDE_SKILL_DIR}/contrast.py" cover engine --evidence evidence/proof-a11y/axe-home.json \
  --note "axe on 4 pages: 3 rules failing, recorded below"
python3 "${CLAUDE_SKILL_DIR}/contrast.py" finding add engine --title "Form fields on /signup have no label" \
  --severity high --wcag 4.1.2 --location "/signup: input#email, input#password" \
  --evidence evidence/proof-a11y/axe-home.json --fix "Add a label element tied to each field"
python3 "${CLAUDE_SKILL_DIR}/contrast.py" finding verify form-fields-on-signup-have-no-label --by evidence-file
```

Map the engine's own impact words to severities: critical or serious to high
(critical only when a core flow is blocked outright), moderate to medium, minor to
low. Read each reported rule before recording it; engines report a few things that
are not barriers in context, and those are rejected with `finding reject`, not
silently dropped.

With no engine installed, say so in the ledger and move on:

```bash
python3 "${CLAUDE_SKILL_DIR}/contrast.py" na engine --note "no rule engine installed; contrast check and manual checklist used"
```

An engine pass never marks a manual item covered. An engine can confirm a label
exists; it cannot confirm the label is right.

## Step 5: the manual review

```bash
python3 "${CLAUDE_SKILL_DIR}/contrast.py" checklist --record
```

This lists the eleven manual items and creates (or extends) the coverage ledger
with each one not run. Work through `checklist.md`: for each item do what its
how-to says on the main flows, save the evidence it names under
`proof/evidence/proof-a11y/`, then record it:

```bash
python3 "${CLAUDE_SKILL_DIR}/contrast.py" cover focus-order --note "tabbed sign-in, search and checkout; order matches reading order" \
  --evidence evidence/proof-a11y/keyboard-pass.txt
python3 "${CLAUDE_SKILL_DIR}/contrast.py" na motion --note "no animation, video or auto-updating content anywhere"
python3 "${CLAUDE_SKILL_DIR}/contrast.py" finding add form-errors --title "Checkout errors are shown only as a red border" \
  --severity high --location "/checkout" --evidence evidence/proof-a11y/checkout-error.png \
  --repro "Submit /checkout with an empty card field" --fix "Add an error message tied to the field and announce it"
python3 "${CLAUDE_SKILL_DIR}/contrast.py" finding verify checkout-errors-are-shown-only-as-a-red-border --by evidence-file
```

`cover` with no `--note` is allowed by the ledger but weak; always say what you did.
Running `checklist` again shows the status of each item. Some of these checks need
the user's hands and ears (the screen-reader pass above all): when you cannot do one
yourself, leave it not run, tell the user exactly what to do from `checklist.md`,
and record their answer. Never mark an item covered on an assumption.

## The manual-review boundary

A tool cannot judge whether an alt text is meaningful, whether a focus order makes
sense, whether a link's name tells you where it goes, whether an error message helps,
or whether a flow can be completed by ear. It can measure contrast, find missing
attributes and flag structural mistakes, and that is a minority of real barriers.
This skill keeps the two apart on purpose: contrast and engine results are recorded
by the tool; the eleven manual items are recorded only by a person who did them.

## What it writes

- `proof/coverage/proof-a11y.json`: one check per colour pair (`contrast-<id>`), the
  `engine` run, and the eleven manual items.
- `proof/findings/proof-a11y/`: a verified finding per failing pair, plus the engine
  and manual findings you add.
- `proof/scores/proof-a11y.json`: `contrast_pairs_passing` against the pair count.
- `proof/evidence/proof-a11y/`: engine reports, screenshots and notes you save.

## Honest limits

Automated checks find a minority of real barriers; the manual items find the rest,
and only on the flows you walk. A pass on contrast is a pass on contrast, not on
accessibility. The contrast arithmetic judges flat colours you supply; gradients,
images behind text and transparency are only as right as the colours you read off
them. The engines report the rules they implement on the pages you gave them.
Confirm the result with a keyboard and a screen reader before telling anyone the app
is accessible, and for a legal or procurement claim have a specialist audit it.

## Output / next

`proof/coverage/proof-a11y.json`, `proof/scores/proof-a11y.json`, findings under
`proof/findings/proof-a11y/`, evidence under `proof/evidence/proof-a11y/`. Then
`/proof-web-e2e` to walk the same flows with a mouse, and `/proof-report` to compile
the verdict.
