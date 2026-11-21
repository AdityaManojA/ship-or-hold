---
name: proof-intake
description: >-
  The four real numbers every business check starts from: landed unit cost, monthly ad spend, daily sessions and daily orders, each with its source, read from the owner or Shopify exports and never estimated. Use for "intake", "start the business audit", "import my Shopify orders" or "what numbers do you need".
argument-hint: "[nothing, or the paths to a Shopify orders CSV and sessions CSV]"
---

# proof-intake

A business audit is only as good as the numbers under it. This skill collects four of
them before any business skill runs, writes down where each one came from, and
checks that they agree with each other. Nothing here is ever estimated: a number the
owner has not given stays missing, and the run waits for it.

What the user typed after `/proof-intake`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/intake.py" set --currency "Rs " --unit-cost 300 --ad-spend 50000 --sessions 20 --orders 0.4 --period "last 30 days"
python3 "${CLAUDE_SKILL_DIR}/intake.py" import-shopify --orders-csv orders.csv --sessions-csv sessions.csv --days 30
python3 "${CLAUDE_SKILL_DIR}/intake.py" check      # exit 2 naming each missing field
python3 "${CLAUDE_SKILL_DIR}/intake.py" record     # the section and the ledger
python3 "${CLAUDE_SKILL_DIR}/intake.py" status
```

`--dir` (default `proof`) works before or after the command. Example exports to try
it on: `${CLAUDE_SKILL_DIR}/orders.example.csv` and `${CLAUDE_SKILL_DIR}/sessions.example.csv`.

## Step 1: ask the owner, in one message

Send these four questions together, in one message, word for word. The wording keeps
visits and orders apart, so "20+" can never be read two ways.

> To check the numbers I need four figures, all for the same recent period (say the
> last 30 days):
> 1. What does one unit cost you landed, the product plus packing and inbound freight,
>    before you sell it? (one amount, with the currency)
> 2. How much do you spend on ads in a month, all platforms together?
> 3. How many **visits** (sessions, people arriving on the site) do you get on an
>    average day? Not orders: visits.
> 4. How many **orders** (completed purchases, not visits, not items) do you get on an
>    average day? If it is less than one a day, say how many in the whole period.

Record each answer with `set`; the source is `owner` unless you say otherwise. Each
field may come from a different place, and `set` can be run again to change one
field without touching the rest. An order count for a whole period goes in divided by
the days in it (12 orders in 30 days is `--orders 0.4`).

## Step 2: or read the Shopify exports

The owner can download two files; no token and no network call are involved.

- Orders: Shopify Admin, **Orders**, **Export**, choose the date range and "CSV for
  Excel, Numbers or other spreadsheet programs".
- Sessions: Shopify Admin, **Analytics**, **Reports**, **Sessions over time**, set the
  same date range, **Export**.

```bash
python3 "${CLAUDE_SKILL_DIR}/intake.py" import-shopify --orders-csv orders_export.csv --sessions-csv sessions.csv --days 30
```

Orders are counted once per order number (the `Name` column; one order spans a row
per line item), leaving out orders whose `Financial Status` is voided or refunded.
The average order value is the mean `Subtotal` (line items after discounts, before
shipping and tax), falling back to `Total`. Sessions are the sum of the `Sessions`
column. `--days` is the length of the period; without it the period runs from the
first to the last order date. These fields are marked `shopify-export`. The unit cost
and the ad spend never come from these files; ask the owner for them.

## Step 3: check and record

`check` exits 2 and names each missing field (and the currency) when any of the four
is missing. When all four are there it prints the conversion (orders divided by
sessions) and the ad spend per order (monthly ad spend divided by 30 days of orders),
and flags:

- more orders than sessions (high: impossible);
- conversion above 20% or below 0.1% (medium: check whether sessions and orders were
  swapped or cover different periods);
- ad spend per order above the average order value, when the AOV is given (medium).

`record` writes `proof/business/intake.md` and the ledger. `/proof-finance` then reads
the intake with `"from_intake": true` instead of estimates.

## Honest limits

The tool checks that the numbers are complete and consistent, not that they are true:
a wrong figure from the owner is still wrong. An export covers the date range the
owner chose; make sure the ad spend covers the same period. Daily figures are
averages, so a launch spike or a sale week moves them.

## Output / next

- `proof/business/intake.json`: each value with its source, the currency, the period
  and the computed conversion and ad spend per order.
- `proof/business/intake.md`, starting with `# The numbers we started from`.
- Coverage `unit-cost`, `ad-spend`, `sessions`, `orders` (covered only when present),
  scores `conversion_pct` and `ad_spend_per_order` (for information, no bar), and
  verified findings for the flags above.

Next: `/proof-finance` with `"from_intake": true`, then the other business skills.
