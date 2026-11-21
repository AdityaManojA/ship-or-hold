---
name: proof-ads
description: >-
  Ad teardown from the public Meta Ad Library: checks each brand ad against its landing page (words, price, offer, button) and lists the themes competitors use that the brand never does. Use for "check my ads", "ad teardown", "what are competitors running" or "do my ads match my pages".
argument-hint: "[path to an ads.json, or nothing to start one]"
---

# proof-ads

An ad makes a promise and the landing page has to keep it. This skill reads the
brand's active ads and two or three competitors' ads in the public Meta Ad Library,
saves the page each brand ad links to, and checks that the page shows what the ad
said: the product, the price, the offer and the kind of button. It then lists the
themes competitors lean on (sizing, fabric, reviews, cash on delivery and so on) that
the brand's ads never mention. Use it for a store that spends on Meta ads. Proof only
reads and records. It never logs in, never buys ads and never edits the store.

What the user typed after `/proof-ads`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/ads.py" template --out ads-input       # an example ads.json and landing pages
python3 "${CLAUDE_SKILL_DIR}/ads.py" check ads-input/ads.json       # print the teardown, record nothing
python3 "${CLAUDE_SKILL_DIR}/ads.py" record ads-input/ads.json      # write the section and the ledger
```

`--dir` (the proof folder, default `proof`) and `--force` go before or after the
command. A filled-in example sits beside the tool: `${CLAUDE_SKILL_DIR}/ads.example.json`,
with its saved pages in the `landing` folder.

## Step 1: read the library as a person would

1. Open `https://www.facebook.com/ads/library/` in the browser. Set the country to
   India (or the country the brand sells in), the category to all ads, and search
   the brand's page name. Pick the page, not a keyword match.
2. Read every active ad. For each one, record word for word: the advertiser, the
   library link of the ad, the date it started running, the format (image, video or
   carousel), the headline, the main copy, the button text, the link it sends people
   to, and any offer (such as "free delivery" or "10% off") and price it states.
3. Do the same for two or three competitors the user names, or the ones in
   `proof/business/market.md` when that section exists.
4. Never log in, never use an access token and never click an ad. The public library
   is enough. If the library shows nothing for the brand, say so and record zero
   brand ads: the section then says no active ads were found, which is itself a
   finding for a store that says it spends on ads.

## Step 2: save each landing page

For every link a brand ad sends people to, open it in the browser and save the
page's HTML (the rendered page, so prices filled in by scripts are there). Put the
files next to `ads.json` and list each one under `landing_pages` as `{url, file}`,
where `file` is relative to `ads.json`. Add `price` when the page shows one the
saved HTML does not carry as text.

## Step 3: fill in ads.json

- `business`, `brand`, `currency` (the prefix money is printed with, such as `"Rs "`;
  there is no default) and `captured` (the day the library was read, YYYY-MM-DD).
- `ads`: `{advertiser, is_brand, library_url, started, format, headline, copy, cta,
  landing_url, offer, price}`. `offer` and `price` are optional. An empty list means
  the library showed no active ads.
- `landing_pages`: `{url, file, price}`; `price` is optional.

A missing field stops the tool with exit code 2.

## What the tool checks

- Match: the main words of the headline (stop words dropped) are looked for in the
  page's title, first heading and visible text. The score is the share found.
- Price: a price in the ad must equal a price on the page.
- Offer: an offer phrase in the ad must appear on the page.
- Button: a shop or buy button needs a page with a way to buy; a learn more button
  fits any page.
- Gaps: a fixed list of themes is matched on every ad's words. A theme at least two
  competitor ads use and no brand ad uses is a gap.

## What it writes

- `proof/business/ads.md`, starting with `# Ad teardown`: the brand ads against
  their pages, the competitor ads with their themes, the gaps and the findings.
- A copy of `ads.json` and the saved pages under `proof/evidence/proof-ads/`.
- Coverage: `brand-ads-read`, `competitor-ads-read`, `landing-match-checked`,
  `gaps-listed`.
- Score: `match_pct`, the average match across brand ads with a saved page (at
  least 60).
- Verified findings: high when an ad promises an offer its page does not show;
  medium when an ad and its page share under 40% of the headline's words; medium
  when the ad's price differs from the page; medium for each of up to five creative
  gaps; low for each brand ad with no saved page; low when no active brand ads were
  found. Recording again replaces the findings from the earlier run.

## Honest limits

The library shows which ads are running, not what they cost, who saw them or how
they perform. The match is word matching on one saved copy of the page: a page that
says the same thing in other words scores low, and a page that changed after it was
saved is not seen. Themes come from a fixed word list, so an ad that talks about fit
without the usual words is missed. A gap is a question to test, not proof that the
theme would sell.

## Output / next

`proof/business/ads.md` plus coverage, the score and findings under `proof/`.
`/proof-fixkit` puts the findings on the 14-day plan, `/proof-business-plan`
includes the section as written, and `/proof-report` and `/proof-board` read the
ledger.
