---
name: proof-check
description: >-
  Reviews the app, suggests the right Proof profile and plans only the skills it needs; skips skills whose inputs are unchanged on a re-run. Use for "test my app", "which check should I run" or a check without a name.
argument-hint: "[quick | startup | webapp | mobile | backend | security | ultra] [--public]"
---

# proof-check

The engine behind every targeted check. Each profile is a short list on purpose: a
skill joins the run only when the profile lists it **and** the review found what it
tests. A web app with no sign-in never runs the auth skill; a private beta never
runs SEO. Anything else stays one `--add` away.

What the user typed after `/proof-check`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py" review --project .
python3 "${CLAUDE_SKILL_DIR}/check.py" profiles
python3 "${CLAUDE_SKILL_DIR}/check.py" plan --profile <name> [--public] [--add proof-x] [--skip proof-x]
python3 "${CLAUDE_SKILL_DIR}/check.py" status
python3 "${CLAUDE_SKILL_DIR}/check.py" next
python3 "${CLAUDE_SKILL_DIR}/check.py" refresh <skill> | --all
```

## Pick a profile

| command | for | core skills |
| --- | --- | --- |
| `/proof-quick` | any repo, in minutes, no running app | secrets, dependencies, deploy config |
| `/proof-startup` | an MVP about to launch | core flows, secrets, deps, sign-in, data, privacy, store rules, unit economics |
| `/proof-webapp` | a web app or PWA | flows, API, web security, sign-in, data, privacy, speed, accessibility |
| `/proof-mobile` | an iOS or Android app | flows, build audit, React Native/Expo, store rules, phone accessibility |
| `/proof-backend` | an API or service | contract, web security, sign-in, data, infra, LLM cost |
| `/proof-security` | security only | secrets, deps, web security, sign-in, data, infra, privacy |
| `/proof-ultracheck` | everything that fits | every skill the review finds a reason for; long, opt in deliberately |

Conditional skills (sign-in, database, PWA, email, translations, LLM safety, RAG,
voice, on-device model) join a profile only when the review finds them. SEO and
AI-search join only with `--public`, for a product meant to be found.

## Steps

1. **Target.** Needs `proof/target.json`. If it is missing, run `/proof-plan`
   first: it records what is under test and asks whether you own it or have written
   permission to test it.
2. **Review.** `check.py review --project .` (once per folder for a product split
   across repos). Show the user what it found and the profile it suggests.
3. **Plan.** If the user named a profile, use it; otherwise use the suggestion, or
   `check.py profiles` to compare how many skills each would run for this app. Then
   `check.py plan --profile <name>`. The plan lists what else fits the app but is
   left out; add any of it with `--add` only if the user asks.
4. **Run.** `check.py next` names the skill; follow that skill's own steps, then ask
   for the next. A check that truly cannot apply is marked not applicable with a
   reason inside the skill, so it stops holding the verdict at Incomplete.
5. **Finish.** `/proof-report` for the computed verdict. The profile's own command
   says whether to continue to `/proof-board` and `/proof-audit-trail`.

## The second run costs less

Keep `proof/` between runs. Each finished skill records a fingerprint of what it reads
(lockfiles for dependency skills, deploy files for infra, the source for the rest;
docs only for finance). Re-plan with the same profile and `check.py next` skips every
skill whose inputs are unchanged, so its instructions and tools are never loaded;
`status` marks those `unchanged` and anything whose inputs moved `stale`. A live
target can change without the code changing: after a redeploy, run
`check.py refresh <skill>` (or `--all`). The board does the same per director.

## Honest limits

A profile is a scope decision, not a pass: skills it leaves out are reported as not
run, never as clean. The review reads files, not running behaviour, so it can miss
a feature or see one that is not live; check its list before planning.

## Output / next

`proof/review.json` (what the review found), `proof/check.json` (the plan) and
`required_skills` in `proof/target.json`. Then `/proof-report`.
