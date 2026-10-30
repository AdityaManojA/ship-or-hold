#!/usr/bin/env python3
"""Compile proof/ into a report and compute the verdict. Standard library only.

    python3 verdict.py                      # reads ./proof, writes proof/report.md + proof/verdict.json
    python3 verdict.py --dir path/to/proof --fail-on medium
    python3 verdict.py --check              # print the verdict; exit 0 Ship, 1 Not yet, 2 Incomplete

The verdict is computed, never written by hand:

    Ship        every required skill reported and covered its checks, no verified
                finding at or above the fail-on severity, every measured score
                within budget
    Not yet     at least one verified finding at or above fail-on, or a score
                over budget (each one is listed with its evidence)
    Incomplete  the verdict cannot rest on anything yet: no target, the ledger
                does not validate, the target is not marked authorised, a
                required skill has not reported, or a required skill still has
                checks it has not run

Only findings with status "verified" count. Unverified findings and false
positives are listed, never counted. A skill the user did not put in
required_skills is optional: not running it changes nothing. If it was run, its
verified findings and scores are evidence like any other and do count.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import validate  # noqa: E402

SHIP, NOT_YET, INCOMPLETE = "Ship", "Not yet", "Incomplete"
EXIT = {SHIP: 0, NOT_YET: 1, INCOMPLETE: 2}
RANK = {s: i for i, s in enumerate(ledger.SEVERITIES)}  # critical=0 ... info=4


def _at_or_above(severity, threshold):
    return RANK[severity] <= RANK[threshold]


def compute(d="proof", fail_on=None):
    """Return a dict with the verdict and everything the report needs."""
    out = {"verdict": None, "checks": [], "blockers": [], "gaps": [], "fail_on": None,
           "target": None, "required": [], "optional_run": [], "optional_not_run": [],
           "findings": [], "coverage": [], "scores": [], "validation": []}

    problems, counts = validate.validate_dir(d)
    out["validation"] = problems
    target = None
    if not os.path.isdir(ledger.root(d)):
        out["gaps"].append("No proof/ folder. Run /proof-plan to describe the target first.")
    elif problems:
        out["gaps"].append("The evidence ledger does not validate (%d problem%s). Fix them, then compile again."
                           % (len(problems), "" if len(problems) == 1 else "s"))
    else:
        target = ledger.load_target(d)

    if target is None and os.path.isdir(ledger.root(d)) and not problems:
        out["gaps"].append("No target.json. Run /proof-plan to describe what is under test.")

    if target is not None:
        out["target"] = target
        out["fail_on"] = fail_on or target.get("fail_on") or "high"
        if not target.get("authorised"):
            out["gaps"].append("target.json says authorised: false. Confirm you own the target or have "
                               "written permission to test it, then set authorised: true.")
        required = list(target.get("required_skills") or [])
        out["required"] = required
        if not required:
            out["gaps"].append("required_skills is empty. Pick at least one skill so the verdict rests on something.")

        reported = ledger.skills_reported(d)
        findings = ledger.load_findings(d)
        coverage = {c["skill"]: c for c in ledger.load_all_coverage(d)}
        scores = ledger.load_all_scores(d)
        out["findings"], out["coverage"], out["scores"] = findings, list(coverage.values()), scores
        out["optional_run"] = [s for s in reported if s not in required]

        for skill in required:
            if skill not in reported:
                out["gaps"].append("%s is required but has not reported. Run /%s." % (skill, skill))
                continue
            cov = coverage.get(skill)
            if cov is None:
                out["gaps"].append("%s is required but has no coverage ledger, so it is not known which checks it ran."
                                   % skill)
                continue
            not_run = [c for c in cov["checks"] if c["status"] == "not_run"]
            if not_run:
                out["gaps"].append("%s still has %d check%s not run: %s. Run them, or mark each not_applicable with a reason."
                                   % (skill, len(not_run), "" if len(not_run) == 1 else "s",
                                      ", ".join(c["id"] for c in not_run[:8]) + (", ..." if len(not_run) > 8 else "")))

        if not out["gaps"]:
            for f in findings:
                if f["status"] == "verified" and _at_or_above(f["severity"], out["fail_on"]):
                    out["blockers"].append({"kind": "finding", "skill": f["skill"], "id": f["id"],
                                            "severity": f["severity"], "title": f["title"],
                                            "evidence": f.get("evidence", []), "repro": f.get("repro")})
            for s in scores:
                for m in s["metrics"]:
                    if not ledger.metric_passes(m):
                        out["blockers"].append({"kind": "score", "skill": s["skill"], "id": m["id"],
                                                "value": m["value"], "unit": m["unit"], "budget": m["budget"],
                                                "op": m["op"], "evidence": m.get("evidence", [])})

    if out["gaps"]:
        out["verdict"] = INCOMPLETE
    elif out["blockers"]:
        out["verdict"] = NOT_YET
    else:
        out["verdict"] = SHIP

    # Human-readable checks, one line each, in the order a reader wants them.
    for g in out["gaps"]:
        out["checks"].append({"ok": False, "text": g})
    if target is not None and not out["gaps"]:
        for skill in out["required"]:
            cov = next((c for c in out["coverage"] if c["skill"] == skill), None)
            covered = sum(1 for c in cov["checks"] if c["status"] == "covered")
            na = sum(1 for c in cov["checks"] if c["status"] == "not_applicable")
            out["checks"].append({"ok": True, "text": "%s reported: %d check%s covered, %d not applicable."
                                  % (skill, covered, "" if covered == 1 else "s", na)})
        verified = [f for f in out["findings"] if f["status"] == "verified"]
        blocking = [b for b in out["blockers"] if b["kind"] == "finding"]
        if blocking:
            for b in blocking:
                out["checks"].append({"ok": False, "text": "%s: %s (%s, verified) %s"
                                      % (b["skill"], b["title"], b["severity"], _ev(b["evidence"]))})
        else:
            below = [f for f in verified if not _at_or_above(f["severity"], out["fail_on"])]
            out["checks"].append({"ok": True, "text": "No verified finding at %s or above%s."
                                  % (out["fail_on"], " (%d below it)" % len(below) if below else "")})
        failing = [b for b in out["blockers"] if b["kind"] == "score"]
        total = sum(len(s["metrics"]) for s in out["scores"])
        if failing:
            for b in failing:
                out["checks"].append({"ok": False, "text": "%s: %s is %s %s, budget %s %s %s"
                                      % (b["skill"], b["id"], _n(b["value"]), b["unit"], b["op"], _n(b["budget"]), _ev(b["evidence"]))})
        elif total:
            out["checks"].append({"ok": True, "text": "All %d measured score%s within budget." % (total, "" if total == 1 else "s")})
    out["counts"] = counts
    out["generated"] = ledger.now()
    return out


def _n(x):
    return ("%d" % x) if float(x).is_integer() else ("%.2f" % x)


def _ev(paths):
    return ("[%s]" % ", ".join(paths)) if paths else "[no evidence listed]"


# ----------------------------------------------------------------- report

def render(r):
    t = r.get("target") or {}
    L = ["# %s · Proof report" % (t.get("name") or "Target"), ""]
    L.append("**Verdict: %s**" % r["verdict"])
    if r.get("fail_on"):
        L.append("")
        L.append("_Blocks Ship: a verified finding at **%s** or above, or a score over budget._" % r["fail_on"])
    L.append("")
    for c in r["checks"]:
        L.append("- %s %s" % ("✓" if c["ok"] else "✗", c["text"]))
    L.append("")

    if t:
        L += ["## Target", ""]
        L.append("- Kind: %s" % t.get("kind"))
        if t.get("urls"):
            L.append("- URLs: %s" % ", ".join(t["urls"]))
        if t.get("repo"):
            L.append("- Repo: %s" % t["repo"])
        if t.get("done_means"):
            L.append("- Done means: %s" % t["done_means"])
        L.append("- Required skills: %s" % (", ".join(r["required"]) or "none"))
        L.append("")

    by_skill = {}
    for f in r["findings"]:
        by_skill.setdefault(f["skill"], []).append(f)
    skills = sorted(set(list(by_skill) + [c["skill"] for c in r["coverage"]] + [s["skill"] for s in r["scores"]]))
    for skill in skills:
        tag = "required" if skill in r["required"] else "optional, run anyway"
        L += ["## %s (%s)" % (skill, tag), ""]
        fs = by_skill.get(skill, [])
        for status, heading in (("verified", "Verified"), ("unverified", "Unverified (not counted)"),
                                ("false_positive", "Ruled out")):
            group = [f for f in fs if f["status"] == status]
            if not group:
                continue
            L.append("**%s**" % heading)
            L.append("")
            for f in sorted(group, key=lambda f: RANK[f["severity"]]):
                line = "- `%s` **%s** · %s" % (f["severity"], f["title"], f.get("location") or f.get("category") or "")
                L.append(line.rstrip(" ·"))
                if f.get("repro"):
                    L.append("  - repro: `%s`" % f["repro"])
                if f.get("evidence"):
                    L.append("  - evidence: %s" % ", ".join("`%s`" % p for p in f["evidence"]))
                if status == "verified" and f.get("verified_by"):
                    L.append("  - verified by %s%s" % (f["verified_by"], (": " + f["verified_note"]) if f.get("verified_note") else ""))
                if status == "false_positive" and f.get("verified_note"):
                    L.append("  - why: %s" % f["verified_note"])
                if f.get("fix"):
                    L.append("  - fix: %s" % f["fix"])
            L.append("")
        cov = next((c for c in r["coverage"] if c["skill"] == skill), None)
        if cov:
            counts = {s: sum(1 for c in cov["checks"] if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
            L.append("**Coverage:** %d covered · %d not applicable · %d not run" % (counts["covered"], counts["not_applicable"], counts["not_run"]))
            L.append("")
            for c in cov["checks"]:
                mark = {"covered": "✓", "not_applicable": "–", "not_run": "○"}[c["status"]]
                note = (" - " + c["note"]) if c.get("note") else ""
                L.append("- %s %s: %s%s" % (mark, c["id"], c["title"], note))
            L.append("")
        sc = next((s for s in r["scores"] if s["skill"] == skill), None)
        if sc and sc["metrics"]:
            L += ["| metric | value | budget | |", "| --- | ---: | ---: | --- |"]
            for m in sc["metrics"]:
                L.append("| %s | %s %s | %s %s | %s |" % (m["id"], _n(m["value"]), m["unit"], m["op"], _n(m["budget"]),
                                                          "✓" if ledger.metric_passes(m) else "✗"))
            L.append("")

    missing = [s for s in r["required"] if s not in skills]
    if missing:
        L += ["## Required, not yet run", ""]
        for s in missing:
            L.append("- %s: run /%s" % (s, s))
        L.append("")

    L.append("_Verified findings rest on the evidence files they list; a screenshot shows one moment, a repro "
             "shows one machine. Re-run the repros that matter before relying on this report. Not legal, "
             "security-assurance or financial advice._")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ SARIF

SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
# A CVSS-style 0-10 number per severity, which code-scanning tools use to rank alerts.
SECURITY_SEVERITY = {"critical": "9.5", "high": "7.5", "medium": "5.0", "low": "2.5", "info": "0.0"}
_LOC = re.compile(r"^([^\s:]+\.[A-Za-z0-9]+):(\d+)$")


def to_sarif(r, include_unverified=False):
    """The findings as a SARIF 2.1.0 log. Only verified findings unless asked otherwise."""
    rules, results, seen = [], [], set()
    for f in r["findings"]:
        if f["status"] == "false_positive":
            continue
        if f["status"] != "verified" and not include_unverified:
            continue
        rule_id = "%s/%s" % (f["skill"], f.get("category") or "finding")
        if rule_id not in seen:
            seen.add(rule_id)
            rules.append({"id": rule_id, "name": rule_id.replace("/", "-"),
                          "shortDescription": {"text": "%s: %s" % (f["skill"], f.get("category") or "finding")},
                          "properties": {"security-severity": SECURITY_SEVERITY[f["severity"]],
                                         "tags": [f["skill"]]}})
        loc = f.get("location") or ""
        m = _LOC.match(loc)
        if m:
            physical = {"artifactLocation": {"uri": m.group(1).replace("\\", "/")},
                        "region": {"startLine": int(m.group(2))}}
        else:
            physical = {"artifactLocation": {"uri": "proof/findings/%s/%s.json" % (f["skill"], f["id"])}}
        text = f["title"]
        if f.get("fix"):
            text += " Fix: " + f["fix"]
        results.append({
            "ruleId": rule_id, "level": SARIF_LEVEL[f["severity"]], "message": {"text": text},
            "locations": [{"physicalLocation": physical}],
            "properties": {"severity": f["severity"], "status": f["status"],
                           "security-severity": SECURITY_SEVERITY[f["severity"]],
                           "evidence": f.get("evidence", []), "repro": f.get("repro") or ""},
        })
    return {"version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{"tool": {"driver": {"name": "Proof", "rules": rules}},
                      "results": results,
                      "properties": {"verdict": r["verdict"]}}]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sarif", help="also write the findings as a SARIF 2.1.0 file to this path")
    ap.add_argument("--sarif-all", action="store_true", help="include unverified findings in the SARIF file")
    ap.add_argument("--dir", default="proof")
    ap.add_argument("--fail-on", choices=ledger.SEVERITIES[:-1], help="override target.json fail_on (default high)")
    ap.add_argument("--check", action="store_true", help="print the verdict only; exit 0 Ship, 1 Not yet, 2 Incomplete")
    ap.add_argument("--out", help="default: <dir>/report.md")
    ap.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    ap.add_argument("--json", action="store_true", help="print verdict.json to stdout instead of the summary")
    a = ap.parse_args(argv)
    ledger.console()
    r = compute(a.dir, a.fail_on)
    if a.sarif:
        ledger.guard_out(a.sarif, a.dir, a.force, ours='"name": "Proof"')
        os.makedirs(os.path.dirname(os.path.abspath(a.sarif)), exist_ok=True)
        with open(a.sarif, "w", encoding="utf-8") as fh:
            json.dump(to_sarif(r, a.sarif_all), fh, indent=2)
        print("Wrote SARIF to %s" % a.sarif)
    if a.check:
        print("Verdict: %s" % r["verdict"])
        for c in r["checks"]:
            print("  %s  %s" % (ledger.OK if c["ok"] else ledger.BAD, c["text"]))
        return EXIT[r["verdict"]]
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k not in ("findings", "coverage", "scores")}, indent=2, ensure_ascii=False))
        return EXIT[r["verdict"]]
    out = a.out or os.path.join(a.dir, "report.md")
    ledger.guard_out(out, a.dir, a.force, ours="Proof report")
    if os.path.isdir(a.dir):
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(render(r))
        with open(os.path.join(a.dir, "verdict.json"), "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in r.items() if k not in ("findings", "coverage", "scores")}, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("Verdict: %s. Wrote %s" % (r["verdict"], out))
    else:
        print("Verdict: %s. %s" % (r["verdict"], r["gaps"][0] if r["gaps"] else ""))
    return EXIT[r["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
