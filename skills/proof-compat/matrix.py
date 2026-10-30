#!/usr/bin/env python3
"""Cross-browser and device compatibility as an evidence matrix.

Standard library only, no network. Rows are targets (flows or pages), columns
are environments (a browser at a viewport on an OS). Every cell is its own
coverage check and needs its own evidence file; a failing cell becomes a finding.

    python3 matrix.py template --out matrix.json --preset web
    python3 matrix.py plan matrix.json
    python3 matrix.py result checkout safari-ios pass --evidence evidence/proof-compat/checkout@safari-ios.png
    python3 matrix.py result checkout safari-ios fail --evidence evidence/proof-compat/checkout@safari-ios.png \
        --title "Pay button hidden under the keyboard" --severity high --repro "see steps"
    python3 matrix.py skip checkout firefox-desktop --note "not supported in this release"
    python3 matrix.py grid
    python3 matrix.py status
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-compat"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VIEWPORT = re.compile(r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")
TARGET_KEYS = ("id", "title")
ENV_KEYS = ("id", "browser", "version", "viewport", "os")

MARK_PASS, MARK_FAIL, MARK_SKIP, MARK_TODO = ledger.OK, ledger.BAD, "--", ".."

_LATEST = "latest stable on the day of the run"

_DESKTOP = [
    {"id": "chrome-desktop", "browser": "Chrome", "version": _LATEST, "viewport": "1440x900", "os": "Windows"},
    {"id": "firefox-desktop", "browser": "Firefox", "version": _LATEST, "viewport": "1440x900", "os": "Windows"},
    {"id": "safari-desktop", "browser": "Safari", "version": _LATEST, "viewport": "1440x900", "os": "macOS"},
    {"id": "edge-desktop", "browser": "Edge", "version": _LATEST, "viewport": "1440x900", "os": "Windows"},
]
_PHONE_CORE = [
    {"id": "chrome-android", "browser": "Chrome", "version": _LATEST, "viewport": "412x915", "os": "Android"},
    {"id": "safari-ios", "browser": "Safari", "version": _LATEST, "viewport": "390x844", "os": "iOS"},
]
_MOBILE_EXTRA = [
    {"id": "safari-ios-small", "browser": "Safari", "version": _LATEST, "viewport": "375x667", "os": "iOS"},
    {"id": "firefox-android-small", "browser": "Firefox", "version": _LATEST, "viewport": "360x800", "os": "Android"},
    {"id": "safari-ipad", "browser": "Safari", "version": _LATEST, "viewport": "820x1180", "os": "iPadOS"},
    {"id": "chrome-android-tablet", "browser": "Chrome", "version": _LATEST, "viewport": "800x1280", "os": "Android"},
]
PRESETS = {
    "web": _DESKTOP + _PHONE_CORE,
    "mobile": _PHONE_CORE + _MOBILE_EXTRA,
    "full": _DESKTOP + _PHONE_CORE + _MOBILE_EXTRA,
}
EXAMPLE_TARGETS = [
    {"id": "home", "title": "Home page loads and the main menu opens", "critical": False},
    {"id": "sign-in", "title": "Sign in with email and password", "critical": True},
    {"id": "checkout", "title": "Checkout from cart to confirmation", "critical": True},
]


def cell_id(target, env):
    return "%s@%s" % (target, env)


def env_label(env):
    return "%s %s %s" % (env["browser"], env["os"], env["viewport"])


def cell_title(target, env):
    return "%s on %s" % (target["id"], env_label(env))


def _matrix_path(d):
    return os.path.join(ledger.root(d), "compat", "matrix.json")


def _load_matrix(d):
    path = _matrix_path(d)
    if not os.path.exists(path):
        raise SystemExit("no matrix plan yet. Run: matrix.py plan <matrix.json>")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- validate

def validate(data):
    """Return a list of problems with a matrix spec; empty means valid."""
    problems = []
    if not isinstance(data, dict):
        return ["the spec must be a JSON object with 'targets' and 'environments'"]
    for section, keys in (("targets", TARGET_KEYS), ("environments", ENV_KEYS)):
        items = data.get(section)
        if not isinstance(items, list) or not items:
            problems.append("the spec needs a non-empty '%s' list" % section)
            continue
        seen = set()
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                problems.append("%s %d is not an object" % (section, i + 1))
                continue
            for key in keys:
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    problems.append("%s %d is missing '%s'" % (section, i + 1, key))
            iid = item.get("id")
            if isinstance(iid, str) and iid:
                if not _ID.match(iid):
                    problems.append("%s id %r must be lowercase letters, digits and hyphens" % (section, iid))
                if iid in seen:
                    problems.append("duplicate %s id %r" % (section[:-1] if section == "targets" else "environment", iid))
                seen.add(iid)
            vp = item.get("viewport")
            if section == "environments" and isinstance(vp, str) and vp and not _VIEWPORT.match(vp):
                problems.append("environment %r viewport %r must look like 390x844" % (iid, vp))
    return problems


def build_template(preset):
    if preset not in PRESETS:
        raise SystemExit("preset must be one of %s" % ", ".join(sorted(PRESETS)))
    return {"targets": [dict(t) for t in EXAMPLE_TARGETS],
            "environments": [dict(e) for e in PRESETS[preset]]}


# ---------------------------------------------------------------- commands

def cmd_template(a):
    if os.path.exists(a.out) and not a.force:
        raise SystemExit("%s already exists; pass --force to overwrite it" % a.out)
    data = build_template(a.preset)
    parent = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(parent, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("%s wrote %s: %d target(s) x %d environment(s) (preset %s). Edit the targets, then run plan."
          % (ledger.OK, a.out, len(data["targets"]), len(data["environments"]), a.preset))
    return 0


def cmd_plan(a):
    try:
        with open(a.spec, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit("cannot read %s: %s" % (a.spec, e))
    problems = validate(data)
    if problems:
        raise SystemExit("the matrix spec is not valid:\n  " + "\n  ".join(problems))
    matrix = {"targets": data["targets"], "environments": data["environments"]}
    path = _matrix_path(a.dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(matrix, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    checks = [(cell_id(t["id"], e["id"]), cell_title(t, e))
              for t in matrix["targets"] for e in matrix["environments"]]
    ledger.init_coverage(a.dir, SKILL, checks)
    print("%s planned %d cell(s) (%d target(s) x %d environment(s)); coverage ledger created."
          % (ledger.OK, len(checks), len(matrix["targets"]), len(matrix["environments"])))
    print("Next: drive each cell, save one evidence file per cell, then record it with result or skip.")
    return 0


def _lookup(d, target_id, env_id):
    matrix = _load_matrix(d)
    targets = {t["id"]: t for t in matrix["targets"]}
    envs = {e["id"]: e for e in matrix["environments"]}
    if target_id not in targets:
        raise SystemExit("no target %r in the matrix" % target_id)
    if env_id not in envs:
        raise SystemExit("no environment %r in the matrix" % env_id)
    return targets[target_id], envs[env_id]


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def _evidence_reused(d, cid, evidence):
    """Return the other cell that already holds this evidence file, if any."""
    cov = ledger.load_coverage(d, SKILL)
    if cov is None:
        return None
    want = _norm(evidence)
    for c in cov["checks"]:
        if c["id"] != cid and any(_norm(e) == want for e in c.get("evidence", [])):
            return c["id"]
    return None


def cmd_result(a):
    target, env = _lookup(a.dir, a.target, a.env)
    cid = cell_id(a.target, a.env)
    other = _evidence_reused(a.dir, cid, a.evidence)
    if other:
        raise SystemExit("%s is already the evidence for %s; one cell, one piece of evidence" % (a.evidence, other))
    if a.outcome == "pass":
        try:
            ledger.mark_coverage(a.dir, SKILL, cid, "covered", note=a.note, evidence=[a.evidence])
        except ledger.LedgerError as e:
            raise SystemExit("cannot record a pass: %s" % e)
        print("%s %s passes, evidence recorded." % (ledger.OK, cid))
        return 0

    if not a.title:
        raise SystemExit("a failing cell needs --title saying what went wrong")
    severity = a.severity or ("high" if target.get("critical") else "medium")
    if not a.severity:
        print("(no --severity given; using %s because the target is %s)"
              % (severity, "critical" if target.get("critical") else "non-critical"))
    problem = ledger._evidence_ok(a.dir, [a.evidence])
    if problem:
        raise SystemExit("cannot record a fail: %s" % problem)
    fid = ledger.slug("%s-%s-%s" % (a.target, a.env, a.title))
    f = ledger.new_finding(SKILL, a.title, severity, id=fid, category="compat:%s" % a.env,
                           location=a.location or cell_title(target, env), repro=a.repro,
                           evidence=[a.evidence], fix=a.fix, cell=cid,
                           environment=dict(env))
    ledger.write_finding(a.dir, f)
    try:
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[a.evidence])
    except ledger.LedgerError as e:
        raise SystemExit("recorded the finding but could not mark coverage: %s" % e)
    if a.verify:
        try:
            ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
        except ledger.LedgerError as e:
            raise SystemExit("finding written but could not verify: %s" % e)
        print("%s %s: finding %r written and verified by its evidence file." % (ledger.BAD, cid, f["id"]))
    else:
        print("%s %s: finding %r written (unverified). Re-run with --verify once the evidence is saved."
              % (ledger.BAD, cid, f["id"]))
    return 0


def cmd_skip(a):
    _lookup(a.dir, a.target, a.env)
    cid = cell_id(a.target, a.env)
    try:
        ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked not applicable." % (ledger.OK, cid))
    return 0


# -------------------------------------------------------------------- grid

def cell_marks(d):
    """Return (matrix, {cell id: mark})."""
    matrix = _load_matrix(d)
    cov = ledger.load_coverage(d, SKILL) or {"checks": []}
    status = {c["id"]: c["status"] for c in cov["checks"]}
    failed = set(f.get("cell") for f in ledger.load_findings(d, SKILL)
                 if f["status"] != "false_positive" and f.get("cell"))
    marks = {}
    for t in matrix["targets"]:
        for e in matrix["environments"]:
            cid = cell_id(t["id"], e["id"])
            s = status.get(cid, "not_run")
            if cid in failed:
                marks[cid] = MARK_FAIL
            elif s == "covered":
                marks[cid] = MARK_PASS
            elif s == "not_applicable":
                marks[cid] = MARK_SKIP
            else:
                marks[cid] = MARK_TODO
    return matrix, marks


LEGEND = "%s pass with evidence   %s failed (finding)   %s not applicable   %s not run" % (
    MARK_PASS, MARK_FAIL, MARK_SKIP, MARK_TODO)


def cmd_grid(a):
    matrix, marks = cell_marks(a.dir)
    envs = [e["id"] for e in matrix["environments"]]
    tids = [t["id"] for t in matrix["targets"]]
    first = max([len("target")] + [len(t) for t in tids])
    widths = [max(len(e), 2) for e in envs]
    rows = [["target".ljust(first)] + [e.ljust(w) for e, w in zip(envs, widths)]]
    rows.append(["-" * first] + ["-" * w for w in widths])
    for t in tids:
        rows.append([t.ljust(first)] + [marks[cell_id(t, e)].ljust(w) for e, w in zip(envs, widths)])
    for r in rows:
        print("  ".join(r).rstrip())
    counts = {m: sum(1 for v in marks.values() if v == m) for m in (MARK_PASS, MARK_FAIL, MARK_SKIP, MARK_TODO)}
    print("")
    print(LEGEND)
    print("%d cell(s): %d pass, %d fail, %d not applicable, %d not run"
          % (len(marks), counts[MARK_PASS], counts[MARK_FAIL], counts[MARK_SKIP], counts[MARK_TODO]))

    L = ["# Compatibility matrix", "",
         "| target | " + " | ".join(envs) + " |",
         "|---|" + "---|" * len(envs)]
    for t in tids:
        L.append("| %s | %s |" % (t, " | ".join(marks[cell_id(t, e)] for e in envs)))
    L += ["", "Environments:", ""]
    for e in matrix["environments"]:
        L.append("- `%s`: %s (%s)" % (e["id"], env_label(e), e["version"]))
    L += ["", "Legend: `%s` pass with evidence, `%s` failed (finding), `%s` not applicable, `%s` not run."
          % (MARK_PASS, MARK_FAIL, MARK_SKIP, MARK_TODO), ""]
    path = os.path.join(ledger.root(a.dir), "compat", "grid.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("wrote %s" % path)
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None or not os.path.exists(_matrix_path(a.dir)):
        print("no matrix plan yet. Run: matrix.py plan <matrix.json>")
        return 0
    _, marks = cell_marks(a.dir)
    counts = {s: sum(1 for c in cov["checks"] if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
    print("cells: %d covered, %d not applicable, %d not run"
          % (counts["covered"], counts["not_applicable"], counts["not_run"]))
    for c in cov["checks"]:
        print("  %s %s: %s" % (marks.get(c["id"], MARK_TODO), c["id"], c["title"]))
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

    t = sub.add_parser("template", help="write a starter matrix spec")
    t.add_argument("--out", required=True)
    t.add_argument("--preset", choices=sorted(PRESETS), default="web")
    t.add_argument("--force", action="store_true", help="overwrite an existing file")
    t.set_defaults(func=cmd_template)

    p = sub.add_parser("plan", help="validate a matrix spec and create one coverage check per cell")
    p.add_argument("spec")
    p.set_defaults(func=cmd_plan)

    r = sub.add_parser("result", help="record a cell pass or fail")
    r.add_argument("target")
    r.add_argument("env")
    r.add_argument("outcome", choices=["pass", "fail"])
    r.add_argument("--evidence", required=True, help="path under proof/ to this cell's screenshot or capture")
    r.add_argument("--note")
    r.add_argument("--title", help="what went wrong (required for fail)")
    r.add_argument("--severity", choices=ledger.SEVERITIES)
    r.add_argument("--repro")
    r.add_argument("--location")
    r.add_argument("--fix")
    r.add_argument("--verify", action="store_true", help="also verify the finding by its evidence file")
    r.set_defaults(func=cmd_result)

    s = sub.add_parser("skip", help="mark a cell not applicable")
    s.add_argument("target")
    s.add_argument("env")
    s.add_argument("--note", required=True)
    s.set_defaults(func=cmd_skip)

    sub.add_parser("grid", help="print the matrix grid and write proof/compat/grid.md").set_defaults(func=cmd_grid)
    sub.add_parser("status", help="coverage and findings so far").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
