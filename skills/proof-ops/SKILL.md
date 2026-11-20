---
name: proof-ops
description: >-
  The operations section of the business plan: team, routines, monthly running costs, and a ranked register of what the business depends on but does not control. Use for "operations plan", "how will this run", "what could stop us" or "single point of failure".
argument-hint: "[path to an ops.json, or nothing to start one]"
---

# proof-ops

A plan that works on paper still has to be run by someone on a Tuesday. This skill
writes down how the business runs on day one and what could stop it: the routines,
who does them, what it costs each month to keep the doors open, and above all what
it depends on that it does not control (a platform, a data source, a supplier, a
single person, a licence). Use it when the product and the price are roughly
settled and before launch. Proof only reads and records. It never edits the
project and never contacts a supplier or a platform.

What the user typed after `/proof-ops`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/ops.py" template --out ops.json   # start from an example
python3 "${CLAUDE_SKILL_DIR}/ops.py" check ops.json            # print the section, record nothing
python3 "${CLAUDE_SKILL_DIR}/ops.py" record ops.json           # write the section and the ledger
```

Each command takes `--dir` (the proof folder, default `proof`). The example the
template writes is `${CLAUDE_SKILL_DIR}/ops.example.json`; every value in it is made
up and must be replaced.

## Step 1: ask the user for what only they know

Ask the user for facts only they know: who works on it and for how many hours a
week, what it costs each month, and what agreements exist. Ask at most three
questions in one message. Good first three:

1. Who works on this, and roughly how many hours a week each?
2. What do you pay for every month to keep it running, and which of those numbers
   come from a bill or a quote?
3. What does it rely on that someone else owns, and do you hold anything in
   writing from them?

Where each input comes from, in this order: the user's own facts first, public
sources with links second (a platform's published terms, a supplier's price list),
a labelled estimate last. If the project is in the working folder you may read it
to find dependencies the user forgot (a payment service, a data feed, a hosting
platform) and ask about each. Do not guess the answers.

## Step 2: write ops.json

- `business` and `currency` (required, a prefix such as `"Rs "` or `"$"`; there is
  no default).
- `team`: `who`, `hours_per_week`, `does`.
- `routines`: `name`, `when` (`daily`, `weekly`, `monthly` or `on_event`), `steps`,
  `owner`, `time_minutes` for one run.
- `monthly_costs`: `label`, `cost`, and `source`: `invoice`, `quote`, `owner` (a
  figure the owner pays or has agreed) or `estimate`.
- `dependencies`: `name`, `kind` (`platform`, `data`, `supplier`, `person`,
  `licence`, `payment`), `used_for`, `permission`, `if_lost`, `fallback` (leave it
  empty if there is none), `likelihood` and `impact`, each 1 to 5.
- `support`: `channel`, `response_target_hours`, `who`.
- `legal`: `item`, `status` (`done`, `to_do`, `unknown`), `note`.
- `kpis`: `name`, `target`, `how_measured`.

Rules for the judgement calls:

- A permission is `written` only if the user says they hold it. Published terms
  that allow the use are `public_terms`, and say where you read them. If nobody has
  looked, it is `unknown`. Never upgrade a permission because it seems likely.
- `likelihood` and `impact` are the owner's call. Offer a number and your reason,
  then use the one they pick.
- An empty `fallback` is an honest answer. Do not write one the user did not give.
- Legal items are a list for the owner to take to a professional. This skill does
  not say what the law requires and is not legal advice.

## Step 3: check, fix, record

```bash
python3 "${CLAUDE_SKILL_DIR}/ops.py" check ops.json
python3 "${CLAUDE_SKILL_DIR}/ops.py" record ops.json
```

The tool adds up the monthly costs and the share that is an estimate. It adds up
team hours and the routines' weekly time (a daily routine counts seven times a
week, a monthly one is divided by 4.3, `on_event` is left out). It scores each
dependency as likelihood times impact and ranks them. It names single points of
failure (impact 4 or 5 with no fallback), platforms and data sources used with
permission `unknown` or `none`, routines with no owner, and whether one person
owns every routine. A missing currency, cost, likelihood or impact stops it with
exit code 2; nothing is filled in.

Read the "What could stop this" paragraph to the user in plain words. For each
high finding ask what they want to do, and record their answer in the input, not
in prose.

## What it writes

- `proof/business/ops.md`: the team, the routines with steps, monthly costs with
  sources and a total, the dependency register ranked by risk, "What could stop
  this", support, the legal list and the KPIs.
- `proof/evidence/proof-ops/ops.json`: a copy of the inputs.
- `proof/coverage/proof-ops.json`: `team-and-hours`, `routines-written`,
  `costs-listed`, `dependencies-ranked`, `support-defined`, `legal-listed`,
  `kpis-set`.
- `proof/scores/proof-ops.json`: `routine_hours_fit` and `single_points_of_failure`.
- `proof/findings/proof-ops/ops-*.json`: a platform or data dependency used
  without permission, each single point of failure, routines that need more hours
  than the team has, costs that are mostly estimates, a routine with no owner, a
  legal item nobody knows the state of, one person owning every routine.
  Recording again replaces them.

## Honest limits

The register holds only what was listed. A dependency nobody thought of is not in
it, and the tool cannot find it. Likelihood and impact are judgements on a
five-point scale, so the ranking is a way to order a conversation, not a
measurement. A written fallback is not a tested one. The hours check counts
routines only, not selling, fixing or thinking. The tool does not read contracts
or terms, and nothing here is legal, tax or employment advice.

## Output / next

`proof/business/ops.md` plus coverage, scores and findings in the ledger. Next is
`/proof-release`: launch-day readiness (alerts, backups, rollback, on-call) is
that skill's job, and it wants proof for each item, so this skill does not repeat
it. `/proof-go-to-market` reads this skill's high findings through the ledger as
blockers to promotion. `/proof-report` computes the verdict.
