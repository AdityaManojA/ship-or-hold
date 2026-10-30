#!/usr/bin/env python3
"""Plan and judge an agent's exploration of a real mobile app. Standard library only.

An AI agent drives the user's own iOS or Android app toward goals, with whatever
driver the session has (mobile-mcp tools, or Midscene, Mobilerun or Maestro on the
PATH). This tool never touches the device: it writes the brief, reads the device
logs for crashes, and turns the agent's session file into evidence.

    python3 mexplore.py plan --app com.example.shop --goal "sign up" --goal "check out"
    python3 mexplore.py logs saved/logcat.txt
    python3 mexplore.py record --session saved/session.json --logcat saved/logcat.txt
    python3 mexplore.py status
"""

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-mobile-explore"

# Command-line drivers the agent can use when no mobile MCP tools are loaded.
DRIVERS = [
    ("adb", "Android device bridge: screenshots, input, logcat"),
    ("xcrun", "Xcode tools: simctl for iOS simulators"),
    ("maestro", "Maestro: scripted and AI-assisted mobile flows"),
    ("midscene", "Midscene: vision-driven actions and assertions"),
    ("mobilerun", "Mobilerun: goal-driven Android agent"),
]
OUTCOMES = ("reached", "dead_end", "crash", "blocked")
SEVERITY = {"crash": "high", "anr": "high", "dead_end": "medium", "blocked": "low"}

FATAL = re.compile(r"FATAL EXCEPTION")
ANR = re.compile(r"\bANR in\s+(\S+)")
NATIVE = re.compile(r"Fatal signal \d+ \((SIG[A-Z]+)\)|\b(SIGSEGV|SIGABRT|SIGBUS|SIGILL)\b")
PROCESS = re.compile(r"Process:\s*([\w.]+)")
EXC_LINE = re.compile(r"^\s*(?:[\w$.]+\.)?[\w$]*(?:Exception|Error)\b.*$")

BRIEF = """# Mobile exploration brief: %(app)s (%(platform)s)

You are exploring the user's own app on their own emulator, simulator or device.
Never touch any other app's data, never sign in to real third-party accounts, and
never make a purchase that charges a real payment method.

## Goals

%(goals)s

## How to work

- Prefer the mobile MCP tools if they are loaded in this session (list elements,
  tap, type, swipe, screenshot). Otherwise use a driver on the PATH:
%(drivers)s
- Take a screenshot after every step and save it under the session folder.
- Keep going toward each goal the way a real user would. If you get stuck for
  three steps in a row, mark that goal dead_end; if the app closes or freezes,
  mark it crash; if something outside the app stops you (no network, a login you
  were not given), mark it blocked.
- When you finish, save the device log (adb logcat -d > logcat.txt, or the iOS
  crash reports) next to the session.

## Session file

Write %(session)s in this shape:

    {"app": "%(app)s", "platform": "%(platform)s", "driver": "mobile-mcp",
     "goals": [
       {"goal": "sign up", "outcome": "reached",
        "steps": [{"action": "tap Sign up", "screenshot": "shots/01.png", "note": ""}]},
       {"goal": "check out", "outcome": "dead_end", "note": "Pay button does nothing",
        "steps": [{"action": "tap Pay", "screenshot": "shots/07.png", "note": "no response"}]}
     ]}

outcome is one of: reached, dead_end, crash, blocked. Screenshot paths are
relative to the session file.
"""


def find_drivers():
    """[(name, purpose, path or None)] for the command-line drivers."""
    return [(n, why, shutil.which(n)) for n, why in DRIVERS]


def parse_logcat(text):
    """Crashes, ANRs and native signals in an adb logcat dump: [{"kind","process","detail","line"}]."""
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if FATAL.search(line):
            proc, detail = "", ""
            for nxt in lines[i + 1:i + 8]:
                m = PROCESS.search(nxt)
                if m and not proc:
                    proc = m.group(1)
                body = nxt.split(": ", 1)[-1] if ": " in nxt else nxt
                if not detail and EXC_LINE.match(body.strip()):
                    detail = body.strip()
            out.append({"kind": "crash", "process": proc, "detail": detail or "fatal exception", "line": i + 1})
            continue
        m = ANR.search(line)
        if m:
            out.append({"kind": "anr", "process": m.group(1), "detail": line.strip()[-160:], "line": i + 1})
            continue
        m = NATIVE.search(line)
        if m:
            out.append({"kind": "crash", "process": "", "detail": "native signal %s" % (m.group(1) or m.group(2)),
                        "line": i + 1})
    return out


def parse_ips(text):
    """An iOS .ips crash report: a JSON header line, then a JSON body. Returns one crash dict or None."""
    parts = text.split("\n", 1)
    try:
        head = json.loads(parts[0])
    except ValueError:
        return None
    body = {}
    if len(parts) > 1:
        try:
            body = json.loads(parts[1])
        except ValueError:
            body = {}
    exc = body.get("exception") or {}
    proc = head.get("app_name") or body.get("procName") or head.get("name") or ""
    detail = " ".join(x for x in (exc.get("type", ""), exc.get("signal", "")) if x) or head.get("bug_type", "crash")
    return {"kind": "crash", "process": proc, "detail": detail, "line": 1}


def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def load_session(path):
    with open(path, encoding="utf-8") as fh:
        s = json.load(fh)
    problems = []
    if not isinstance(s.get("goals"), list) or not s["goals"]:
        problems.append("goals must be a non-empty list")
    for i, g in enumerate(s.get("goals") or []):
        if not isinstance(g, dict) or not g.get("goal"):
            problems.append("goal %d needs a 'goal'" % (i + 1))
            continue
        if g.get("outcome") not in OUTCOMES:
            problems.append("goal %d outcome must be one of %s" % (i + 1, ", ".join(OUTCOMES)))
        if not isinstance(g.get("steps", []), list):
            problems.append("goal %d steps must be a list" % (i + 1))
    if problems:
        raise SystemExit("session file %s is not valid: %s" % (path, "; ".join(problems)))
    return s


def _copy_in(d, path, sub=""):
    """Copy a file into this skill's evidence folder and return its ledger-relative path."""
    dest_dir = os.path.join(ledger.evidence_dir(d, SKILL), sub) if sub else ledger.evidence_dir(d, SKILL)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


# ---------------------------------------------------------------- commands

def cmd_plan(a):
    goals = [g.strip() for g in a.goal if g.strip()]
    if not goals:
        raise SystemExit("give at least one --goal")
    found = find_drivers()
    text = BRIEF % {
        "app": a.app, "platform": a.platform,
        "goals": "\n".join("%d. %s" % (i, g) for i, g in enumerate(goals, 1)),
        "drivers": "\n".join("  - %s: %s (%s)" % (n, why, "found" if p else "not installed") for n, why, p in found),
        "session": "session.json",
    }
    brief = os.path.join(ledger.evidence_dir(a.dir, SKILL), "brief.md")
    with open(brief, "w", encoding="utf-8") as fh:
        fh.write(text)
    ledger.init_coverage(a.dir, SKILL, [("goal-%d" % i, g) for i, g in enumerate(goals, 1)])
    print("%s wrote %s with %d goal(s)." % (ledger.OK, brief, len(goals)))
    print("Drivers on the PATH:")
    for n, why, p in found:
        print("  %s %s  %s" % (ledger.OK if p else "  ", n, why if p else "(not installed)"))
    print("Prefer the mobile MCP tools when they are loaded in the session. Only explore your own app.")
    return 0


def cmd_logs(a):
    text = _read(a.path)
    crashes = [parse_ips(text)] if a.path.lower().endswith(".ips") else parse_logcat(text)
    crashes = [c for c in crashes if c]
    if not crashes:
        print("%s no crashes, ANRs or native signals in %s" % (ledger.OK, a.path))
        return 0
    for c in crashes:
        print("  %s [%s] %s line %d: %s" % (ledger.BAD, c["kind"], c["process"] or "?", c["line"], c["detail"]))
    return 0


def cmd_record(a):
    s = load_session(a.session)
    base = os.path.dirname(os.path.abspath(a.session))
    goals = s["goals"]
    checks = [("goal-%d" % i, g["goal"]) for i, g in enumerate(goals, 1)]
    if a.logcat or a.crash_report:
        checks.append(("device-logs", "No crash or ANR in the device logs"))
    ledger.init_coverage(a.dir, SKILL, checks, keep_existing=False)
    session_rel = _copy_in(a.dir, a.session)
    findings = []
    for i, g in enumerate(goals, 1):
        shots = []
        for st in g.get("steps") or []:
            shot = st.get("screenshot")
            if shot:
                p = shot if os.path.isabs(shot) else os.path.join(base, shot)
                if os.path.isfile(p):
                    shots.append(_copy_in(a.dir, p, "shots"))
        ev = shots or [session_rel]
        cid = "goal-%d" % i
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=ev,
                             note="%s in %d step(s)" % (g["outcome"], len(g.get("steps") or [])))
        if g["outcome"] != "reached":
            last = (g.get("steps") or [{}])[-1]
            why = g.get("note") or last.get("note") or last.get("action") or ""
            title = {"dead_end": "Could not complete '%s': a dead end",
                     "crash": "The app crashed during '%s'",
                     "blocked": "'%s' was blocked before it could finish"}[g["outcome"]] % g["goal"]
            if why:
                title += " (%s)" % why
            findings.append((cid, title, SEVERITY[g["outcome"]], ev[-1:],
                             "Reproduce from the steps in the session file, fix, and explore the goal again."))
    crashes, log_rels = [], []
    for path, kind in [(p, "logcat") for p in ([a.logcat] if a.logcat else [])] + [(p, "ips") for p in a.crash_report]:
        rel = _copy_in(a.dir, path, "logs")
        log_rels.append(rel)
        text = _read(path)
        found = [parse_ips(text)] if kind == "ips" else parse_logcat(text)
        for c in found:
            if c:
                crashes.append((c, rel))
    if a.logcat or a.crash_report:
        ledger.mark_coverage(a.dir, SKILL, "device-logs", "covered", evidence=log_rels,
                             note="%d crash(es) or ANR(s)" % len(crashes))
    for c, rel in crashes:
        label = "ANR" if c["kind"] == "anr" else "Crash"
        title = "%s in %s: %s" % (label, c["process"] or s.get("app", "the app"), c["detail"])
        findings.append(("device-logs", title, SEVERITY[c["kind"]], [rel],
                         "Fix the cause shown at line %d of the log, then explore again." % c["line"]))
    seen = set()
    for cid, title, sev, ev, fix in findings:
        fid = ledger.slug("mexplore-%s-%s" % (cid, title))[:60]
        if fid in seen:
            continue
        seen.add(fid)
        f = ledger.new_finding(SKILL, title[:160], sev, id=fid, category="explore:%s" % cid, evidence=ev, fix=fix,
                               repro="Follow the steps in %s" % session_rel)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    reached = sum(1 for g in goals if g["outcome"] == "reached")
    print("%s recorded %d goal(s), %d reached, %d finding(s)." % (
        ledger.OK if not seen else ledger.BAD, len(goals), reached, len(seen)))
    for _, title, sev, _, _ in findings:
        print("  [%s] %s" % (sev, title))
    return 0


def cmd_status(a):
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="write the exploration brief and list the drivers")
    p.add_argument("--app", required=True, help="the app's package name or bundle id")
    p.add_argument("--goal", action="append", default=[], help="a goal to reach (repeatable)")
    p.add_argument("--platform", choices=("android", "ios"), default="android")
    p.set_defaults(func=cmd_plan)
    lg = sub.add_parser("logs", help="find crashes in a logcat dump or an iOS .ips report")
    lg.add_argument("path")
    lg.set_defaults(func=cmd_logs)
    r = sub.add_parser("record", help="turn the agent's session into evidence")
    r.add_argument("--session", required=True, help="the session JSON the agent wrote")
    r.add_argument("--logcat", help="an adb logcat dump from the run")
    r.add_argument("--crash-report", action="append", default=[], help="an iOS .ips crash report (repeatable)")
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
