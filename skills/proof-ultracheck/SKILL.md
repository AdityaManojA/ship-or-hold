---
name: proof-ultracheck
description: >-
  Every skill that fits the app, all required; long and token-heavy, so prefer a targeted profile. Use only for "ultracheck", "test absolutely everything", "every check there is" or "leave nothing out".
argument-hint: "[--public] [--skip proof-x] [--project path]..."
---

# proof-ultracheck

Every skill that fits the app, all required. Nothing joins on a guess: a skill is
added only when the review found what it tests (a PWA manifest, an Expo app, a
database, retrieval, a voice feature). Before starting, tell the user in one line
how many skills the plan holds and that a targeted profile would be shorter; go
ahead on their word or if they asked for the ultra check by name.

What the user typed after `/proof-ultracheck`: `$ARGUMENTS`

## Steps

1. **Target.** Needs `proof/target.json`; if missing, run `/proof-plan` first.
2. **Review.** Show the user what the review found; give `--project` once per folder
   when the product spans several repositories:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" review --project .
   ```
3. **Plan.**

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile ultra --project .
   python3 "${CLAUDE_SKILL_DIR}/../proof-check/check.py" plan --profile ultra --public --skip proof-voice
   ```

   SEO and AI-search join only with `--public`. Proof only reads and records: no
   skill edits the project, so there is no mutation testing here.
4. **Run.** `check.py next` names each skill; follow its own steps. On a later run
   it skips every skill whose inputs are unchanged. A check that
   cannot apply (no design exports, no device) is marked not applicable with a
   reason inside the skill, so it stops holding the verdict at Incomplete.
5. **Finish: the report.** Always, whatever the verdict and without asking, in this
   order: `/proof-report` for the computed verdict; `/proof-board --separate`, which ends with
   `board.py tally --open` and the meeting page; then `/proof-business-plan`: write
   `proof/summary.md` (half a page, with one line per director) and run `plan.py compile`,
   which writes `proof/business-plan.md`, `proof/one-pager.md` and
   `proof/business-plan.pdf` with every director's answer in it. Show the user the
   verdict, the board's decision and the summary in full, and send the PDF. Then
   `/proof-audit-trail`. A business section this check did not run is listed in the plan
   as not done, never filled in.

## Honest limits

Long, and some skills need things only you can provide: a device or emulator,
design exports, database query results, usage records with prices, someone to walk
through a screen reader. The review reads files, not running behaviour. Ship means
every required check passed on the build you tested.

## Output / next

`proof/review.json`, `proof/check.json` and `required_skills` in
`proof/target.json`. Finish with `/proof-report`, `/proof-board`, `/proof-business-plan`
(the summary, the plan and its PDF) and `/proof-audit-trail`.
