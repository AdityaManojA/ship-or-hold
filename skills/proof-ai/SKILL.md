---
name: proof-ai
description: >-
  Tests an app's own language-model feature for safety and reliability, across the
  known problem categories for LLM features: prompt injection, insecure handling of
  model output, leaking secrets or personal data, taking real actions without
  confirmation, overreliance and hallucination. It tracks which categories you
  covered and records each result against your own app, holding failures to the
  verification gate. Use when the user says "test my AI feature", "red team my
  chatbot", "is my LLM app safe", "prompt injection", "does my assistant leak",
  "LLM security", or "test the model feature". Cites the OWASP Top 10 for LLM apps.
argument-hint: "[a category to focus on, or blank for all]"
---

# proof-ai

If your app puts a language model in front of users or lets one take actions, it has
a new class of ways to go wrong. This skill walks the known categories, on your own
app, and records how it holds up, with the transcript behind each result.

What the user typed after `/proof-ai`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/redteam.py" deck
python3 "${CLAUDE_SKILL_DIR}/redteam.py" plan
python3 "${CLAUDE_SKILL_DIR}/redteam.py" result prompt-injection pass --evidence evidence/proof-ai/pi-1.txt
python3 "${CLAUDE_SKILL_DIR}/redteam.py" status
```

## Authorisation

Test your own app, or one you are authorised to test. These are safety checks on a
feature you own, not an attack on someone else's service.

## Step 1: the deck and the plan

```bash
python3 "${CLAUDE_SKILL_DIR}/redteam.py" deck     # the categories, what to check, what safe looks like
python3 "${CLAUDE_SKILL_DIR}/redteam.py" plan     # a coverage ledger, one check per category
```

Each category names what to look for and what a safe result looks like, with an
OWASP LLM Top 10 id to read more.

## Step 2: run real cases against your app

For each category, run several real cases against your app through the normal
interface (or with the built-in browser tools), and save each transcript to
`proof/evidence/proof-ai/`. For scale across many phrasings, `/proof-swarm` can deal
a crowd of cases; treat its results as leads and confirm the real ones here.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/redteam.py" result pii-leak fail \
  --evidence evidence/proof-ai/pii-3.txt \
  --title "Assistant echoes another user's email when asked to summarise the thread" \
  --severity high --verify
python3 "${CLAUDE_SKILL_DIR}/redteam.py" na excessive-agency --note "the feature cannot take any action"
```

A category passes only with a transcript behind it; a failure writes a finding you
verify from the transcript.

## An LLM inside a mobile app

For an assistant inside an iOS or Android app, add the mobile checks:

```bash
python3 "${CLAUDE_SKILL_DIR}/redteam.py" deck --mobile
python3 "${CLAUDE_SKILL_DIR}/redteam.py" plan --mobile
```

Six more categories join the plan: `offline-fallback` (airplane mode or no model),
`clipboard-injection` (pasted or shared-in text), `deeplink-injection` (links,
intents and notification payloads), `image-text-injection` (text read from photos
or the camera), `on-device-leak` (app storage, device logs, backups, keyboard
learning, lock-screen notifications) and `model-integrity` (a swapped or corrupt
model file). Run each on your own build, on your own emulator or device: drive it by
hand, with `/proof-mobile-explore`, or with a mobile automation tool, and save the
screen recording, screenshots or `adb logcat` output as evidence.

To run an evaluation suite against the app's real model, point it at what the app
calls: the backend API it uses, or, for a model bundled on the device, the same
model file served locally (for example with llama.cpp's `llama-server`) so the
prompts and settings match the app. Speed, memory, heat and battery of the
on-device model are measured by `/proof-llm-device`.

## Automated evaluations

For scale, run an evaluation tool against your own feature and import its report.
Each failed test becomes a finding with the raw report as evidence, and the pass
rate becomes a score with a budget, so it counts in the verdict:

```bash
promptfoo eval -o promptfoo.json
python3 "${CLAUDE_SKILL_DIR}/eval_import.py" promptfoo promptfoo.json --min-pass 0.95

# a garak report (.jsonl)
python3 "${CLAUDE_SKILL_DIR}/eval_import.py" garak garak.report.jsonl --min-pass 0.95
```

For an eval suite of your own (a custom runner, an LLM judge, Langfuse
experiments), save one row per case and check as JSON or JSON lines, with `case`,
`metric`, `value` (true/false, 0/1 or a 0..1 score) and `comment`, and import it:

```bash
python3 "${CLAUDE_SKILL_DIR}/eval_import.py" results evals-out.jsonl --name policy-evals --min-pass 0.9
python3 "${CLAUDE_SKILL_DIR}/eval_import.py" results judge-scores.json --pass-at 0.7   # 0..1 scores
```

A runner that already loops over its results needs only a few lines to write that
file, for example after a Langfuse `run_experiment`:

```python
with open("evals-out.jsonl", "w") as fh:
    for item in result.item_results:
        for ev in item.evaluations or []:
            fh.write(json.dumps({"case": item.item.id, "metric": ev.name,
                                 "value": ev.value, "comment": ev.comment}) + "\n")
```

It prints the pass rate per metric, records the overall pass rate as a score with
your budget, and turns each failed row into a finding.

Failures arrive unverified; read the transcript in the report, then verify or
reject. `/proof-doctor --for proof-ai` shows which of these tools are installed.

## Honest limits

Model behaviour is non-deterministic: a category that held up once can fail on a
different phrasing or a later model version, so cover each with several cases and
re-test after changes. A pass means the cases you tried behaved safely, not that the
feature is safe against every input. This checks behaviour, not the model's weights
or training.

## Output / next

`proof/coverage/proof-ai.json`, findings under `proof/findings/proof-ai/`,
transcripts under `proof/evidence/proof-ai/`. Then `/proof-report`.
