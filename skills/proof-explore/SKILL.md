---
name: proof-explore
description: >-
  Exploratory testing with a swarm of user personas. Scaffolds a persona profile
  and brief for the target, runs the scenario swarm so a crowd of simulated users
  each tries the product their own way, then harvests the problems they report into
  candidate findings for a human to reproduce and verify. Finds the odd cases a
  scripted test never lists. Use when the user says "explore the app", "try to
  break it", "exploratory testing", "what would users trip on", "unscripted
  testing", or "throw a crowd at it".
argument-hint: "[what to explore, e.g. the checkout]"
---

# proof-explore

Scripted tests check what you thought of. Exploratory testing finds what you did
not. This skill sends a varied crowd of simulated users through the product, each
with a different mood, skill level, device and intent, and collects where they got
stuck, so you have a list of real-feeling problems to chase down.

What the user typed after `/proof-explore`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/explore.py" scaffold --target "a web checkout" --out proof/explore
python3 "${CLAUDE_SKILL_DIR}/explore.py" harvest --results proof/swarm/explore-checkout/results.json
python3 "${CLAUDE_SKILL_DIR}/explore.py" status
```

## Step 1: scaffold

```bash
python3 "${CLAUDE_SKILL_DIR}/explore.py" scaffold --target "a web checkout"
```

This writes a swarm profile (personas varying by mood, skill, device and intent)
and a brief under `proof/explore/`. Open the brief and fill in the
"What you can see and do" section with the real screens, fields, buttons and rules
of the target, so the crowd is exploring the actual product.

## Step 2: run the swarm

Use `/proof-swarm` with the scaffolded files:

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-swarm/swarm.py" init --profile proof/explore/profile.json --brief proof/explore/brief.md
python3 "${CLAUDE_SKILL_DIR}/../proof-swarm/swarm.py" prompts
# run the waves of sub-agents, then:
python3 "${CLAUDE_SKILL_DIR}/../proof-swarm/swarm.py" tally
```

Offer `--quick` (20 agents) for a first look before a full run.

## Step 3: harvest

```bash
python3 "${CLAUDE_SKILL_DIR}/explore.py" harvest --results proof/swarm/explore-checkout/results.json
```

This groups the problems the crowd reported (repeats collapse into one) and writes
them as **candidate findings**, unverified, with the agents' own repro steps.

## Step 4: reproduce, then verify

A simulated user's complaint is a lead, not evidence. For each candidate that
matters, reproduce it against the real app, save the evidence (a screenshot, a
capture, a failing test), and only then verify the finding through `proof-core`.
Drop the ones that do not reproduce with a reason.

## Honest limits

The crowd is one model wearing many persona cards, so it shares that model's
blind spots and leans agreeable. It is good at surfacing confusing moments, awkward
inputs and overlooked paths; it is not a measure of how often real users hit them.
Nothing it reports counts until a human reproduces it for real.

## Output / next

`proof/explore/` (profile, brief), the swarm run under `proof/swarm/`, candidate
findings under `proof/findings/proof-explore/`, a report under
`proof/evidence/proof-explore/`. Reproduce and verify, then `/proof-report`.
