---
name: proof-offer
description: >-
  Turns a product into an offer that answers the objections buyers raised: every obstacle listed, each solution scored, the stack built and each promise checked against what exists today. Use for "build my offer", "why is nobody buying", "what should I promise" or "add a guarantee".
argument-hint: "[path to an offer.json, or nothing to start one]"
---

# proof-offer

A product is what was built. An offer is the reason someone buys it. This skill
builds the offer from the obstacles buyers named: it lists everything that stands
between the buyer and the result they want, turns each obstacle into a solution,
keeps the solutions that are worth a lot to the buyer and cheap to deliver, and marks
which parts exist today. Use it after buyers have been asked and before the pitch is
written. Proof only reads and records. It never edits the project.

What the user typed after `/proof-offer`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/offer.py" template --out offer.json   # start from an example
python3 "${CLAUDE_SKILL_DIR}/offer.py" check offer.json            # print the offer, record nothing
python3 "${CLAUDE_SKILL_DIR}/offer.py" record offer.json           # write the section and the ledger
```

Every command takes `--dir` (the proof folder, default `proof`) and `--force`. A
filled-in example sits beside the tool: `${CLAUDE_SKILL_DIR}/offer.example.json`.

## Step 1: list the problems

Walk through the buyer's path in three stages and write down every obstacle, in the
buyer's own words:

- `before`: what stops them buying (doubt, price, not knowing where to start).
- `during`: what gets in the way while they use it (effort, waiting, confusion).
- `after`: what the result leaves them needing next.

Where the problems come from, in this order:

1. The user's own facts first: what real buyers told them, support messages, refund
   reasons. Mark these `owner`.
2. The buyer panel and public sources second. If `proof/business/buyers.md` exists,
   pull the objections from it and mark them `panel`. If `proof/business/market.md`
   exists, pull what customers complain about in competitors' public reviews and mark
   them `competitors`, keeping the links that section gives.
3. A labelled estimate last. Mark it `estimate`. The report counts these separately.

Aim for at least ten. Fewer is recorded as a gap. Ask the user at most three
questions in one message, and only for facts that only they know (what buyers
actually said to them, what exists today, what a claimed guarantee would cost).

## Step 2: turn each problem into solutions and score them

For each problem write one or more solutions, then score each one honestly:

- `value` from 1 to 5: what it is worth to the buyer.
- `cost` from 1 to 5: what it takes the business to deliver, every time.
- `exists`: `yes`, `partly` or `no`. This means today, not after the next release.
- `role`: `core` (the main thing sold), `bonus` (added to remove an obstacle),
  `guarantee` (takes the risk off the buyer), `later` (worth doing, not yet) or
  `not_ours` (outside what this business can fix).

The tool puts a solution in the stack only when its role is core, bonus or guarantee
and it scores value 4 or more and cost 2 or less. Anything else given a stack role is
listed as left out, with its scores.

## Step 3: fill in the rest of `offer.json`

- `currency` (required): the prefix money is printed with, such as `"Rs "` or `"$"`.
  There is no default.
- `core` (required): one sentence, the result the buyer gets.
- `name` (required): the offer's name.
- `problems`: `{id, stage, problem, source}` for each.
- `solutions`: `{problem, solution, value, cost, exists, role}` for each, where
  `problem` is a problem id.
- `guarantee` (optional): `{promise, cost_if_claimed, expected_claim_rate}`. The rate
  is a share from 0 to 1.
- `urgency` (optional): `{what, true, ends}`. `true` is `true` only when the deadline
  or the limit is real.
- `scores` (optional): `{before, after}`, each with `outcome`, `belief`, `speed` and
  `ease` from 1 to 10. These say how much the buyer wants the result, how sure they
  are it will work, how soon it arrives and how little effort it takes, for the plain
  product and then for the offer.
- `unsolved` (optional): `{problem, why}` for each problem the offer does not answer.
- `retest` (optional): how the new pitch will be put in front of buyers again.

A missing required field stops the tool with exit code 2.

## Step 4: check, then record

```bash
python3 "${CLAUDE_SKILL_DIR}/offer.py" check offer.json
python3 "${CLAUDE_SKILL_DIR}/offer.py" record offer.json
```

The tool works out the problems by stage and by source, which problems have no
solution at all, the stack, which stack items do not exist today, the high-value
solutions marked later or not ours (the big objections the offer cannot answer yet),
the guarantee's expected cost (the cost if claimed times the expected claim rate) and
the change in each before and after score.

## What it writes

- `proof/business/offer.md`, starting with `# The offer`: the problem list by stage,
  the scored solutions table, the stack, the guarantee and its expected cost, the
  before and after scores, and a plain section called "What the offer cannot fix".
- A copy of the inputs under `proof/evidence/proof-offer/`.
- Coverage: `problems-listed`, `solutions-scored`, `stack-built`, `guarantee-costed`,
  `promises-exist`, `retest-planned`.
- Scores: `problems_answered_pct` (for information, no bar) and
  `stack_promises_missing` (must be 0).
- Verified findings the tool computes: a high one for each stack item that is not
  built; a medium one for each stack item that is only partly built; a medium one
  when a value-5 solution is marked later or not ours; a medium one when the urgency
  is not true; a low one for each problem with no solution; a low one when fewer than
  ten problems are listed. Recording again replaces the findings from the earlier run.

## Rules for the offer itself

- Every promise must be deliverable and affordable. If the business cannot do it for
  every buyer who takes the offer, or cannot pay for the guarantee at the expected
  claim rate, it does not go in.
- No fake reviews, no fake scarcity, no fake discounts, no fake endorsements. A
  deadline, a limit or a "was" price is used only when it is real.
- Follow the advertising and consumer rules of the user's market. Guarantees, refunds
  and claims about results are regulated in most places. This is not legal advice.
- Say what the offer cannot fix. The section is in the file so the pitch does not
  imply otherwise.

## Honest limits

The value and cost scores are judgements by whoever filled in the file, and so are
the before and after scores. The tool adds them up. It cannot tell whether a 4 should
have been a 2. `exists` is the user's word; the tool does not look at the product.
The expected claim rate is a guess until real buyers have had the chance to claim. An
offer that reads well here has still not been tested, which is why the last coverage
check is the re-test.

## Output / next

`proof/business/offer.md`, plus coverage, scores and findings in the ledger under
`proof/`. Re-test the new pitch with the buyers skill (`/proof-buyers`) and compare it with the first
round. `/proof-pricing` prices the offer, `/proof-business-plan` includes the section
as written, and `/proof-report` and `/proof-board` read the ledger.
