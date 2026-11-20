---
name: proof-buyers
description: >-
  Puts the pitch in front of a seeded panel of simulated buyers, each a sub-agent playing one target customer, and tallies who would buy, who passes and why. Use for "would people buy this", "test my pitch", "buyer panel" or "what are the objections".
argument-hint: "[paths to customer.json and pitch.md] [--n 20] [--seed N] [--free]"
---

# proof-buyers

A pitch that sounds right to its author can fall flat with the person it is for. This
skill deals a panel of simulated buyers from the target customer, hands each one the
pitch word for word as its own sub-agent, and counts what they say: who would buy,
who passes, the reasons in their own words, and what would change a no. It writes the
"Buyer panel" section of the business plan and records the evidence in the Proof
ledger. Use it once the offer and the customer are written down, and again after the
pitch changes. You are the moderator. You never write an answer for a buyer. Proof
only reads and records; it never edits the project.

What the user typed after `/proof-buyers`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/buyers.py" <command>
```

Below, `BUYERS` means exactly that.

```bash
BUYERS template --out panel-inputs/                     # example customer.json and pitch.md
BUYERS init --customer customer.json --pitch pitch.md --n 20 [--seed N] [--free]
BUYERS prompts                                          # write briefs, print one prompt per buyer
BUYERS save b007                                        # file a buyer's JSON reply from stdin
BUYERS check                                            # missing or broken answers
BUYERS tally [--min-buy-rate 25]                        # section file, results.json, ledger
BUYERS retest --pitch pitch-v2.md                       # same buyers, new pitch
BUYERS status
```

Options: `--dir proof` (the proof folder) and `--force`. State lives in
`proof/business/panel/`. The example inputs are
`${CLAUDE_SKILL_DIR}/customer.example.json` and `${CLAUDE_SKILL_DIR}/pitch.example.md`.

## Step 1: the customer

Write `customer.json` with the user. Their own facts come first: who has bought, who
has asked, what those people said. Public sources with links come second (for
example the market section from `/proof-market`). An estimate comes last and you say
it is one. Ask at most three questions in one message, only for facts the user alone
knows:

1. Who exactly is the buyer, and where?
2. What do those people do today instead of buying this?
3. What does one price buy, and in what currency?

The fields:

- `business`, `target`, `where`.
- `currency`: required, a prefix such as `Rs ` or `$`. There is no default.
- `age`: `[lowest, highest]`.
- `segments`: `{name, share, income: [lowest, highest]}`. Optional `income_period`
  at the top level (for example `a month`) is printed after each income.
- `behaviours`: `{name, share, how}`, what the buyer does today.
- `objections`: the doubts a buyer might carry in. One is dealt to each buyer.
- `habits`: small true-to-life habits. One is dealt to each buyer.
- `price_for`: what one price buys, such as `one lunch delivered`. Required unless
  the product is free, so every buyer answers in the same unit.
- `free`: true when the product costs the buyer nothing.

Shares in `segments` and in `behaviours` must each sum to 1, or the tool stops.
A missing field is an error with exit code 2, never a guess.

Before dealing, show the user the customer profile in a few lines: who, where, the
segments and behaviours with their shares, and the price unit. A wrong customer makes
every number wrong, so get a yes first.

## Step 2: the pitch

`pitch.md` is 3 to 6 plain lines, the way a buyer would first hear it. Use the user's
own words. Do not polish it; the point is to test what they would really say.

## Step 3: deal

```bash
BUYERS init --customer customer.json --pitch pitch.md --n 20
```

Suggest 20 buyers first. Tell the user the cost before a larger run: one sub-agent
call per buyer, so 50 buyers is 50 calls and 100 is 100, and a retest costs the same
again. A cheaper, faster model is fine for the buyers; the moderator does the
counting, not the thinking. `init` prints the seed. The same seed, customer and size
always deal the same buyers.

With `--free` (or `"free": true`) "buys" means "would use it" and no price questions
are asked. Otherwise each buyer gives four price points in the stated currency and
unit.

## Step 4: run the panel

```bash
BUYERS prompts
```

This writes one brief per buyer under `proof/business/panel/briefs/` and lists them
in waves of 10 with the exact prompt for each. Launch each wave as ONE message with
one Agent call per buyer:

- `subagent_type`: `general-purpose`
- `description`: `buyer <id>`
- `prompt`: `Read <brief path> and follow it exactly. It is your whole brief.`

Give the sub-agent nothing else. No summary of the product, no hint of what you hope
to hear. Wait for the whole wave before the next. Each buyer writes one file into
`proof/business/panel/answers/`; in the default permission mode that is one approval
per file, so suggest accept-edits mode for the run.

Each brief says who the buyer is (name, age, segment, income, what they do today, a
habit, the doubt on their mind), gives the pitch word for word, tells them not to be
agreeable and not to invent facts, and gives the exact JSON to write.

After the last wave:

```bash
BUYERS check       # lists missing or broken answers
BUYERS prompts     # re-briefs only those
```

If a buyer printed its JSON in its reply instead of writing the file, file it as it
is with `BUYERS save <id>` and the reply on stdin. `save` refuses anything that is
not a valid answer. Never fix, complete or write an answer yourself; brief that
buyer again or leave them out.

## Step 5: tally and read it to the user

```bash
BUYERS tally --min-buy-rate 25
```

Read the result in this order: the headline, the reasons for passing with the
buyers' words as written, what would change a no, then the groups that differ most.
Never paraphrase a quote. Say every time that these are simulated buyers.

## Step 6: retest a revised pitch

When the user changes the pitch to answer the main objection:

```bash
BUYERS retest --pitch pitch-v2.md
BUYERS prompts     # run the waves again
BUYERS tally
```

`retest` archives the run under `proof/business/panel/runs/<n>/`, deals the same
buyers from the same seed with the new pitch and clears their answers. After the
next tally the section shows before and after side by side and which reasons for
passing moved.

## What it writes

- `proof/business/buyers.md`: the headline "N would buy, M pass (X%)" ("use it" when
  free), tables by segment and by behaviour with groups under 5 buyers marked thin,
  why they pass and why they buy with one or two quotes each, what would change a
  no, median price points when asked, the seed, and the note on simulated buyers.
- `proof/business/panel/results.json`: the same tally as data.
- `proof/evidence/proof-buyers/`: the customer profile and the pitch as dealt.
- Coverage: `panel-run`, `objections-ranked`, `flip-conditions`, `retested` (marked
  not applicable, with a note, until a retest has been tallied; a re-test is optional
  and never holds the verdict).
- Score: `buy_rate` in percent against `--min-buy-rate` (default 25).
- Findings, all computed from the answers: one per reason for passing given by at
  least a quarter of the panel (`buyers-pass-<code>`); a revised pitch that did not
  move the main objection; a panel under 20 buyers. A new tally replaces them.

## Honest limits

These are simulated buyers, not people. Every one is the same language model wearing
a different card, so the panel shares that model's leanings, and language models
lean agreeable: read the buy rate as an upper bound. The panel is good at surfacing
objections and wording you had not thought of. It does not measure demand, and its
price points are a range to test, not a price. Panel quotes are never customer
testimonials and must never be shown as such. Confirm what matters with real buyers
and real money.

## Output / next

`proof/business/buyers.md`, `proof/business/panel/` and the ledger entries for
`proof-buyers`. The business plan includes the section file as written. Next:
`/proof-finance` with a price inside the tested range, then `/proof-report` and
`/proof-board`.
