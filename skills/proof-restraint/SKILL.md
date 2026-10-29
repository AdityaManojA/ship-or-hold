---
name: proof-restraint
description: >-
  Finds code, files and dependencies that probably should not exist: functions and
  classes nothing references, oversized source files, declared-but-unimported
  packages, and left-in debug and TODO markers. The cheapest code to own is the
  code you delete, so each candidate is something to confirm and remove. Offline,
  read-only, results in the ledger as low-severity notes that do not block a ship.
  Use when the user says "what can I delete", "find dead code", "is this bloated",
  "unused dependencies", "clean this up", "reduce surface area", or "simplify".
argument-hint: "[a path, default here]"
---

# proof-restraint

Every line of code is something to read, test, secure and maintain. The best way to
make the next change cheaper and safer is to carry less. This skill points at the
parts that look like dead weight, so you can confirm and remove them.

What the user typed after `/proof-restraint`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/bloat.py" scan .
python3 "${CLAUDE_SKILL_DIR}/bloat.py" deadcode src/
python3 "${CLAUDE_SKILL_DIR}/bloat.py" deps .
python3 "${CLAUDE_SKILL_DIR}/bloat.py" record .
python3 "${CLAUDE_SKILL_DIR}/bloat.py" status
```

## What it looks for

- **Unused code:** top-level Python functions and classes whose names appear
  nowhere else in the tree. A whole-word reference count, so it is a strong hint,
  not proof.
- **Oversized files:** source files past a line threshold, which tend to hold more
  than one responsibility.
- **Unused dependencies:** packages in `requirements.txt` or `package.json` that
  the code never imports.
- **Debug and TODO markers:** `console.log`, leftover `debugger;`, debug prints,
  and `TODO`/`FIXME`/`HACK`/`XXX`.
- **Stale files** (when git is present): files untouched for a long time.

## Step 1: scan

```bash
python3 "${CLAUDE_SKILL_DIR}/bloat.py" scan .
```

Read the candidates. This is a conversation starter: some "dead" code is an entry
point, a plugin, or called by reflection or a name the scan cannot see.

## Step 2: confirm, then delete

For each candidate, confirm it is truly unused (search the whole codebase, check
dynamic uses, check other repos that import it) before removing it. Removing
something and watching the tests still pass is the cleanest proof it was dead.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/bloat.py" record .
```

Writes the candidates to the ledger as **low-severity** notes. They are advisory on
purpose: restraint makes the next change cheaper, it does not block a release. The
report lists every candidate so you can work through them.

## Honest limits

This is heuristic. It will call some live code dead (anything referenced
dynamically, by a framework, by a string name, or from another repository) and miss
bloat it has no signal for. A large file is not always a problem and a small one is
not always fine. Treat every result as a question, not an answer, and let the tests
tell you whether a deletion was safe.

## Output / next

`proof/coverage/proof-restraint.json`, low-severity findings under
`proof/findings/proof-restraint/`, a report under `proof/evidence/proof-restraint/`.
Then `/proof-report`.
