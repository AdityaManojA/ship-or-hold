---
name: proof-board
description: >-
  Four seats (Investor, Growth, Builder, Operator) read the evidence, debate each other and vote Go, Go with conditions or Hold; a solo founder gets an Advisory Panel with do-today, freelancer and CA-or-lawyer actions. Use for "present this to the board", "board meeting", "should we launch" or "get sign-off".
argument-hint: "[--seats investor,builder]"
---

# proof-board

The testing and business skills say what is true about the product. The board says
what to do about it. The team presents, four seats question it from their own area,
argue with each other in a second round, and vote. The decision rests on the same
ledger as everything else: a seat cannot wave away a verified critical bug, and
cannot invent one.

**The user does not do anything.** Run every step below yourself, then show the
meeting. It plays out in the chat and the page plays itself.

What the user typed after `/proof-board`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" brief          # the presentation and the briefs
python3 "${CLAUDE_SKILL_DIR}/board.py" check          # validate the first-round answers
python3 "${CLAUDE_SKILL_DIR}/board.py" debate-brief   # the second round: every seat reads the others
python3 "${CLAUDE_SKILL_DIR}/board.py" debate-check   # validate the debate files
python3 "${CLAUDE_SKILL_DIR}/board.py" tally --open   # the decision, the meeting, the one-page debate
python3 "${CLAUDE_SKILL_DIR}/board.py" play           # the same meeting, paced, in a terminal
```

`--dir` works before or after the command. The seats, what each reads and which
hold a veto are in `${CLAUDE_SKILL_DIR}/seats.md`.

## The board

| seat | weighs | its area | veto |
| --- | --- | --- | --- |
| Investor | the money: does a sale earn money, which revenue is live, how much rests on estimates | finance, pricing, intake, AI cost, release | a failing finance score (a sale that loses money), a verified high finding in finance or pricing (no live revenue, a price below cost) |
| Growth | the market and the customer: the promise, the offer, the ads, reach | market, buyers, offer, ads, go to market, SEO, GEO, explore, slop, accessibility, languages, email, Fix Kit | none |
| Builder | the product and the code: core flows, speed, security, the experience | every engineering, security and product-experience skill, plus the Fix Kit | a verified high finding in a core flow, the API, load, security, infrastructure or AI |
| Operator | running it and the exposure: support, dependencies, readiness, privacy, licences | operations, release, privacy, licences, store listings, email, AI | a launch-blocking readiness item not done, a verified high finding in operations, release or privacy |

The seats are archetypes, not people. No seat is a named person, none quotes
anyone, and `check` rejects an answer whose summary or risks attribute a rule or a
saying to a person ("X's rule", "X said", "as X put it", "according to X"). The
seats should sound like four sharp advisors who weigh different things, and they
disagree by design.

Every seat sees the product dossier, its own area of the evidence in full, and every
other area in one line each (only what could block a launch), so no seat reads the
whole ledger and the four readings start from different places.

## Stage: Board or Advisory Panel

`proof/target.json` records the client's `stage` (`/proof-plan` sets it with
`target.py init --stage` or `target.py stage`). A missing stage is read as `team`.

- `team` and `company`: the **Board**. Every action has an owner, one of the four
  seats: investor, growth, builder or operator.
- `solo`: the **Advisory Panel**, everywhere (briefs, minutes, transcript, meeting
  page, `decision.json`). A solo founder has no executives, so actions are not
  assigned to owners. Every condition carries a `bucket`, and `check` rejects one
  without it:
  - `do_today`: **Do today.** The owner can do it alone, today.
  - `freelancer`: **Hire a freelancer.** A paid hand for a few days.
  - `professional`: **Ask a CA or lawyer.** Anything a qualified accountant or
    lawyer must check or sign.
  Blockers from the evidence get a bucket by their skill: privacy and licences go to
  a CA or lawyer, engineering and accessibility fixes to a freelancer, the rest to
  do today.

## Step 1: evidence first

The board judges the ledger, so the business and testing skills (or a profile such as
`/proof-startup` or `/proof-business`) and `/proof-report` come first. With no evidence
the board can only hold. When `proof/business/idea.md` exists and there is no product
dossier yet, the board reads the idea file in its place.

Then write the product dossier, `proof/product.md`, in at most 300 words, from what
the repo itself says: the README, the landing page copy, pricing pages, launch or
pitch documents. Cover what the product is, who it is for, the problem it solves,
what it promises, how it is sold and priced, and the alternatives people use today
if the documents name them. Facts only, each followed by the file it came from in
brackets; write "not documented" rather than guessing. Every seat can cite it as
`[product]`. Without it, `brief` warns and the seats judge from the evidence alone.

## Step 2: the presentation

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" brief
```

Writes `proof/board/presentation.md` (the product, the computed verdict, every
verified finding and score with a reference in square brackets, and the ask) and one
brief per seat: its job, its area in full, the rest in one line, and the answer
format. `--seats investor,builder` convenes a smaller board.

On a later run, a seat whose brief is unchanged keeps its answer; `brief` lists only
the seats whose evidence moved, and says so when nobody needs to meet again. A new
first round clears the old debate. `--fresh` clears every answer and convenes
everyone.

## Step 3: the first round

Run what `brief` printed. By default that is ONE Agent call, which sits as every
seat that needs to meet and writes each answer from `briefs/panel.md`:

- `subagent_type`: `general-purpose`
- `description`: `board`
- `prompt`: `Read <brief path> and follow it exactly. It is your whole brief.`

`brief --separate` prints one brief per seat instead, to send as one parallel Agent
call each: more independent readings at about four times the fixed cost.

Then `board.py check`. It rejects a high-severity risk that cites nothing, a
reference not in the evidence, a rule or saying attributed to a person, a non-empty
`quote`, an owner that is not a seat, and for a solo founder a condition without a
bucket. Send the seat its problems and have it fix its own file. Never write a
seat's answer.

## Step 4: the debate

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" debate-brief
```

Writes `proof/board/briefs/debate.md`: every seat's vote, summary and numbered
risks with their evidence. Run it as ONE Agent call, the same way as step 3. Each
seat writes `proof/board/debate/<seat>.json` with at least one challenge to another
seat (`to`, `about`: that seat's risk number or `"vote"`, the challenge, and the
evidence it cites), what it concedes, and a `final_vote` with a `final_reason`.

Then `board.py debate-check`. It rejects a challenge to the seat itself or to a seat
that did not answer, an `about` that is not a real risk number or `"vote"`, evidence
that does not exist or is missing, a seat with no challenge, an invalid final vote,
and a rule or saying attributed to a person. Send the problems back as in step 3.

## Step 5: answer the questions

Answer each question from the evidence, or say plainly that the evidence does not
cover it:

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" answer investor 1 "Payback is month 8 at plan [proof-finance/score:year1_operating_profit]"
```

## Step 6: the meeting runs itself

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" tally --open
```

`tally` computes the decision and prints the whole meeting. **Show it in the chat
as it comes, step by step, without waiting for the user**: the opening (the verdict
going in, the four seats, the agenda); each agenda item (what the team showed, each
seat that spoke, the computed outcome: Blocker, Concerns, Clear or Not covered);
other points and questions; the debate (who challenged whom, what was conceded, the
votes that moved and why); the vote; the decision; the actions.

The decision is computed from the final votes. A seat with a valid debate file votes
its `final_vote`; a seat without one keeps its first-round vote.

- **Hold** when the evidence is incomplete, when the ledger triggers a veto, when
  fewer than half the seats answered, or when a majority votes to hold. A veto comes
  from the ledger, never from a vote, so no debate can talk it away.
- **Go with conditions** when nothing blocks but the Proof verdict is Not yet, or a
  seat voted for or raised conditions.
- **Go** when the verdict is Ship and every seat that answered voted go.

`decision.json` keeps the decision, reasons, votes, vetoes and conditions, and adds
`stage`, `panel_name` ("Board" or "Advisory Panel"), `debate` (every challenge:
from, to, about, challenge, evidence), `vote_changes` (seat, from, to, why),
`first_votes`, and for a solo founder a `bucket` on every condition.

`proof/board/debate.md` is the debate on about one printed page, for the founder: a
single `# Boardroom debate` heading, at most two challenges per seat, what was
conceded, the votes that moved and why, and the decision.

`proof/board/meeting.html` plays the same meeting on its own, with the debate as its
own step before the vote; `--open` opens it in the default browser (skipped in CI or
with no screen). Scrolling or opening a dropdown pauses it until the reader presses
Continue. `play` paces the same meeting in a terminal.

## Honest limits

The seats are four readings by one model, so they share its blind spots, and the
debate is the same model arguing with itself from four starting points: it surfaces
disagreements the evidence supports, it does not add new evidence. The votes are
advice. What makes the decision trustworthy is that it cannot contradict the
evidence: vetoes come from the ledger, not from a seat's view or a debate. The
solo buckets are a first sort, not professional advice; a CA or lawyer decides what
is theirs. A board Go is not market validation, legal sign-off or a forecast.

## Output / next

`proof/board/presentation.md`, `briefs/` (including `panel.md` and `debate.md`),
`answers/`, `debate/`, `qa.json`, `minutes.md`, `transcript.txt`, `debate.md`,
`decision.json` and `meeting.html`. Then `/proof-business-plan`: it puts the
decision, the debate, the vote and the actions into the plan and the client report.
Work through the actions, re-run the skills they touch, and convene the board again.
