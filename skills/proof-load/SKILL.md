---
name: proof-load
description: >-
  Writes a load plan and scores the result (p95, p99, errors, throughput) against a budget. Use for "load test", "can it handle traffic", "stress test" or "performance under load".
argument-hint: "[--target URL --vus N --duration S]"
---

# proof-load

Average speed on an idle machine tells you little about a busy one. This skill sets
a budget for behaviour under load and checks a real run against it, so "it felt
fast" becomes "p95 was 420 ms at 50 users, under the 500 ms budget".

What the user typed after `/proof-load`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/loadplan.py" plan --target http://localhost:3000/ --vus 50 --duration 60
python3 "${CLAUDE_SKILL_DIR}/loadplan.py" record --measured measured.json --budget proof/load/budget.json
python3 "${CLAUDE_SKILL_DIR}/loadplan.py" from-k6 --summary k6-summary.json --budget proof/load/budget.json
python3 "${CLAUDE_SKILL_DIR}/loadplan.py" status
```

## Authorisation

Only run load against a target you own or are authorised to test. A load test is
traffic; against someone else's service it is abuse. Check `proof/target.json`, and
prefer a staging or local environment so real users are not affected.

## Step 1: plan

```bash
python3 "${CLAUDE_SKILL_DIR}/loadplan.py" plan --target http://localhost:3000/ --vus 50 --duration 60
```

This writes `proof/load/budget.json` (the thresholds) and `proof/load/load.js` (a
runner script). Edit the budget to match what the product actually needs.

## Step 2: run the load

If k6 is installed (check with `/proof-doctor --for proof-load`):

```bash
k6 run --summary-export k6-summary.json proof/load/load.js
```

Without k6, run any load driver you have and collect the same numbers (p95, p99,
error rate, requests per second) into a `measured.json`.

## Step 3: score

```bash
python3 "${CLAUDE_SKILL_DIR}/loadplan.py" from-k6 --summary k6-summary.json --budget proof/load/budget.json
# or, from your own numbers:
python3 "${CLAUDE_SKILL_DIR}/loadplan.py" record --measured measured.json --budget proof/load/budget.json
```

Each metric is scored against its budget and written to the ledger. A metric
missing from the result is an error, never a pass.

## Honest limits

A load test measures one scenario, on one environment, for one duration. Results
depend heavily on the machine, the network, the data and how close it is to
production. A pass means the budget held for that run; it is not a promise about a
traffic spike of a different shape. Run it more than once, and against something
that resembles production.

## Output / next

`proof/load/` (budget and runner), scores under `proof/scores/proof-load.json`,
coverage. Then `/proof-report`.
