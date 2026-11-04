---
name: proof-security
description: >-
  Targeted security-only check for any app: secrets, deps, infra, plus web security, sign-in, database, privacy and LLM safety when present. Use for "security check", "security audit" or "is my app secure".
argument-hint: "[--add proof-x] [--skip proof-x]"
---

# proof-security

One pass over everything that can leak or be broken into, and nothing else. For a change on one branch, `/proof-sec-diff` is cheaper still.

What the user typed after `/proof-security`: `$ARGUMENTS`

## What it runs

| skill | checks |
| --- | --- |
| `/proof-sec-secrets`, `/proof-sec-deps` | leaked keys and vulnerable packages |
| `/proof-infra` | Dockerfile, compose, CI and IaC settings |

Only when the review finds it:

| found | adds |
| --- | --- |
| a web app or API | `/proof-sec-web` |
| sign-in | `/proof-auth` |
| a database | `/proof-database` |
| a web app | `/proof-privacy` |
| an LLM | `/proof-ai` |

Common additions: `/proof-mobile-audit` for a built app, `/proof-licenses`, `/proof-sec-diff` for one branch.

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first (what is
   under test, and whether you are authorised to test it).
2. **Plan.** Review the app, then plan this profile. Show the user the review and the
   list before running anything:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile security --project .
   ```

   `--add proof-x` adds a skill the profile leaves out; `--skip proof-x` drops one.
3. **Run.** `check.py next` names the next skill; follow that skill's own steps, then
   ask again. Run the skills in this session, one at a time: every extra agent starts
   with a large fixed context, so spawn one only if the user asks. Work from each
   tool's printed summary; open evidence files only to verify a finding. Proof only reads and records: no skill edits the project.
4. **Run it again later.** Keep `proof/` and repeat the same command: the plan keeps
   what earlier runs learnt, and `check.py next` skips every skill whose inputs have
   not changed, so only the changed areas run. After redeploying a live target, mark
   its live skills with `check.py refresh <skill>`.
5. **Finish.** `/proof-report`. Then offer the board in one line and run `/proof-board` and `/proof-audit-trail` only on a yes.

## Example

For a Django web app with sign-in and a database, the plan holds 7 skills:

```
/proof-sec-secrets  committed secrets
/proof-sec-deps  vulnerable dependencies
/proof-sec-web  web security checklist
/proof-auth  sign-in, sessions, reset, logout, rate limits
/proof-database  row-level security, public buckets, risky migrations
/proof-infra  deployment and CI config
/proof-privacy  trackers, consent and personal data
```

## Not the right fit?

Only want the offline part? `/proof-quick`. A change on one branch? `/proof-sec-diff`. Everything for a launch? `/proof-startup`.

## Honest limits

Active testing only against a target you are authorised to test; anything needing it is otherwise recorded as not run. A clean security check is not a guarantee: it covers the listed classes, verified. Ship means every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json`, `required_skills` in `proof/target.json`, and
each skill's own evidence. Then `/proof-report`.
