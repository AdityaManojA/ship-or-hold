---
name: proof-finance
description: >-
  Unit economics from the user's numbers: margin, break-even, cash needed and what-ifs, never guessed. Use for "unit economics", "is this profitable", "break-even" or "do the numbers".
argument-hint: "[path to a numbers.json, or --template to start one]"
---

# proof-finance

The product can work and still lose money. This skill does the arithmetic that says
whether each sale earns anything, when it breaks even, and how much cash it takes to
get there, with every figure traceable to a number you gave.

What the user typed after `/proof-finance`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" --template numbers.json   # start from an example
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" numbers.json              # print the report
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" numbers.json --record     # also write scores to the ledger
```

## Step 1: the inputs

Write a `numbers.json` (start from `--template`):

- `currency`: the prefix money is printed with, such as `"$"` or `"Rs "`. Set it; the
  report prints every amount with it.
- `price` per unit, and `unit` (what one sale is). For a product that is free to its
  user, the unit is `1,000 visits` and the price is what 1,000 visits earn, from
  `/proof-pricing`.
- `variable`: the costs that come with each sale, as `{label, cost}` entries.
- `fixed_monthly`: the costs that do not change with volume.
- `startup`: the one-off costs to open.
- `plan_per_day`: how many units a day you expect to sell.
- optional `capacity_per_day`, `days_per_month`, and one way to say the start is slow:
  `ramp_months` (a straight line up to plan) or `ramp_per_day` (12 numbers, the volume
  you expect in each month of year one). Month one is almost never the plan; if no
  real figure exists, say the ramp is an estimate wherever its results are quoted.

- optional `target_per_day`: the owner's target orders a day, for the Target scale
  scenario. Without it that row says "set target_per_day"; it is never guessed.
- optional `cod` for cash on delivery and Return-to-Origin (Indian D2C):
  `{"share": 0.6, "rto_rate": 0.25, "rto_cost": 140, "cod_fee": 30}`. `share` and
  `rto_rate` are fractions from 0 to 1 or percents up to 100; `rto_rate` is below 1.
  `rto_cost` is the forward and reverse shipping and packing of one returned order;
  `cod_fee` is optional. The cost per delivered order becomes
  `variable + share x cod_fee + share x rto_cost x rto_rate / (1 - rto_rate)`, and
  the report shows that arithmetic step by step.

Every cost is an estimate; mark it as one and get real quotes. A missing required
field is an error, so the tool never quietly fills a gap with zero.

### Numbers from the intake

With `"from_intake": true` in `numbers.json` (or `--intake path/to/intake.json`) the
tool reads `proof/business/intake.json` under `--dir` (written by `/proof-intake`):
the landed unit cost becomes one variable item, "Landed unit cost (intake)"; the
monthly ad spend becomes one fixed item, "Ads (intake)"; and today's orders a day
come from `daily_orders`. A missing or incomplete intake is an error (exit 2). To stop
double counting, `numbers.json` must then leave out `plan_per_day` and any fixed item
labelled as ads; the tool refuses otherwise. `price` still comes from `numbers.json`.

## Step 2: read the result

```bash
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" numbers.json
```

You get the contribution per unit, the margin at your planned volume, break-even per
day, the cash low point (how much you need before it pays for itself), the payback
month, and three what-ifs (price down 10%, sales down 25%, variable costs up 20%).

"## Three scenarios" puts side by side: **Status quo** (`plan_per_day`, or the
intake's daily orders), **Break-even** (the orders a day where operating profit is
zero, and the ad spend per order at that volume when ads are a fixed item) and
**Target scale** (`target_per_day`). Columns: orders a day, revenue a month,
operating profit a month, profit margin, ad spend per order when known. The
twelve-month table appears only when `ramp_months` or `ramp_per_day` is given;
otherwise one line says every month is the same.

### Sliders: what-if

```bash
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" numbers.json --what-if "unit_cost=320,conversion=3,ad_spend=40000"
```

Prints the scenario table and the Investor vote that results ("Investor: Hold, year
one loses ..." or "Investor: Go, ..."), Go only when each order earns something and
year one makes a profit. It writes nothing. Keys: `unit_cost` (the intake's unit
cost, or the whole variable cost without the intake), `conversion` (percent of the
intake's daily sessions that order; only with the intake), `ad_spend` (a month),
`price`, `orders` (a day) and `target` (a day).

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" numbers.json --record
```

Writes `proof/finance.md` and scores to the ledger: each unit earns more than it
costs (`contribution_per_unit`), year one makes an operating profit
(`year1_operating_profit`), and `breakeven_orders_per_day` for information (left out
when the business never breaks even). If a unit loses money before fixed costs, that
is recorded as a finding.

## Honest limits

These are projections from your inputs, not a forecast. They are only as good as the
costs and the sales estimate you put in, and real life is lumpier than a monthly
average. Confirm costs with suppliers and the structure, payroll and tax with an
accountant. This is not financial advice.

## Output / next

`proof/finance.md`, scores under `proof/scores/proof-finance.json`, coverage, and a
finding if a unit loses money. `/proof-business-plan` includes `proof/finance.md` as
the plan's numbers section. Then `/proof-report`.
