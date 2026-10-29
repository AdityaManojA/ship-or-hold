#!/usr/bin/env python3
"""Work through the web security checklist, recording coverage and verified findings.

Standard library only, no network. This tool does the bookkeeping for a defensive
security review of an app you own or are authorised to test: it tracks which
weakness classes you have looked at, records each finding with its evidence, holds
findings to a verification gate before they count, and runs a few safe, read-only
analyses on responses and assets you captured. It never attacks a target itself.

    python3 secweb.py classes [--json]
    python3 secweb.py start
    python3 secweb.py cover authz-object --note "checked with two test accounts" --evidence evidence/proof-sec-web/idor.txt
    python3 secweb.py na clickjacking --note "no framing surface; pure API"
    python3 secweb.py finding add authz-object --title "..." --severity critical --evidence <path> --repro "..."
    python3 secweb.py finding verify <id> --by evidence-file
    python3 secweb.py finding reject <id> --reason "..."
    python3 secweb.py scan-headers evidence/proof-sec-web/response.txt
    python3 secweb.py status
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-sec-web"
CLASSES_DOC = os.path.join(HERE, "attack-classes.md")


def _classes():
    """Parse the machine-readable class list from attack-classes.md (single source of truth)."""
    with open(CLASSES_DOC, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S)
    if not m:
        raise SystemExit("attack-classes.md has no JSON class list")
    return json.loads(m.group(1))


def _require_authorised(dir_):
    """Refuse active work unless the target is declared owned/authorised.

    A read-only analysis of a file you already captured is always allowed; this
    guard is for the skill's active steps and is called by the SKILL workflow, not
    by the pure-analysis commands.
    """
    t = ledger.load_target(dir_)
    if t is None:
        return  # no target yet; proof-plan will set authorisation. The SKILL tells the model to run it first.
    if not t.get("authorised"):
        raise SystemExit("target.json says authorised: false. Confirm you own this target or have written "
                         "permission to test it, set authorised: true via /proof-plan, then continue.")


# ------------------------------------------------------------------ classes

def cmd_classes(a):
    classes = _classes()
    if a.json:
        print(json.dumps(classes, indent=2))
        return 0
    group = None
    for c in classes:
        if c["group"] != group:
            group = c["group"]
            print("\n[%s]" % group)
        print("  %-18s %s" % (c["id"], c["title"]))
    print("\n%d classes. Start the ledger with: secweb.py start" % len(classes))
    return 0


def cmd_start(a):
    classes = _classes()
    ledger.init_coverage(a.dir, SKILL, [(c["id"], c["title"]) for c in classes])
    print("%s coverage ledger created with %d classes, all not-run." % (ledger.OK, len(classes)))
    print("Work through them: cover <id> (looked, with evidence), na <id> (not applicable, with a note).")
    return 0


def _valid_class(cid):
    if cid not in {c["id"] for c in _classes()}:
        raise SystemExit("no class %r. See: secweb.py classes" % cid)


def cmd_cover(a):
    _valid_class(a.class_id)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.class_id, "covered", note=a.note,
                             evidence=[a.evidence] if a.evidence else None)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked covered." % (ledger.OK, a.class_id))
    return 0


def cmd_na(a):
    _valid_class(a.class_id)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.class_id, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked not applicable." % (ledger.OK, a.class_id))
    return 0


# ----------------------------------------------------------------- findings

def cmd_finding_add(a):
    _valid_class(a.class_id)
    f = ledger.new_finding(SKILL, a.title, a.severity, category=a.class_id,
                           location=a.location, repro=a.repro, fix=a.fix,
                           evidence=[a.evidence] if a.evidence else [],
                           references=["OWASP WSTG"] if a.wstg is None else ["OWASP %s" % a.wstg])
    ledger.write_finding(a.dir, f)
    # Recording a finding means we looked at the class.
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov and any(c["id"] == a.class_id for c in cov["checks"]):
        try:
            ledger.mark_coverage(a.dir, SKILL, a.class_id, "covered",
                                 evidence=[a.evidence] if a.evidence else None)
        except ledger.LedgerError:
            pass
    print("%s finding %r written (unverified). Verify it before it counts:" % (ledger.BAD, f["id"]))
    print("   secweb.py finding verify %s --by evidence-file" % f["id"])
    return 0


def cmd_finding_verify(a):
    try:
        ledger.verify(a.dir, SKILL, a.id, by=a.by, note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s finding %r verified by %s." % (ledger.OK, a.id, a.by))
    return 0


def cmd_finding_reject(a):
    try:
        ledger.reject(a.dir, SKILL, a.id, a.reason)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s finding %r marked a false positive." % (ledger.OK, a.id))
    return 0


# -------------------------------------------------------------- scan-headers

# A read-only analysis of an HTTP response the user already captured to a file.
# It sends nothing and connects to nothing.
_WANTED = {
    "content-security-policy": "Stops many script-injection and framing problems.",
    "x-content-type-options": "Should be 'nosniff' so the browser does not guess content types.",
    "strict-transport-security": "Forces HTTPS for future visits.",
    "x-frame-options": "Blocks the page being framed (clickjacking), unless the CSP does it.",
    "referrer-policy": "Limits what is leaked in the Referer header.",
}


def _parse_headers(text):
    headers, cookies = {}, []
    for line in text.splitlines():
        if not line.strip():
            break  # headers end at the first blank line
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name == "set-cookie":
            cookies.append(value)
        else:
            headers[name] = value
    return headers, cookies


def cmd_scan_headers(a):
    with open(a.response, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    headers, cookies = _parse_headers(text)
    notes = []
    for name, why in _WANTED.items():
        if name not in headers:
            if name == "x-frame-options" and "frame-ancestors" in headers.get("content-security-policy", ""):
                continue
            notes.append("missing %s - %s" % (name, why))
    if headers.get("x-content-type-options", "").lower() not in ("", "nosniff") and "x-content-type-options" in headers:
        notes.append("x-content-type-options should be 'nosniff'")
    acao = headers.get("access-control-allow-origin")
    if acao == "*" and headers.get("access-control-allow-credentials", "").lower() == "true":
        notes.append("CORS allows any origin with credentials, which is unsafe")
    for c in cookies:
        low = c.lower()
        first = c.split("=", 1)[0].strip()
        if "httponly" not in low:
            notes.append("cookie %r has no HttpOnly flag" % first)
        if "secure" not in low:
            notes.append("cookie %r has no Secure flag" % first)
        if "samesite" not in low:
            notes.append("cookie %r has no SameSite attribute" % first)
    result = {"checked": a.response, "headers_found": sorted(headers), "cookies": len(cookies), "notes": notes}
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        print("Checked %s: %d headers, %d cookies" % (a.response, len(headers), len(cookies)))
        if not notes:
            print("%s no header or cookie problems found in this response." % ledger.OK)
        for n in notes:
            print("  %s %s" % (ledger.BAD, n))
        print("\nThis is a read-only look at one captured response. Record real problems with: "
              "secweb.py finding add headers ...")
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no coverage ledger yet. Run: secweb.py start")
        return 0
    counts = {s: sum(1 for c in cov["checks"] if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
    print("classes: %d covered, %d not applicable, %d not run" % (counts["covered"], counts["not_applicable"], counts["not_run"]))
    for c in cov["checks"]:
        if c["status"] == "not_run":
            print("  ... %s: %s" % (c["id"], c["title"]))
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

    c = sub.add_parser("classes", help="list the weakness classes")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_classes)

    sub.add_parser("start", help="create the coverage ledger").set_defaults(func=cmd_start)

    cv = sub.add_parser("cover", help="mark a class looked at (with evidence)")
    cv.add_argument("class_id")
    cv.add_argument("--note")
    cv.add_argument("--evidence")
    cv.set_defaults(func=cmd_cover)

    na = sub.add_parser("na", help="mark a class not applicable (with a reason)")
    na.add_argument("class_id")
    na.add_argument("--note", required=True)
    na.set_defaults(func=cmd_na)

    fd = sub.add_parser("finding", help="add, verify or reject a finding")
    fsub = fd.add_subparsers(dest="faction", required=True)
    fa = fsub.add_parser("add")
    fa.add_argument("class_id")
    fa.add_argument("--title", required=True)
    fa.add_argument("--severity", required=True, choices=ledger.SEVERITIES)
    fa.add_argument("--evidence")
    fa.add_argument("--repro")
    fa.add_argument("--location")
    fa.add_argument("--fix")
    fa.add_argument("--wstg", help="the OWASP WSTG id, e.g. WSTG-ATHZ-04")
    fa.set_defaults(func=cmd_finding_add)
    fv = fsub.add_parser("verify")
    fv.add_argument("id")
    fv.add_argument("--by", required=True, choices=["evidence-file", "repro-run", "manual"])
    fv.add_argument("--note")
    fv.set_defaults(func=cmd_finding_verify)
    fr = fsub.add_parser("reject")
    fr.add_argument("id")
    fr.add_argument("--reason", required=True)
    fr.set_defaults(func=cmd_finding_reject)

    sh = sub.add_parser("scan-headers", help="read-only check of a captured HTTP response")
    sh.add_argument("response", help="a file holding a captured HTTP response (headers)")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_scan_headers)

    sub.add_parser("status", help="coverage and findings so far").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
