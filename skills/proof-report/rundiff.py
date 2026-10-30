#!/usr/bin/env python3
"""Compare two Proof runs: what got fixed, what is new, and what moved.

Standard library only. `save` stores a compact snapshot of the current run under
proof/runs/; `compare` lines up two snapshots (by default the last two) and reports
the verdict change, findings fixed and newly found, severity changes, and every
score that moved, with the percentage.

    python3 rundiff.py save --label "before fix"
    python3 rundiff.py compare                     # the last two snapshots
    python3 rundiff.py compare runs/a.json runs/b.json --json
    python3 rundiff.py list
"""

import argparse
import datetime
import json
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

_spec = importlib.util.spec_from_file_location("proof_verdict", os.path.join(HERE, "verdict.py"))
verdict = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verdict)


def _runs_dir(d):
    return os.path.join(ledger.root(d), "runs")


def snapshot(d, label=None, fail_on=None):
    r = verdict.compute(d, fail_on)
    return {
        "taken": ledger.now(), "label": label or "", "verdict": r["verdict"],
        "findings": {"%s/%s" % (f["skill"], f["id"]): {"severity": f["severity"], "title": f["title"]}
                     for f in r["findings"] if f["status"] == "verified"},
        "scores": {"%s/%s" % (s["skill"], m["id"]): {"value": m["value"], "unit": m["unit"],
                                                     "budget": m["budget"], "op": m["op"]}
                   for s in r["scores"] for m in s["metrics"]},
        "gaps": len(r["gaps"]),
    }


def diff(a, b):
    fa, fb = a["findings"], b["findings"]
    out = {"from": {"label": a["label"], "taken": a["taken"], "verdict": a["verdict"]},
           "to": {"label": b["label"], "taken": b["taken"], "verdict": b["verdict"]},
           "fixed": sorted(k for k in fa if k not in fb),
           "new": sorted(k for k in fb if k not in fa),
           "severity_changed": sorted(k for k in fa if k in fb and fa[k]["severity"] != fb[k]["severity"]),
           "scores": []}
    for k in sorted(set(a["scores"]) | set(b["scores"])):
        va, vb = a["scores"].get(k), b["scores"].get(k)
        if va is None or vb is None:
            out["scores"].append({"id": k, "from": va and va["value"], "to": vb and vb["value"],
                                  "change": "added" if va is None else "removed"})
            continue
        if va["value"] == vb["value"]:
            continue
        pct = ((vb["value"] - va["value"]) / abs(va["value"]) * 100.0) if va["value"] else None
        out["scores"].append({"id": k, "from": va["value"], "to": vb["value"], "unit": vb["unit"],
                              "percent": round(pct, 1) if pct is not None else None,
                              "passes_now": ledger.metric_passes(vb), "passed_before": ledger.metric_passes(va)})
    out["_findings_a"], out["_findings_b"] = fa, fb
    return out


def render(dd):
    fa, fb = dd.pop("_findings_a"), dd.pop("_findings_b")
    L = ["%s (%s) -> %s (%s)" % (dd["from"]["verdict"], dd["from"]["label"] or dd["from"]["taken"],
                                 dd["to"]["verdict"], dd["to"]["label"] or dd["to"]["taken"])]
    L.append("%d fixed, %d new, %d changed severity, %d score(s) moved" % (
        len(dd["fixed"]), len(dd["new"]), len(dd["severity_changed"]), len(dd["scores"])))
    for k in dd["fixed"]:
        L.append("  %s fixed  [%s] %s" % (ledger.OK, fa[k]["severity"], fa[k]["title"]))
    for k in dd["new"]:
        L.append("  %s new    [%s] %s" % (ledger.BAD, fb[k]["severity"], fb[k]["title"]))
    for k in dd["severity_changed"]:
        L.append("  ~  %s: %s -> %s" % (fb[k]["title"], fa[k]["severity"], fb[k]["severity"]))
    for s in dd["scores"]:
        if "change" in s:
            L.append("  ~  %s %s" % (s["id"], s["change"]))
            continue
        pct = (" (%+.1f%%)" % s["percent"]) if s["percent"] is not None else ""
        state = ""
        if s["passes_now"] != s["passed_before"]:
            state = " now within budget" if s["passes_now"] else " now over budget"
        L.append("  ~  %s: %s -> %s %s%s%s" % (s["id"], verdict._n(s["from"]), verdict._n(s["to"]),
                                                 s["unit"], pct, state))
    return "\n".join(L)


def _list(d):
    folder = _runs_dir(d)
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".json"))


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_save(a):
    if not os.path.isdir(ledger.root(a.dir)):
        print("error: no %s folder" % a.dir, file=sys.stderr)
        return 2
    snap = snapshot(a.dir, a.label, a.fail_on)
    os.makedirs(_runs_dir(a.dir), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(_runs_dir(a.dir), "%s-%d.json" % (stamp, len(_list(a.dir)) + 1))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)
    print("%s saved run %s: %s, %d verified finding(s)" % (ledger.OK, os.path.basename(path), snap["verdict"],
                                                          len(snap["findings"])))
    return 0


def cmd_compare(a):
    if a.a and a.b:
        sa, sb = _load(a.a), _load(a.b)
    else:
        runs = _list(a.dir)
        if len(runs) < 2:
            print("need two saved runs (have %d). Run: rundiff.py save" % len(runs), file=sys.stderr)
            return 2
        sa, sb = _load(runs[-2]), _load(runs[-1])
    dd = diff(sa, sb)
    if a.json:
        dd.pop("_findings_a")
        dd.pop("_findings_b")
        print(json.dumps(dd, indent=2))
    else:
        print(render(dd))
    return 0


def cmd_list(a):
    for p in _list(a.dir):
        s = _load(p)
        print("  %s  %-10s %3d finding(s)  %s" % (os.path.basename(p), s["verdict"], len(s["findings"]), s["label"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save", help="store a snapshot of the current run")
    s.add_argument("--label")
    s.add_argument("--fail-on", choices=ledger.SEVERITIES[:-1])
    s.set_defaults(func=cmd_save)
    c = sub.add_parser("compare", help="compare two snapshots (default: the last two)")
    c.add_argument("a", nargs="?")
    c.add_argument("b", nargs="?")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_compare)
    sub.add_parser("list", help="saved snapshots").set_defaults(func=cmd_list)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
