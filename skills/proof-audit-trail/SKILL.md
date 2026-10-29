---
name: proof-audit-trail
description: >-
  Packages a finished Proof run into a timestamped, hash-stamped evidence folder.
  It copies the whole ledger and every evidence file, writes a manifest with the
  SHA-256 of each file and the computed verdict, and can re-check the pack later to
  prove nothing changed. Use when the user says "package the results", "evidence
  pack", "sign off the run", "hand this to a reviewer", "audit trail", "prove these
  results", or at the end of a Proof pass. Offline, standard library only.
argument-hint: "[build | verify <pack>]"
---

# proof-audit-trail

A report is only as trustworthy as the evidence behind it and the certainty that the
evidence has not been edited since. This skill freezes a run: it copies everything,
hashes every file, and records the verdict, so a reviewer can open the pack and
confirm it is exactly what was produced.

What the user typed after `/proof-audit-trail`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/pack.py" build --dir proof
python3 "${CLAUDE_SKILL_DIR}/pack.py" verify proof/audit/run-2026-10-30-120000
```

## Step 1: run the report first

Build the verdict before packaging, so the pack records it:

```bash
python3 "${CLAUDE_SKILL_DIR}/../proof-report/verdict.py"
python3 "${CLAUDE_SKILL_DIR}/pack.py" build --dir proof
```

`build` writes `proof/audit/run-<timestamp>/` containing a copy of the whole `proof/`
ledger, a `manifest.json` with the SHA-256 and size of every file, and a `README.txt`
summarising the verdict and file count.

## Step 2: verify later

Anyone can re-check the pack:

```bash
python3 "${CLAUDE_SKILL_DIR}/pack.py" verify proof/audit/run-2026-10-30-120000
```

It recomputes every hash and reports any file that is missing or changed. An intact
pack means the report and all its evidence are exactly as packed.

## Honest limits

The hashes prove the files have not changed since packing; they do not prove the
findings were right in the first place. This is a tamper-evident envelope around the
run, not a signature of authenticity. For stronger guarantees, store the pack
somewhere write-once, or sign the manifest with your own key outside this tool.

## Output / next

`proof/audit/run-<timestamp>/` with the copied ledger, `manifest.json` and
`README.txt`. Hand the folder to whoever needs to trust the run; they verify it with
the command above.
