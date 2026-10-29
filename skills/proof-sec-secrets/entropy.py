#!/usr/bin/env python3
"""Scan a project for secrets that should not be committed. Offline, read-only.

Standard library only, no network. Finds two kinds of thing: strings that match a
known credential shape (a cloud key prefix, a private-key header, a token
assignment), and long high-entropy strings that look generated. It never prints a
secret in full: every match is shown and stored masked, so the scan output and the
evidence files are safe to keep.

    python3 entropy.py scan .
    python3 entropy.py scan src/config.js --json
    python3 entropy.py record .            # write masked findings to the ledger
    python3 entropy.py status
"""

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-sec-secrets"

# Directories and files never worth scanning.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
             ".next", ".nuxt", "vendor", ".mypy_cache", ".pytest_cache", "coverage"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
            ".tar", ".mp4", ".mov", ".woff", ".woff2", ".ttf", ".eot", ".lock", ".min.js"}
MAX_BYTES = 2 * 1024 * 1024

# Named credential shapes. Each is (id, human title, regex). These are shapes, not
# any real secret. The matched text is always masked before it is shown or stored.
PATTERNS = [
    ("aws-access-key", "Cloud access key id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", "Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("github-token", "Version-control token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b")),
    ("slack-token", "Chat platform token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key", "Payment platform secret key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("private-key", "Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("jwt", "Signed token (JWT)", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("generic-assignment", "Secret-looking assignment",
     re.compile(r"(?i)(?:api[_-]?key|secret|password|passwd|token|access[_-]?key|private[_-]?key)"
                r"""\s*[:=]\s*['"]([^'"\s]{12,})['"]""")),
]

# Values that look like a secret shape but are placeholders, so they do not count.
PLACEHOLDER = re.compile(r"(?i)(example|changeme|placeholder|your[_-]?|xxxx|<[^>]+>|dummy|sample|test[_-]?key|fake)")


def shannon(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def mask(secret):
    """Show at most the first 4 characters, then stars. Never the whole value."""
    secret = secret.strip().strip("'\"")
    if len(secret) <= 6:
        return "*" * len(secret)
    return secret[:4] + "*" * (len(secret) - 4)


TOKEN = re.compile(r"[A-Za-z0-9+/_\-]{20,}")


def scan_text(text, path):
    """Yield dicts: {pattern, title, line, col, masked, kind}. No full secrets."""
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:
            line = line[:4000]
        for pid, title, rx in PATTERNS:
            for m in rx.finditer(line):
                value = m.group(1) if m.groups() else m.group(0)
                if PLACEHOLDER.search(value):
                    continue
                out.append({"pattern": pid, "title": title, "line": lineno,
                            "col": m.start() + 1, "masked": mask(value), "kind": "pattern"})
        # Entropy pass: long tokens that are not already a named match.
        for m in TOKEN.finditer(line):
            tok = m.group(0)
            if len(tok) < 24 or PLACEHOLDER.search(tok):
                continue
            if shannon(tok) >= 4.0 and not _looks_like_hash_path(line, tok):
                out.append({"pattern": "high-entropy", "title": "High-entropy string",
                            "line": lineno, "col": m.start() + 1, "masked": mask(tok),
                            "kind": "entropy", "entropy": round(shannon(tok), 2)})
    # De-duplicate: a pattern hit and an entropy hit on the same spot -> keep the pattern.
    seen = {}
    for item in out:
        key = (item["line"], item["col"])
        if key not in seen or item["kind"] == "pattern":
            seen[key] = item
    return list(seen.values())


def _looks_like_hash_path(line, tok):
    # Integrity hashes, lockfile digests and the like are noisy and rarely secrets.
    low = line.lower()
    return any(w in low for w in ("sha256", "sha512", "integrity", "sri", "checksum", "hash:"))


def _iter_files(root):
    if os.path.isfile(root):
        yield root
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in SKIP_EXT or f.endswith(".min.js"):
                continue
            path = os.path.join(base, f)
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
            except OSError:
                continue
            yield path


def _scan_path(root):
    findings = []
    for path in _iter_files(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for hit in scan_text(text, path):
            hit["file"] = os.path.relpath(path, root if os.path.isdir(root) else os.path.dirname(root) or ".")
            findings.append(hit)
    return findings


def cmd_scan(a):
    findings = _scan_path(a.path)
    if a.json:
        print(json.dumps(findings, indent=2))
        return 1 if findings else 0
    if not findings:
        print("%s no secret-shaped strings found in %s." % (ledger.OK, a.path))
        return 0
    print("%s %d secret-shaped string(s) found (values masked):" % (ledger.BAD, len(findings)))
    for f in findings:
        extra = " entropy %.2f" % f["entropy"] if "entropy" in f else ""
        print("  %s:%d  %s  %s%s" % (f.get("file", a.path), f["line"], f["title"], f["masked"], extra))
    print("\nReview each. A real committed secret should be rotated, then removed from the history.")
    print("Record the confirmed ones (masked) with: entropy.py record %s" % a.path)
    return 1


def cmd_record(a):
    findings = _scan_path(a.path)
    ledger.init_coverage(a.dir, SKILL, [("scan", "Scan the tree for committed secrets")])
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report_path = os.path.join(ev_dir, "secret-scan.txt")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("Secret scan of %s\n%d candidate(s), values masked.\n\n" % (a.path, len(findings)))
        for f in findings:
            fh.write("%s:%d  %s  %s\n" % (f.get("file", a.path), f["line"], f["title"], f["masked"]))
    rel = os.path.relpath(report_path, ledger.root(a.dir)).replace(os.sep, "/")
    ledger.mark_coverage(a.dir, SKILL, "scan", "covered", evidence=[rel],
                         note="%d candidate(s)" % len(findings))
    written = 0
    for f in findings:
        # One finding per distinct (file, pattern). Masked only; never the secret.
        fid = ledger.slug("secret-%s-%s-%d" % (f["pattern"], os.path.basename(f.get("file", "x")), f["line"]))
        severity = "low" if f["kind"] == "entropy" else "high"
        finding = ledger.new_finding(
            SKILL, "Possible %s in %s" % (f["title"].lower(), f.get("file", a.path)), severity,
            id=fid, category="secret", location="%s:%d" % (f.get("file", a.path), f["line"]),
            evidence=[rel], fix="Rotate the credential, remove it from the code and the git history, "
                               "and load it from an environment variable or a secret manager instead.")
        ledger.write_finding(a.dir, finding)
        written += 1
    print("%s recorded a scan over %s: %d candidate finding(s), values masked in %s"
          % (ledger.OK, a.path, written, rel))
    if written:
        print("Each is unverified. Confirm a real secret by hand, then verify it:")
        print("  (via proof-core) ledger verify, by=manual, note=\"confirmed committed secret, rotating\"")
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no scan recorded yet. Run: entropy.py record <path>")
        return 0
    findings = ledger.load_findings(a.dir, SKILL)
    print("secret scan: %d finding(s) recorded" % len(findings))
    for f in findings:
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan a file or tree (prints masked candidates)")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)
    r = sub.add_parser("record", help="write masked findings to the ledger")
    r.add_argument("path")
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
