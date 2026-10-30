#!/usr/bin/env python3
"""Run Semgrep (when installed) or import its JSON output into the Proof ledger.

Standard library only. Proof does not ship Semgrep; when it is on PATH this runs it
with the rules you choose and records each result as a finding. Static analysis
flags patterns, not proven bugs, so results arrive unverified: confirm each one,
then verify it, or reject it with a reason.

    python3 semgrep_import.py run src/ --config p/owasp-top-ten     # needs semgrep on PATH
    python3 semgrep_import.py import semgrep.json
    python3 semgrep_import.py import semgrep.json --verify          # trust the tool's results
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

SKILL = "proof-sec-web"
SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low",
            "CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


def load_results(path):
    with open(path, encoding="utf-8-sig") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            raise SystemExit("%s is not JSON. Run semgrep with --json." % path)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SystemExit("%s does not look like semgrep --json output (no results list)." % path)
    return data


def import_results(d, path, verify=False):
    data = load_results(path)
    ev_dir = ledger.evidence_dir(d, SKILL)
    dest = os.path.join(ev_dir, "semgrep.json")
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    rel = os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")
    if ledger.load_coverage(d, SKILL) is None:
        ledger.init_coverage(d, SKILL, [("static-analysis", "Static analysis with Semgrep")])
    else:
        cov = ledger.load_coverage(d, SKILL)
        if not any(c["id"] == "static-analysis" for c in cov["checks"]):
            ids = [(c["id"], c["title"]) for c in cov["checks"]] + [("static-analysis", "Static analysis with Semgrep")]
            ledger.init_coverage(d, SKILL, ids)
    ledger.mark_coverage(d, SKILL, "static-analysis", "covered", evidence=[rel],
                         note="%d result(s)" % len(data["results"]))
    written = 0
    for res in data["results"]:
        extra = res.get("extra") or {}
        check = res.get("check_id", "semgrep-rule")
        path_ = res.get("path", "?")
        line = (res.get("start") or {}).get("line", 0)
        sev = SEVERITY.get(str(extra.get("severity", "WARNING")).upper(), "medium")
        msg = " ".join(str(extra.get("message", check)).split())[:160]
        fid = ledger.slug("sg-%s-%s-%s" % (check.split(".")[-1], os.path.basename(path_), line))
        f = ledger.new_finding(SKILL, msg, sev, id=fid, category="static-analysis",
                               location="%s:%s" % (path_, line), evidence=[rel],
                               references=["semgrep rule %s" % check])
        ledger.write_finding(d, f)
        if verify:
            ledger.verify(d, SKILL, fid, by="evidence-file")
        written += 1
    return written, rel


def cmd_import(a):
    n, rel = import_results(a.dir, a.path, a.verify)
    state = "verified" if a.verify else "unverified; confirm each, then verify or reject"
    print("%s imported %d semgrep result(s) (%s). Evidence: %s" % (ledger.OK, n, state, rel))
    return 0


def cmd_run(a):
    exe = shutil.which("semgrep")
    if not exe:
        print("semgrep is not on PATH. Install it, or review the code by hand with attack-classes.md.",
              file=sys.stderr)
        return 2
    out = os.path.join(ledger.evidence_dir(a.dir, SKILL), "semgrep.json")
    cmd = [exe, "scan", "--json", "--output", out, "--config", a.config, a.path]
    print("running: %s" % " ".join(cmd))
    try:
        subprocess.run(cmd, timeout=a.timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print("semgrep timed out after %ds" % a.timeout, file=sys.stderr)
        return 2
    if not os.path.isfile(out):
        print("semgrep produced no output file", file=sys.stderr)
        return 2
    n, rel = import_results(a.dir, out, a.verify)
    print("%s semgrep found %d result(s). Evidence: %s" % (ledger.OK, n, rel))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import", help="import semgrep --json output")
    i.add_argument("path")
    i.add_argument("--verify", action="store_true")
    i.set_defaults(func=cmd_import)
    r = sub.add_parser("run", help="run semgrep (if installed) and import the result")
    r.add_argument("path")
    r.add_argument("--config", required=True, help="semgrep rules, e.g. p/owasp-top-ten or a local rules file")
    r.add_argument("--timeout", type=int, default=900)
    r.add_argument("--verify", action="store_true")
    r.set_defaults(func=cmd_run)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
