---
name: proof-sec-diff
description: >-
  Scopes a security review to what a branch changed. It reads a git diff (from a
  base branch, a patch file, or stdin), lists the changed files and added lines,
  and flags which weakness classes each change most likely touches, so a reviewer
  or a CI gate looks only at what moved. Records the scope in the ledger as
  review checks to work through. Use when the user says "review my branch", "what
  did this PR change", "security review of the diff", "scope the review", "check
  the changes", or wants a fast pre-merge security gate.
argument-hint: "[--base main]"
---

# proof-sec-diff

A full security audit on every pull request is too slow to run and too slow to
read. This skill narrows the review to the lines that changed and points at the
weakness classes those lines touch, so a pre-merge check stays fast and focused.

What the user typed after `/proof-sec-diff`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/diffscope.py" changed --base main
python3 "${CLAUDE_SKILL_DIR}/diffscope.py" changed --diff changes.patch
git diff main... | python3 "${CLAUDE_SKILL_DIR}/diffscope.py" changed --stdin
python3 "${CLAUDE_SKILL_DIR}/diffscope.py" record --base main
```

It reads a unified diff three ways: by running `git diff <base>...` in the repo
(read-only), by reading a patch file, or from stdin. It never writes to git.

## Step 1: see what moved

```bash
python3 "${CLAUDE_SKILL_DIR}/diffscope.py" changed --base main
```

For each changed file it shows how many lines were added and which weakness classes
the added code hints at (authorisation, authentication, injection, cross-site
scripting, request forgery, secrets, deserialisation, file paths, weak crypto,
headers and CORS). These are signals that say "look here", not verdicts.

## Step 2: record the scope

```bash
python3 "${CLAUDE_SKILL_DIR}/diffscope.py" record --base main
```

This writes a coverage ledger with one `review-<class>` check per class the diff
touches, each starting not-run. It is your focused to-do list.

## Step 3: review each flagged class

For each `review-<class>` check, review the changed code for that class, using
`/proof-sec-web` and its `attack-classes.md` entry, but only over the files in the
scope. Mark each check covered (with evidence) or not applicable (with a reason).
Record any real problem as a `proof-sec-web` finding and verify it.

## Honest limits

The signals are keyword hints on added lines. They will point at some changes that
are fine and miss a problem that uses none of the watched words, so a human still
skims the whole diff. A clean scope means nothing obvious stood out, not that the
change is safe. Removed lines and changes elsewhere that the diff does not show
(a changed call site far from the edit) are out of its view.

## Output / next

`proof/coverage/proof-sec-diff.json` and a scope report under
`proof/evidence/proof-sec-diff/`. Then `/proof-sec-web` over the flagged files, and
`/proof-report`.
