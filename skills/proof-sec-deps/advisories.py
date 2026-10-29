#!/usr/bin/env python3
"""Audit a project's dependencies for known-vulnerable versions. Offline.

Standard library only, no network. It reads the dependency manifests and lockfiles
in a project (Python and Node), compares them against an advisory file you provide
(exported from your own source, or the bundled example format), and records a
finding for each affected package. For a live, always-current audit, use the
ecosystem's own tool (pip-audit, npm audit, and so on); this gives a repeatable,
offline check and a tidy ledger entry.

    python3 advisories.py deps .                         # list what is installed
    python3 advisories.py audit . --advisories adv.json  # match against advisories
    python3 advisories.py template --out adv.json
    python3 advisories.py status
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-sec-deps"


def _parse_requirements(text):
    deps = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9._\-]+)\s*==\s*([0-9A-Za-z.\-]+)", line)
        if m:
            deps[m.group(1).lower()] = m.group(2)
    return deps


def _parse_package_lock(data):
    deps = {}
    try:
        obj = json.loads(data)
    except ValueError:
        return deps
    # npm v2/v3 lockfile: "packages": {"node_modules/x": {"version": ...}}
    for path, info in (obj.get("packages") or {}).items():
        if not path:
            continue
        name = path.split("node_modules/")[-1]
        if isinstance(info, dict) and info.get("version"):
            deps[name.lower()] = info["version"]
    # npm v1: "dependencies": {"x": {"version": ...}}
    def walk(d):
        for name, info in (d or {}).items():
            if isinstance(info, dict):
                if info.get("version"):
                    deps.setdefault(name.lower(), info["version"])
                walk(info.get("dependencies"))
    walk(obj.get("dependencies"))
    return deps


def _parse_package_json(data):
    deps = {}
    try:
        obj = json.loads(data)
    except ValueError:
        return deps
    for section in ("dependencies", "devDependencies"):
        for name, spec in (obj.get(section) or {}).items():
            deps[name.lower()] = re.sub(r"^[\^~>=<\s]+", "", str(spec))
    return deps


MANIFESTS = {
    "requirements.txt": ("pip", _parse_requirements),
    "package-lock.json": ("npm", _parse_package_lock),
    "package.json": ("npm", _parse_package_json),
}


def collect(root):
    """Return {ecosystem: {name: version}} found under root (top level + one deep)."""
    found = {}
    candidates = [root] + [os.path.join(root, d) for d in os.listdir(root)
                           if os.path.isdir(os.path.join(root, d)) and d not in
                           (".git", "node_modules", "__pycache__", ".venv", "venv")] if os.path.isdir(root) else [root]
    for folder in candidates:
        for fname, (eco, parser) in MANIFESTS.items():
            path = os.path.join(folder, fname)
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        deps = parser(fh.read())
                except OSError:
                    continue
                bucket = found.setdefault(eco, {})
                for name, ver in deps.items():
                    bucket.setdefault(name, ver)
    return found


def _ver_tuple(v):
    parts = re.split(r"[.\-+]", str(v))
    out = []
    for p in parts:
        out.append((0, int(p)) if p.isdigit() else (1, p))
    return tuple(out)


def _in_range(version, spec):
    """spec like '<1.2.3' or '>=1.0,<1.4' or '==2.0.1'. Empty/'*' means any."""
    if not spec or spec == "*":
        return True
    v = _ver_tuple(version)
    for clause in spec.split(","):
        clause = clause.strip()
        m = re.match(r"(<=|>=|<|>|==)\s*([0-9A-Za-z.\-+]+)", clause)
        if not m:
            continue
        op, target = m.group(1), _ver_tuple(m.group(2))
        ok = {"<": v < target, "<=": v <= target, ">": v > target,
              ">=": v >= target, "==": v == target}[op]
        if not ok:
            return False
    return True


def cmd_template(a):
    sample = {
        "advisories": [
            {"id": "ADV-0001", "ecosystem": "npm", "package": "example-lib",
             "vulnerable": "<1.4.2", "severity": "high",
             "title": "Prototype pollution in example-lib before 1.4.2",
             "fixed": "1.4.2", "reference": "your advisory source url"},
            {"id": "ADV-0002", "ecosystem": "pip", "package": "example-pkg",
             "vulnerable": ">=2.0,<2.3.1", "severity": "critical",
             "title": "Remote code execution in example-pkg 2.0 to 2.3.0",
             "fixed": "2.3.1", "reference": "your advisory source url"},
        ]
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(sample, fh, indent=2)
    print("%s wrote an advisory template to %s. Fill it from your own advisory export." % (ledger.OK, a.out))
    return 0


def cmd_deps(a):
    found = collect(a.path)
    if not found:
        print("No Python or Node dependency files found under %s." % a.path)
        return 0
    for eco, deps in sorted(found.items()):
        print("[%s] %d package(s)" % (eco, len(deps)))
        for name in sorted(deps):
            print("  %s %s" % (name, deps[name]))
    return 0


def cmd_audit(a):
    with open(a.advisories, encoding="utf-8") as fh:
        advisories = json.load(fh).get("advisories", [])
    found = collect(a.path)
    ledger.init_coverage(a.dir, SKILL, [("audit", "Check dependencies against advisories")])
    hits = []
    for adv in advisories:
        eco = adv.get("ecosystem")
        name = str(adv.get("package", "")).lower()
        installed = found.get(eco, {}).get(name)
        if installed is None:
            continue
        if _in_range(installed, adv.get("vulnerable", "*")):
            hits.append((adv, installed))
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "dep-audit.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Dependency audit of %s against %s\n%d match(es)\n\n" % (a.path, a.advisories, len(hits)))
        for adv, installed in hits:
            fh.write("%s %s (installed %s) %s [%s] fixed in %s\n" % (
                adv.get("ecosystem"), adv.get("package"), installed, adv.get("id"),
                adv.get("severity"), adv.get("fixed", "?")))
    rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")
    ledger.mark_coverage(a.dir, SKILL, "audit", "covered", evidence=[rel],
                         note="%d affected package(s)" % len(hits))
    for adv, installed in hits:
        sev = adv.get("severity", "medium")
        if sev not in ledger.SEVERITIES:
            sev = "medium"
        fid = ledger.slug("dep-%s-%s" % (adv.get("package", "x"), adv.get("id", "")))
        f = ledger.new_finding(
            SKILL, "%s %s is vulnerable (%s)" % (adv.get("package"), installed, adv.get("id")), sev,
            id=fid, category="dependency",
            location="%s:%s@%s" % (adv.get("ecosystem"), adv.get("package"), installed),
            evidence=[rel], fix="Upgrade to %s or later." % adv.get("fixed", "a fixed version"),
            references=[adv.get("reference")] if adv.get("reference") else None)
        ledger.write_finding(a.dir, f)
        # The advisory match is itself the evidence; verify by the report file.
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    if a.json:
        print(json.dumps([{"package": adv.get("package"), "installed": inst, "id": adv.get("id"),
                           "severity": adv.get("severity"), "fixed": adv.get("fixed")} for adv, inst in hits], indent=2))
    else:
        if not hits:
            print("%s no advisory matched an installed package." % ledger.OK)
        else:
            print("%s %d affected package(s):" % (ledger.BAD, len(hits)))
            for adv, installed in hits:
                print("  [%s] %s %s -> upgrade to %s (%s)" % (adv.get("severity"), adv.get("package"),
                                                              installed, adv.get("fixed", "?"), adv.get("id")))
        print("\nRecorded and verified against %s. For a live audit, also run the ecosystem's own tool." % rel)
    return 0


def cmd_status(a):
    findings = ledger.load_findings(a.dir, SKILL)
    print("dependency audit: %d finding(s)" % len(findings))
    for f in findings:
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deps", help="list installed dependencies")
    d.add_argument("path")
    d.set_defaults(func=cmd_deps)
    au = sub.add_parser("audit", help="match dependencies against an advisory file")
    au.add_argument("path")
    au.add_argument("--advisories", required=True)
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=cmd_audit)
    t = sub.add_parser("template", help="write an advisory file template")
    t.add_argument("--out", required=True)
    t.set_defaults(func=cmd_template)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
