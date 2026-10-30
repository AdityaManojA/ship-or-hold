---
name: proof-full-check-no-seo
description: >-
  Runs the whole Proof pack as one full check, but without the SEO and AI-search
  (GEO) skills. Every other applicable skill is required: user flows, API,
  security, secrets, dependencies, performance, accessibility, load, test strength,
  exploratory testing, bloat, mobile, AI safety and unit economics, ending with the
  computed verdict. For products that do not need to be found yet: internal tools,
  builds in stealth, private betas. Use when the user says "full check without
  SEO", "test everything except SEO", "we are in stealth", "internal tool, full
  audit", or "skip SEO".
argument-hint: "[--has-llm] [--skip proof-x]"
---

# proof-full-check-no-seo

The same full check as `/proof-full-check`, with search engine optimisation and
AI-search citability left out. Leaving them out is a scope decision, not a pass:
they are not required, so not running them never holds the verdict back, and you
can still run `/proof-seo` or `/proof-geo` on their own later when the product goes
public.

What the user typed after `/proof-full-check-no-seo`: `$ARGUMENTS`

## The tool

This skill uses the full-check tool with `--no-seo`:

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --no-seo
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" status
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" next
```

## Step 1: the target

A full check needs `proof/target.json`. If it is missing, run `/proof-plan` first:
it records what is under test and asks whether you own it or have written
permission to test it. The security and load skills refuse to act against a live
target until that is yes.

## Step 2: plan

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --no-seo
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --no-seo --has-llm
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" plan --no-seo --skip proof-finance
```

It picks every skill that fits the target's kind except `proof-seo` and
`proof-geo`, writes them into `target.json` as required, and prints the run order.
The AI safety skill needs `--has-llm`; mobile skills follow the target's kind or
`--mobile ios|android`.

## Step 3: work through them

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-full-check/fullcheck.py" next
```

Run the skill it names, following that skill's own steps, then ask for the next.
`status` shows each skill as not started, in progress or done, and flags verified
high or critical findings as they appear.

## Step 4: the verdict

When `next` says everything is done, run `/proof-report` for the computed verdict,
`/proof-board` to present it to the board of directors, then `/proof-audit-trail`
to pack the run.

## When you go public

Run `/proof-seo` and `/proof-geo` on their own. Their results join the report as
optional evidence. To make them required, add them with `/proof-plan`
(`require add proof-seo proof-geo`) or re-plan with `/proof-full-check`.

## Honest limits

Everything said about `/proof-full-check` applies: it is long, it is the sum of its
skills and their limits, and Ship means every required check passed on the build
you tested. Leaving out SEO means nothing here tells you whether the product can be
found by search engines or AI assistants.

## Output / next

Writes `proof/fullcheck.json` and updates `required_skills` in `proof/target.json`.
Each skill writes its own evidence. Finish with `/proof-report`, then
`/proof-audit-trail`.
