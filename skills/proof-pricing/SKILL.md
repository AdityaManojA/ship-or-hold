---
name: proof-pricing
description: >-
  Sets a price from what buyers said, what competitors charge and what one unit costs, or what 1,000 visits earn when the product is free to its user. Use for "what should I charge", "set my price", "is my price right" or "how does a free product make money".
argument-hint: "[path to a pricing.json, or nothing to start one]"
---

# proof-pricing

A price is a guess until three things have been checked: what buyers said they would
pay, what competitors charge for the same job, and what the business needs each sale
to earn. This skill does that arithmetic and writes the pricing section of the
business plan. Use it before launch, or when a price is being changed. It also covers
the product that is free to its user and earns from someone else (adverts, referral
fees, sponsors). There the real price is what one visit earns, and only revenue lines
that are live today count. Proof only reads and records. It never edits the project.

What the user typed after `/proof-pricing`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/pricing.py" template --out pricing.json          # a paid example
python3 "${CLAUDE_SKILL_DIR}/pricing.py" template --out pricing.json --free   # a free-to-user example
python3 "${CLAUDE_SKILL_DIR}/pricing.py" sensitivity pricing.json             # the four survey points only
python3 "${CLAUDE_SKILL_DIR}/pricing.py" check pricing.json                   # print the report, record nothing
python3 "${CLAUDE_SKILL_DIR}/pricing.py" record pricing.json                  # write the section and the ledger
```

Every command takes `--dir` (the proof folder, default `proof`) and `--force`.
Two filled-in examples sit beside the tool: `${CLAUDE_SKILL_DIR}/pricing.example.json`
and `${CLAUDE_SKILL_DIR}/pricing-free.example.json`.

## Step 1: pick the model

Ask who hands over money. If the user of the product pays, the model is `paid`. If
the user pays nothing and an advertiser, a partner or a sponsor pays, the model is
`free_to_user`. Do not force a free product into a price per user. That is the
mistake this skill exists to avoid.

## Step 2: gather the inputs

Take facts in this order and say which kind each one is:

1. The user's own facts first: the candidate price, what one price buys, the cost of
   serving one unit, payout reports from an advert or referral account.
2. Public sources second, each with a link: competitor price pages, published rate
   cards.
3. A labelled estimate last. For a revenue line, write `"source": "estimate"` and the
   report marks it as one.

Ask the user at most three questions in one message, and only for facts that only
they know (their cost per unit, their candidate price, which revenue lines pay out
today). Look up the rest.

`pricing.json` always needs `currency` (the prefix money is printed with, such as
`"Rs "` or `"$"`; there is no default) and `model`.

For `paid`:

- `price` (the candidate), `price_for` (what one price buys, such as "one cup") and
  `variable_cost` (the cost of one unit). All three are required.
- The four-answer survey, given one of three ways: `answers` (a list of
  `{too_cheap, cheap, expensive, too_expensive}`), `answers_file` (a CSV with those
  four columns) or `answers_dir` (a folder of JSON files that each hold those four
  keys). The buyer panel's folder, `proof/business/panel/answers`, works as it is: its
  `price_too_cheap`, `price_bargain`, `price_getting_expensive` and
  `price_too_expensive` answers are read as the same four.
- `competitor_prices`: `{name, price, includes, url}` for each.
- Optional: `candidates` (other prices to compare), `ladder`
  (`{name, price, why_step_up}`), `opening_offer` (`{what, ends}`) and `tests`
  (`{price, how}`).

For `free_to_user`:

- `revenue_lines`: `{name, kind, amount, status, source}` for each. `kind` is
  `per_1000_pageviews` (add `pages_per_visit`), `per_action` (add `rate`, the share of
  visits that take the action, from 0 to 1) or `fixed_monthly`. `status` is `live`,
  `unproven` or `paused`. `source` is a link, `owner` or `estimate`.
- `fixed_monthly_cost` and `variable_cost_per_1000_visits`. Both are required.
- Optional: `tests` (`{how}`) and `competitor_prices`.

A missing required field stops the tool with exit code 2. It never fills a gap with
zero.

## Step 3: read the result before recording

```bash
python3 "${CLAUDE_SKILL_DIR}/pricing.py" check pricing.json
```

For a paid product the report gives:

- The price-sensitivity range. Four points come out of the survey: the point of
  marginal cheapness (below it, more buyers call the price too cheap to trust than
  call it expensive), the optimal point (the fewest buyers reject it), the
  indifference point (as many call it cheap as call it expensive) and the point of
  marginal expensiveness (above it, more call it too expensive than call it a
  bargain). The range between the first and the last is what buyers called acceptable.
- How many answers were dropped. An answer is dropped when a value is missing or the
  four are not in rising order. If more than a quarter are dropped, or fewer than 10
  usable answers remain, no range is reported and the tool says why. That usually
  means buyers answered in mixed units, so ask again with the unit in every question.
- Where the candidate sits against the range and against the lowest, median and
  highest competitor price.
- What one unit earns at each candidate, in money and as a share of the price. A
  price at or below the cost of one unit is flagged.

For a free product the report gives revenue per 1,000 visits line by line and in
total, counted two ways: live lines only (the base case) and live plus unproven (the
upside). It shows what each line would add, what is left after the cost of serving
1,000 visits, and the break-even visits a month and a day for both cases. A paused
line always counts as zero. A fixed monthly line is set against the fixed cost and is
not spread over visits.

## Step 4: record

```bash
python3 "${CLAUDE_SKILL_DIR}/pricing.py" record pricing.json
```

## What it writes

- `proof/business/pricing.md`, starting with `# Pricing`: the decision table, the
  range or why there is none, the competitor comparison, the margin at each candidate
  or the per-1,000-visits table and break-even, the ladder or the revenue lines with
  their status, the opening offer, and what to test with real people.
- A copy of the inputs under `proof/evidence/proof-pricing/`.
- Coverage: `buyers-asked`, `competitors-compared`, `margin-checked`,
  `ladder-or-lines`, `real-test-planned`.
- Scores: `margin_per_unit` (above zero) and `usable_answers_pct` (75 or more, only
  when answers were given) for a paid product; `live_revenue_per_1000_visits` (above
  zero) for a free one.
- Verified findings the tool computes: the price does not cover the cost of one unit;
  no revenue line is live; the answers cannot be used; the price sits outside what
  buyers called acceptable; most revenue rests on an estimate; no real-buyer price
  test is planned. Recording again replaces the findings from the earlier run.

## Rules for the price itself

- Simulated answers only pick what to test. A panel of simulated buyers narrows the
  candidates. It does not set the price.
- Real survey answers beat simulated ones. When both exist, use the real ones and say
  so.
- Never price below cost without saying so and for how long. Put it in
  `opening_offer` with a real end date.
- No invented "was" prices. A crossed-out price must be one the product was really
  sold at.

## Honest limits

The range is only as good as the people who answered. Ten answers show a direction,
not a market. The four-question survey measures what people say, not what they pay,
and it knows nothing about how the price is presented. Competitor prices go stale, so
check the links on the day. Break-even assumes a 30-day month and steady earnings per
visit, and real traffic is lumpier than that. Tax, and the pricing and advertising
rules of the user's market, are outside this tool. This is not financial or legal
advice.

## Output / next

`proof/business/pricing.md`, plus coverage, scores and findings in the ledger under
`proof/`. `/proof-business-plan` includes the section as written, `/proof-finance`
takes the chosen price into the unit economics, and `/proof-report` and
`/proof-board` read the ledger. If the candidate changed, run the tool again so no
stale finding is left behind.
