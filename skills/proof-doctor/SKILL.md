---
name: proof-doctor
description: >-
  Reports which optional external tools are installed (Playwright, Lighthouse, axe,
  Semgrep, TruffleHog, k6, Maestro, Schemathesis, promptfoo and more), the version
  it found, which Proof skills each one unlocks, and what you lose without it. No
  network, detection by PATH. Use when the user says "what do I need to install",
  "check my tools", "run the doctor", "is Playwright installed", "setup check",
  "why is this skill using the slow path", or before a run to see what is available.
argument-hint: "[--for proof-<skill>] [--json]"
---

# proof-doctor

Proof needs only Python 3.8. Every skill has a built-in fallback path. But when an
external tool is installed, the matching skill uses it for stronger evidence, a
measured score instead of a coarse check, a scripted run instead of a manual one.
This skill tells you what is on hand.

What the user typed after `/proof-doctor`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/doctor.py"
python3 "${CLAUDE_SKILL_DIR}/doctor.py" --for proof-web-e2e
python3 "${CLAUDE_SKILL_DIR}/doctor.py" --json
```

## How to read it

Each row is a tool: whether it was found, the version, the Proof skills it unlocks,
and (for the ones missing) the one line of what you lose and the product to install.
`--for proof-<skill>` narrows the table to the tools that skill can use. `--json`
prints the same data for another tool to read; a skill may run
`doctor.py --for <itself> --json` to decide which path to take.

## What to do with it

- Nothing, if you are happy with the fallbacks. The run still works and still
  produces a verdict.
- Install the one or two tools that unlock the skills the user is requiring. The
  install hint names the product; follow its own install guide.
- If a skill is using a slow or coarse path and you did not expect it, run this to
  see whether its tool is missing or just not on PATH.

## Which skills each tool helps

The table's "unlocks" column names the Proof skills each tool strengthens. A few of
the common pairings:

- Playwright: scripted browser runs for `proof-web-e2e` (otherwise the built-in
  browser tools drive the flows by hand).
- Lighthouse: measured performance, SEO and accessibility scores for
  `proof-web-perf`, `proof-seo` and `proof-a11y` (otherwise coarser checks).
- Semgrep: pattern-based static analysis for `proof-sec-web` and `proof-sec-diff`.
- k6: scripted load tests for `proof-load`.
- Maestro: mobile flow automation for `proof-mobile-e2e`.

None of these changes the verdict rules. A skill using a fallback still records the
same kind of evidence; it is just slower or coarser to get there.

## Honest limits

Detection is by PATH and a quick version call. A tool can be installed but broken,
or installed in an environment this process cannot see. A yes here means "found on
PATH", not "works". If a tool is found but a skill still fails to use it, check the
tool directly. This skill reads nothing about the target and changes nothing.

## Output / next

Prints to the screen; writes nothing. Run `/proof-plan` to describe the target and
`/proof-report` to compile the verdict.
