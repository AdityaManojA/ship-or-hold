---
name: proof-sec-secrets
description: >-
  Scans a project for secrets that should not be committed: cloud keys, tokens,
  private keys, and long high-entropy strings. It runs offline, reads only, and
  never prints or stores a secret in full (every match is masked), so the scan
  output is safe to keep and share. Findings go in the ledger for the report. Use
  when the user says "scan for secrets", "did I commit a key", "check for leaked
  credentials", "secret scan", "any API keys in the code", or as part of a security
  pass. Detects TruffleHog or Gitleaks when installed and defers to them.
argument-hint: "[a path to scan, default the repo root]"
---

# proof-sec-secrets

A committed secret is one of the easiest mistakes to make and one of the worst to
leave. This skill looks for them and records what it finds, masked, so you can act
without the scan itself leaking anything.

What the user typed after `/proof-sec-secrets`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/entropy.py" scan .
python3 "${CLAUDE_SKILL_DIR}/entropy.py" record .
python3 "${CLAUDE_SKILL_DIR}/entropy.py" status
```

`scan` prints every candidate with its value masked (at most the first few
characters, then stars). `record` writes the candidates to the ledger and a masked
report under `proof/evidence/proof-sec-secrets/`. Neither ever writes a secret in
full.

## Step 1: scan

```bash
python3 "${CLAUDE_SKILL_DIR}/entropy.py" scan .
```

It skips the usual noise (`.git`, `node_modules`, build output, binaries,
lockfiles) and looks for known credential shapes (cloud access keys, API keys,
version-control and chat tokens, payment keys, private-key blocks, signed tokens)
and long high-entropy strings. Placeholders like `your_api_key` or `changeme` are
ignored.

If TruffleHog or Gitleaks is installed (check with
`/proof-doctor --for proof-sec-secrets`), prefer it: it verifies some secrets by
checking whether they are live, which this built-in scan does not do. Record its
findings through the ledger the same way.

## Step 2: review, do not trust blindly

Every candidate needs a human. Some high-entropy strings are hashes or ids, not
secrets. Open each location and decide. This skill will not auto-verify a secret,
because confirming one usually means recognising what it is.

## Step 3: record and act

```bash
python3 "${CLAUDE_SKILL_DIR}/entropy.py" record .
```

For each real secret: **rotate it first** (assume it is already compromised once it
has been in the repo), then remove it from the code and from the git history, and
load it from an environment variable or a secret manager instead. Then verify the
finding through `proof-core` as `manual` with a note that it was confirmed and
rotated.

## Honest limits

This finds secrets that match a known shape or look random enough; it will miss a
secret that looks ordinary, and it will flag some strings that are not secrets. It
does not check whether a key is live. A clean scan means nothing obvious was found,
not that there is nothing. Removing a secret from the latest commit is not enough;
it stays in the history until the history is rewritten, and it must be rotated
regardless.

## Output / next

`proof/coverage/proof-sec-secrets.json`, findings under
`proof/findings/proof-sec-secrets/`, a masked report under
`proof/evidence/proof-sec-secrets/`. Then `/proof-sec-deps` and `/proof-report`.
