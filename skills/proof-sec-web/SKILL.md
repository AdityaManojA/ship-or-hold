---
name: proof-sec-web
description: >-
  A defensive security audit of a web app and its API that you own or are
  authorised to test. Works through a checklist of weakness classes (access
  control, injection, cross-site scripting, request forgery, business logic,
  security headers and more), records which classes were looked at, and holds every
  finding to a verification gate before it counts, with safe read-only checks on
  captured responses. Use when the user says "security review", "audit the app",
  "check for vulnerabilities", "is this secure", "pen test my own app", "OWASP
  check", or "find and fix security issues". Tests only targets the user is
  authorised to test; it never probes third-party systems.
argument-hint: "[a weakness class to focus on, or blank for all]"
---

# proof-sec-web

Go through the common web weaknesses one class at a time, on an app you are allowed
to test, and leave behind two things: a record of what you looked at, and for each
real problem a finding with evidence and a fix. The point is to find and fix, not to
pile up unverified maybes. A finding counts only once it is verified.

What the user typed after `/proof-sec-web`: `$ARGUMENTS`

## Authorisation first

This skill tests only what the user owns or has written permission to test. Before
any active step, read `proof/target.json`. If it is missing, run `/proof-plan`. If
`authorised` is false, stop and ask the user to confirm ownership and set it true.
Never point this skill at a third party's system. Keep every check non-destructive:
read, do not change or delete data, and never do anything that could take the
service down.

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/secweb.py" <command>
```

The weakness classes, and how to look for each safely, are in `attack-classes.md`
in this folder. Read it; it is the checklist this skill follows and it cites the
OWASP Web Security Testing Guide by section.

## Step 1: open the ledger

```bash
python3 "${CLAUDE_SKILL_DIR}/secweb.py" classes
python3 "${CLAUDE_SKILL_DIR}/secweb.py" start
```

`start` creates a coverage ledger with every class marked not-run, so at the end the
report can show what was examined, not only what was found.

## Step 2: work through the classes

For each class, read its entry in `attack-classes.md` and look at the app the safe
way it describes (prefer reading the source and confirming with a benign signal on a
test instance; use `/proof-doctor --for proof-sec-web` to see whether Semgrep is
available for static analysis). Then record the class:

```bash
# looked, nothing wrong, with a note and any evidence:
python3 "${CLAUDE_SKILL_DIR}/secweb.py" cover headers --note "all standard headers present" --evidence evidence/proof-sec-web/headers.txt

# does not apply here, with a reason:
python3 "${CLAUDE_SKILL_DIR}/secweb.py" na xxe --note "no XML parsing anywhere in the app"
```

A quick read-only helper for the config classes, over a response you captured:

```bash
python3 "${CLAUDE_SKILL_DIR}/secweb.py" scan-headers evidence/proof-sec-web/response.txt
```

It reports missing security headers, weak cookie flags and unsafe CORS from a saved
response. It sends nothing.

## Step 3: record and verify findings

When you find a real problem, write it, then verify it:

```bash
python3 "${CLAUDE_SKILL_DIR}/secweb.py" finding add authz-object \
  --title "Account B can read account A's invoices by changing the id" \
  --severity critical --wstg WSTG-ATHZ-04 \
  --evidence evidence/proof-sec-web/idor-a-vs-b.txt \
  --repro "As B, GET /api/invoices/<A's id>; A's invoice is returned" \
  --fix "Check resource ownership server-side on every request"

python3 "${CLAUDE_SKILL_DIR}/secweb.py" finding verify <id> --by evidence-file
```

Verify by `evidence-file` (the saved capture shows it), `repro-run` (you re-ran the
repro and saved the output), or `manual` (a note saying who confirmed and how). If a
finding turns out wrong, `finding reject <id> --reason ...`; it stays in the report
as ruled out. Only verified findings count toward the verdict.

## Step 4: dependencies

Vulnerable dependencies are their own skill. Run `/proof-sec-deps` and mark the
`deps` class covered here.

## Static analysis with Semgrep

When Semgrep is installed, run it with the rules you choose and import the results:

```bash
python3 "${CLAUDE_SKILL_DIR}/semgrep_import.py" run src/ --config p/owasp-top-ten
python3 "${CLAUDE_SKILL_DIR}/semgrep_import.py" import semgrep.json   # output you already have
```

Each result becomes an unverified finding at its file and line, with the raw output
as evidence and the `static-analysis` check marked covered. Pattern matches are leads:
confirm each one in the code, then verify it, or reject it with a reason.

## Honest limits

This is a structured review, not a guarantee. It finds the classes you work through
on the build you test; a class marked covered means you looked, not that the app is
proof against it. Confirmations are deliberately benign, so a source-level finding
is stronger than a single observed signal. For anything high-stakes, have a
professional confirm the critical findings and re-run the repros.

## Output / next

`proof/coverage/proof-sec-web.json`, findings under
`proof/findings/proof-sec-web/`, evidence under `proof/evidence/proof-sec-web/`.
Then `/proof-sec-deps`, `/proof-sec-secrets`, and `/proof-report`.
