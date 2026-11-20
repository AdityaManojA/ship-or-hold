---
name: proof-startup
description: >-
  Launch check for a startup: core flows, launch-sinking risks and the whole business side, then the board votes and the business plan is compiled. Use for "startup check", "are we ready to launch", "MVP check" or "can we ship this".
argument-hint: "[--public] [--add proof-x] [--skip proof-x]"
---

# proof-startup

What a founder needs to know before opening the doors: does the product work, will it embarrass you, and is it a business. It reads web or mobile from the review and runs the matching flows, then the business side: who else is there, whether anyone would buy, at what price, whether the numbers work, how it reaches people and how it runs. The result is a business plan and a half-page summary, with the technical report behind it.

What the user typed after `/proof-startup`: `$ARGUMENTS`

## What it runs

| skill | checks |
| --- | --- |
| `/proof-web-e2e` or `/proof-mobile-e2e` | the core user flows, from what the review found |
| `/proof-sec-secrets`, `/proof-sec-deps` | leaked keys and vulnerable packages |
| `/proof-market`, `/proof-buyers` | competitors and the gap; what a panel of simulated buyers says to the pitch |
| `/proof-pricing`, `/proof-offer` | the price, or what one visit earns when it is free; the offer built from buyers' objections |
| `/proof-finance` | unit economics: margin, break-even, cash needed |
| `/proof-go-to-market`, `/proof-ops` | positioning, channels and a first real test; routines, costs and dependencies |

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
   ask again. Run the skills in this session, one at a time: every extra agent starts
   with a large fixed context, so spawn one only if the user asks. Work from each
   tool's printed summary; open evidence files only to verify a finding. Proof only reads and records: no skill edits the project.
4. **Run it again later.** Keep `proof/` and repeat the same command: the plan keeps
   what earlier runs learnt, and `check.py next` skips every skill whose inputs have
   not changed, so only the changed areas run. After redeploying a live target, mark
   its live skills with `check.py refresh <skill>`.
5. **Finish: the report.** Always, whatever the verdict and without asking, in this
   order: `/proof-report` for the computed verdict; `/proof-board`, which ends with
   `board.py tally --open` and the meeting page; then `/proof-business-plan`: write
   `proof/summary.md` (half a page, with one line per director) and run `plan.py compile`,
   which writes `proof/business-plan.md`, `proof/one-pager.md` and
   `proof/business-plan.pdf` with every director's answer in it. Show the user the
   verdict, the board's decision and the summary in full, and send the PDF. Then
   `/proof-audit-trail`. A business section this check did not run is listed in the plan
   as not done, never filled in.

## Example

For a Next.js MVP with email sign-in, a Postgres database and an LLM chat feature, the plan holds 15 skills:

```
/proof-web-e2e  end-to-end user flows
/proof-sec-secrets  committed secrets
/proof-sec-deps  vulnerable dependencies
/proof-auth  sign-in, sessions, reset, logout, rate limits
/proof-database  row-level security, public buckets, risky migrations
/proof-privacy  trackers, consent and personal data
/proof-email  transactional emails
/proof-ai  LLM feature safety
/proof-market  competitors, what their customers complain about, and the gap
/proof-buyers  a seeded panel of simulated buyers hears the pitch
/proof-pricing  the price, or what one visit earns when the product is free
/proof-offer  the offer built from the objections buyers raised
/proof-finance  unit economics
/proof-go-to-market  positioning, channels and a first real-world test with a pass mark
/proof-ops  day-one routines, monthly costs and what the business depends on
```

## Not the right fit?

Only the business side, with no app to test yet? `/proof-business`. Not launching yet? `/proof-quick` for a fast scan. Need depth in one area? `/proof-webapp`, `/proof-mobile` or `/proof-security`.

## Honest limits

Performance, accessibility, load and code quality are left out unless added, and the report says so. The business skills need facts only the user has (how it makes money, who buys, what it costs); they never guess them, and an estimate stays labelled as one. Simulated buyers and directors are not customers or investors. Ship means every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json`, `required_skills` in `proof/target.json`, and
each skill's own evidence, the sections under `proof/business/`, then `proof/board/`,
`proof/summary.md`, `proof/business-plan.md` and `proof/one-pager.md`.
