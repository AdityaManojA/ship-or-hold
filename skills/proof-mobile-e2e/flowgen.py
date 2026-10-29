#!/usr/bin/env python3
"""Plan mobile app flows, generate runner files, and record each as evidence.

Standard library only, no network. Generates Maestro flow files (YAML) from a flow
spec for the model or the user to run on a device or emulator, and records each
flow's result in the ledger the same way the web end-to-end skill does: a pass
needs an evidence file (a screenshot), a failure becomes a finding.

    python3 flowgen.py plan flows.json
    python3 flowgen.py maestro sign-in --out proof/mobile/sign-in.yaml --app com.example.app
    python3 flowgen.py result sign-in pass --evidence evidence/proof-mobile-e2e/sign-in.png
    python3 flowgen.py result checkout fail --evidence <path> --title "..." --severity high
    python3 flowgen.py status
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-mobile-e2e"


def _flows_path(d):
    return os.path.join(ledger.root(d), "mobile", "flows.json")


def _load_flows(d):
    path = _flows_path(d)
    if not os.path.exists(path):
        raise SystemExit("no flow plan yet. Run: flowgen.py plan <flows.json>")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["flows"]


def cmd_plan(a):
    with open(a.spec, encoding="utf-8") as fh:
        data = json.load(fh)
    flows = data.get("flows")
    if not isinstance(flows, list) or not flows:
        raise SystemExit("the spec needs a non-empty 'flows' list")
    seen = set()
    for i, f in enumerate(flows):
        for key in ("id", "title", "steps", "expect"):
            if key not in f:
                raise SystemExit("flow %d is missing '%s'" % (i + 1, key))
        if f["id"] in seen:
            raise SystemExit("duplicate flow id %r" % f["id"])
        seen.add(f["id"])
    os.makedirs(os.path.dirname(_flows_path(a.dir)), exist_ok=True)
    with open(_flows_path(a.dir), "w", encoding="utf-8") as fh:
        json.dump({"flows": flows}, fh, ensure_ascii=False, indent=2)
    ledger.init_coverage(a.dir, SKILL, [(f["id"], f["title"]) for f in flows])
    print("%s planned %d mobile flow(s); coverage ledger created." % (ledger.OK, len(flows)))
    return 0


def _yaml_escape(s):
    if any(c in s for c in ':#\"\n') or s != s.strip():
        return '"%s"' % s.replace('"', '\\"')
    return s


def cmd_maestro(a):
    flows = {f["id"]: f for f in _load_flows(a.dir)}
    if a.flow_id not in flows:
        raise SystemExit("no flow %r in the plan" % a.flow_id)
    flow = flows[a.flow_id]
    app = a.app or "YOUR.APP.ID"
    lines = ["# Maestro flow for %s. Run: maestro test %s" % (a.flow_id, os.path.basename(a.out)),
             "appId: %s" % app, "---", "- launchApp"]
    for step in flow["steps"]:
        # Each step becomes a commented TODO plus a best-guess tapOn, for the author to refine.
        lines.append("# step: %s" % step)
        lines.append("- tapOn: %s" % _yaml_escape(step))
    lines.append("# expect: %s" % flow["expect"])
    lines.append("- takeScreenshot: %s" % ("evidence/%s/%s" % (SKILL, a.flow_id)))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("%s wrote a Maestro flow to %s (refine the tapOn selectors to match the UI)." % (ledger.OK, a.out))
    print("Run it with Maestro (needs a device or emulator). Without Maestro, drive the flow by hand")
    print("and save a screenshot to evidence/%s/%s.png." % (SKILL, a.flow_id))
    return 0


def cmd_result(a):
    flows = {f["id"]: f for f in _load_flows(a.dir)}
    if a.flow_id not in flows:
        raise SystemExit("no flow %r in the plan" % a.flow_id)
    flow = flows[a.flow_id]
    if a.outcome == "pass":
        try:
            ledger.mark_coverage(a.dir, SKILL, a.flow_id, "covered", note=a.note, evidence=[a.evidence])
        except ledger.LedgerError as e:
            raise SystemExit("cannot record a pass: %s" % e)
        print("%s %s passes, evidence recorded." % (ledger.OK, a.flow_id))
        return 0
    if not a.title:
        raise SystemExit("a failing flow needs --title")
    severity = a.severity or ("high" if flow.get("critical") else "medium")
    f = ledger.new_finding(SKILL, a.title, severity, category="flow:%s" % a.flow_id,
                           location=a.flow_id, repro=a.repro, evidence=[a.evidence], fix=a.fix)
    ledger.write_finding(a.dir, f)
    ledger.mark_coverage(a.dir, SKILL, a.flow_id, "covered", evidence=[a.evidence])
    if a.verify:
        ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
        print("%s %s: finding %r written and verified." % (ledger.BAD, a.flow_id, f["id"]))
    else:
        print("%s %s: finding %r written (unverified); add evidence and re-run with --verify." % (ledger.BAD, a.flow_id, f["id"]))
    return 0


def cmd_skip(a):
    flows = {f["id"]: f for f in _load_flows(a.dir)}
    if a.flow_id not in flows:
        raise SystemExit("no flow %r in the plan" % a.flow_id)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.flow_id, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked not applicable." % (ledger.OK, a.flow_id))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no mobile flow plan yet.")
        return 0
    counts = {s: sum(1 for c in cov["checks"] if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
    print("mobile flows: %d covered, %d not applicable, %d not run" % (counts["covered"], counts["not_applicable"], counts["not_run"]))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="validate a flow spec and create coverage")
    p.add_argument("spec")
    p.set_defaults(func=cmd_plan)
    m = sub.add_parser("maestro", help="generate a Maestro YAML flow")
    m.add_argument("flow_id")
    m.add_argument("--out", required=True)
    m.add_argument("--app", help="the app id, e.g. com.example.app")
    m.set_defaults(func=cmd_maestro)
    r = sub.add_parser("result", help="record a flow pass or fail")
    r.add_argument("flow_id")
    r.add_argument("outcome", choices=["pass", "fail"])
    r.add_argument("--evidence", required=True)
    r.add_argument("--note")
    r.add_argument("--title")
    r.add_argument("--severity", choices=ledger.SEVERITIES)
    r.add_argument("--repro")
    r.add_argument("--fix")
    r.add_argument("--verify", action="store_true")
    r.set_defaults(func=cmd_result)
    s = sub.add_parser("skip", help="mark a flow not applicable")
    s.add_argument("flow_id")
    s.add_argument("--note", required=True)
    s.set_defaults(func=cmd_skip)
    sub.add_parser("status", help="coverage and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
