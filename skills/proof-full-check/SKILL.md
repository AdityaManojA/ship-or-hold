---
name: proof-full-check
description: >-
  Runs the whole Proof pack as one full check. It works out every skill that
  applies to the target (web, API, mobile or repo), makes all of them required so
  the verdict waits for every one, then walks through them in order: user flows,
  API, security, secrets, dependencies, infra, privacy, performance,
  accessibility, translations, emails, load, exploratory testing, bloat, slop,
  mobile, AI safety, SEO, AI-search citability and unit economics, ending with the
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

Some skills are never part of a full check, because each needs setup, a second
run or a person, or only some projects need it. `plan` lists them; run any of them
on its own when you want it:

- `/proof-mutation`: test strength (runs the suite once per change)
- `/proof-compat`: the browser and device matrix (needs each browser and device)
- `/proof-visual`: visual regression (means something from the second run)
- `/proof-release`: the launch checklist (items only a person can prove)
- `/proof-design-match`: screens against the design and the last build
- `/proof-mobile-explore`: an agent explores the real app on a device
- `/proof-mobile-a11y`: phone accessibility from UI trees
- `/proof-llm-device`: on-device LLM speed, memory, heat and battery
- `/proof-pwa`: an installable web app (manifest, service worker, offline)
- `/proof-react-native`: a React Native or Expo project's config and code

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

Finish in this order, every time, without asking:

1. `/proof-report`: `verdict.py` computes Ship, Not yet or Incomplete and writes
   `proof/report.md` and `proof/report.html`.
2. `/proof-board`, whatever the verdict. An Incomplete or Not yet run is exactly
   what the board should hear: untested areas show as Not covered. Write the
   briefs, convene the directors, `check`, then `board.py tally --open`, which opens
   the meeting page in the user's browser. The page links to the full report.
3. `/proof-audit-trail` packs the run with a hash for every file.

The full check is not done until the meeting page has opened (in CI or with no
screen, its path is printed instead). If the board cannot run, open the report
with `verdict.py --open` so the user still sees a page.

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
