#!/usr/bin/env python3
"""Import LLM evaluation results (promptfoo, garak) into the Proof ledger.

Standard library only, no network. Run the evaluation tool yourself against your
own app's model feature, then import its output here: each failed test becomes a
finding with the raw report as evidence, and the pass rate becomes a score with a
budget, so it counts in the verdict.

    promptfoo eval -o promptfoo.json        # then:
    python3 eval_import.py promptfoo promptfoo.json --min-pass 0.95
    garak ... (writes a report .jsonl)      # then:
    python3 eval_import.py garak garak.report.jsonl --min-pass 0.95
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-ai"
MAX_FINDINGS = 25


def _copy(d, path, name):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), name)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


def _ensure_check(d, cid, title):
    cov = ledger.load_coverage(d, SKILL)
    checks = [(c["id"], c["title"]) for c in cov["checks"]] if cov else []
    if not any(c == cid for c, _ in checks):
        ledger.init_coverage(d, SKILL, checks + [(cid, title)])


def _find_results(obj):
    """promptfoo output layouts vary by version: find the list of per-test results."""
    if isinstance(obj, list) and obj and all(isinstance(x, dict) and "success" in x for x in obj):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            got = _find_results(v)
            if got is not None:
                return got
    return None


def parse_promptfoo(path):
    with open(path, encoding="utf-8-sig") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            raise SystemExit("%s is not JSON" % path)
    results = _find_results(data)
    if results is None:
        raise SystemExit("could not find test results in %s; is it promptfoo -o output?" % path)
    fails = []
    for r in results:
        if r.get("success"):
            continue
        tc = r.get("testCase") or {}
        desc = tc.get("description") or ((r.get("prompt") or {}).get("label")) or "test"
        reason = (r.get("gradingResult") or {}).get("reason") or r.get("error") or "assertion failed"
        fails.append({"name": str(desc), "reason": " ".join(str(reason).split())[:200]})
    return len(results), fails


def parse_garak(path):
    total = passed = 0
    fails = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("entry_type") != "eval":
                continue
            t = int(num.parse_number(e.get("total", 0), field="total"))
            p = int(num.parse_number(e.get("passed", 0), field="passed"))
            total += t
            passed += p
            if p < t:
                fails.append({"name": "%s / %s" % (e.get("probe", "probe"), e.get("detector", "detector")),
                              "reason": "%d of %d attempts got through" % (t - p, t)})
    if total == 0:
        raise SystemExit("no eval entries in %s; is it a garak report .jsonl?" % path)
    return total, passed, fails


def _record(d, tool, rel, total, passed, fails, min_pass, verify):
    cid = "eval-%s" % tool
    _ensure_check(d, cid, "Automated evaluation with %s" % tool)
    rate = passed / float(total) if total else 0.0
    ledger.mark_coverage(d, SKILL, cid, "covered", evidence=[rel],
                         note="%d of %d passed (%.1f%%)" % (passed, total, rate * 100))
    existing = [s for s in ledger.load_all_scores(d) if s["skill"] == SKILL]
    metrics = [m for m in (existing[0]["metrics"] if existing else []) if m["id"] != "%s_pass_rate" % tool]
    metrics.append({"id": "%s_pass_rate" % tool, "value": round(rate, 4), "unit": "fraction",
                    "budget": min_pass, "op": ">=", "source": tool, "evidence": [rel]})
    ledger.write_scores(d, SKILL, metrics)
    for i, f in enumerate(fails[:MAX_FINDINGS]):
        fid = ledger.slug("%s-%d-%s" % (tool, i + 1, f["name"]))[:60]
        fd = ledger.new_finding(SKILL, "%s failed: %s" % (tool, f["name"])[:160], "medium", id=fid,
                                category="eval:%s" % tool, evidence=[rel], repro=f["reason"])
        ledger.write_finding(d, fd)
        if verify:
            ledger.verify(d, SKILL, fid, by="evidence-file")
    mark = ledger.OK if rate >= min_pass else ledger.BAD
    print("%s %s: %d of %d passed (%.1f%%, budget %.0f%%). %d failure finding(s). Evidence: %s"
          % (mark, tool, passed, total, rate * 100, min_pass * 100, min(len(fails), MAX_FINDINGS), rel))
    if len(fails) > MAX_FINDINGS:
        print("  (only the first %d failures became findings; the full list is in the evidence file)" % MAX_FINDINGS)


def cmd_promptfoo(a):
    total, fails = parse_promptfoo(a.path)
    rel = _copy(a.dir, a.path, "promptfoo.json")
    _record(a.dir, "promptfoo", rel, total, total - len(fails), fails, a.min_pass, a.verify)
    return 0


def cmd_garak(a):
    total, passed, fails = parse_garak(a.path)
    rel = _copy(a.dir, a.path, "garak.report.jsonl")
    _record(a.dir, "garak", rel, total, passed, fails, a.min_pass, a.verify)
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, func in (("promptfoo", cmd_promptfoo), ("garak", cmd_garak)):
        p = sub.add_parser(name, help="import %s results" % name)
        p.add_argument("path")
        p.add_argument("--min-pass", type=float, default=0.95, help="pass-rate budget (0..1)")
        p.add_argument("--verify", action="store_true", help="verify failure findings by the report file")
        p.set_defaults(func=func)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
