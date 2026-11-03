---
name: proof-quick
description: >-
  The fastest, cheapest Proof check: offline scans of committed secrets, vulnerable dependencies and deployment config, run on any repo in minutes with no running app, no browser and no sub-agents. Use when the user says "quick check", "quick scan", "sanity check", "scan my repo", "check for leaked keys", "is anything obviously wrong", or wants a check without spending many tokens.
argument-hint: "[--add proof-x]"
---

# proof-quick

Three offline scans and a verdict. Nothing is driven, nothing is deployed, no sub-agents run, so it costs a fraction of any other profile. Good before every push, or as the first look at a repo you have not seen.

What the user typed after `/proof-quick`: `$ARGUMENTS`

## What it runs

| skill | checks |
| --- | --- |
| `/proof-sec-secrets` | committed keys and tokens, values masked |
| `/proof-sec-deps` | packages with known advisories and the version to move to |
| `/proof-infra` | risky settings in Dockerfiles, compose, CI and IaC |

For more, move up a profile rather than adding to this one: `/proof-security` for a full security pass, or the profile for your product (`/proof-webapp`, `/proof-mobile`, `/proof-backend`).

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first (what is
   under test, and whether you are authorised to test it).
2. **Plan.** Review the app, then plan this profile. Show the user the review and the
   list before running anything:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile quick --project .
   ```

   `--add proof-x` adds a skill the profile leaves out; `--skip proof-x` drops one.
3. **Run.** `check.py next` names the next skill; follow that skill's own steps, then
   ask again. `check.py status` shows progress and flags verified high or critical
   findings as they appear. Proof only reads and records: no skill edits the project.
4. **Finish.** `/proof-report` for the verdict. Stop there: no board, no audit pack, unless the user asks.

## Example

For any repository, the plan holds 3 skills:

```
/proof-sec-secrets  committed secrets
/proof-sec-deps  vulnerable dependencies
/proof-infra  deployment and CI config
```

## Not the right fit?

Need more than offline scans? `/proof-security` for a full security pass, `/proof-startup` before a launch.

## Honest limits

Offline scans only: nothing here proves the app works or that a live service is configured safely. A key is flagged from its shape, not checked against the provider. Ship means every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json`, `required_skills` in `proof/target.json`, and
each skill's own evidence. Then `/proof-report`.
