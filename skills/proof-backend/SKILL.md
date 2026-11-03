---
name: proof-backend
description: >-
  A targeted check for an API or backend service: the OpenAPI contract, the web security checklist, secrets, dependencies, deploy config, plus sign-in, database, email, LLM safety, LLM cost and RAG only when the service has them. No browser, UI or SEO checks. Use when the user says "check my API", "backend check", "audit my server", "test my FastAPI / Express / Django service", or "API security".
argument-hint: "[--add proof-x] [--skip proof-x]"
---

# proof-backend

A service with no UI should not pay for UI checks. This runs the contract and security work a backend needs, and the data and AI skills when the code shows them.

What the user typed after `/proof-backend`: `$ARGUMENTS`

## What it runs

| skill | checks |
| --- | --- |
| `/proof-api` | responses against the OpenAPI spec, awkward-input cases |
| `/proof-sec-web` | the web security checklist, with a verification gate |
| `/proof-sec-secrets`, `/proof-sec-deps` | leaked keys and vulnerable packages |
| `/proof-infra` | Dockerfile, compose, CI and IaC settings |

Only when the review finds it:

| found | adds |
| --- | --- |
| sign-in | `/proof-auth` |
| a database | `/proof-database` |
| email | `/proof-email` |
| an LLM | `/proof-ai`, `/proof-llm-cost` |
| retrieval | `/proof-rag` |

Common additions: `/proof-load` (traffic), `/proof-resilience` (errors and timeouts), `/proof-licenses` (SBOM).

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first (what is
   under test, and whether you are authorised to test it).
2. **Plan.** Review the app, then plan this profile. Show the user the review and the
   list before running anything:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile backend --project .
   ```

   `--add proof-x` adds a skill the profile leaves out; `--skip proof-x` drops one.
3. **Run.** `check.py next` names the next skill; follow that skill's own steps, then
   ask again. `check.py status` shows progress and flags verified high or critical
   findings as they appear. Proof only reads and records: no skill edits the project.
4. **Finish.** `/proof-report`. Then offer the board in one line and run `/proof-board` and `/proof-audit-trail` only on a yes.

## Example

For a FastAPI service with JWT sign-in, Postgres and an OpenAI call, the plan holds 9 skills:

```
/proof-api  API contract and awkward inputs
/proof-sec-secrets  committed secrets
/proof-sec-deps  vulnerable dependencies
/proof-sec-web  web security checklist
/proof-auth  sign-in, sessions, reset, logout, rate limits
/proof-database  row-level security, public buckets, risky migrations
/proof-infra  deployment and CI config
/proof-ai  LLM feature safety
/proof-llm-cost  LLM cost per user and response times
```

## Not the right fit?

There is a web front end too? `/proof-webapp` covers the API as well. Security only? `/proof-security`.

## Honest limits

Active security and API cases run only against a target you are authorised to test. Load is left out unless added, because it needs a safe environment. Ship means every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json`, `required_skills` in `proof/target.json`, and
each skill's own evidence. Then `/proof-report`.
