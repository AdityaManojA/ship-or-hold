---
name: proof-business
description: >-
  The business side only, no running app needed: market, simulated buyers, price, offer, numbers, go to market and operations, then the board votes and the business plan is compiled. Use for "business check", "is this a business", "will anyone pay" or "write me a business plan".
argument-hint: "[--add proof-x] [--skip proof-x]"
---

# proof-business

For the question a first-time founder actually asks: not "does the code work" but "is this a business". It needs an idea and the facts only the owner knows, not a deployed app. Each skill writes one section of the business plan and records its evidence, the board votes on it, and `/proof-business-plan` compiles the result. To check the product itself as well, use `/proof-startup`, which runs these and the core technical checks together.

What the user typed after `/proof-business`: `$ARGUMENTS`

## What it runs

| skill | answers |
| --- | --- |
| `/proof-market` | who already solves this, what they charge, what their customers complain about, and whether a gap exists |
| `/proof-buyers` | what a seeded panel of simulated buyers says to the pitch, and why they pass |
| `/proof-pricing` | the price, or what one visit earns when the product is free to its user |
| `/proof-offer` | the offer built from the objections buyers raised, and what it cannot fix |
| `/proof-finance` | unit economics: margin, break-even, cash needed |
| `/proof-go-to-market` | positioning, channels, and a first real-world test with a pass mark |
| `/proof-ops` | day-one routines, monthly costs, and what the business depends on that it does not control |

The order matters: buyers hear the pitch after the market is mapped, pricing reads what
buyers said, the offer answers their objections, and the numbers use the price.

Common additions: `/proof-release` (the launch-day checklist), `/proof-seo` and
`/proof-geo` for a public site, `/proof-llm-cost` when the product calls a model.

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first. For a
   business-only run the target is the business: its name, and a URL or repo if there
   is one. No live system is probed by these skills, but public pages are read, so the
   user still confirms the target is theirs.
2. **The idea file.** Write `proof/business/idea.md` before anything else:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-business-plan/plan.py" idea-template
   ```

   Fill it from what the user has said. Ask, in one message and at most three
   questions, for what only they know: how it makes money, who the customer is, and
   what it costs to run. Write "not known" rather than guess.
3. **Plan.** Show the user the list before running anything:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile business --project .
   ```

   `--add proof-x` adds a skill the profile leaves out; `--skip proof-x` drops one.
4. **Run.** `check.py next` names the next skill; follow that skill's own steps, then
   ask again. Two of them use sub-agents and say so before they start: the market
   research reads many public pages, and the buyer panel is one sub-agent per buyer
   (20 by default). Everything else runs in this session. Proof only reads and
   records: no skill edits the project.
5. **Run it again later.** Keep `proof/` and repeat the same command; `check.py next`
   skips every skill whose inputs have not changed.
6. **Finish: the report.** Always, whatever the verdict and without asking, in this
   order: `/proof-report` for the computed verdict; `/proof-board`, which ends with
   `board.py tally --open` and the meeting page; then `/proof-business-plan`: write
   `proof/summary.md` (half a page, with one line per director) and run `plan.py compile`,
   which writes `proof/business-plan.md`, `proof/one-pager.md` and
   `proof/business-plan.pdf` with every director's answer in it. Show the user the
   verdict, the board's decision and the summary in full, and send the PDF. Then
   `/proof-audit-trail`. A business section this check did not run is listed in the plan
   as not done, never filled in.

## Example

For a free seat-finder site that earns from ads and referral fees, the plan holds 7
skills and the run ends with three files the owner reads first:

```
proof/summary.md         half a page: the verdict, the three numbers, the one thing to do this week
proof/business-plan.md   every section, the board's decision, what is not done yet
proof/one-pager.md       for someone deciding in two minutes
```

## Not the right fit?

Launching a built product? `/proof-startup` adds the core flows and the launch-sinking
risks. Only the numbers? `/proof-finance`. Only the code? `/proof-webapp`,
`/proof-mobile`, `/proof-backend` or `/proof-security`.

## Honest limits

Nothing here is market validation. The competitors are what public pages show on the
day, the buyers are simulated and lean agreeable, and the numbers are only as good as
the inputs, with estimates labelled as estimates. The one thing that settles it is the
first real-world test in the go-to-market section, with real people and a pass mark
written before it runs. Not financial or legal advice.

## Output / next

`proof/business/` (the idea file and one section per skill), `proof/finance.md`,
`proof/check.json`, `required_skills` in `proof/target.json`, each skill's evidence in
the ledger, then `proof/board/`, `proof/summary.md`, `proof/business-plan.md` and
`proof/one-pager.md`. Next: work through the board's actions and compile again.
