---
name: proof-mutation
description: >-
  Measures whether a project's tests actually catch bugs, using mutation testing.
  It makes one small change to the source at a time (flip a comparison, swap an
  operator, invert a boolean), runs the test command, and sees whether the tests
  notice. Changes the tests miss are "survivors" and mark gaps. The mutation score
  goes in the ledger. Use when the user says "are my tests any good", "mutation
  testing", "test the tests", "coverage lies", "do my tests catch bugs", or "test
  quality".
argument-hint: "[--src path --test-cmd \"...\"]"
---

# proof-mutation

Line coverage tells you which code ran during the tests, not whether the tests
would notice if it broke. Mutation testing answers the second question: it breaks
the code on purpose, in small ways, and checks that a test fails. A change that
slips through is a hole in the suite.

What the user typed after `/proof-mutation`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/mutate.py" sites src/thing.py
python3 "${CLAUDE_SKILL_DIR}/mutate.py" run --src src/thing.py --test-cmd "python -m pytest -q" --max 40
python3 "${CLAUDE_SKILL_DIR}/mutate.py" status
```

The built-in mutator handles Python. For JavaScript and other languages, use a
dedicated mutation tool (check `/proof-doctor --for proof-mutation`) and record its
score through the ledger.

## Before you run it

Your files are never edited. The tool copies the project (the git top level above
`--src`, or `--root`) to a temporary folder, leaving out `.git` and the `proof/`
folder and linking `node_modules` and virtual environments instead of copying them,
then mutates and tests the copy and deletes it at the end. Paths in the test
command that point into the project are pointed at the copy.

`--in-place` edits the real files instead, for a project that cannot be copied.
Before each change it writes the original to a restore journal under `proof/`, and
the next run puts back anything a crash left changed. Use it only on a clean
working tree you can restore from git.

It refuses to start unless the tests pass first: a mutation score only means
something when the baseline is green. It is not part of `/proof-full-check`; run it
on its own when you want it.

## Step 1: see the sites

```bash
python3 "${CLAUDE_SKILL_DIR}/mutate.py" sites src/thing.py
```

## Step 2: run

```bash
python3 "${CLAUDE_SKILL_DIR}/mutate.py" run --src src/ --test-cmd "python -m pytest -q" --max 40 --min-score 0.6
```

For each mutation it runs your test command. `--max` caps how many it tries (spread
across the file), `--timeout` bounds each run, `--min-score` sets the budget. The
score (killed / tested) is written to the ledger, and the survivors are listed.

## Step 3: close the gaps

Each survivor is a change your tests did not catch. Add or strengthen a test so the
change would fail, then run again and watch the survivor turn into a kill.

## Honest limits

A high score means the tests caught the mutations tried, not that they are perfect;
some surviving mutations are "equivalent" (they do not change behaviour) and cannot
be killed. Mutation testing is slow, because it runs the suite once per mutation, so
scope it to the code that matters. It needs a reliable, non-flaky test command.

## Output / next

Scores under `proof/scores/proof-mutation.json`, coverage, a survivors report under
`proof/evidence/proof-mutation/`, and a finding if any mutation survived. Then
`/proof-report`.
