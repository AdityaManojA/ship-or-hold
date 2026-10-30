---
name: proof-report
description: >-
  Compiles a Proof run into one report and computes the verdict: Ship, Not yet, or
  Incomplete. It reads the evidence ledger, counts only verified findings, checks
  measured scores against their budgets, confirms every required skill reported and
  covered its checks, and writes proof/report.md and proof/verdict.json. The model
  never writes the verdict; this tool computes it. Use when the user says "the
  report", "what's the verdict", "can we ship", "compile the results", "is it ready",
  or after running the testing skills.
argument-hint: "[--fail-on critical|high|medium|low]"
---

# proof-report

The last step. Everything the other skills recorded becomes one report, and the
verdict is computed from it, not written by hand. Ship means every required skill
reported and covered its checks and nothing verified is blocking. Not yet means
something verified is blocking, listed with its evidence. Incomplete means the
verdict cannot rest on anything yet.

What the user typed after `/proof-report`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/verdict.py"            # writes proof/report.md and proof/verdict.json
python3 "${CLAUDE_SKILL_DIR}/verdict.py" --check    # prints the verdict; exit 0 Ship, 1 Not yet, 2 Incomplete
python3 "${CLAUDE_SKILL_DIR}/verdict.py" --fail-on medium
```

## How the verdict is computed

- **Incomplete** when any of these is true: there is no `proof/target.json`; the
  ledger does not validate; the target is not marked authorised; a required skill
  has not reported; a required skill still has checks marked not-run. Each gap is
  listed, so you know exactly what to do next.
- **Not yet** when the ledger is complete but something blocks: a verified finding
  at or above the fail-on severity (default `high`, or whatever `target.json` sets),
  or a measured score over its budget. Each blocker is listed with its evidence.
- **Ship** when every required skill reported and covered its checks, no verified
  finding is at or above fail-on, and every measured score is within budget.

Only findings with status `verified` count. Unverified findings and false positives
appear in the report but never change the verdict. A skill the user did not require
is optional: not running it is never a gap, but if it was run, its verified findings
and scores count like any other evidence.

## Step 1: validate first

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-core/validate.py" --dir proof
```

The report refuses a ledger that does not validate, so fix any problems it lists
first (missing evidence files, a verified finding with no evidence, and so on).

## Step 2: compile and read it

Run `verdict.py`, then read the user:

1. The verdict and, if it is not Ship, the blockers or gaps in order.
2. For each required skill, what it covered and what it found.
3. The single most important thing to do next.

## Step 3: CI

`verdict.py --check` exits non-zero unless the verdict is Ship, so a pipeline can
gate on it:

```bash
python3 verdict.py --check || echo "not shippable yet"
```

## Step 4: other formats

```bash
python3 "${CLAUDE_SKILL_DIR}/verdict.py" --sarif proof/proof.sarif   # findings as SARIF 2.1.0
python3 "${CLAUDE_SKILL_DIR}/htmlreport.py"                          # proof/report.html
```

SARIF is the format code-scanning tools read. Upload it (for example with GitHub's
upload-sarif action) and verified findings appear as alerts on pull requests, each
with a severity score from 0 to 10. Only verified findings go in unless you add
`--sarif-all`.

The HTML report is one self-contained page with the screenshots embedded, for
sending to a client, a manager or a reviewer.

## Step 5: compare runs

```bash
python3 "${CLAUDE_SKILL_DIR}/rundiff.py" save --label "before fix"
# fix things, re-run the skills
python3 "${CLAUDE_SKILL_DIR}/rundiff.py" save --label "after fix"
python3 "${CLAUDE_SKILL_DIR}/rundiff.py" compare
```

`compare` lines up the last two snapshots: the verdict change, findings fixed and
newly found, severity changes, and every score that moved, with the percentage
("lcp: 3 -> 2 s (-33.3%) now within budget").

## Honest limits

The report is only as good as the evidence under it. A verified finding rests on the
file it points to: a screenshot is one moment, a repro is one machine. Ship means
the checks that were required passed on the build that was tested; it is not a proof
that no bug exists. Re-run the repros that matter before relying on it.

## Output / next

`proof/report.md` and `proof/verdict.json`. If the verdict is Not yet, fix the
blockers and run the relevant skill again. If Incomplete, do what each gap says.
`/proof-audit-trail` packages the whole run with hashes once you are done.
