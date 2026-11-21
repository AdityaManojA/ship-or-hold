# Plan: from a diagnostic dump to a self-serve audit

Goal: turn a Proof run from a 38-page diagnostic into a short report a founder pays for and acts on, then put a web app on top. This phase builds the logic (the tools, the ledger rules, the board, the report). The web app comes after the logic is tested.

Status key: **Phase 1** = built in this phase, in the skills and tools. **Phase 2** = the web app and billing, later.

## 1. Pipeline and data guardrails (fix first)

### 1.1 Gate the run on real numbers - Phase 1
- New skill `proof-intake`: a mandatory four-field pre-flight intake before any business skill runs.
  - Landed unit cost, monthly ad spend, daily sessions, daily orders (plus currency and the period they cover).
  - Each field records where it came from: `owner`, `shopify-export` or `analytics-export`. Nothing is guessed.
- Read-only Shopify sync, offline: `intake.py import-shopify` reads the Shopify Admin exports a store owner can download (orders CSV, sessions report CSV) and fills daily orders, daily sessions and average order value from them. No API token and no network call; the owner downloads and hands over the files.
- The gate: `proof-intake` is the first required skill in the `business` and `startup` profiles. `check.py next` will not move past it, and `unit_economics.py` reads the intake (`"from_intake": true`) instead of estimates, so the model can never again rest on a question like whether "20+" meant visits or orders.
- The intake is checked for consistency: orders cannot exceed sessions, conversion above 20% or below 0.1% is flagged for a second look, ad spend per order is computed and shown.
- Phase 2: a real Shopify OAuth app with read-only scopes (`read_orders`, `read_analytics`) in the web app.

### 1.2 Never export Incomplete - Phase 1
- `plan.py compile` refuses to write the client PDF while the verdict is Incomplete. It writes the Markdown, lists exactly what is unfinished, and says how to finish or descope each item.
- New `check.py descope <skill> <check> --reason "..."`: marks a check that does not apply to this client as out of scope, with the reason. Descoped checks go to the appendix under "Not covered in this audit"; they are never shown as passed.
- The offer skill marks `promises-exist` as covered once it has looked (an unbuilt promise is a finding, not a gap), so a proposed offer no longer holds the verdict at Incomplete.
- Profiles descope the manual accessibility items (screen reader, 200% zoom, form errors, rule engine) automatically when the tools for them are absent, with that reason, instead of leaving them not run.

### 1.3 Sanitise or hide the simulated buyer panel - Phase 1
- Strict schema validation in `buyers.py`:
  - Each segment carries its own age range; a buyer's age is always drawn inside their segment's range. A segment whose name states an age range that disagrees with its `age` field is rejected.
  - The customer profile states `gender` (`female`, `male` or `any`); names are drawn from the matching pool, so a "Working woman or young mother" is never called Ben or aged 19.
  - Income stays in the profile's currency and period.
- The client report no longer prints 20 synthetic quotes. The panel section shows hypotheses: objection themes with counts, what would change a no, and the before-and-after of a re-test. The quotes stay in the evidence for the owner, under `proof/business/panel/quotes.md`, never in the PDF.

### 1.4 Fail silently on blocked scrapers - Phase 1
- `market.py` rejects client-facing text that admits a scraping failure ("refused the reader", "timed out", "blocked", "login wall", "could not be reached"): those notes belong in `research_notes`, which goes to the appendix only.
- Rows with no price are left out of the client table and the price range, and counted in the appendix.
- The market skill tells the researcher to retry a blocked page through the browser, then to swap in a backup competitor that loads, before recording a gap.

## 2. Narrative and boardroom mechanics

### 2.1 Nine seats to four archetypes - Phase 1
- The board becomes four seats that disagree by design:
  - **Investor** (finance): unit economics, pricing, cash. Veto: a sale that loses money, no live revenue, a price below cost.
  - **Growth** (marketing and sales): market, buyers, offer, ads, go to market, search.
  - **Builder** (tech and product): flows, speed, security, accessibility, the product experience. Veto: a verified high finding in a core flow or in security.
  - **Operator** (ops and legal): operations, dependencies, launch readiness, privacy, legal. Veto: a launch-blocking readiness item, a dependency the business cannot survive losing without permission, a verified high privacy finding.

### 2.2 Make them debate - Phase 1
- A second round after the first answers: `board.py debate-brief` gives each seat the other three positions; each answers with challenges to named claims of another seat (citing evidence), what it concedes, and a final vote.
- `board.py debate-check` validates that every challenge targets another seat's actual claim and cites evidence that exists.
- `board.py tally` uses the final votes and writes `proof/board/debate.md`: one page of cross-examination, the votes that changed and why.

### 2.3 Match the client's stage - Phase 1
- `target.json` records `stage`: `solo`, `team` or `company`.
- For `solo` the board is called the **Advisory Panel**, and actions are not assigned to executives. Every action goes into one of three buckets: **Do today**, **Hire a freelancer**, **Ask a CA or lawyer**.
- For `team` and `company`, actions keep an owner, using the four seat names.

### 2.4 Drop the named-rule gimmick - Phase 1
- No seat is a named person any more, and the briefs say so. `board.py check` rejects answers that attribute a rule to a person ("X's rule", "X said").
- The researched personas and quotes are removed from the board. The seats sound like sharp advisors, not system prompts.

## 3. Higher-value analysis and copy-paste deliverables

### 3.1 Audit live ad creatives - Phase 1
- New skill `proof-ads`: Claude reads the brand's active ads and two or three competitors' ads in the public Meta Ad Library with the browser and records them in `ads.json` (advertiser, copy, headline, call to action, landing URL, offer, start date, format), with the landing page saved.
- `ads.py` checks ad-to-landing-page match (the product, price, offer and promise in the ad appear on the page it sends people to), flags ads whose offer the page does not honour, and finds creative gaps: themes competitors use that the brand does not (sizing, fabric, cash on delivery, reviews, price anchors).
- Output: a one-page teardown, `proof/business/ads.md`, with findings in the ledger.
- Phase 2: the Ad Library API with an access token, run from the web app.

### 3.2 Upgrade the financial model - Phase 1
- `unit_economics.py` replaces the flat twelve-month table, when every month is the same, with three scenarios side by side:
  - **Status quo**: today's orders a day from the intake.
  - **Break-even**: the orders a day where the business stops losing money, and the ad spend per order that allows.
  - **Target scale**: the owner's target orders a day.
- Cash on delivery and Return-to-Origin are modelled for Indian D2C: the share of orders on COD, the RTO rate, the cost of each RTO (forward and reverse shipping, packing) and any COD fee. The cost per delivered order includes the expected RTO cost.
- The twelve-month table is shown only when a real month-by-month ramp is given.

### 3.3 Ship a copy-paste Fix Kit - Phase 1
- New skill `proof-fixkit`, run after the checks, writes files a founder can paste straight in:
  - the exact `<title>` and meta description for each audited page, within length limits;
  - replacement hex codes for every failing colour pair, as close to the brand colour as possible while reaching 4.5:1;
  - WhatsApp review-request scripts (day 3 and day 10 after delivery);
  - a ready-to-fill centimetre size chart for each product type;
  - a draft 7-day exchange policy, marked as a draft for a lawyer to check;
  - a **14-Day Fix Kit**: every verified finding and every kit item, ordered by severity and effort, placed on a day from 1 to 14 and bucketed for the client's stage.

## 4. Product UX, PDF polish and SaaS packaging

### 4.1 Make the web app the product - Phase 2
- Live sliders for unit cost, conversion rate and ad spend, recomputing the model on each change, so the founder watches the Investor's vote flip from Hold to Go.
- Phase 1 prepares it: the finance model and the Investor's veto are pure functions of the inputs, so the web app can call them directly; `unit_economics.py what-if` takes the slider values on the command line and prints the vote that results.

### 4.2 Trim the PDF to 6-8 pages - Phase 1
- `plan.py compile` writes two PDFs:
  - `proof/report.pdf`, the client report, in this order: cover, Executive Summary, Boardroom Debate, Annotated Screenshots of the product page, 14-Day Fix Kit. No ledger codes (`proof-a11y/...`, `gtm.json`) in these pages.
  - `proof/appendix.pdf`: the full plan, every director's answer, the evidence citations, descoped checks and research notes.
- The page count of `report.pdf` is checked and reported; over 8 pages is flagged.
- Annotated screenshots: screenshots of the product page saved under `proof/business/screens/`, with boxes and labels from `annotations.json` drawn over them.
- Tables wrap properly: fixed layout, set column widths, long codes and URLs broken anywhere.

### 4.3 Teaser verdict and credit pricing - Phase 1 logic, Phase 2 billing
- Phase 1: `plan.py teaser` writes the free teaser, `proof/teaser.md` and `proof/teaser.pdf`: the automated tech score (from the technical checks only, which need no owner input) and the one bluntest board veto or hold reason.
- Phase 1: `plan.py refire --previous <old proof folder>` writes the Re-Fire: which findings were fixed, which remain, which are new, and how the verdict and the votes moved. It goes into the next report.
- Phase 1: white-label PDFs for agencies: `proof/branding.json` (agency name, colour, logo) replaces Proof's name on the cover and footer.
- Phase 2: per-audit credits for founders (each credit includes one free Re-Fire within 30 days), a monthly Agency Plan with white-label PDFs, the free teaser as the sign-up step, payments.

## Order of work in Phase 1
1. Guardrails: intake gate, never export Incomplete, descope, panel validation, market sanitising.
2. Board: four archetypes, debate, stage-aware actions, no named rules.
3. Analysis: finance scenarios and RTO, ad teardown, Fix Kit.
4. Report: client PDF and appendix, annotated screenshots, teaser, Re-Fire, white-label.
5. Tests for each, the full suite green, an end-to-end run on a real store.

## Phase 1 status (2026-11-21)
Done, all on branch `audit-pipeline-v2`, 806 tests passing.
- Every Phase 1 item above is built: the intake gate, never exporting Incomplete, descope, panel validation, market sanitising, four seats with a debate, stage-aware actions, finance scenarios with COD and RTO, the ad teardown, the Fix Kit, the client report and appendix PDFs, annotated screenshots, the teaser, Re-Fire and white-label.
- End-to-end test on a real Shopify store (a women's nightwear brand): the intake gate stopped the run on missing unit cost and orders; the market guardrail caught five scraper-failure notes; four accessibility checks were descoped with reasons; the verdict reached Not yet (not Incomplete); the Advisory Panel held on an Investor veto; report.pdf is 7 pages.

What the real run fixed:
- The scraper-failure filter flagged ordinary business words ("refunds are refused"); it now only matches failures of the reader itself.
- Contrast fixes turned white button labels black; neutral text on a brand colour now moves the brand colour.
- Fix Kit tasks read like diagnoses; they are now plain jobs with pages named in words, identical jobs merged, and one job across several pages or things is one task (40 tasks became 28).
- The debate carried raw score names, unformatted numbers, file paths and cut-off sentences; all are now plain sentences.
- The kit table used a fixed layout, so each task wrapped into a tall row: the report went from 12 pages to 7.

Still to watch in Phase 1 testing:
- A trade mark task can appear twice (from the brand check and from operations); merge if it recurs.
- The intake and ad teardown sections stay "not done yet" until the owner gives unit cost and orders and the ad library is read.

## Phase 2: the web app (later)
- Live financial sliders and the Investor's vote flipping in real time (4.1).
- Shopify OAuth read-only sync (1.1) and the Meta Ad Library API (3.1).
- Free teaser as sign-up, per-audit credits with a free 30-day Re-Fire, the Agency Plan with white-label PDFs (4.3).
- Accounts, run history, payments.
