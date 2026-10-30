#!/usr/bin/env python3
"""The pre-launch readiness gate: track each launch item with evidence, and record what is not ready.

Standard library only, no network. This tool does the bookkeeping for a launch
readiness review: which operational items (alerts, backups, rollback, access,
traffic, people, legal) have been proven with an evidence file, which do not apply,
and which are not ready yet, recorded as findings that must be verified before they
count. It checks nothing on a live system itself.

    python3 release.py list [--json]
    python3 release.py start [--date 2026-11-12]
    python3 release.py done restore-tested --evidence evidence/proof-release/restore.log [--note "..."]
    python3 release.py na status-comms --note "internal tool; users are told in the team chat"
    python3 release.py gap alerts-reach-human --title "Alerts go to an unread shared inbox" [--severity high]
    python3 release.py verify <finding-id> --by manual --note "seen by Sam on 2026-11-01"
    python3 release.py reject <finding-id> --reason "..."
    python3 release.py status [--json]
"""

import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-release"
CHECKLIST_DOC = os.path.join(HERE, "checklist.md")


def items():
    """Parse the machine-readable item list from checklist.md (single source of truth)."""
    with open(CHECKLIST_DOC, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S)
    if not m:
        raise SystemExit("checklist.md has no JSON item list")
    out = json.loads(m.group(1))
    for it in out:
        for key in ("id", "title", "group", "critical"):
            if key not in it:
                raise SystemExit("checklist.md item %r has no %r" % (it.get("id"), key))
    return out


def _item(iid):
    for it in items():
        if it["id"] == iid:
            return it
    raise SystemExit("no checklist item %r. See: release.py list" % iid)


def _parse_date(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise SystemExit("dates look like YYYY-MM-DD, got %r" % text)


def days_until(launch, today=None):
    """Whole days from today to the launch date; negative once it has passed."""
    today = today or datetime.date.today()
    return (_parse_date(launch) - today).days


def _coverage_or_exit(d):
    cov = ledger.load_coverage(d, SKILL)
    if cov is None:
        raise SystemExit("no release ledger yet. Run: release.py start")
    return cov


# --------------------------------------------------------------------- list

def cmd_list(a):
    its = items()
    if a.json:
        print(json.dumps(its, indent=2))
        return 0
    group = None
    for it in its:
        if it["group"] != group:
            group = it["group"]
            print("\n[%s]" % group)
        print("  %-22s %s%s" % (it["id"], it["title"], "  (critical)" if it["critical"] else ""))
    crit = sum(1 for it in its if it["critical"])
    print("\n%d items, %d critical. Start the ledger with: release.py start" % (len(its), crit))
    return 0


# -------------------------------------------------------------------- start

def cmd_start(a):
    its = items()
    previous = ledger.load_coverage(a.dir, SKILL)
    launch = previous.get("launch_date") if previous else None
    if a.date:
        _parse_date(a.date)
        launch = a.date
    ledger.init_coverage(a.dir, SKILL, [(it["id"], it["title"]) for it in its])
    if launch:
        # The coverage file allows extra keys; mark_coverage keeps them on rewrite.
        cov = ledger.load_coverage(a.dir, SKILL)
        cov["launch_date"] = launch
        with open(ledger.coverage_path(a.dir, SKILL), "w", encoding="utf-8") as fh:
            json.dump(cov, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
    print("%s release ledger created with %d items (%d critical); any already recorded are kept."
          % (ledger.OK, len(its), sum(1 for it in its if it["critical"])))
    if launch:
        print("   planned launch: %s (%s)" % (launch, _days_text(days_until(launch))))
    print("Prove each item: done <id> --evidence <file under proof/>, na <id> --note, or gap <id> --title.")
    return 0


# ------------------------------------------------------------- done / na

def cmd_done(a):
    _item(a.item_id)
    _coverage_or_exit(a.dir)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.item_id, "covered", note=a.note, evidence=[a.evidence])
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s done, evidence %s." % (ledger.OK, a.item_id, a.evidence))
    return 0


def cmd_na(a):
    _item(a.item_id)
    _coverage_or_exit(a.dir)
    if not (a.note or "").strip():
        raise SystemExit("marking %s not applicable needs a note saying why" % a.item_id)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.item_id, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked not applicable." % (ledger.OK, a.item_id))
    return 0


# ---------------------------------------------------------------- findings

def default_severity(item):
    return "high" if item["critical"] else "medium"


def cmd_gap(a):
    it = _item(a.item_id)
    severity = a.severity or default_severity(it)
    fid = a.id or ledger.slug("%s %s" % (it["id"], a.title))
    try:
        if os.path.exists(ledger.finding_path(a.dir, SKILL, fid)):
            raise SystemExit("finding %r already exists; pass --id to record a separate one" % fid)
        f = ledger.new_finding(SKILL, a.title, severity, id=fid, category="release:%s" % it["group"],
                               location="checklist:%s" % it["id"],
                               description="Launch item not ready: %s" % it["title"],
                               fix=a.fix, evidence=[a.evidence] if a.evidence else [])
        ledger.write_finding(a.dir, f)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s gap %r recorded for %s (%s, unverified). Verify it before it counts:"
          % (ledger.BAD, fid, it["id"], severity))
    print("   release.py verify %s --by manual --note \"who confirmed it and how\"" % fid)
    print("Once fixed, prove the item with: release.py done %s --evidence <file>" % it["id"])
    return 0


def cmd_verify(a):
    try:
        ledger.verify(a.dir, SKILL, a.id, by=a.by, note=a.note,
                      evidence=[a.evidence] if a.evidence else None)
    except (ledger.LedgerError, OSError) as e:
        raise SystemExit(str(e))
    print("%s gap %r verified by %s." % (ledger.OK, a.id, a.by))
    return 0


def cmd_reject(a):
    try:
        ledger.reject(a.dir, SKILL, a.id, a.reason)
    except (ledger.LedgerError, OSError) as e:
        raise SystemExit(str(e))
    print("%s gap %r marked a false positive." % (ledger.OK, a.id))
    return 0


# ------------------------------------------------------------------ status

def _days_text(n):
    if n > 0:
        return "%d day%s to launch" % (n, "" if n == 1 else "s")
    if n == 0:
        return "launch is today"
    return "launch date passed %d day%s ago" % (-n, "" if n == -1 else "s")


def status_rows(d):
    """Every checklist item with its state, critical not-done items first.

    Order: critical items not done, then other items not done, then items done or
    not applicable; checklist order (and so group order) within each band.
    """
    cov = _coverage_or_exit(d)
    state = {c["id"]: c for c in cov["checks"]}
    gaps = {}
    for f in ledger.load_findings(d, SKILL):
        loc = f.get("location", "")
        if loc.startswith("checklist:") and f["status"] != "false_positive":
            gaps.setdefault(loc.split(":", 1)[1], []).append(f["id"])
    rows = []
    for pos, it in enumerate(items()):
        c = state.get(it["id"], {})
        status = c.get("status", "not_run")
        done = status in ("covered", "not_applicable")
        band = 2 if done else (0 if it["critical"] else 1)
        rows.append({"id": it["id"], "title": it["title"], "group": it["group"],
                     "critical": it["critical"], "status": status, "gaps": gaps.get(it["id"], []),
                     "note": c.get("note"), "evidence": c.get("evidence", []), "_key": (band, pos)})
    rows.sort(key=lambda r: r["_key"])
    for r in rows:
        r.pop("_key")
    return rows, cov.get("launch_date")


def cmd_status(a):
    if ledger.load_coverage(a.dir, SKILL) is None:
        print("no release ledger yet. Run: release.py start")
        return 0
    rows, launch = status_rows(a.dir)
    today = _parse_date(a.today) if a.today else None
    days = days_until(launch, today) if launch else None
    blocking = [r for r in rows if r["critical"] and r["status"] == "not_run"]
    if a.json:
        print(json.dumps({"launch_date": launch, "days_remaining": days,
                          "critical_not_done": [r["id"] for r in blocking], "items": rows}, indent=2))
        return 0
    if launch:
        print("planned launch: %s (%s)" % (launch, _days_text(days)))
    done = sum(1 for r in rows if r["status"] == "covered")
    na = sum(1 for r in rows if r["status"] == "not_applicable")
    print("items: %d done, %d not applicable, %d not done" % (done, na, len(rows) - done - na))
    if blocking:
        print("\nCRITICAL, not done (these block the launch):")
        for r in blocking:
            print("  %s %-22s %s%s" % (ledger.BAD, r["id"], r["title"],
                                       "  gaps: " + ", ".join(r["gaps"]) if r["gaps"] else ""))
    else:
        print("\n%s every critical item is done or not applicable." % ledger.OK)
    rest = [r for r in rows if r not in blocking]
    group = None
    for r in rest:
        if r["group"] != group:
            group = r["group"]
            print("\n[%s]" % group)
        mark = {"covered": ledger.OK, "not_applicable": "n/a", "not_run": "..."}[r["status"]]
        print("  %-3s %-22s %s%s" % (mark, r["id"], r["title"],
                                     "  gaps: " + ", ".join(r["gaps"]) if r["gaps"] else ""))
    findings = ledger.load_findings(a.dir, SKILL)
    if findings:
        print("\ngaps recorded:")
        for f in findings:
            print("  [%s] %s (%s) %s" % (f["severity"], f["title"], f["status"], f["id"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof", help="the proof/ folder (default: proof)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="list the launch-readiness items")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    st = sub.add_parser("start", help="create the release ledger with every item not run")
    st.add_argument("--date", help="planned launch date, YYYY-MM-DD")
    st.set_defaults(func=cmd_start)

    dn = sub.add_parser("done", help="mark an item proven, with an evidence file under proof/")
    dn.add_argument("item_id")
    dn.add_argument("--evidence", required=True, help="a non-empty file under proof/")
    dn.add_argument("--note")
    dn.set_defaults(func=cmd_done)

    na = sub.add_parser("na", help="mark an item not applicable, with a reason")
    na.add_argument("item_id")
    na.add_argument("--note", required=True)
    na.set_defaults(func=cmd_na)

    gp = sub.add_parser("gap", help="record an item that is not ready (an unverified finding)")
    gp.add_argument("item_id")
    gp.add_argument("--title", required=True)
    gp.add_argument("--severity", choices=ledger.SEVERITIES,
                    help="default: high for critical items, medium otherwise")
    gp.add_argument("--id", help="finding id (default: built from the item and title)")
    gp.add_argument("--evidence", help="a file under proof/ that shows the gap")
    gp.add_argument("--fix", help="what would make the item ready")
    gp.set_defaults(func=cmd_gap)

    vf = sub.add_parser("verify", help="verify a recorded gap so it counts")
    vf.add_argument("id")
    vf.add_argument("--by", required=True, choices=["evidence-file", "repro-run", "manual"])
    vf.add_argument("--note")
    vf.add_argument("--evidence", help="add an evidence file under proof/ before verifying")
    vf.set_defaults(func=cmd_verify)

    rj = sub.add_parser("reject", help="mark a recorded gap a false positive")
    rj.add_argument("id")
    rj.add_argument("--reason", required=True)
    rj.set_defaults(func=cmd_reject)

    ss = sub.add_parser("status", help="readiness so far, critical items not done first")
    ss.add_argument("--json", action="store_true")
    ss.add_argument("--today", help="count days from this date instead of today, YYYY-MM-DD")
    ss.set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
