#!/usr/bin/env python3
"""Plan a web app's user flows, record each result as ledger evidence.

Standard library only, no network. Every flow becomes a coverage check; a failed
flow becomes a finding backed by a screenshot or a page capture; a passing flow is
only recorded with an evidence file behind it.

    python3 flows.py plan flows.json
    python3 flows.py result checkout pass --evidence evidence/proof-web-e2e/checkout.png
    python3 flows.py result checkout fail --evidence evidence/proof-web-e2e/checkout.png \
        --title "Order submits twice on a double click" --severity high --repro "see steps"
    python3 flows.py skip gift-card --note "no gift cards in this build"
    python3 flows.py edge-cases checkout
    python3 flows.py script checkout --out proof/web/checkout.py --python
    python3 flows.py status
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-web-e2e"

EDGE_CASES = [
    "Submit the form twice quickly (double click, or Enter then click).",
    "Press the browser Back button in the middle of the flow, then go forward.",
    "Refresh the page mid-flow and see whether progress survives.",
    "Let the session expire (or sign out in another tab), then continue.",
    "Throttle the network to slow 3G and watch for lost or duplicated submits.",
    "Leave a required field empty, then one with only spaces.",
    "Paste a very long value, and one with accents and emoji, into each field.",
    "Paste into a field instead of typing (some validation only runs on keypress).",
    "Do the whole flow at a narrow mobile width.",
    "Do the whole flow with the keyboard only, watching focus order.",
    "Cross a date or timezone boundary if the flow uses dates.",
    "Open a second tab and run the flow there at the same time.",
    "Deep-link straight to a late step without doing the earlier ones.",
]


def _flows_path(d):
    return os.path.join(ledger.root(d), "web", "flows.json")


def _load_flows(d):
    path = _flows_path(d)
    if not os.path.exists(path):
        raise SystemExit("no flow plan yet. Run: flows.py plan <flows.json>")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["flows"]


def _default_severity(flow):
    return "high" if flow.get("critical") else "medium"


# -------------------------------------------------------------------- plan

def cmd_plan(a):
    with open(a.spec, encoding="utf-8") as fh:
        data = json.load(fh)
    flows = data.get("flows")
    if not isinstance(flows, list) or not flows:
        raise SystemExit("the spec needs a non-empty 'flows' list")
    seen = set()
    for i, f in enumerate(flows):
        for key in ("id", "title", "start", "steps", "expect"):
            if key not in f:
                raise SystemExit("flow %d is missing '%s'" % (i + 1, key))
        if not isinstance(f["steps"], list) or not f["steps"]:
            raise SystemExit("flow %r needs a non-empty 'steps' list" % f["id"])
        if f["id"] in seen:
            raise SystemExit("duplicate flow id %r" % f["id"])
        seen.add(f["id"])
    os.makedirs(os.path.dirname(_flows_path(a.dir)), exist_ok=True)
    with open(_flows_path(a.dir), "w", encoding="utf-8") as fh:
        json.dump({"flows": flows}, fh, ensure_ascii=False, indent=2)
    ledger.init_coverage(a.dir, SKILL, [(f["id"], f["title"]) for f in flows])
    _write_checklist(a.dir, flows)
    print("%s planned %d flow(s); coverage ledger created. Next: drive each flow, then record it." % (ledger.OK, len(flows)))
    return 0


def _write_checklist(d, flows):
    L = ["# Web end-to-end checklist", ""]
    for f in flows:
        tag = " (critical)" if f.get("critical") else ""
        L.append("## %s - %s%s" % (f["id"], f["title"], tag))
        L.append("")
        L.append("Start: %s" % f["start"])
        L.append("")
        for step in f["steps"]:
            L.append("- [ ] %s" % step)
        L.append("")
        L.append("Expect: %s" % f["expect"])
        L.append("")
        L.append("Evidence file: `evidence/%s/%s.png` (or `.txt` for a page capture)" % (SKILL, f["id"]))
        L.append("")
        L.append("Edge cases to try on this flow:")
        for e in EDGE_CASES:
            L.append("- [ ] %s" % e)
        L.append("")
    path = os.path.join(ledger.root(d), "web", "checklist.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


# ------------------------------------------------------------------ result

def cmd_result(a):
    flows = {f["id"]: f for f in _load_flows(a.dir)}
    if a.flow_id not in flows:
        raise SystemExit("no flow %r in the plan" % a.flow_id)
    flow = flows[a.flow_id]
    if a.outcome == "pass":
        try:
            ledger.mark_coverage(a.dir, SKILL, a.flow_id, "covered",
                                 note=a.note, evidence=[a.evidence])
        except ledger.LedgerError as e:
            raise SystemExit("cannot record a pass: %s" % e)
        print("%s %s passes, evidence recorded." % (ledger.OK, a.flow_id))
        return 0

    # fail
    if not a.title:
        raise SystemExit("a failing flow needs --title saying what went wrong")
    severity = a.severity or _default_severity(flow)
    if not a.severity:
        print("(no --severity given; using %s because the flow is %s)"
              % (severity, "critical" if flow.get("critical") else "non-critical"))
    f = ledger.new_finding(SKILL, a.title, severity, category="flow:%s" % a.flow_id,
                           location=a.location or flow["start"], repro=a.repro,
                           evidence=[a.evidence], fix=a.fix)
    ledger.write_finding(a.dir, f)
    # We did look at this flow, so it is covered even though it failed.
    try:
        ledger.mark_coverage(a.dir, SKILL, a.flow_id, "covered", evidence=[a.evidence])
    except ledger.LedgerError as e:
        raise SystemExit("recorded the finding but could not mark coverage: %s" % e)
    if a.verify:
        try:
            ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
            print("%s %s: finding %r written and verified by its evidence file." % (ledger.BAD, a.flow_id, f["id"]))
        except ledger.LedgerError as e:
            raise SystemExit("finding written but could not verify: %s" % e)
    else:
        print("%s %s: finding %r written (unverified). Verify it before it counts: add evidence and re-run with --verify."
              % (ledger.BAD, a.flow_id, f["id"]))
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


def cmd_edge_cases(a):
    if a.flow_id:
        flows = {f["id"]: f for f in _load_flows(a.dir)}
        if a.flow_id not in flows:
            raise SystemExit("no flow %r in the plan" % a.flow_id)
        print("Edge cases to try on %r:\n" % a.flow_id)
    else:
        print("Edge cases to try on any flow:\n")
    for e in EDGE_CASES:
        print("  - " + e)
    return 0


# ------------------------------------------------------------------ script

_PY_TEMPLATE = '''\
"""Starter browser script for the %(id)s flow. Fill in each TODO.

Needs a browser automation library (for example Playwright). If none is installed,
drive this flow with the built-in browser tools instead and save the same evidence.

    python3 %(name)s
"""
import sys

START = %(start)r
EVIDENCE_PNG = %(png)r
EVIDENCE_TXT = %(txt)r
STEPS = %(steps)r


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Drive this flow with the built-in browser "
              "tools and save a screenshot to", EVIDENCE_PNG)
        return 2
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(START, wait_until="networkidle")
            for step in STEPS:
                pass  # TODO: perform this step - %%s
            # TODO: assert the expected end state; raise on failure.
            page.screenshot(path=EVIDENCE_PNG, full_page=True)
            with open(EVIDENCE_TXT, "w", encoding="utf-8") as fh:
                fh.write(page.inner_text("body"))
        except Exception as e:
            page.screenshot(path=EVIDENCE_PNG, full_page=True)
            print("FAILED:", e)
            return 1
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_NODE_TEMPLATE = '''\
// Starter browser script for the %(id)s flow. Fill in each TODO.
// Needs a browser automation library (for example @playwright/test's chromium).
// If none is installed, drive this flow with the built-in browser tools instead.
const START = %(start)s;
const EVIDENCE_PNG = %(png)s;
const EVIDENCE_TXT = %(txt)s;
const STEPS = %(steps)s;

(async () => {
  let chromium;
  try { ({ chromium } = require('playwright')); }
  catch (e) { console.log('Playwright not installed. Use the built-in browser tools and save', EVIDENCE_PNG); process.exit(2); }
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const fs = require('fs');
  try {
    await page.goto(START, { waitUntil: 'networkidle' });
    for (const step of STEPS) { /* TODO: perform step */ }
    // TODO: assert the expected end state; throw on failure.
    await page.screenshot({ path: EVIDENCE_PNG, fullPage: true });
    fs.writeFileSync(EVIDENCE_TXT, await page.innerText('body'));
  } catch (e) {
    await page.screenshot({ path: EVIDENCE_PNG, fullPage: true });
    console.log('FAILED:', e.message); process.exitCode = 1;
  } finally { await browser.close(); }
})();
'''


def cmd_script(a):
    flows = {f["id"]: f for f in _load_flows(a.dir)}
    if a.flow_id not in flows:
        raise SystemExit("no flow %r in the plan" % a.flow_id)
    flow = flows[a.flow_id]
    png = "evidence/%s/%s.png" % (SKILL, a.flow_id)
    txt = "evidence/%s/%s.txt" % (SKILL, a.flow_id)
    if a.node:
        body = _NODE_TEMPLATE % {"id": a.flow_id, "start": json.dumps(flow["start"]),
                                 "png": json.dumps(png), "txt": json.dumps(txt),
                                 "steps": json.dumps(flow["steps"])}
    else:
        body = _PY_TEMPLATE % {"id": a.flow_id, "name": os.path.basename(a.out),
                               "start": flow["start"], "png": png, "txt": txt, "steps": flow["steps"]}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("%s wrote starter script %s (fill in the TODOs; it saves evidence to %s)" % (ledger.OK, a.out, png))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no flow plan yet. Run: flows.py plan <flows.json>")
        return 0
    counts = {s: sum(1 for c in cov["checks"] if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
    print("flows: %d covered, %d not applicable, %d not run" % (counts["covered"], counts["not_applicable"], counts["not_run"]))
    for c in cov["checks"]:
        print("  %s %s: %s" % ({"covered": ledger.OK, "not_applicable": "--", "not_run": "..."}[c["status"]], c["id"], c["title"]))
    findings = ledger.load_findings(a.dir, SKILL)
    if findings:
        print("findings:")
        for f in findings:
            print("  [%s] %s (%s) %s" % (f["severity"], f["title"], f["status"], f["id"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="validate a flow spec and create the coverage ledger")
    p.add_argument("spec")
    p.set_defaults(func=cmd_plan)

    r = sub.add_parser("result", help="record a flow pass or fail")
    r.add_argument("flow_id")
    r.add_argument("outcome", choices=["pass", "fail"])
    r.add_argument("--evidence", required=True, help="path under proof/ to a screenshot or page capture")
    r.add_argument("--note")
    r.add_argument("--title", help="what went wrong (required for fail)")
    r.add_argument("--severity", choices=ledger.SEVERITIES)
    r.add_argument("--repro")
    r.add_argument("--location")
    r.add_argument("--fix")
    r.add_argument("--verify", action="store_true", help="also verify the finding by its evidence file")
    r.set_defaults(func=cmd_result)

    s = sub.add_parser("skip", help="mark a flow not applicable")
    s.add_argument("flow_id")
    s.add_argument("--note", required=True)
    s.set_defaults(func=cmd_skip)

    e = sub.add_parser("edge-cases", help="print edge cases to try")
    e.add_argument("flow_id", nargs="?")
    e.set_defaults(func=cmd_edge_cases)

    sc = sub.add_parser("script", help="write a starter browser script for a flow")
    sc.add_argument("flow_id")
    sc.add_argument("--out", required=True)
    g = sc.add_mutually_exclusive_group()
    g.add_argument("--python", action="store_true")
    g.add_argument("--node", action="store_true")
    sc.set_defaults(func=cmd_script)

    sub.add_parser("status", help="coverage and findings so far").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
