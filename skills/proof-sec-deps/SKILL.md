---
name: proof-sec-deps
description: >-
  Audits a project's dependencies for known-vulnerable versions. It reads the
  Python and Node manifests and lockfiles, matches them against an advisory file
  (your own export, or the bundled format), and records a verified finding for each
  affected package with the version to upgrade to. Offline and repeatable. Use when
  the user says "audit dependencies", "any vulnerable packages", "check my
  lockfile", "supply chain", "CVE check", or as part of a security pass. Defers to
  pip-audit, npm audit or Trivy when a live, always-current check is wanted.
argument-hint: "[a path to the project, default here]"
---

# proof-sec-deps

Old dependencies are where a lot of real risk hides. This skill reads what the
project actually has installed and checks it against a list of known-bad versions,
so each hit is a concrete "upgrade this package to that version", recorded with
evidence.

What the user typed after `/proof-sec-deps`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/advisories.py" deps .
python3 "${CLAUDE_SKILL_DIR}/advisories.py" audit . --advisories adv.json
python3 "${CLAUDE_SKILL_DIR}/advisories.py" template --out adv.json
python3 "${CLAUDE_SKILL_DIR}/advisories.py" status
```

It reads `requirements.txt`, `package-lock.json` and `package.json` at the project
root and one level down, so a typical front-end and back-end split is covered.

## Two ways to get advisories

- **Live and current (preferred when available):** run the ecosystem's own auditor,
  which fetches the latest advisories. Check what is installed with
  `/proof-doctor --for proof-sec-deps`, then run `pip-audit`, `npm audit`, or
  `trivy fs .`. Record the real findings through the ledger.
- **Offline and repeatable (this tool):** keep an advisory file exported from your
  own source (write one with `template` to see the shape), and run `audit`. This
  gives the same answer every time and needs no network, which suits CI and
  air-gapped review. It is only as current as the file you give it.

## Step 1: see what is installed

```bash
python3 "${CLAUDE_SKILL_DIR}/advisories.py" deps .
```

## Step 2: audit

```bash
python3 "${CLAUDE_SKILL_DIR}/advisories.py" audit . --advisories adv.json
```

Each advisory names an ecosystem, a package, a vulnerable version range, a
severity and the fixed version. A match writes a finding, verified against the
audit report (the match is the evidence), with the upgrade target in the fix.

## Step 3: act

Upgrade each affected package to its fixed version or later, then run the audit
again and confirm the findings clear. Where an upgrade is not possible yet, record
why and track it.

## Honest limits

The offline audit is only as good as the advisory file: a package missing from the
file is not checked, and a brand-new advisory will not be there until you update
it. Version-range matching handles ordinary version strings; unusual pre-release or
epoch schemes may need a second look. A clean result means nothing in your advisory
file matched, not that every dependency is safe. Transitive dependencies are
covered only to the depth the lockfile records.

## Output / next

`proof/coverage/proof-sec-deps.json`, findings under
`proof/findings/proof-sec-deps/`, a report under `proof/evidence/proof-sec-deps/`.
Then `/proof-report`.
