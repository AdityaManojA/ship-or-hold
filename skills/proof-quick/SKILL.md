---
name: proof-quick
description: >-
  The cheapest check: offline scans of secrets, dependencies and deploy config on any repo, no running app or sub-agents. Use for "quick check", "quick scan", "sanity check" or "check for leaked keys".
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
   ask again. Work from each tool's printed summary; open evidence files only to
   verify a finding. Proof only reads and records: no skill edits the project.
4. **Run it again later.** Keep `proof/` and repeat the same command: the plan keeps
   what earlier runs learnt, and `check.py next` skips every skill whose inputs have
   not changed, so only the changed areas run. After redeploying a live target, mark
   its live skills with `check.py refresh <skill>`.
5. **Finish.** `/proof-report` for the verdict. Stop there: no board, no audit pack, unless the user asks.

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
