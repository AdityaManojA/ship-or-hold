---
name: proof-board
description: >-
  Presents the project to a board of directors and gets a launch decision. Nine
  directors (CEO, CTO, CISO, CFO, CMO, Head of Sales, COO, CPO, General Counsel)
  are sub-agents that each hear the same presentation built from the Proof
  evidence, judge it through their own role, vote Go, Go with conditions or Hold,
  and back every serious risk with a finding or score from the ledger. The
  decision is computed from the evidence and the votes, and the meeting is written
  as an interactive page you can replay director by director. Use when the user
  says "present this to the board", "board of directors", "what would the execs
  say", "board meeting", "should we launch", "get sign-off", or "pitch it to the
  CTO, CFO and sales".
argument-hint: "[--seats ceo,cto,cfo]"
---

# proof-board

The testing skills say what is true about the product. The board says what to do
about it. The team presents; nine directors, each a sub-agent with a different job,
question it through their own lens and vote. Their decision rests on the same
ledger as everything else, so a director cannot wave away a verified critical bug,
and a director cannot invent one.

What the user typed after `/proof-board`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" seats
python3 "${CLAUDE_SKILL_DIR}/board.py" brief
python3 "${CLAUDE_SKILL_DIR}/board.py" check
python3 "${CLAUDE_SKILL_DIR}/board.py" answer cto 1 "Load was tested at 50 users; 200 is planned for next week"
python3 "${CLAUDE_SKILL_DIR}/board.py" tally
```

The seats, what each one reads and which ones hold a veto are in `seats.md` in this
folder.

## The board

| seat | judges | veto |
| --- | --- | --- |
| CEO | the overall call and the trade-offs | none (chairs) |
| CTO | does it work and keep working: flows, API, load, tests, infra | core flows, API, load, mobile flows |
| CISO | security and customer data | security, secrets, dependencies, infra, AI |
| CFO | the money: unit economics, cost of fixing versus delaying | unit economics |
| CMO | how the market meets it: SEO, AI search, accessibility, email | none |
| Sales | can it be sold: demo-ability, buyer security questions, pricing | none |
| COO | running it: launch readiness, load, email, alerts, rollback | launch-blocking readiness items |
| CPO | the experience: flows, exploratory testing, accessibility, visual | none |
| Legal | exposure: privacy, accessibility, policies, AI | privacy |

## Step 1: have evidence first

The board judges the ledger, so run the testing skills (or `/proof-full-check`) and
`/proof-report` first. With no evidence the board can only hold.

## Step 2: the presentation

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" brief
```

This turns the ledger into `proof/board/presentation.md`, the pitch the board hears:
the product, the computed verdict, what was tested and what it found, every
verified finding and score with a reference in square brackets, and the ask. It
writes one brief per director with their role and the exact answer format, and
lists them. Use `--seats ceo,cto,cfo,sales` for a smaller board. Running `brief`
again clears the previous meeting's answers, so the board always judges the current
evidence.

## Step 3: convene the board

Send ONE message with one Agent tool call per director brief (all nine in parallel):

- `subagent_type`: `general-purpose`
- `description`: `board <seat>`
- `prompt`: `Read <brief path> and follow it exactly. It is your whole brief.`

Each director writes `proof/board/answers/<seat>.json`. Then:

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" check
```

`check` refuses an answer whose high-severity risk cites nothing, or that cites a
reference not in the evidence: a director cannot make up a finding. Send the
director its problems and ask it to fix the file, or file a corrected answer with
`board.py save <seat>` from stdin. Never write a director's answer yourself.

## Step 4: questions from the board

Directors may ask the team questions. Answer each one from the evidence (or say
plainly that the evidence does not cover it):

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" answer cfo 1 "Payback is month 8 at the planned volume [proof-finance/score:year1_operating_profit]"
```

## Step 5: the decision and the meeting

```bash
python3 "${CLAUDE_SKILL_DIR}/board.py" tally
```

The decision is computed:

- **Hold** when the Proof evidence is incomplete, when the ledger itself triggers a
  veto (a verified high or critical finding in a vetoing seat's area, a sale that
  loses money, a launch-blocking readiness item not done), when fewer than half the
  directors answered, or when a majority votes to hold.
- **Go with conditions** when nothing blocks but the Proof verdict is Not yet, or
  any director voted for conditions or raised them.
- **Go** when the Proof verdict is Ship and every director who answered voted go.

It writes `proof/board/minutes.md`, `proof/board/decision.json`, and
`proof/board/meeting.html`, an interactive page that runs the meeting step by step,
the way a board takes it:

1. **Opening.** The team presents: the verdict, finding counts, scores, coverage,
   the nine directors in the room, and the agenda.
2. **One agenda item per audit area**, in board order: product and user flows,
   reliability and performance, security, infrastructure, privacy and legal,
   accessibility and reach, AI features, code health, finance, launch readiness.
   Only areas with evidence, or that the target requires, are on the agenda; the
   rest are listed as not tested. Each item has three parts:
   - the team presents that area: what was run, its verified findings, its scores
     against budget, the checks not yet run;
   - the board discusses: each director whose risks cite evidence from that area
     speaks, with every risk linked to its evidence;
   - the item outcome, computed: **Blocker** (a verified high or critical finding,
     or a veto), **Concerns** (a medium finding, a score over budget, or a required
     check not run), **Clear**, or **Not covered** (required but not run).
3. **Other concerns**: judgement calls that are not tied to one audit.
4. **Questions from the board**, with the team's answers.
5. **The vote**: each director's verdict, revealed one after another, and the tally.
6. **The decision**, with every reason, and where each audit landed.
7. **Actions**, grouped by owner, as a checklist you can tick off (remembered in
   your browser), then the evidence appendix every reference links to.

Move with Next and Previous, the arrow keys or the step list; each step has its
own link (`#step-3`). "Show all" puts the whole meeting on one page for reading or
printing.

Open the page for the user (the built-in browser, or send the file), and run the
same meeting in chat, item by item: the opening in two lines, then for each agenda
item what the team showed, what each director said, and the outcome; then the
questions, the vote, the decision and the top actions. Pause after each agenda item
if the user wants to discuss it.

## Honest limits

The directors are simulated: nine sub-agents reading the same presentation, so they
share one model's blind spots, and their votes are advice. What makes the decision
trustworthy is that it cannot contradict the evidence: vetoes come from the ledger,
not from the directors' mood. A board Go is not market validation, legal sign-off or
a financial forecast; it says the evidence and the directors' judgement do not
object to launching.

## Output / next

`proof/board/presentation.md`, `briefs/`, `answers/`, `qa.json`, `minutes.md`,
`decision.json` and `meeting.html`. Work through the actions, re-run the skills
they touch, and convene the board again.
