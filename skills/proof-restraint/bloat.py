#!/usr/bin/env python3
"""Find code, files and dependencies that probably should not exist. Offline.

Standard library only, no network. The cheapest bug is in code you never wrote and
the cheapest thing to maintain is the file you deleted. This tool points at likely
dead weight: Python functions and classes that nothing else references, very large
source files, files untouched for a long time (by git), left-in debug markers, and
declared dependencies that the code never imports. Everything it reports is a
candidate for a human to confirm, then remove.

    python3 bloat.py scan .
    python3 bloat.py deadcode src/           # Python symbols nothing references
    python3 bloat.py deps .                  # declared-but-unimported packages
    python3 bloat.py record .
    python3 bloat.py status
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-restraint"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
BIG_FILE_LINES = 600
DEBUG_MARKERS = re.compile(r"(?i)\b(TODO|FIXME|XXX|HACK)\b|console\.log\(|print\(\s*['\"]debug|debugger;")


def _py_files(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def _all_source(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() in (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".vue", ".java"):
                yield os.path.join(base, f)


# ------------------------------------------------------------- dead code

def deadcode(root):
    """Python top-level defs/classes whose names appear nowhere else. Heuristic."""
    defs = {}  # name -> (file, line)
    corpus = []
    for path in _py_files(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        corpus.append(src)
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_") or node.name in ("main",):
                    continue
                if any(isinstance(d, ast.Name) and d.id in ("fixture", "task") for d in node.decorator_list):
                    continue
                defs.setdefault(node.name, (os.path.relpath(path, root), node.lineno))
    blob = "\n".join(corpus)
    dead = []
    for name, (path, line) in sorted(defs.items()):
        # count references by whole-word; a definition counts once, so >1 means used
        uses = len(re.findall(r"\b%s\b" % re.escape(name), blob))
        if uses <= 1:
            dead.append({"name": name, "file": path, "line": line})
    return dead


# --------------------------------------------------------------- big / old / debug

def big_files(root):
    out = []
    for path in _all_source(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                n = sum(1 for _ in fh)
        except OSError:
            continue
        if n > BIG_FILE_LINES:
            out.append({"file": os.path.relpath(path, root), "lines": n})
    return sorted(out, key=lambda x: -x["lines"])


def debug_markers(root):
    out = []
    for path in _all_source(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if DEBUG_MARKERS.search(line):
                        out.append({"file": os.path.relpath(path, root), "line": i,
                                    "text": line.strip()[:80]})
        except OSError:
            continue
    return out


def stale_files(root, days=365):
    """Files git has not touched in <days>, via git log. Empty if git is unavailable."""
    try:
        p = subprocess.run(["git", "-C", root, "ls-files"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=30, universal_newlines=True)
        if p.returncode != 0:
            return []
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    import time
    now = time.time()
    for rel in p.stdout.splitlines():
        if not rel.strip():
            continue
        try:
            q = subprocess.run(["git", "-C", root, "log", "-1", "--format=%ct", "--", rel],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
                               universal_newlines=True)
            ts = q.stdout.strip()
            if ts and (now - int(ts)) > days * 86400:
                out.append({"file": rel, "days": int((now - int(ts)) / 86400)})
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return sorted(out, key=lambda x: -x["days"])[:50]


# --------------------------------------------------------------- unused deps

def unused_deps(root):
    """Declared packages whose names never appear in an import. Heuristic, per ecosystem."""
    out = []
    req = os.path.join(root, "requirements.txt")
    if os.path.isfile(req):
        corpus = "\n".join(open(p, encoding="utf-8", errors="replace").read() for p in _py_files(root))
        with open(req, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                m = re.match(r"^([A-Za-z0-9._\-]+)", line)
                if not m:
                    continue
                pkg = m.group(1)
                mod = pkg.lower().replace("-", "_")
                if not re.search(r"\b(?:import|from)\s+%s" % re.escape(mod.split("_")[0]), corpus, re.I):
                    out.append({"ecosystem": "pip", "package": pkg})
    pkg_json = os.path.join(root, "package.json")
    if os.path.isfile(pkg_json):
        try:
            obj = json.load(open(pkg_json, encoding="utf-8"))
        except (OSError, ValueError):
            obj = {}
        corpus = "\n".join(open(p, encoding="utf-8", errors="replace").read()
                           for p in _all_source(root) if p.endswith((".js", ".jsx", ".ts", ".tsx", ".vue")))
        for name in (obj.get("dependencies") or {}):
            if name not in corpus:
                out.append({"ecosystem": "npm", "package": name})
    return out


# --------------------------------------------------------------- commands

def cmd_scan(a):
    dead = deadcode(a.path)
    big = big_files(a.path)
    markers = debug_markers(a.path)
    deps = unused_deps(a.path)
    if a.json:
        print(json.dumps({"deadcode": dead, "big_files": big, "debug_markers": markers,
                          "unused_deps": deps}, indent=2))
        return 0
    print("Restraint scan of %s:" % a.path)
    print("  %d possibly-unused Python symbol(s)" % len(dead))
    print("  %d large file(s) (>%d lines)" % (len(big), BIG_FILE_LINES))
    print("  %d debug/TODO marker(s)" % len(markers))
    print("  %d possibly-unused dependenc(y/ies)" % len(deps))
    for d in dead[:20]:
        print("    dead? %s  %s:%d" % (d["name"], d["file"], d["line"]))
    for b in big[:10]:
        print("    big   %s  (%d lines)" % (b["file"], b["lines"]))
    for d in deps[:20]:
        print("    dep?  %s (%s)" % (d["package"], d["ecosystem"]))
    print("\nEach is a candidate. Confirm it is truly unused before deleting; the cheapest")
    print("code to own is the code you remove. Record with: bloat.py record %s" % a.path)
    return 0


def cmd_deadcode(a):
    for d in deadcode(a.path):
        print("%s  %s:%d" % (d["name"], d["file"], d["line"]))
    return 0


def cmd_deps(a):
    deps = unused_deps(a.path)
    if not deps:
        print("%s no obviously-unused declared dependencies." % ledger.OK)
    for d in deps:
        print("%s %s (%s) is declared but never imported" % (ledger.BAD, d["package"], d["ecosystem"]))
    return 0


def cmd_record(a):
    dead = deadcode(a.path)
    big = big_files(a.path)
    deps = unused_deps(a.path)
    markers = debug_markers(a.path)
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "restraint.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Restraint scan of %s\n\n" % a.path)
        fh.write("Possibly-unused symbols:\n")
        for d in dead:
            fh.write("  %s  %s:%d\n" % (d["name"], d["file"], d["line"]))
        fh.write("\nLarge files:\n")
        for b in big:
            fh.write("  %s  %d lines\n" % (b["file"], b["lines"]))
        fh.write("\nPossibly-unused dependencies:\n")
        for d in deps:
            fh.write("  %s (%s)\n" % (d["package"], d["ecosystem"]))
        fh.write("\nDebug / TODO markers: %d\n" % len(markers))
    rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")
    ledger.init_coverage(a.dir, SKILL, [
        ("deadcode", "Unused functions and classes"),
        ("big-files", "Oversized source files"),
        ("unused-deps", "Declared-but-unimported dependencies"),
        ("debug-markers", "Left-in debug and TODO markers")])
    for cid, items in [("deadcode", dead), ("big-files", big), ("unused-deps", deps), ("debug-markers", markers)]:
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[rel], note="%d candidate(s)" % len(items))
    # One low-severity finding per category that has candidates. Restraint findings
    # are advisory: they default to low and are not meant to block a ship.
    for cid, title, items in [
            ("deadcode", "Unused code to consider deleting", dead),
            ("unused-deps", "Dependencies declared but never imported", deps)]:
        if items:
            fid = ledger.slug("restraint-%s" % cid)
            f = ledger.new_finding(SKILL, "%s (%d candidate%s)" % (title, len(items), "" if len(items) == 1 else "s"),
                                   "low", id=fid, category="restraint", evidence=[rel],
                                   fix="Confirm each is unused, then remove it.")
            ledger.write_finding(a.dir, f)
            ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    print("%s recorded a restraint scan: %d dead-symbol, %d big-file, %d unused-dep, %d marker candidate(s). Report: %s"
          % (ledger.OK, len(dead), len(big), len(deps), len(markers), rel))
    print("These are low severity by design: they do not block a ship, they make the next change cheaper.")
    return 0


def cmd_status(a):
    findings = ledger.load_findings(a.dir, SKILL)
    print("restraint: %d finding(s)" % len(findings))
    for f in findings:
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="all restraint checks at once")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)
    dc = sub.add_parser("deadcode", help="Python symbols nothing references")
    dc.add_argument("path")
    dc.set_defaults(func=cmd_deadcode)
    dp = sub.add_parser("deps", help="declared-but-unimported dependencies")
    dp.add_argument("path")
    dp.set_defaults(func=cmd_deps)
    r = sub.add_parser("record", help="write candidates to the ledger")
    r.add_argument("path")
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
