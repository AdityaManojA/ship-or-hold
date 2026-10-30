---
name: proof-llm-cost
description: >-
  Works out what an app's language-model calls cost and how fast they answer,
  from usage records you export, against a budget: cost per call and per
  conversation, cost per active user per day, the projected monthly cost of a
  heavy user, tokens per call, response time at the median, 95th and 99th
  percentile, error rate and prompt-cache share. Flags runaway conversations,
  duplicate calls and model calls in the code with no output-token cap. Prices come
  only from a file you fill in; it never guesses one and never calls a provider.
  Optional, not part of a full check. Use when the user says "what does the AI
  cost", "LLM cost per user", "are we losing money on the model", "token usage",
  "OpenAI bill", "how slow are the AI responses", "spend limits", or "cost per
  conversation".
argument-hint: "[usage records, or blank to set up prices]"
---

# proof-llm-cost

An app built on a model API can lose money on every user without anyone noticing:
a long system prompt, retrieved context that grows, an agent loop that retries, a
reply with no length cap. This skill turns exported usage into the numbers that
show it, each against a budget you set, and records them as evidence.

It is optional and not part of `/proof-full-check`. Run it on its own when the
product calls a model API.

What the user typed after `/proof-llm-cost`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/llmcost.py" prices --out proof/llm-cost/prices.json
python3 "${CLAUDE_SKILL_DIR}/llmcost.py" analyze usage.jsonl --prices proof/llm-cost/prices.json
python3 "${CLAUDE_SKILL_DIR}/llmcost.py" record usage.jsonl --prices proof/llm-cost/prices.json
python3 "${CLAUDE_SKILL_DIR}/llmcost.py" scan-code backend/app --record
python3 "${CLAUDE_SKILL_DIR}/llmcost.py" status
```

## Step 1: prices

`prices` writes a template (it will not replace a file Proof did not write unless
you pass `--force`). Every number in it is a placeholder: fill in your provider's
current prices in USD per 1 million tokens for input, output and cached input,
under the model names exactly as they appear in your records. A record model also
matches a key when it starts with the key and a dash (dated model versions). A
model with no price stops the run and is named; Proof never guesses a price.
`prices.example.json` next to this file shows the shape.

## Step 2: export usage

One row per model call, as JSON, JSON lines or CSV. Any subset of these keys is
read: `model`, `input_tokens` (or `prompt_tokens`), `output_tokens` (or
`completion_tokens`), `cached_tokens`, `latency_ms` (or `duration_ms`, or
`latency_s`), `user` (or `user_id`), `conversation` (or `session_id`, `trace_id`,
`thread_id`), `timestamp` (ISO 8601 or epoch), `status` or `error`, `prompt_hash`.

- **Your own logs:** log one line per call with those fields; this gives the most.
- **Langfuse:** export the generations of a period (tokens, latency, model, user,
  session) as CSV or JSON from the UI or the public API.
- **The provider's usage export:** the OpenAI usage dashboard and Costs/Usage API
  give tokens per model and day; with no user or conversation ids, the per-user and
  per-conversation figures are marked not applicable.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/llmcost.py" record usage.jsonl --prices prices.json \
  --budget-cost-per-user-day 0.15 --budget-latency-p95-ms 6000 --max-calls 20
```

Each metric is a coverage check and a score against its budget; a metric the
records cannot measure is not applicable with the reason, never a pass. Budgets
(defaults are starting points): `--budget-cost-per-request-p50` (0.02 USD),
`--budget-cost-per-request-p95` (0.05), `--budget-cost-per-conversation-p50` (0.10),
`--budget-cost-per-conversation-p95` (0.50), `--budget-cost-per-user-day` (0.25),
`--budget-heavy-user-month` (10), `--budget-tokens-per-request-p95` (16000),
`--budget-latency-p50-ms` (3000), `--budget-latency-p95-ms` (8000),
`--budget-latency-p99-ms` (15000), `--budget-error-rate` (0.02) and
`--budget-cache-hit-share` (at least 0).

A metric over budget is a medium finding. A conversation with more than
`--max-calls` calls (default 25) or more than `--max-conversation-cost` USD is a
runaway (medium, ids listed). The same `prompt_hash` sent twice within 5 seconds is
a duplicate (low). The records, the prices file and a summary are copied to
evidence.

**For proof-finance:** `proof/evidence/proof-llm-cost/cost-summary.json` holds the
LLM cost per active user per month (typical and heavy). Copy it into the variable
costs of your `/proof-finance` numbers so the unit economics carry the real model
bill.

## Step 4: caps in the code

```bash
python3 "${CLAUDE_SKILL_DIR}/llmcost.py" scan-code backend/app src --record
```

Finds model calls (OpenAI, Anthropic and similar SDK calls) with no
`max_tokens`, `max_output_tokens` or equivalent within the call, each a low finding
with file and line. `--json` prints the result; without `--record` nothing is
written.

## Honest limits

The figures cover only the records you export: a sample week is not a month, and
calls that were not logged are not counted. The provider's bill is the truth;
compare the total with it. Prices change, so the file must be current. The monthly
projection assumes a user spends their average active-day amount every day for 30
days, which overstates light users and is meant for the heavy-user line. The code
scan is a pattern match: a cap set in a shared helper or config can be missed.

## Output / next

`proof/coverage/proof-llm-cost.json`, `proof/scores/proof-llm-cost.json`, findings
under `proof/findings/proof-llm-cost/`, inputs and `cost-summary.json` under
`proof/evidence/proof-llm-cost/`. Then `/proof-finance` and `/proof-report`.
