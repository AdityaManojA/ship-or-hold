---
name: proof-licenses
description: >-
  Checks the licences of a project's dependencies and writes a software bill of
  materials (SBOM). Reads npm lockfiles and node_modules, yarn and pnpm lockfiles,
  and Python site-packages metadata or requirements and pyproject, classifies each
  licence (permissive, weak copyleft, strong copyleft, network copyleft or unknown,
  including SPDX OR and AND expressions), judges each against your project's own
  licence and an optional allow and deny list, and writes a CycloneDX 1.5 SBOM as
  evidence. Offline, read-only, optional (not part of a full check). Use when the
  user says "check licenses", "licence compliance", "any GPL in my app", "AGPL
  dependencies", "open source compliance", "make an SBOM", "software bill of
  materials", or "can we ship these dependencies".
argument-hint: "[project folders, default here] [--project-license MIT]"
---

# proof-licenses

Every dependency comes with terms. A closed-source app that bundles a GPL library,
or a hosted service built on an AGPL one, can be obliged to publish its own code.
Enterprise buyers and app stores increasingly ask for an SBOM too. This skill lists
every dependency with its licence, flags the ones that conflict with how you ship,
and writes the SBOM, with the files it read as evidence.

It is optional and not part of a full check: run it before a release, a sale to a
company that asks, or an open-source launch.

What the user typed after `/proof-licenses`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/licenses.py" scan .
python3 "${CLAUDE_SKILL_DIR}/licenses.py" scan frontend backend --site-packages backend/.venv/Lib/site-packages
python3 "${CLAUDE_SKILL_DIR}/licenses.py" record . --project-license proprietary --deny AGPL-3.0-only
python3 "${CLAUDE_SKILL_DIR}/licenses.py" sbom . --out sbom.cdx.json
python3 "${CLAUDE_SKILL_DIR}/licenses.py" status
```

## Step 1: point it at what you ship

Give it each project folder (`scan frontend backend mobile`). It reads, per folder:

- **npm:** `package-lock.json` (or `npm-shrinkwrap.json`); version 2 and 3 lockfiles
  carry each package's licence. `yarn.lock` and `pnpm-lock.yaml` give names and
  versions only, so install first: the licences are then read from `node_modules`.
- **Python:** the licences live in the installed packages, so pass the environment
  with `--site-packages` (for example `.venv/Lib/site-packages` on Windows,
  `.venv/lib/python3.12/site-packages` elsewhere). Without it, `requirements.txt`
  and `pyproject.toml` give names with unknown licences.

Dev dependencies (marked `dev` in the npm lockfile) are listed but never above low,
since they are not shipped.

## Step 2: say how you ship

`--project-license` decides what counts as a conflict:

| Your project | Flagged |
| --- | --- |
| `proprietary` (default) | network copyleft (AGPL, SSPL) high, strong copyleft (GPL) medium, weak copyleft (LGPL, MPL) low, unknown low |
| permissive (`MIT`, `Apache-2.0`, ...) | GPL and AGPL medium (they would change your project's terms), unknown low |
| `GPL-3.0-only` and the like | AGPL low, unknown low |

`--deny` makes a licence high wherever it appears; `--allow` clears one you have
reviewed. Both repeat. For an SPDX expression, `OR` takes the most permissive
choice and `AND` the strictest.

Weak copyleft in a mobile app needs care: a React Native or Flutter bundle links
libraries into one binary, which is not the dynamic linking LGPL assumes.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/licenses.py" record . --project-license proprietary
```

Writes coverage (inventory, network copyleft, strong copyleft, weak copyleft,
unknown, policy, SBOM), one verified finding per category with the packages named,
the full list as `proof/evidence/proof-licenses/licenses.json`, and the SBOM as
`proof/evidence/proof-licenses/sbom.cdx.json`. Re-recording drops findings that now
pass and keeps any you marked a false positive. `sbom --out` writes a copy
elsewhere and will not replace a file Proof did not write unless you add `--force`.

## Honest limits

This is not legal advice. It reports what packages declare, and declarations can be
wrong, missing or out of date; an unknown licence means "go and read the LICENSE
file", not "fine". It does not read licences of vendored code, fonts, images or
native SDKs added outside the package managers, and it does not judge how a
dependency is used (linked, modified, served over a network), which is often what
decides the obligation. For a sale or a dispute, have counsel confirm.

## Output / next

`proof/coverage/proof-licenses.json`, findings under `proof/findings/proof-licenses/`,
the dependency list and the CycloneDX SBOM under `proof/evidence/proof-licenses/`.
Then `/proof-report`.
