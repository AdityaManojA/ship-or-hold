---
name: proof-infra
description: >-
  Checks infrastructure and deployment config for risky settings: containers that
  run as root or use unpinned images, secrets baked into images, databases
  published on every interface, privileged or host-sharing pods, firewall rules
  open to the whole internet, public storage buckets, unencrypted volumes, CI
  workflows that expose secrets to untrusted code, and committed environment files.
  Reads Dockerfiles, Docker Compose, Kubernetes manifests, Terraform and CI
  workflows, offline. Use when the user says "check my infra", "Dockerfile review",
  "is my Kubernetes config safe", "terraform security", "audit the deployment",
  "CI security", or "misconfiguration".
argument-hint: "[a path, default the repo root]"
---

# proof-infra

Many production incidents start in a config file, not the code: a database port
open to the internet, a bucket readable by anyone, a container running as root, a
CI job that hands secrets to a pull request from a stranger. This skill reads those
files and points at each risky setting by file and line.

What the user typed after `/proof-infra`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/infra.py" scan .
python3 "${CLAUDE_SKILL_DIR}/infra.py" record .
python3 "${CLAUDE_SKILL_DIR}/infra.py" rules
```

## What it reads

- **Dockerfiles:** no non-root `USER`, an unpinned or `latest` base image, `ADD`
  from a URL, a script piped from the network into a shell, secrets in `ENV` or
  `ARG`.
- **Docker Compose:** privileged services, host networking, database ports published
  on all interfaces, plain-text secrets.
- **Kubernetes manifests:** privileged containers, host network, PID or IPC,
  privilege escalation allowed, no `runAsNonRoot`, unpinned images, no resource
  limits.
- **Terraform:** ingress open to `0.0.0.0/0`, public-read or public-write buckets,
  public databases, encryption turned off.
- **CI workflows:** `pull_request_target` jobs that check out the pull request's code
  (which runs untrusted code with your secrets), secrets echoed in a step,
  `permissions: write-all`, third-party actions pinned to a branch.
- **Committed `.env` files** (example and template files are ignored).

`rules` lists every rule with its severity.

## Step 1: scan

```bash
python3 "${CLAUDE_SKILL_DIR}/infra.py" scan .
```

Results are sorted by severity, each with its file, line and the line itself
(secret values masked).

## Step 2: record

```bash
python3 "${CLAUDE_SKILL_DIR}/infra.py" record .
```

Each hit becomes a finding at its file and line, verified by the scan report, with
the fix. The coverage ledger lists each kind of config as covered or not
applicable. A setting that is intentional (a compose file only used on a laptop, a
public bucket that really serves public files) can be rejected through proof-core
with the reason.

## Going deeper

For a full policy check, run a dedicated scanner when one is installed (check with
`/proof-doctor --for proof-infra`), such as Trivy's config scan or Checkov, and
record what it finds that this skill does not.

## Honest limits

These are line-level rules on files as written, not on what is actually deployed.
They miss settings that come from variables, modules, Helm values or the cloud
console, and they cannot see who can reach a port through the network around it. A
clean scan means none of these common mistakes appear in the files; it is not a
cloud security review. Settings that look risky may be fine for a local development
file, which is why each one can be rejected with a reason.

## Output / next

`proof/coverage/proof-infra.json`, findings under `proof/findings/proof-infra/`,
the scan report under `proof/evidence/proof-infra/`. Then `/proof-sec-secrets`,
`/proof-sec-deps` and `/proof-report`.
