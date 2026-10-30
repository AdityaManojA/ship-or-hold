#!/usr/bin/env python3
"""Check infrastructure and deployment config for risky settings. Offline, read-only.

Standard library only, no network. Reads the config files in a project and flags
settings that commonly cause incidents: containers that run as root or pull an
unpinned image, databases exposed on every interface, privileged pods, firewall
rules open to the whole internet, public storage buckets, unencrypted volumes,
CI workflows that run untrusted code with secrets, and environment files committed
to the repo. Every hit points to a file and line.

    python3 infra.py scan .
    python3 infra.py scan . --json
    python3 infra.py record .
    python3 infra.py rules
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-infra"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "vendor", ".terraform", "proof"}
DB_PORTS = {"5432", "3306", "6379", "27017", "9200", "5984", "11211", "1433"}
SECRET_NAME = re.compile(r"(?i)(password|passwd|secret|api_?key|token|private_?key|access_?key)")
PLACEHOLDER = re.compile(r"(?i)(\$\{|\$\(|changeme|example|placeholder|xxx|<|your_|dummy)")

# id, file kind, severity, title, fix
RULES = {
    "docker-root": ("dockerfile", "medium", "Container runs as root",
                    "Add a USER instruction for a non-root user before the final CMD."),
    "docker-latest": ("dockerfile", "low", "Base image is not pinned to a version",
                      "Pin the base image to a version tag or a digest."),
    "docker-add-url": ("dockerfile", "low", "ADD downloads from a URL",
                       "Download with a checksum check in a RUN step, or COPY a vendored file."),
    "docker-pipe-shell": ("dockerfile", "medium", "A script is piped from the network into a shell",
                          "Download, verify a checksum, then run."),
    "docker-secret": ("dockerfile", "high", "A secret is baked into the image",
                      "Pass secrets at runtime or with build secrets, never ENV or ARG values."),
    "compose-privileged": ("compose", "high", "Service runs privileged",
                           "Remove privileged: true; grant only the specific capabilities needed."),
    "compose-host-network": ("compose", "medium", "Service uses the host network",
                             "Use a bridge network and publish only the ports needed."),
    "compose-db-exposed": ("compose", "high", "Database port published on every interface",
                           "Bind to 127.0.0.1 or keep the database on an internal network only."),
    "compose-secret": ("compose", "medium", "Plain-text secret in the compose file",
                       "Load secrets from an env file kept out of git or a secret store."),
    "k8s-privileged": ("kubernetes", "high", "Container runs privileged",
                       "Remove privileged: true."),
    "k8s-host": ("kubernetes", "high", "Pod shares the host network, PID or IPC namespace",
                 "Remove hostNetwork, hostPID and hostIPC."),
    "k8s-escalation": ("kubernetes", "medium", "Privilege escalation allowed",
                       "Set allowPrivilegeEscalation: false."),
    "k8s-nonroot": ("kubernetes", "medium", "Workload does not require a non-root user",
                    "Set securityContext.runAsNonRoot: true."),
    "k8s-latest": ("kubernetes", "low", "Image is not pinned to a version",
                   "Pin images to a version tag or digest."),
    "k8s-limits": ("kubernetes", "low", "No resource limits set",
                   "Set CPU and memory limits so one workload cannot starve the node."),
    "tf-open-ingress": ("terraform", "high", "Firewall rule open to the whole internet",
                        "Restrict cidr_blocks to known ranges; never 0.0.0.0/0 for admin or database ports."),
    "tf-public-bucket": ("terraform", "high", "Storage bucket is publicly readable or writable",
                         "Make the bucket private and serve public files through a CDN with an origin policy."),
    "tf-public-db": ("terraform", "high", "Database is publicly accessible",
                     "Set publicly_accessible = false and reach it through a private network."),
    "tf-unencrypted": ("terraform", "medium", "Storage or database encryption is turned off",
                       "Turn encryption at rest on."),
    "ci-untrusted-checkout": ("ci", "high", "Workflow runs pull-request code with repository secrets",
                              "Do not check out the PR head in a pull_request_target workflow, or split it into an unprivileged job."),
    "ci-secret-echo": ("ci", "high", "A secret is printed in a CI step",
                       "Never echo secrets; pass them as environment variables to the tool that needs them."),
    "ci-write-all": ("ci", "medium", "Workflow grants write access to everything",
                     "Set the minimum permissions each job needs."),
    "ci-unpinned-action": ("ci", "low", "Third-party action pinned to a branch",
                           "Pin actions to a release tag or, better, a full commit SHA."),
    "env-committed": ("env", "high", "An environment file is committed",
                      "Remove it from git, rotate any secrets it held, and add it to .gitignore."),
}
KINDS = [("dockerfile", "Dockerfiles"), ("compose", "Docker Compose"), ("kubernetes", "Kubernetes manifests"),
         ("terraform", "Terraform"), ("ci", "CI workflows"), ("env", "Committed environment files")]


def classify(path):
    name = os.path.basename(path).lower()
    norm = "/" + path.replace("\\", "/").lower()
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "dockerfile"
    if re.match(r"(docker-)?compose(\.[\w-]+)?\.ya?ml$", name):
        return "compose"
    if name.endswith(".tf"):
        return "terraform"
    if "/.github/workflows/" in norm and name.endswith((".yml", ".yaml")):
        return "ci"
    if name == ".gitlab-ci.yml":
        return "ci"
    if name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template", ".dist"))):
        return "env"
    if name.endswith((".yml", ".yaml")):
        return "yaml"  # decided by content
    return None


def _hit(out, rule, path, line, text):
    out.append({"rule": rule, "file": path, "line": line, "text": text.strip()[:120]})


def scan_dockerfile(path, lines, out):
    users = []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        up = s.upper()
        if up.startswith("FROM "):
            img = s.split()[1] if len(s.split()) > 1 else ""
            if img.lower() != "scratch" and "@sha256:" not in img and "$" not in img:
                tag = img.split("/")[-1]
                if ":" not in tag or tag.endswith(":latest"):
                    _hit(out, "docker-latest", path, i, s)
        elif up.startswith("USER "):
            users.append((i, s.split(None, 1)[1].strip() if len(s.split()) > 1 else ""))
        elif up.startswith("ADD ") and re.search(r"https?://", s):
            _hit(out, "docker-add-url", path, i, s)
        if up.startswith("RUN ") and re.search(r"(curl|wget)[^|]*\|\s*(sudo\s+)?(ba)?sh\b", s):
            _hit(out, "docker-pipe-shell", path, i, s)
        if up.startswith(("ENV ", "ARG ")):
            m = re.match(r"(?i)(ENV|ARG)\s+(\w+)[=\s]+(.+)", s)
            if m and SECRET_NAME.search(m.group(2)) and m.group(3).strip("\"' ") and not PLACEHOLDER.search(m.group(3)):
                _hit(out, "docker-secret", path, i, "%s %s=***" % (m.group(1), m.group(2)))
    last_user = users[-1][1] if users else None
    if last_user is None or last_user in ("root", "0", "0:0"):
        line = users[-1][0] if users else 1
        _hit(out, "docker-root", path, line, "no USER instruction" if not users else "USER %s" % last_user)


def scan_compose(path, lines, out):
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if re.match(r"privileged:\s*true", s):
            _hit(out, "compose-privileged", path, i, s)
        if re.match(r"network_mode:\s*[\"']?host", s):
            _hit(out, "compose-host-network", path, i, s)
        m = re.match(r"-\s*[\"']?(?:(\d+\.\d+\.\d+\.\d+):)?(\d+):(\d+)", s)
        if m and m.group(3) in DB_PORTS and m.group(1) not in ("127.0.0.1",):
            _hit(out, "compose-db-exposed", path, i, s)
        m2 = re.match(r"-?\s*[\"']?(\w+)[\"']?\s*[:=]\s*[\"']?([^\"'\s#]+)", s)
        if m2 and SECRET_NAME.search(m2.group(1)) and not PLACEHOLDER.search(m2.group(2)) and len(m2.group(2)) >= 4 \
                and not m2.group(1).lower().endswith(("_file", "file")):
            _hit(out, "compose-secret", path, i, "%s: ***" % m2.group(1))


def _k8s_docs(lines):
    doc, start = [], 1
    for i, line in enumerate(lines, 1):
        if line.strip() == "---":
            if doc:
                yield start, doc
            doc, start = [], i + 1
        else:
            doc.append((i, line))
    if doc:
        yield start, doc


WORKLOADS = {"pod", "deployment", "statefulset", "daemonset", "job", "cronjob", "replicaset"}


def scan_kubernetes(path, lines, out):
    for start, doc in _k8s_docs(lines):
        text = "\n".join(l for _, l in doc)
        km = re.search(r"^kind:\s*(\w+)", text, re.M)
        if not km or km.group(1).lower() not in WORKLOADS:
            continue
        for i, line in doc:
            s = line.strip()
            if re.match(r"privileged:\s*true", s):
                _hit(out, "k8s-privileged", path, i, s)
            if re.match(r"host(Network|PID|IPC):\s*true", s):
                _hit(out, "k8s-host", path, i, s)
            if re.match(r"allowPrivilegeEscalation:\s*true", s):
                _hit(out, "k8s-escalation", path, i, s)
            m = re.match(r"-?\s*image:\s*[\"']?([^\"'\s]+)", s)
            if m and "@sha256:" not in m.group(1):
                tag = m.group(1).split("/")[-1]
                if ":" not in tag or tag.endswith(":latest"):
                    _hit(out, "k8s-latest", path, i, s)
        if not re.search(r"runAsNonRoot:\s*true", text):
            _hit(out, "k8s-nonroot", path, start, "kind: %s without runAsNonRoot: true" % km.group(1))
        if "containers:" in text and not re.search(r"^\s*limits:", text, re.M):
            _hit(out, "k8s-limits", path, start, "kind: %s without resources.limits" % km.group(1))


def scan_terraform(path, lines, out):
    text = "\n".join(lines)
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if "0.0.0.0/0" in s or "::/0" in s:
            # Only an ingress rule is a risk; look back a few lines for the block type.
            ctx = "\n".join(lines[max(0, i - 15):i]).lower()
            if "ingress" in ctx or "source_ranges" in s.lower() or "security_rule" in ctx:
                if "egress" not in "\n".join(lines[max(0, i - 4):i]).lower():
                    _hit(out, "tf-open-ingress", path, i, s)
        if re.match(r"acl\s*=\s*\"public-read(-write)?\"", s):
            _hit(out, "tf-public-bucket", path, i, s)
        if re.match(r"block_public_(acls|policy)\s*=\s*false", s):
            _hit(out, "tf-public-bucket", path, i, s)
        if re.match(r"publicly_accessible\s*=\s*true", s):
            _hit(out, "tf-public-db", path, i, s)
        if re.match(r"(storage_)?encrypted\s*=\s*false", s):
            _hit(out, "tf-unencrypted", path, i, s)
    return text


def scan_ci(path, lines, out):
    text = "\n".join(lines)
    prt = re.search(r"^\s*pull_request_target\s*:", text, re.M) or re.search(r"on:\s*\[?[^\n]*pull_request_target", text)
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if prt and re.search(r"github\.event\.pull_request\.head\.(sha|ref)|github\.head_ref", s):
            _hit(out, "ci-untrusted-checkout", path, i, s)
        if re.search(r"(echo|printf|cat)\b[^\n]*\$\{\{\s*secrets\.", s):
            _hit(out, "ci-secret-echo", path, i, s)
        if re.match(r"permissions:\s*write-all", s):
            _hit(out, "ci-write-all", path, i, s)
        m = re.match(r"-?\s*uses:\s*([\w.-]+/[\w./-]+)@([\w./-]+)", s)
        if m and not m.group(1).startswith(("actions/", "github/")) and m.group(2) in ("master", "main", "latest", "dev"):
            _hit(out, "ci-unpinned-action", path, i, s)


def scan(root):
    hits, kinds_found = [], set()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS or (d == ".github")]
        for f in files:
            full = os.path.join(base, f)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            kind = classify(rel)
            if kind is None:
                continue
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            if kind == "yaml":
                text = "\n".join(lines)
                if re.search(r"^apiVersion:", text, re.M) and re.search(r"^kind:", text, re.M):
                    kind = "kubernetes"
                elif re.search(r"^services:\s*$", text, re.M):
                    kind = "compose"
                else:
                    continue
            kinds_found.add(kind)
            if kind == "dockerfile":
                scan_dockerfile(rel, lines, hits)
            elif kind == "compose":
                scan_compose(rel, lines, hits)
            elif kind == "kubernetes":
                scan_kubernetes(rel, lines, hits)
            elif kind == "terraform":
                scan_terraform(rel, lines, hits)
            elif kind == "ci":
                scan_ci(rel, lines, hits)
            elif kind == "env":
                _hit(hits, "env-committed", rel, 1, "environment file in the tree")
    return hits, kinds_found


def cmd_rules(a):
    for rid, (kind, sev, title, _) in RULES.items():
        print("  %-22s %-11s %-7s %s" % (rid, kind, sev, title))
    return 0


def cmd_scan(a):
    hits, kinds = scan(a.path)
    if a.json:
        print(json.dumps({"kinds": sorted(kinds), "hits": hits}, indent=2))
        return 0
    print("Config found: %s" % (", ".join(sorted(kinds)) or "none"))
    if not hits:
        print("%s no risky settings found." % ledger.OK)
        return 0
    for h in sorted(hits, key=lambda h: ({"high": 0, "medium": 1, "low": 2}[RULES[h["rule"]][1]], h["file"], h["line"])):
        kind, sev, title, _ = RULES[h["rule"]]
        print("  %s [%s] %s  %s:%d  %s" % (ledger.BAD, sev, title, h["file"], h["line"], h["text"]))
    return 0


def cmd_record(a):
    hits, kinds = scan(a.path)
    ev = os.path.join(ledger.evidence_dir(a.dir, SKILL), "infra-scan.txt")
    with open(ev, "w", encoding="utf-8") as fh:
        fh.write("Infrastructure config scan of %s\nConfig found: %s\n\n" % (a.path, ", ".join(sorted(kinds)) or "none"))
        for h in hits:
            fh.write("%s:%d  %s  %s\n" % (h["file"], h["line"], h["rule"], h["text"]))
    rel = os.path.relpath(ev, ledger.root(a.dir)).replace(os.sep, "/")
    ledger.init_coverage(a.dir, SKILL, KINDS)
    for kid, title in KINDS:
        if kid in kinds or (kid == "env"):
            ledger.mark_coverage(a.dir, SKILL, kid, "covered", evidence=[rel],
                                 note="%d hit(s)" % sum(1 for h in hits if RULES[h["rule"]][0] == kid))
        else:
            ledger.mark_coverage(a.dir, SKILL, kid, "not_applicable", note="no %s in the project" % title.lower())
    for h in hits:
        kind, sev, title, fix = RULES[h["rule"]]
        fid = ledger.slug("infra-%s-%s-%d" % (h["rule"], h["file"], h["line"]))[:60]
        f = ledger.new_finding(SKILL, "%s (%s)" % (title, os.path.basename(h["file"])), sev, id=fid,
                               category="infra:%s" % h["rule"], location="%s:%d" % (h["file"], h["line"]),
                               evidence=[rel], fix=fix)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    print("%s recorded an infra scan: %d finding(s) across %s." % (
        ledger.OK if not hits else ledger.BAD, len(hits), ", ".join(sorted(kinds)) or "no config"))
    print("A setting that is intentional (a dev-only compose file, say) can be rejected with a reason.")
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan a project's config files")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)
    r = sub.add_parser("record", help="write the scan to the ledger")
    r.add_argument("path")
    r.set_defaults(func=cmd_record)
    sub.add_parser("rules", help="list every rule").set_defaults(func=cmd_rules)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
