---
name: proof-market
description: >-
  Maps who already solves the problem, what they charge, what their customers complain about and whether a gap exists, from public pages with every fact linked. Use for "who are my competitors", "is this market crowded", "what do they charge" or "find the gap".
argument-hint: "[the business in one line, or paths to competitors.csv and market.json]"
---

# proof-market

Before a plan says "nobody does this", someone has to look. This skill reads the
public pages a buyer would read, lists who already solves the problem and what a
buyer uses instead, notes what each charges and what their customers complain about,
and says whether a gap is left. It writes the "Market and competition" section of the
business plan and records the evidence in the Proof ledger. Use it early, before
pricing and before the buyer panel. Proof only reads and records; it never edits the
project.

What the user typed after `/proof-market`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/market.py" template --out research/          # example inputs to start from
python3 "${CLAUDE_SKILL_DIR}/market.py" check competitors.csv market.json   # print the section, record nothing
python3 "${CLAUDE_SKILL_DIR}/market.py" record competitors.csv market.json  # write the section and the ledger
```

Options: `--dir proof` (the proof folder) and `--force` (overwrite a file Proof did
not write). The tool counts and writes. It never opens a web page. The example
inputs are `${CLAUDE_SKILL_DIR}/competitors.example.csv` and
`${CLAUDE_SKILL_DIR}/market.example.json`; every row in them is made up.

## The rules of the research

These matter more than the tool.

- Public sources only: the competitor's own site and price page, public review
  pages, public forum threads, public listings. Nothing behind a login or a paywall.
- Read pages as a person would. Open one page, read what it shows, move on. No bulk
  fetching, no getting round a block, no guessing what a page says from its address.
- Never invent a competitor, a price, a rating, a review count or a quote. If a page
  does not show it, write `not found`. A blank that is honest beats a number that is
  made up.
- Every row carries the link the facts came from and the date you read it.
- Quotes are word for word, short (the tool refuses anything over 300 characters),
  and linked to the page they sit on. Never tidy a quote, never merge two.
- When a page is blocked (it will not load, wants a login, shows a captcha, answers
  403 or 429), first retry it in the browser as a person would. If it still will not
  load, swap in a backup competitor whose pages do load. Only then record the gap.
- What could not be reached goes in `research_notes` in `market.json` and nowhere
  else: a page that would not load, a review site that wanted a login, a price
  hidden behind "contact sales". Research notes go to the appendix
  (`proof/business/research-notes.md`), never to the client section. Never write
  the failure into a client field (a row in `competitors.csv`, a complaint, the gap
  or the threat): the tool refuses text such as "refused", "timed out", "blocked",
  "login wall", "could not be reached", "could not be read", "page did not load",
  "captcha", "403" or "429" there, naming the field and the row.

## Step 1: get the facts only the user has

Ask at most three questions in one message, and only for what is not already in the
conversation or the project:

1. What does the business sell, to whom, and where?
2. Which competitors do they already know of?
3. What currency should prices be shown in (a prefix such as `Rs ` or `$`)?

The user's own facts come first. Public pages with links come second. An estimate
comes last and is always labelled as one, in `research_notes`.

## Step 2: find who is out there

Search the way a buyer would, then open the pages. Sort each into one type:

- `direct`: sells the same thing to the same buyer.
- `indirect`: solves the same problem a different way.
- `substitute`: what the buyer does today instead, including doing nothing or doing
  it by hand. A substitute is often free.

Write one row per competitor in `competitors.csv`:

`name,type,url,where,price,what_it_sells,their_line,rating,review_count,source_date`

- `price`: the number only, in the plan's currency, for the same unit on every row
  (say the unit once in `price_for` in `market.json`). Write `free` when it costs
  the buyer nothing and `not found` when the page shows no price. A row with no
  price is left out of the client table and the price range; the section says only
  how many more were looked at, and the appendix lists them.
- `their_line`: their own headline, word for word.
- `rating`, `review_count`: as the page shows them, or `not found`.

## Step 3: read what their customers say

Read public reviews and threads. Group the complaints into themes in `market.json`:

```json
{"theme": "Lunch arrives late", "product_fixes": true, "core": true,
 "sources": [{"quote": "...", "url": "https://...", "date": "2026-11-01"}]}
```

`core` is true for the problem this business exists to solve. `product_fixes` is
true only when the product as planned removes the complaint. Be strict on both. A
theme with fewer than three sources is marked thin.

Then fill the rest of `market.json`: `business`, `currency` (required, no default),
`gap` (one or two plain sentences, or `"none"` when you found no gap), `threat` (the
competitor move that would hurt most), `research_notes` (a list, empty when the
research hit no walls; the old name `could_not_verify` is still accepted), and
optional `revenue_facts` as
`{claim, url, status}` where status says how far to trust it.

## Step 4: check, then record

```bash
python3 "${CLAUDE_SKILL_DIR}/market.py" check competitors.csv market.json
python3 "${CLAUDE_SKILL_DIR}/market.py" record competitors.csv market.json
```

`check` prints the section and the problems it would record. A missing column, a
missing currency, a price that is not a plain number, a quote with no link, or a
client field that admits a scraping failure stops the tool with exit code 2. Fix the
input; do not work round it.

The tool computes: how many of each type; the lowest, median and highest price and
how many rows had none; the share of rows with both a link and a date; how many
sources stand behind each complaint theme; whether every alternative is free.

## What it writes

- `proof/business/market.md`, the client section: the table of rows with a
  comparable price, sorted direct, indirect, substitute; how many more were looked
  at but publish no comparable price; the price range; complaints ranked by evidence
  with links and thin marks; the gap; the threat; revenue facts if given. It never
  mentions a page the research could not read.
- `proof/business/research-notes.md` (`# Research notes`, appendix only): the
  research notes and the rows left out for having no comparable price. Written only
  when there is something to say.
- `proof/evidence/proof-market/`: a copy of both input files.
- Coverage: `competitors-listed`, `substitutes-listed`, `prices-compared`,
  `complaints-themed`, `gap-stated`, `threat-stated`, `research-notes`. A part the
  inputs do not contain stays `not_run`, except `research-notes`, which is marked
  not applicable, with a note, when the research hit no walls.
- Score: `sources_linked_pct`, which must be exactly 100.
- Findings, all computed from the inputs: the core problem is thinly evidenced; the
  loudest complaints are ones this product does not fix; every alternative is free
  to the buyer; a row with no link or date; no gap was found. Recording again
  replaces the earlier findings.

Read the result to the user in this order: who is there, what they charge, what
people complain about most, the gap or the lack of one, and (to the owner, not the
client) what you could not check.

## Honest limits

This is desk research on one day. Pages change, prices differ by city and plan, and
reviews skew towards the angry and the delighted. The tool checks that a link and a
date are present; it cannot check that the page says what the row says, so the user
should open the links that matter. Three public complaints show a problem exists,
not how many people have it. No gap on the page does not prove there is none, and a
gap on the page does not prove anyone will pay to have it filled.

## Output / next

`proof/business/market.md`, coverage and one score for `proof-market`, findings under
`proof/findings/proof-market/`. The business plan includes the section file as
written. Next: `/proof-buyers` to put the pitch in front of a simulated panel,
`/proof-finance` for the numbers, then `/proof-report` and `/proof-board`.
