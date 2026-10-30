---
name: proof-slop
description: >-
  Finds the tells of machine-made filler in a product's copy, UI and code, each with
  a file and line: hype words, em-dash chains, decorative emoji, "not just X, it's
  Y" framing, numbers that look invented, placeholder text left in, stock
  purple-to-blue gradients, gradient headline text, glass panels everywhere, a
  stock typeface as the only voice, banner comments, comments that repeat the
  code, errors caught and ignored, and assistant chatter left in the source.
  Computes a score against a budget and records findings in the ledger. Offline,
  read-only. Use when the user says "does this look AI-generated", "remove the
  slop", "check for AI slop", "is the copy generic", "does the UI look templated",
  "clean up generated code", or "make it read like a person made it".
argument-hint: "[folders or files to scan, blank for the project root]"
---

# proof-slop

A product reads as generated when its copy promises everything in the same
adjectives, its numbers are too round or too precise to be real, its hero is the
same violet gradient as a thousand others, and its code is full of divider banners
and comments that say what the next line already says. None of these break a test,
but customers and reviewers notice them. This skill finds them and points at each
one.

It is a filter, not a style guide: it never tells you which colours or fonts to
use, only where the stock defaults and filler are.

What the user typed after `/proof-slop`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/slop.py" scan src/
python3 "${CLAUDE_SKILL_DIR}/slop.py" scan src/ --json
python3 "${CLAUDE_SKILL_DIR}/slop.py" record src/ public/ --budget 70
python3 "${CLAUDE_SKILL_DIR}/slop.py" status
```

`--exclude 'docs/*'` skips paths (repeatable). Dependency, build and hidden folders
are skipped already, and any line containing `slop: ignore` is left alone.

## Step 1: scan

Run `scan` on the folders that hold the product: pages and components, styles,
marketing copy, and the source. Read the examples it prints. Copy is read from
the visible text of markup files and from prose files; styling from CSS and
component files; code habits from source files.

## Step 2: look at each hit

Each check has a reason a person should confirm before changing anything:

| Check | What it flags | Severity |
| --- | --- | --- |
| Hype words | Adjectives any product could claim | low |
| Em dashes | Long dashes in visible copy | low |
| Emoji | Emoji in headings, buttons, bullets or log lines | low |
| "Not just X" framing | Contrast framing in place of a plain claim | low |
| Invented-looking numbers | Percentages over 100, times like 0.0001 ms, five nines, large multiples, user-count boasts | medium |
| Placeholders | Lorem ipsum, stand-in names, example addresses, "your company" | medium |
| Stock gradients | Violet-to-blue or violet-to-pink ramps | low |
| Gradient text | Headline text filled with a gradient | low |
| Glass panels | Background blur used three or more times in one file | low |
| Stock type | A default typeface as the only family | low |
| Banner comments | Divider lines of repeated characters | low |
| Echo comments | Three or more comments in a file that restate the next line | low |
| Swallowed errors | Empty catch blocks, bare `except: pass` | medium |
| Assistant chatter | "I hope this helps", "As an AI", "here's the updated code" | medium |

A number with a real source is not slop: keep it and cite the source on the page.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/slop.py" record src/ public/ --budget 70
```

Writes one coverage check per row above (marked not applicable when no file of
that kind was scanned), one verified finding per check and file with the first
examples and their lines, the full scan as evidence, and a `slop-score` from 0 to
100 against the budget. The score loses points per check and file with hits,
capped per check, so one noisy file cannot sink it alone. Most findings are low,
so they do not block a ship at the default fail-on level; the medium ones do if
the target sets `fail_on` to medium.

## Step 4: fix and re-run

Fix the real ones, mark deliberate exceptions with `slop: ignore`, and run
`record` again. The board's CTO reads this skill's results.

## Honest limits

These are patterns, not judgement. A hype word can be the right word, a gradient
can be the brand, and a precise number can be measured. Copy inside JavaScript
strings that are not in markup is not read. The checks are tuned for English. A
clean score means none of these tells were found, not that the product has a
voice: that still takes a person.

## Output / next

`proof/coverage/proof-slop.json`, findings under `proof/findings/proof-slop/`,
`proof/scores/proof-slop.json`, the scan under `proof/evidence/proof-slop/scan.json`.
Then `/proof-report`.
