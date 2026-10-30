---
name: proof-full-check
description: >-
  Runs the whole Proof pack as one full check. It works out every skill that
  applies to the target (web, API, mobile or repo), makes all of them required so
  the verdict waits for every one, then walks through them in order: user flows,
  API, security, secrets, dependencies, infra, privacy, performance,
  accessibility, browser compatibility, visual regression, translations, emails,
  load, exploratory testing, bloat, slop, mobile, AI safety, SEO,
  AI-search citability, unit economics and launch readiness, ending with the
  computed verdict. Use when the
  user says "full check", "test everything", "run all the tests", "complete
  audit", "check the whole app", or "everything before launch".
argument-hint: "[--has-llm] [--skip proof-x]"
---

# proof-full-check

One command for everything. Instead of choosing skills one by one, this works out
which ones apply to the target, requires all of them, and takes you through them in
a sensible order. The verdict at the end only says Ship when every one of them has
reported and covered its checks.

For the same run without SEO and AI-search checks (an internal tool, a build still
in stealth, a product that does not need to be found), use `/proof-full-check-no-seo`.

What the user typed after `/proof-full-check`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" plan
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" status
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" next
```

## Step 1: the target

A full check needs `proof/target.json`. If it is missing, run `/proof-plan` first:
it records what is under test and asks whether you own it or have written
permission to test it. The security and load skills refuse to act against a live
target until that is yes.

## Step 2: plan the full check

```bash
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" plan
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" plan --has-llm          # the app has an LLM feature
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" plan --mobile android   # also run the mobile skills
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" plan --skip proof-finance
```

It picks the skills that fit the target's kind (a web app gets the web skills, an
API gets the API and security skills, a repo gets secrets, dependencies, infra,
bloat and slop), writes them into `target.json` as required, and prints the run
order. The AI safety skill is only included with `--has-llm`; leave out anything
that does not apply with `--skip`.

Mutation testing (`/proof-mutation`) is never part of a full check: it runs the
test suite once per change and not every project needs it. Run it on its own when
you want to measure how strong the tests are.

## Step 3: work through them

```bash
python3 "${CLAUDE_SKILL_DIR}/fullcheck.py" next
```

Run the skill it names, following that skill's own steps, then ask for the next
one. `status` shows every skill as not started, in progress (with how many checks
are still not run) or done, and flags verified high or critical findings as they
appear. Each skill writes its own evidence; this tool only tracks progress.

A check that truly does not apply is marked not applicable with a reason inside the
skill itself, so it stops holding the verdict at Incomplete.

## Step 4: the verdict

When `next` says everything is done, run `/proof-report`. It computes Ship, Not yet
or Incomplete from all the evidence. Then present it to the board with
`/proof-board`: nine directors (CEO, CTO, CISO, CFO, CMO, Sales, COO, CPO, Legal)
judge the evidence and the board's Go / Go with conditions / Hold decision comes out
as an interactive meeting page. Go straight on without asking: the board ends with
`board.py tally --open`, which opens the meeting page in the user's browser by
itself (in CI or with no screen it prints the path instead). Finally `/proof-audit-trail` packs the run with a
hash for every file.

## Honest limits

A full check is long: some skills run many sub-agents (the swarm), some need a
running app, a device or a load tool. It is the sum of its skills, each with its
own limits, not a guarantee that nothing is wrong. Ship means every required check
passed on the build you tested. Skip what genuinely does not apply rather than
rushing it, because a check marked covered should mean someone really looked.

## Output / next

Writes `proof/fullcheck.json` (the plan) and updates `required_skills` in
`proof/target.json`. Each skill writes its own findings, coverage and evidence.
Finish with `/proof-report`, then `/proof-audit-trail`.
