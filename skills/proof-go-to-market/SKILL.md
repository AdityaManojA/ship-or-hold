---
name: proof-go-to-market
description: >-
  The go-to-market section of the business plan: positioning, channels with costs, brand basics and a dated first test with a pass mark, held until ledger blockers have fixes. Use for "go to market", "how do I get customers", "marketing plan" or "launch plan".
argument-hint: "[path to a gtm.json, or nothing to start one]"
---

# proof-go-to-market

A product nobody hears about fails as surely as one that does not work. This skill
writes down how the product reaches its buyers: who it is for and the one line that
positions it, the channels that buyer really uses and what each costs, the brand
basics (name checks, voice, promise), and a dated plan that starts with one cheap
test on real people whose pass mark is written before it runs. It also holds one
rule: nothing is promoted while the evidence ledger still holds a blocker. Use it
once the product and its buyers are roughly known and before any money or effort
goes on promotion. Proof only reads and records. It never edits the project, posts
anything, or contacts anyone.

What the user typed after `/proof-go-to-market`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/gtm.py" template --out gtm.json   # start from an example
python3 "${CLAUDE_SKILL_DIR}/gtm.py" check gtm.json            # print the section, record nothing
python3 "${CLAUDE_SKILL_DIR}/gtm.py" record gtm.json           # write the section and the ledger
```

Each command takes `--dir` (the proof folder, default `proof`). The example the
template writes is `${CLAUDE_SKILL_DIR}/gtm.example.json`; every value in it is made
up and must be replaced.

## Step 1: read what is already known

1. If `proof/business/buyers.md` exists, read it. If `proof/business/market.md`
   exists, read it. Take the channels and the wording from what buyers and
   competitors' customers actually said: the places they named, the words they used
   for the problem. Do not write a channel list from general knowledge when those
   files are there.
2. If neither exists, say so and carry on with the owner's own facts. Mark each
   channel's `evidence` honestly.

## Step 2: get the facts only the owner has

Ask at most three questions in one message, for facts only they know. Typically:
the monthly budget for reaching buyers (plan for no budget unless the user gives
one; `0` is a valid answer and means the plan must work unpaid), the launch date
if there is one, and which name checks they have already run. Everything else you
can draft for them to correct.

Where each input comes from, in this order: the user's own facts first, public
sources with links second, a labelled estimate last.

## Step 3: write gtm.json

- `business`, `currency` (required, a prefix such as `"Rs "` or `"$"`; there is no
  default) and `today` (required, an ISO date; the tool never reads the clock).
- `positioning`: `for_who`, `problem`, `what_it_is`, `unlike`. All four are needed.
  The tool joins them as "For <for_who>, who <problem>, <business> is
  <what_it_is>, unlike <unlike>." so write each part to fit that sentence.
- `promise`: one line the business can keep.
- `voice`: three traits, each with `trait`, `do` and `do_not`.
- `name_checks`: one entry each for `trademark`, `domain`, `handle` and `meaning`,
  with `status` `done` or `to_do` and a `note`. A name check is something the owner
  runs. Never set one to `done` because the name sounds free to you; set it only
  when the user says they ran it, and put what they did in the note.
- `channels`: `name`, `cost_per_month`, `first_action`, `measure`, and `evidence`:
  `panel` (the buyer panel pointed to it), `competitors` (competitors' customers
  are there, with a link in your notes), `owner` (the owner's own experience) or
  `estimate` (a guess).
- `budget_per_month`: the number the user gave. If they gave none, ask; do not
  invent one.
- `first_test`: `what`, `where`, `starts`, `ends`, `people` (how many are asked),
  `pass` and `fail` (plain words with a number in each) and `measure`. The test
  must involve real people. The simulated panel is not a first test.
- `countdown`: `date`, `task`, `critical` (true or false) and `owner` for each
  task, and an optional `launch_date`.
- `before_promotion`: each `fix` with a `due` date and, when it answers a finding
  in the ledger, a `ref` such as `proof-privacy/<finding-id>`.
- optional `accessibility`: brand colour pairs with `ratio` and `passes`, copied
  from a `/proof-a11y` contrast run.

No invented statistics, testimonials or partner names anywhere in the file. If a
number has no source, it does not go in.

## Step 4: check, fix, record

```bash
python3 "${CLAUDE_SKILL_DIR}/gtm.py" check gtm.json
python3 "${CLAUDE_SKILL_DIR}/gtm.py" record gtm.json
```

`check` prints the section and what would be recorded. A missing currency, a
missing `today`, a bad date or a channel with no cost stops the tool with exit
code 2; nothing is filled in for you.

The promotion gate reads the ledger through the same code that computes the
verdict. Every verified finding at high or critical, from any skill, needs a
`before_promotion` entry whose `ref` is `<skill>/<id>` and whose `due` is on or
before the day the first test starts. When there is no ledger to read, the gate is
reported as not checked, never as passed.

## What it writes

- `proof/business/go-to-market.md`: the positioning sentence, the promise and voice
  table, name checks, channels against the budget, the "before any promotion" list,
  the first test with its marks, and the countdown with critical tasks marked.
- `proof/evidence/proof-go-to-market/gtm.json` and `promotion-gate.json`.
- `proof/coverage/proof-go-to-market.json`: `positioning-stated`, `channels-chosen`,
  `brand-basics`, `first-test-defined`, `countdown-dated`, `promotion-gate`.
- `proof/scores/proof-go-to-market.json`: `channel_cost_within_budget` and, when
  the gate could be checked, `blockers_without_a_fix_date`.
- `proof/findings/proof-go-to-market/gtm-*.json`: an open blocker with no dated
  fix, a first test with no pass mark, channels over budget, every channel resting
  on an estimate, a trademark or domain check still to do, a channel with no
  measure, paid reach with no budget. Recording again replaces them.

## Honest limits

This is a plan, not a result. The tool checks that the plan is complete, dated and
inside its budget; it cannot tell whether a channel will work, whether the
positioning is true, or whether the pass mark is the right one. It does not search
a trade mark register or a domain registry, and a `done` name check is only the
owner's word. A fix with a due date is a promise, not a fix: the blocker stays in
the ledger until its own skill shows it gone. Only the first test, run on real
people and read against the marks written here, says anything about demand.

## Output / next

`proof/business/go-to-market.md` plus coverage, scores and findings in the ledger.
Then `/proof-ops` for how the business runs, and `/proof-release` a few days before
the launch date for launch-day readiness (alerts, backups, rollback, on-call); give
it the same `launch_date`. `/proof-report` computes the verdict and `/proof-board`
reads this section's evidence.
