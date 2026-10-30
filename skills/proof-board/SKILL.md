---
name: proof-board
description: >-
  Presents the project to a board of directors and gets a launch decision, with
  nothing for the user to do. Nine directors (CEO, CTO, CISO, CFO, CMO, Head of
  Sales, COO, CPO, General Counsel) are sub-agents; each applies the researched,
  sourced thinking of one person (Steve Jobs, Werner Vogels, Steve Katz, Ruth
  Porat, David Ogilvy, John H. Patterson, Tim Cook, Tony Fadell, Brad Smith),
  quotes them word for word with sources, and backs every serious risk with a
  finding or score from the ledger. The meeting runs by itself, item by item
  through the audits, in the chat and as a page that plays on its own. Use when
  the user says "present this to the board", "board of directors", "what would
  the execs say", "board meeting", "should we launch", or "get sign-off".
argument-hint: "[--seats ceo,cto,cfo]"
---

# proof-board

The testing skills say what is true about the product. The board says what to do
about it. The team presents, nine directors question it through their own role and
vote, and the decision rests on the same ledger as everything else: a director
cannot wave away a verified critical bug, and cannot invent one.

**The user does not do anything.** Run every step below yourself, then show the
meeting. It plays out in the chat and the page plays itself.

What the user typed after `/proof-board`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" brief      # the presentation and one brief per director
python3 "${CLAUDE_SKILL_DIR}/board.py" check      # validate the directors' answers
python3 "${CLAUDE_SKILL_DIR}/board.py" tally --open   # the decision, printed as the full meeting; opens the page
python3 "${CLAUDE_SKILL_DIR}/board.py" play       # the same meeting, paced, in a terminal
```

The seats, what each reads and which hold a veto are in `seats.md`. The researched
lenses (facts, principles, behaviour and verbatim quotes with sources) are in
`personas.json`.

## The board

| seat | lens | judges | veto |
| --- | --- | --- | --- |
| CEO | Steve Jobs | the overall call: focus, quality, saying no | none (chairs) |
| CTO | Werner Vogels | does it work and keep working, and does it read as made by people (reads /proof-slop) | core flows, API, load, mobile flows |
| CISO | Steve Katz | security and customer data | security, secrets, dependencies, infra, AI |
| CFO | Ruth Porat | unit economics, cost of fixing versus delaying | unit economics |
| CMO | David Ogilvy | how the market meets it: claims, research, reach | none |
| Sales | John H. Patterson | can it be demonstrated and sold | none |
| COO | Tim Cook | running it: readiness, operations, privacy posture | launch-blocking readiness items |
| CPO | Tony Fadell | the product experience | none |
| Legal | Brad Smith | exposure: privacy, trust, regulation | privacy |

Each director applies that person's documented principles and quotes only the
verbatim quotes in `personas.json`, every one with its source and date. A director
never writes as the person, never alters or extends a quote, and `check` rejects
any quote id that is not in that seat's researched list.

Quotes are rare on purpose. A director quotes only when the person's exact words
are the reason for a high-severity call that cites evidence, and at most once in
the whole answer. Never for colour, to open or close, or because a quote is loosely
related. Most answers quote nothing.

## Step 1: evidence first

The board judges the ledger, so the testing skills (or `/proof-full-check`) and
`/proof-report` come first. With no evidence the board can only hold.

## Step 2: the presentation

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" brief
```

Writes `proof/board/presentation.md` (the product, the computed verdict, every
verified finding and score with a reference in square brackets, and the ask) and one
brief per director: their role, their lens with its quotes, and the answer format.
`--seats ceo,cto,cfo,sales` convenes a smaller board. A new brief clears the previous
meeting's answers, so the board always judges the current evidence.

## Step 3: convene the board

Send ONE message with one Agent tool call per director brief, all in parallel:

- `subagent_type`: `general-purpose`
- `description`: `board <seat>`
- `prompt`: `Read <brief path> and follow it exactly. It is your whole brief.`

Then `board.py check`. It rejects a high-severity risk that cites nothing, a
reference not in the evidence, a quote id outside the seat's research, a quote on a
medium or low point or without evidence, and more than one quote. Send the
director its problems and have it fix its own file. Never write a director's answer.

## Step 4: answer the board's questions

Answer each question from the evidence, or say plainly that the evidence does not
cover it:

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" answer cfo 1 "Payback is month 8 at plan [proof-finance/score:year1_operating_profit]"
```

## Step 5: the meeting runs itself

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" tally --open
```

`tally` computes the decision and prints the whole meeting. **Show it in the chat
as it comes, step by step, without waiting for the user**:

1. **Opening:** the verdict going in, who is at the table, the agenda.
2. **Each agenda item** (one per audit area: user flows, reliability, security,
   infrastructure, privacy and legal, reach, AI, code health, finance, launch
   readiness): what the team showed; each director who spoke, with their point, the
   evidence, and, only where it decided a high-severity call, the one quote it
   rested on ("Katz's view: ... He said: '...' (source, year)"); and the computed outcome: Blocker, Concerns, Clear or Not
   covered.
3. **Other points, questions and answers, the vote, the decision, the top actions.**

The decision is computed:

- **Hold** when the evidence is incomplete, when the ledger triggers a veto (a
  verified high or critical finding in a vetoing seat's area, a sale that loses
  money, a launch-blocking readiness item not done), when fewer than half the
  directors answered, or when a majority votes to hold.
- **Go with conditions** when nothing blocks but the Proof verdict is Not yet, or a
  director voted for or raised conditions.
- **Go** when the verdict is Ship and every director who answered voted go.

It also writes `proof/board/meeting.html`, and `--open` opens it in the user's
default browser (skipped in CI or with no screen, where it prints the path instead).
In an app with a built-in browser pane you can show it there too: it plays the same meeting on its own, step by step, with a timeline
of the agenda, each item's outcome appearing as it is reached, the directors'
quotes with links to their sources, the vote, the decision and an action checklist.
Each step shows short points and the quotes; the full text is in a dropdown, and
opening one holds the meeting on that step. Nothing needs a click. `play` paces the
same meeting in a terminal.

## Honest limits

The directors are sub-agents applying documented thinking, not the people: nine
readings by one model, so they share its blind spots, and their votes are advice. The
quotes are verbatim and sourced, but a quote applied to a new product is still an
interpretation. What makes the decision trustworthy is that it cannot contradict
the evidence: vetoes come from the ledger, not from a director's view. A board Go
is not market validation, legal sign-off or a forecast.

## Output / next

`proof/board/presentation.md`, `briefs/`, `answers/`, `qa.json`, `minutes.md`,
`transcript.txt`, `decision.json` and `meeting.html`. Work through the actions,
re-run the skills they touch, and convene the board again.
