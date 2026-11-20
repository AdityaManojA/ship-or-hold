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

Every cost is an estimate; mark it as one and get real quotes. A missing required
field is an error, so the tool never quietly fills a gap with zero.

## Step 2: read the result

```bash
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" numbers.json
```

You get the contribution per unit, the margin at your planned volume, break-even per
day, year one month by month, the cash low point (how much you need before it pays
for itself), the payback month, and three what-ifs (price down 10%, sales down 25%,
variable costs up 20%).

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/unit_economics.py" numbers.json --record
```

Writes `proof/finance.md` and two scores to the ledger (each unit earns more than it
costs; year one makes an operating profit). If a unit loses money before fixed
costs, that is recorded as a finding.

## Honest limits

These are projections from your inputs, not a forecast. They are only as good as the
costs and the sales estimate you put in, and real life is lumpier than a monthly
average. Confirm costs with suppliers and the structure, payroll and tax with an
accountant. This is not financial advice.

## Output / next

`proof/finance.md`, scores under `proof/scores/proof-finance.json`, coverage, and a
finding if a unit loses money. `/proof-business-plan` includes `proof/finance.md` as
the plan's numbers section. Then `/proof-report`.
