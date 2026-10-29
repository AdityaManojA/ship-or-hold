---
name: proof-web-e2e
description: >-
  End-to-end testing of a web app's real user flows, where every result is backed
  by evidence. Plans the critical flows (sign in, add to cart, checkout, and so on),
  starts the dev server, drives each flow with a browser (a scripted run when a
  browser library is installed, the built-in browser tools otherwise), saves a
  screenshot or page capture for each, and records a pass or a failing finding in
  the ledger. Use when the user says "test the web app", "end to end", "e2e", "click
  through the flows", "does the login work", "test the checkout", or "browser test".
argument-hint: "[a flow to focus on, or blank for all]"
---

# proof-web-e2e

Walk the app the way a user would, flow by flow, and leave proof of each walk. A
flow passes only when there is a screenshot or page capture behind it; a flow fails
into a finding with a repro, not a sentence.

What the user typed after `/proof-web-e2e`: `$ARGUMENTS`

## The tools

```bash
python3 "${CLAUDE_SKILL_DIR}/flows.py" <command>     # plan, record, status
python3 "${CLAUDE_SKILL_DIR}/serve.py" ...           # start the dev server, wait, run, tear down
```

## Step 1: know the target

If `proof/target.json` exists, read it. Only drive a live, non-local URL when the
target is marked authorised. A local dev server (localhost / 127.0.0.1) is always
fine. If there is no target yet, run `/proof-plan` first.

## Step 2: list the flows

With the user, or by reading the routes and pages, write a flow spec (shape:
`flows.example.json`): an id, a title, whether it is `critical`, the start URL, the
steps, and the expected end state. Then:

```bash
python3 "${CLAUDE_SKILL_DIR}/flows.py" plan flows.json
```

This writes `proof/web/flows.json`, a `checklist.md`, and a coverage ledger with
every flow marked not-run.

## Step 3: start the app

If the app is not already running, start it and wait for its port:

```bash
python3 "${CLAUDE_SKILL_DIR}/serve.py" --run "npm run dev" --port 3000 -- <the command that drives the flows>
```

`serve.py` waits for the port, runs the inner command, and always tears the server
down afterward, even on Ctrl-C. For a server you started another way, use
`serve.py --wait --port 3000`.

## Step 4: drive each flow

Preferred, when a browser automation library is installed (check with
`/proof-doctor --for proof-web-e2e`): generate a starter script, fill in the steps,
and run it. It saves a full-page screenshot and the page text as evidence.

```bash
python3 "${CLAUDE_SKILL_DIR}/flows.py" script checkout --out proof/web/checkout.py --python
```

Fallback, with no library: drive the flow with the built-in browser tools
(navigate, read_page, find, computer, get_page_text), and save evidence yourself by
writing the page text to `proof/evidence/proof-web-e2e/<flow>.txt`. Say plainly that
a text capture is weaker evidence than a screenshot.

## Step 5: record each flow

```bash
# it worked:
python3 "${CLAUDE_SKILL_DIR}/flows.py" result checkout pass --evidence evidence/proof-web-e2e/checkout.png

# it broke (writes a finding; --verify promotes it using the evidence file):
python3 "${CLAUDE_SKILL_DIR}/flows.py" result checkout fail \
  --evidence evidence/proof-web-e2e/checkout.png \
  --title "Order submits twice on a fast double click" --severity high \
  --repro "Open /cart, Pay, click Pay again within 1s; two orders appear" --verify

# not in this build:
python3 "${CLAUDE_SKILL_DIR}/flows.py" skip gift-card --note "no gift cards yet"
```

Run the edge cases on every critical flow:

```bash
python3 "${CLAUDE_SKILL_DIR}/flows.py" edge-cases checkout
```

Two rules: never record a pass without an evidence file (the tool refuses it), and
never fail a flow on a guess. Reproduce a suspected bug once more, save the
evidence, then record it.

## Honest limits

A pass proves that one path on this build on this machine. A flaky step needs a
second run before it becomes a finding. A text capture is weaker than a screenshot.
Passing every flow is not the absence of all bugs; it is the presence of the ones
you checked.

## Output / next

`proof/web/flows.json`, `proof/web/checklist.md`, `proof/coverage/proof-web-e2e.json`,
findings under `proof/findings/proof-web-e2e/`, evidence under
`proof/evidence/proof-web-e2e/`. Then `/proof-report`.
