---
name: proof-backend
description: >-
  Targeted check for an API or backend: contract, web security, secrets, deps, infra, plus sign-in, database, email, LLM and RAG when present. Use for "check my API", "backend check" or "audit my server".
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
each skill's own evidence. Then `/proof-report`, `/proof-board` and `/proof-business-plan`, which writes
`proof/summary.md`, `proof/business-plan.md`, `proof/one-pager.md` and `proof/business-plan.pdf`.
