---
name: proof-webapp
description: >-
  Targeted check for a web app or PWA: flows, web security, privacy, speed and accessibility, plus API, sign-in, database and LLM when present. Use for "check my web app", "test my website" or "is my site ready".
argument-hint: "[--public] [--add proof-x] [--skip proof-x]"
---

# proof-webapp

Everything a web app needs, nothing it does not. The review decides the conditional skills, so a static marketing site and a SaaS dashboard get different runs from the same command.

What the user typed after `/proof-webapp`: `$ARGUMENTS`

## What it runs

| skill | checks |
| --- | --- |
| `/proof-web-e2e` | critical user flows in a browser |
| `/proof-sec-secrets`, `/proof-sec-deps`, `/proof-sec-web` | secrets, packages, the web security checklist |
| `/proof-privacy` | trackers before consent, cookies, personal data in URLs |
| `/proof-web-perf`, `/proof-a11y` | speed budget and WCAG accessibility |

Only when the review finds it:

| found | adds |
| --- | --- |
| an API | `/proof-api` |
| sign-in | `/proof-auth` |
| a database | `/proof-database` |
| a PWA | `/proof-pwa` |
| email | `/proof-email` |
| translations | `/proof-i18n` |
| an LLM | `/proof-ai` |
| `--public` | `/proof-seo`, `/proof-geo` |

Common additions: `/proof-resilience` (offline, errors, double submits), `/proof-compat` (browser matrix), `/proof-load`, `/proof-finance`.

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first (what is
   under test, and whether you are authorised to test it).
2. **Plan.** Review the app, then plan this profile. Show the user the review and the
   list before running anything:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile webapp --project .
   ```

   Add `--public` when the product is meant to be found by search engines and AI
   assistants. Leave it off for a private beta or an internal tool.

   `--add proof-x` adds a skill the profile leaves out; `--skip proof-x` drops one.
3. **Run.** `check.py next` names the next skill; follow that skill's own steps, then
   ask again. Work from each tool's printed summary; open evidence files only to
   verify a finding. Proof only reads and records: no skill edits the project.
4. **Run it again later.** Keep `proof/` and repeat the same command: the plan keeps
   what earlier runs learnt, and `check.py next` skips every skill whose inputs have
   not changed, so only the changed areas run. After redeploying a live target, mark
   its live skills with `check.py refresh <skill>`.
5. **Finish.** `/proof-report`. Then offer the board in one line ("Want me to present this to the board?") and run `/proof-board` and `/proof-audit-trail` only on a yes.

## Example

For a React dashboard with a REST API, sign-in and a database, the plan holds 10 skills:

```
/proof-web-e2e  end-to-end user flows
/proof-api  API contract and awkward inputs
/proof-sec-secrets  committed secrets
/proof-sec-deps  vulnerable dependencies
/proof-sec-web  web security checklist
/proof-auth  sign-in, sessions, reset, logout, rate limits
/proof-database  row-level security, public buckets, risky migrations
/proof-privacy  trackers, consent and personal data
/proof-web-perf  performance budget
/proof-a11y  accessibility
```

## Not the right fit?

A phone app? `/proof-mobile`. Only the API? `/proof-backend`. Launching a startup and want the money and the board too? `/proof-startup`.

## Honest limits

Flows and live checks need the app running or deployed and your authorisation in `target.json`. Code quality, load and unit economics are out of scope unless added. Ship means every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json`, `required_skills` in `proof/target.json`, and
each skill's own evidence. Then `/proof-report`.
