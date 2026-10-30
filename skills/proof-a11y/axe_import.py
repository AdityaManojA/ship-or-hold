#!/usr/bin/env python3
"""Run the axe accessibility CLI (when installed) or import its JSON into the ledger.

Standard library only. When the `axe` command is on PATH this runs it against a URL
and records each rule violation as a finding, with the affected elements. Automated
rules catch a minority of real barriers, so pair this with the manual checklist in
contrast.py. Violations arrive unverified unless you pass --verify.

    python3 axe_import.py run http://localhost:3000/checkout     # needs the axe CLI
    python3 axe_import.py import axe-results.json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-a11y"
IMPACT = {"critical": "high", "serious": "high", "moderate": "medium", "minor": "low"}


def _pages(data):
    """axe CLI writes a list of page results; accept a single result object too."""
    if isinstance(data, dict) and "violations" in data:
        return [data]
    if isinstance(data, list) and all(isinstance(p, dict) for p in data):
        return [p for p in data if "violations" in p]
    raise SystemExit("this does not look like axe JSON output (no violations list).")


def _ensure_check(d, cid, title):
    cov = ledger.load_coverage(d, SKILL)
    checks = [(c["id"], c["title"]) for c in cov["checks"]] if cov else []
    if not any(c == cid for c, _ in checks):
        ledger.init_coverage(d, SKILL, checks + [(cid, title)])


def import_results(d, path, verify=False):
    with open(path, encoding="utf-8-sig") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            raise SystemExit("%s is not JSON" % path)
    pages = _pages(data)
    dest = os.path.join(ledger.evidence_dir(d, SKILL), "axe-results.json")
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    rel = os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")
    _ensure_check(d, "engine", "Automated rule engine pass")
    total = sum(len(p.get("violations", [])) for p in pages)
    ledger.mark_coverage(d, SKILL, "engine", "covered", evidence=[rel],
                         note="axe: %d violation(s) on %d page(s)" % (total, len(pages)))
    written = 0
    for page in pages:
        url = page.get("url", "")
        for v in page.get("violations", []):
            nodes = v.get("nodes", [])
            targets = []
            for n in nodes[:5]:
                t = n.get("target")
                targets.append(" ".join(t) if isinstance(t, list) else str(t))
            sev = IMPACT.get(str(v.get("impact", "moderate")), "low")
            fid = ledger.slug("axe-%s-%s" % (v.get("id", "rule"), url.rsplit("/", 1)[-1] or "page"))
            title = "%s (%d element%s)" % (v.get("help") or v.get("id"), len(nodes), "" if len(nodes) == 1 else "s")
            f = ledger.new_finding(SKILL, title[:160], sev, id=fid, category="a11y:%s" % v.get("id", "rule"),
                                   location=url or None, evidence=[rel],
                                   repro="Elements: %s" % "; ".join(targets) if targets else None,
                                   references=[v["helpUrl"]] if v.get("helpUrl") else None)
            ledger.write_finding(d, f)
            if verify:
                ledger.verify(d, SKILL, fid, by="evidence-file")
            written += 1
    return written, rel


def cmd_import(a):
    n, rel = import_results(a.dir, a.path, a.verify)
    print("%s imported %d axe violation(s)%s. Evidence: %s" % (
        ledger.OK, n, "" if a.verify else " (unverified)", rel))
    return 0


def cmd_run(a):
    exe = shutil.which("axe")
    if not exe:
        print("the axe CLI is not on PATH. Use contrast.py and the manual checklist instead.", file=sys.stderr)
        return 2
    t = ledger.load_target(a.dir)
    if t is not None and not t.get("authorised") and not ("localhost" in a.url or "127.0.0.1" in a.url):
        print("target.json says authorised: no; refusing to scan a non-local URL.", file=sys.stderr)
        return 2
    out_dir = ledger.evidence_dir(a.dir, SKILL)
    out = os.path.join(out_dir, "axe-results.json")
    cmd = [exe, a.url, "--save", out]
    print("running: %s" % " ".join(cmd))
    try:
        subprocess.run(cmd, timeout=a.timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print("axe timed out", file=sys.stderr)
        return 2
    if not os.path.isfile(out):
        print("axe wrote no results file; check `axe --help` for the save option in your version.", file=sys.stderr)
        return 2
    n, rel = import_results(a.dir, out, a.verify)
    print("%s axe found %d violation(s). Evidence: %s" % (ledger.OK, n, rel))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import", help="import axe JSON output")
    i.add_argument("path")
    i.add_argument("--verify", action="store_true")
    i.set_defaults(func=cmd_import)
    r = sub.add_parser("run", help="run the axe CLI against a URL and import")
    r.add_argument("url")
    r.add_argument("--timeout", type=int, default=300)
    r.add_argument("--verify", action="store_true")
    r.set_defaults(func=cmd_run)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
