---
name: proof-business-plan
description: >-
  The main result of a Proof run: compiles the verdict, the board's decision, the numbers and every business section into one business plan, a half-page summary and a one-pager. Use for "business plan", "write the plan", "the summary", "one-pager" or "what is the result".
argument-hint: "[check | compile] [--open]"
---

# proof-business-plan

Everything else in Proof produces evidence. This skill turns it into the thing a founder acts on, or hands to a partner or a lender: one plan, a half-page summary and a one-pager, built only from what the other skills recorded. It is the last step of a run, after the board has met, and it is the result the user is shown first.

What the user typed after `/proof-business-plan`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/plan.py" check            # the verdict, what is there, what is missing
python3 "${CLAUDE_SKILL_DIR}/plan.py" compile          # write proof/business-plan.md and proof/one-pager.md
python3 "${CLAUDE_SKILL_DIR}/plan.py" compile --open   # and open the plan
python3 "${CLAUDE_SKILL_DIR}/plan.py" idea-template    # write proof/business/idea.md to fill in
```

Every command takes `--dir` (default `proof`) before the command name. The exit code
follows the verdict: 0 Ship, 1 Not yet, 2 Incomplete.

## What goes into the plan

| part | where it comes from |
| --- | --- |
| The verdict and what blocks it | computed from the ledger, the same way `/proof-report` does |
| The board's decision and actions | `proof/board/decision.json`, from `/proof-board` |
| The key numbers | every measured score the business skills recorded, with its bar |
| The idea | `proof/business/idea.md`, or the board's product dossier `proof/product.md` |
| Market and competition | `/proof-market` |
| Buyer panel | `/proof-buyers` |
| Pricing | `/proof-pricing` |
| The offer | `/proof-offer` |
| The numbers | `/proof-finance` |
| Go to market | `/proof-go-to-market` |
| Operations | `/proof-ops` |
| Launch readiness | `/proof-release`, one line of status |
| The product itself | one line per engineering and quality check, with the findings that matter most |
| What is not done yet | every missing part, with the skill that produces it |

## Steps

1. **The idea file.** If `proof/business/idea.md` does not exist, write it now from what
   the user has said: what it is, who it is for, what it sells and at what price, how it
   makes money, what it costs to run, what is already decided, and what is not known.
   `plan.py idea-template` gives the shape. Ask for anything only the user knows, at
   most three questions in one message. Write "not known" rather than guess a budget, a
   market size or a traffic figure.

2. **See what is there.**

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/plan.py" check
   ```

   It prints the verdict, each number against its bar, which sections exist, and for
   each missing part the skill that produces it. Offer to run the missing ones. Never
   fill a missing section yourself from guesses: the plan says "Not done" instead.

3. **The board.** If the board has not met on today's evidence, run `/proof-board`
   first. The plan carries its decision, its vote and its actions.

4. **Write the summary.** `proof/summary.md`, half a page (80 to 450 words), plain
   words, written from the sections and the tool's output, never from memory:

   - What the business is and who it is for, in two sentences.
   - The computed verdict, stated in those exact words, and the three numbers behind it.
   - What the buyer panel and the board agreed on, and where they disagreed.
   - The biggest risk and what will be done about it.
   - What is needed to start: cash, the first real-world test, the fixes that come first.
   - The one thing to do this week.

   When the verdict is Not yet or Incomplete, the summary leads with what has to change
   and which skill changes it. Never soften a failing check. Say plainly which numbers
   rest on an estimate. The tool refuses a summary that does not state the verdict.

5. **Compile.**

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/plan.py" compile --open
   ```

   Writes `proof/business-plan.md` (the verdict, the board's decision, the key numbers,
   the summary, the idea, then every section in the order a reader needs them, launch
   readiness, the engineering in one page, and what is not done yet) and
   `proof/one-pager.md` for someone deciding in two minutes, with the same numbers word
   for word.

6. **Show the result.** Give the user, in the chat: the verdict and the board's decision
   in one line, the summary in full, then the three or four things to do next. Point to
   the plan, the one-pager and the meeting page. This is the result of the run; the
   technical report is the appendix.

## Rules

- A number in the summary comes from the tool's output or from a section file. If it is
  not there, it does not go in.
- Simulated buyers and directors are named as simulated wherever they are quoted.
- Estimates stay labelled as estimates all the way into the summary.
- Proof only reads and records. This skill never edits the project.

## Honest limits

The plan is only as good as what was recorded. A section built on estimates reads as
confidently as one built on invoices unless the labels are kept, so keep them. The
verdict says whether the checks passed on what was tested; it is not a forecast, an
investment recommendation, or financial or legal advice. A simulated panel and a
simulated board find objections well and predict real behaviour badly: the first test
with real people in the go-to-market section is what settles it.

## Output / next

`proof/business/idea.md`, `proof/summary.md`, `proof/business-plan.md`,
`proof/one-pager.md`. Then `/proof-audit-trail` to pack the run with a hash for every
file. Work through the board's actions, re-run the skills they touch, and compile again:
the plan always shows today's evidence.
