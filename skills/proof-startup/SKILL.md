---
name: proof-startup
description: >-
  A lean launch check for a startup MVP: the core user flows, the risks that sink a launch (leaked keys, vulnerable packages, broken sign-in, open data, privacy, store rejection) and whether each sale makes money, then a board of directors votes on the launch. Only the skills the product needs run. Use when the user says "startup check", "launch check", "are we ready to launch", "MVP check", "can we ship this", "pre-launch audit", or "present it to the board".
argument-hint: "[--public] [--add proof-x] [--skip proof-x]"
---

# proof-startup

What a founder needs to know before opening the doors, and nothing that can wait: does the product work, will it embarrass you, and does it make money. It reads web or mobile from the review and runs the matching flows.

What the user typed after `/proof-startup`: `$ARGUMENTS`

## What it runs

| skill | checks |
| --- | --- |
| `/proof-web-e2e` or `/proof-mobile-e2e` | the core user flows, from what the review found |
| `/proof-sec-secrets`, `/proof-sec-deps` | leaked keys and vulnerable packages |
| `/proof-finance` | unit economics: margin, break-even, cash needed |

Only when the review finds it:

| found | adds |
| --- | --- |
| sign-in | `/proof-auth` |
| a database | `/proof-database` |
| a web app | `/proof-privacy` |
| a mobile app | `/proof-store-ready` |
| email | `/proof-email` |
| an LLM | `/proof-ai` |
| `--public` | `/proof-seo`, `/proof-geo` |

Common additions when the user asks: `/proof-web-perf` (speed), `/proof-a11y` (accessibility), `/proof-release` (launch checklist), `/proof-load` (launch-day traffic).

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first (what is
   under test, and whether you are authorised to test it).
2. **Plan.** Review the app, then plan this profile. Show the user the review and the
   list before running anything:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile startup --project .
   ```

   Add `--public` when the product is meant to be found by search engines and AI
   assistants. Leave it off for a private beta or an internal tool.

   `--add proof-x` adds a skill the profile leaves out; `--skip proof-x` drops one.
3. **Run.** `check.py next` names the next skill; follow that skill's own steps, then
   ask again. `check.py status` shows progress and flags verified high or critical
   findings as they appear. Proof only reads and records: no skill edits the project.
4. **Finish.** `/proof-report`, then `/proof-board` whatever the verdict (it ends with `board.py tally --open`, which opens the meeting page), then `/proof-audit-trail`.

## Example

For a Next.js MVP with email sign-in, a Postgres database and an LLM chat feature, the plan holds 9 skills:

```
/proof-web-e2e  end-to-end user flows
/proof-sec-secrets  committed secrets
/proof-sec-deps  vulnerable dependencies
/proof-auth  sign-in, sessions, reset, logout, rate limits
/proof-database  row-level security, public buckets, risky migrations
/proof-privacy  trackers, consent and personal data
/proof-email  transactional emails
/proof-ai  LLM feature safety
/proof-finance  unit economics
```

## Not the right fit?

Not launching yet? `/proof-quick` for a fast scan. Need depth in one area? `/proof-webapp`, `/proof-mobile` or `/proof-security`.

## Honest limits

Lean by design: performance, accessibility, load and code quality are left out unless added, and the report says so. Unit economics need real numbers from the user; the finance skill never guesses them. Ship means every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json`, `required_skills` in `proof/target.json`, and
each skill's own evidence. Then `/proof-report`.
