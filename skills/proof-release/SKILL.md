---
name: proof-release
description: >-
  The pre-launch readiness gate. Walks through the operational things that decide
  whether launch day goes well (error tracking and alerts that reach a person,
  backups and a restore that was actually tested, a rollback that was tried,
  secrets and access, two-factor, domain and certificate renewal, rate limits,
  load, an on-call owner, support and status comms, legal pages, kill switches)
  and asks for evidence on each, not a yes. Items that are not ready become
  findings that must be verified. Use when the user says "are we ready to launch",
  "launch readiness", "pre-launch checklist", "go-live check", "production
  readiness review", "what could go wrong on launch day", or "launch gate".
argument-hint: "[planned launch date YYYY-MM-DD, or an item id to focus on]"
---

# proof-release

A launch goes badly for operational reasons far more often than for missing
features: the alert went to nobody, the backup had never been restored, the rollback
was never tried, the certificate expired. This skill is the gate before launch. Run
it a few days before the planned date, go through each readiness item with the
user, and record proof for each one. A screenshot of the alert arriving on a phone
is proof; "yes, we have alerts" is not. Items that are not ready are recorded as
gaps, verified, and fixed or consciously accepted before launch.

What the user typed after `/proof-release`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/release.py" <command>
```

The items, and for each one what good looks like, how to prove it and what counts
as evidence, are in `${CLAUDE_SKILL_DIR}/checklist.md`. Read it before starting.
The JSON block at its top is the list the tool reads, including which items are
critical (launch-blocking).

## Step 1: open the ledger

```bash
python3 "${CLAUDE_SKILL_DIR}/release.py" list
python3 "${CLAUDE_SKILL_DIR}/release.py" start --date 2026-11-12
```

`start` creates a coverage ledger with every item marked not run. `--date` records
the planned launch date, and `status` then shows the days remaining. Running
`start` again keeps what is already recorded.

## Step 2: go through the items with the user

Take the groups in order: observability, recovery, access, traffic, people, legal.
For each item, read its section in `checklist.md`, ask the user to do the proving
step, and save what comes back under `proof/evidence/proof-release/`. Prefer real
proof: a screenshot of the test error in the tracker, the restore log with row
counts, the rollback run's output, the rota with names. Then record it:

```bash
# proven, with the evidence file (it must exist under proof/ and not be empty):
python3 "${CLAUDE_SKILL_DIR}/release.py" done restore-tested --evidence evidence/proof-release/restore.log --note "restored last night's backup to scratch; row counts match"

# does not apply, with a reason:
python3 "${CLAUDE_SKILL_DIR}/release.py" na status-comms --note "internal tool for 12 staff; updates go in the team chat"
```

Some items have their own skill. For `load-tested` run `/proof-load`, for
`analytics-consent` run `/proof-privacy`, for `secrets-managed` run
`/proof-sec-secrets`, and use their output files as the evidence here.

## Step 3: record what is not ready

When an item is not ready, record a gap:

```bash
python3 "${CLAUDE_SKILL_DIR}/release.py" gap alerts-reach-human --title "Error alerts go to a shared inbox nobody watches" --id alerts-unwatched --fix "Route to the on-call phone"
python3 "${CLAUDE_SKILL_DIR}/release.py" verify alerts-unwatched --by manual --note "confirmed with Sam: no one is subscribed"
```

Without `--id` the finding id is built from the item and the title, and the tool
prints it. The severity defaults to high for a critical item and medium otherwise; pass
`--severity` to change it. The category is `release:<group>`. A gap is born
unverified and counts only once verified, by `manual` (a note saying who confirmed
and how), `evidence-file` (with `--evidence`) or `repro-run`. A gap that turns out
wrong is closed with `reject <id> --reason "..."`. When the item is fixed, prove
it with `done` as in step 2.

## Step 4: read the state

```bash
python3 "${CLAUDE_SKILL_DIR}/release.py" status
```

`status` lists critical items that are not done first, since these block the
launch, then the rest by group, the recorded gaps and the days to launch. Add
`--json` for a machine-readable copy. Walk the user through what is left and agree
who does each item before the date.

## What it writes

- `proof/coverage/proof-release.json`: every item and its state, plus the launch date.
- `proof/findings/proof-release/<id>.json`: one per gap.
- `proof/evidence/proof-release/`: the screenshots, logs and documents you saved.

## Honest limits

The tool cannot see your infrastructure. It checks that an evidence file exists and
is not empty, not that it shows what it claims; reading it honestly is your job and
the user's. A done item proves the thing worked when it was tested, not that it
still works on launch day, so re-check the critical items the day before. The list
covers the common operational risks for a small team; it is not a compliance audit
and it does not know your product's own failure modes.

## Output / next

`proof/coverage/proof-release.json` and `proof/findings/proof-release/`. Add
`proof-release` to `required_skills` with `/proof-plan` if the launch should wait
on it, then run `/proof-report` for the verdict. Related: `/proof-load`,
`/proof-privacy`, `/proof-sec-secrets`.
