---
name: proof-business-plan
description: >-
  The result of every Proof run: a 6 to 8 page client report (summary, board debate, annotated screenshots, 14-day fix kit), an appendix with the evidence, a free teaser and a re-fire. Use for "business plan", "the report", "the summary", "teaser" or "what changed since last time".
argument-hint: "[check | compile | pdf | teaser | refire --previous <dir>]"
---

# proof-business-plan

Everything else in Proof produces evidence. This skill turns it into what a founder pays for and acts on: a short client report that leads with the answer, an appendix that holds every piece of evidence behind it, a one-pager, and the free teaser that comes before a paid audit. It is the last step of every check, after the board has debated, and it is the result the user is shown first.

What the user typed after `/proof-business-plan`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/plan.py" check                      # the verdict, what is there, what is missing
python3 "${CLAUDE_SKILL_DIR}/plan.py" compile                    # plan, one-pager, report.pdf and appendix.pdf
python3 "${CLAUDE_SKILL_DIR}/plan.py" compile --no-pdf           # the Markdown only
python3 "${CLAUDE_SKILL_DIR}/plan.py" pdf                        # print report.pdf and appendix.pdf again
python3 "${CLAUDE_SKILL_DIR}/plan.py" pdf --draft                # an internal draft of an Incomplete run, marked DRAFT
python3 "${CLAUDE_SKILL_DIR}/plan.py" teaser                     # the free teaser: tech score and the one bluntest veto
python3 "${CLAUDE_SKILL_DIR}/plan.py" refire --previous old/proof   # what changed since an earlier run
python3 "${CLAUDE_SKILL_DIR}/plan.py" idea-template              # write proof/business/idea.md to fill in
```

Every command takes `--dir` (default `proof`) before the command name. `compile` and
`check` exit with the verdict: 0 Ship, 1 Not yet, 2 Incomplete.

## What the client gets

| file | what it is |
| --- | --- |
| `proof/report.pdf` | 6 to 8 pages, in this order: cover, Executive Summary, Boardroom Debate (or the Advisory Panel for a solo founder), What we saw on your store (annotated screenshots), 14-Day Fix Kit, and a Re-Fire page when there is one. No ledger codes or evidence file names on these pages. The page count is checked; over 8 is flagged. |
| `proof/appendix.pdf` | the full plan: the verdict and what blocks it, every seat's answer with the ledger entry behind each risk, the key numbers, every business section, the product itself, checks descoped for this client, and the research notes |
| `proof/one-pager.md` | the same numbers, word for word, for someone deciding in two minutes |
| `proof/teaser.md`, `proof/teaser.pdf` | free: the automated tech score and the one bluntest veto, before any owner input |
| `proof/refire.md` | on a later run: what was fixed, what is still open, what is new, how the verdict and votes moved |

The full plan stays in `proof/business-plan.md`. The buyer panel's verbatim quotes stay in
`proof/business/panel/quotes.md` for the owner and never go in either PDF.

## Steps

1. **The idea file.** If `proof/business/idea.md` does not exist, write it from what the
   user has said (`plan.py idea-template` gives the shape). Write "not known" rather than
   guess. The numbers themselves come from `/proof-intake`, never from this file.

2. **See what is there.** `plan.py check` prints the verdict, each number against its
   bar, which sections exist, and for each missing part the skill that produces it. Never
   fill a missing section yourself: the plan says "Not done" instead.

3. **Never export Incomplete.** A client report never leads with Incomplete. For each gap
   the check lists, either finish the check, or, when it does not apply to this client,
   descope it with a real reason:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" descope proof-a11y screen-reader --reason "no screen-reader tester on this audit; recommended before a public launch"
   ```

   A descoped check is listed in the appendix under "Not covered in this audit". It never
   counts as passed. `compile` refuses the client PDF while the verdict is Incomplete and
   lists what is left; `pdf --draft` prints an internal copy marked DRAFT.

4. **Screenshots.** Save two or three screenshots of the product page and the cart (the
   browser tools can save a screenshot to disk) under `proof/business/screens/`, and write
   `proof/business/screens/annotations.json`: a list of `{"image": "product.png",
   "title": "Product page", "notes": [{"x": 62, "y": 18, "w": 30, "h": 8, "label": "No size
   chart next to the size picker", "severity": "high"}]}` where x, y, w and h are
   percentages of the image. One note per finding that can be seen; label it in plain
   words. A box outside the image is rejected.

5. **White-label, when an agency runs the audit.** `proof/branding.json`:
   `{"name": "Agency name", "colour": "#0a7f5a", "logo": "branding/logo.png"}` (the logo
   path relative to the proof folder). The cover and footer then say "Prepared by" the
   agency and the tool's name is left out.

6. **Write the summary.** `proof/summary.md`, half a page (80 to 450 words), plain words,
   from the sections and the tool's output, never from memory: what the business is and
   who it is for; the computed verdict in those exact words and the three numbers behind
   it; one line per seat with its final vote and the point that decided it; the biggest
   risk; what is needed to start; the one thing to do this week. When the verdict is Not
   yet, lead with what has to change. Never soften a failing check. Say plainly which
   numbers rest on an estimate. The tool refuses a summary that does not state the verdict.

7. **Compile.** `plan.py compile`. It writes the plan, the one-pager and both PDFs, and
   prints the page count of the report. With no Chrome, Chromium or Edge on the machine the
   HTML is still written and the tool says how to print it; nothing is installed.

8. **Show the result.** In the chat: the verdict and the board's decision in one line, the
   summary in full, the first three days of the fix kit. Send `report.pdf`; mention the
   appendix and the meeting page.

## The teaser and the re-fire

- **Teaser** (`plan.py teaser`): run after the automated technical checks alone, before the
  owner has given any numbers. The tech score is 100 less 25 for each verified critical
  finding, 15 for each high, 5 for each medium, 1 for each low and 5 for each missed budget,
  from technical skills only. The one thing is a board veto when the board has met, else
  the worst verified blocker.
- **Re-fire** (`plan.py refire --previous <old proof folder>`): run the same check again
  after the owner has worked through the kit, then compare. Findings are matched by skill
  and id, or by skill and title. The result is a page in the next report.

## Rules

- A number in the summary comes from the tool's output or from a section file.
- Simulated buyers and board seats are named as simulated wherever they appear.
- Estimates stay labelled as estimates all the way into the summary.
- Proof only reads and records. This skill never edits the project.

## Honest limits

The report is only as good as what was recorded. A descoped check is a decision about
scope, not a pass, and the appendix says so. The page count is read from the PDF's page
objects, which can differ by one from what a viewer shows. The verdict says whether the
checks passed on what was tested; it is not a forecast, an investment recommendation, or
financial or legal advice. A simulated panel and a simulated board find objections well
and predict real behaviour badly: the first test with real people settles it.

## Output / next

`proof/summary.md`, `proof/business-plan.md`, `proof/one-pager.md`, `proof/report.pdf`,
`proof/appendix.pdf` (with their HTML), and on request `proof/teaser.md`/`.pdf` and
`proof/refire.md`. Then `/proof-audit-trail` to pack the run with a hash for every file.
Work through the fix kit, run the check again, `plan.py refire`, and compile again.
