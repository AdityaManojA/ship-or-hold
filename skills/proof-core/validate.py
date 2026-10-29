#!/usr/bin/env python3
"""Validate everything under a proof/ folder against evidence-schema.json.

    python3 validate.py                 # checks ./proof
    python3 validate.py --dir path/to/proof
    python3 validate.py --strict        # also fail on unverified findings older than --stale-days

Exit 0 when every file is well formed, 1 when any is not, 2 when the folder is
missing. Standard library only, no network.

Besides the schema, it checks the things a schema cannot say:

* every evidence path a verified finding or a covered check points to exists and
  is non-empty, and lives under proof/;
* a verified finding says how it was verified;
* a false positive says why;
* a finding file's name matches the id inside it, and its folder matches its skill.
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger  # noqa: E402


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as e:
        return None, "%s: cannot read JSON (%s)" % (path, e)


def _rel(d, path):
    return os.path.relpath(path, d).replace(os.sep, "/")


def _evidence_problems(d, paths, owner):
    out = []
    base = ledger.root(d)
    for p in paths or []:
        full = p if os.path.isabs(p) else os.path.join(base, p)
        if not os.path.isfile(full):
            out.append("%s: evidence file missing: %s" % (owner, p))
        elif os.path.getsize(full) == 0:
            out.append("%s: evidence file empty: %s" % (owner, p))
        elif os.path.commonpath([os.path.abspath(full), base]) != base:
            out.append("%s: evidence outside proof/: %s" % (owner, p))
    return out


def validate_dir(d="proof", strict=False, stale_days=14):
    """Return (problems, counts). ``problems`` is a list of strings, empty when clean."""
    problems = []
    counts = {"target": 0, "findings": 0, "coverage": 0, "scores": 0}
    base = ledger.root(d)
    if not os.path.isdir(base):
        return ["%s: no such folder (run /proof-plan first)" % d], counts

    tpath = ledger.target_path(d)
    if os.path.exists(tpath):
        obj, err = _load(tpath)
        if err:
            problems.append(err)
        else:
            counts["target"] = 1
            problems += ["target.json: " + p for p in ledger.check(obj, "target")]
    else:
        problems.append("target.json is missing (run /proof-plan)")

    fbase = os.path.join(base, "findings")
    if os.path.isdir(fbase):
        for skill in sorted(os.listdir(fbase)):
            folder = os.path.join(fbase, skill)
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(folder, name)
                rel = _rel(base, path)
                obj, err = _load(path)
                if err:
                    problems.append(err)
                    continue
                counts["findings"] += 1
                errs = ledger.check(obj, "finding")
                problems += ["%s: %s" % (rel, p) for p in errs]
                if errs:
                    continue
                if obj["id"] != name[:-5]:
                    problems.append("%s: file name does not match id %r" % (rel, obj["id"]))
                if obj["skill"] != skill:
                    problems.append("%s: folder does not match skill %r" % (rel, obj["skill"]))
                if obj["status"] == "verified":
                    if not obj.get("verified_by"):
                        problems.append("%s: verified but verified_by is missing" % rel)
                    if obj.get("verified_by") in ("evidence-file", "repro-run"):
                        if not obj.get("evidence"):
                            problems.append("%s: verified by %s but lists no evidence" % (rel, obj["verified_by"]))
                        problems += _evidence_problems(d, obj.get("evidence"), rel)
                    if obj.get("verified_by") == "repro-run" and not obj.get("repro"):
                        problems.append("%s: verified by repro-run but has no repro" % rel)
                    if obj.get("verified_by") == "manual" and not obj.get("verified_note"):
                        problems.append("%s: manual verification needs verified_note" % rel)
                if obj["status"] == "false_positive" and not obj.get("verified_note"):
                    problems.append("%s: false positive without a reason" % rel)
                if strict and obj["status"] == "unverified":
                    try:
                        created = datetime.datetime.fromisoformat(obj["created"].replace("Z", "+00:00"))
                        age = (datetime.datetime.now(datetime.timezone.utc) - created).days
                        if age > stale_days:
                            problems.append("%s: unverified for %d days" % (rel, age))
                    except ValueError:
                        problems.append("%s: created is not an ISO timestamp" % rel)

    cbase = os.path.join(base, "coverage")
    if os.path.isdir(cbase):
        for name in sorted(os.listdir(cbase)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(cbase, name)
            rel = _rel(base, path)
            obj, err = _load(path)
            if err:
                problems.append(err)
                continue
            counts["coverage"] += 1
            errs = ledger.check(obj, "coverage")
            problems += ["%s: %s" % (rel, p) for p in errs]
            if errs:
                continue
            if obj["skill"] != name[:-5]:
                problems.append("%s: file name does not match skill %r" % (rel, obj["skill"]))
            ids = [c["id"] for c in obj["checks"]]
            if len(ids) != len(set(ids)):
                problems.append("%s: duplicate check ids" % rel)
            for c in obj["checks"]:
                if c["status"] == "not_applicable" and not c.get("note"):
                    problems.append("%s: check %s is not_applicable without a note" % (rel, c["id"]))
                if c["status"] == "covered":
                    problems += _evidence_problems(d, c.get("evidence"), "%s:%s" % (rel, c["id"]))

    sbase = os.path.join(base, "scores")
    if os.path.isdir(sbase):
        for name in sorted(os.listdir(sbase)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(sbase, name)
            rel = _rel(base, path)
            obj, err = _load(path)
            if err:
                problems.append(err)
                continue
            counts["scores"] += 1
            errs = ledger.check(obj, "scores")
            problems += ["%s: %s" % (rel, p) for p in errs]
            if errs:
                continue
            if obj["skill"] != name[:-5]:
                problems.append("%s: file name does not match skill %r" % (rel, obj["skill"]))
            for m in obj["metrics"]:
                problems += _evidence_problems(d, m.get("evidence"), "%s:%s" % (rel, m["id"]))

    return problems, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    ap.add_argument("--strict", action="store_true", help="also flag findings left unverified for too long")
    ap.add_argument("--stale-days", type=int, default=14)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    ledger.console()
    problems, counts = validate_dir(a.dir, a.strict, a.stale_days)
    if a.json:
        print(json.dumps({"ok": not problems, "problems": problems, "counts": counts}, indent=2))
    else:
        print("%s: %d target, %d findings, %d coverage, %d scores" % (
            a.dir, counts["target"], counts["findings"], counts["coverage"], counts["scores"]))
        for p in problems:
            print("  ! " + p)
        print("OK" if not problems else "%d problem(s)" % len(problems))
    if not os.path.isdir(a.dir):
        return 2
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
