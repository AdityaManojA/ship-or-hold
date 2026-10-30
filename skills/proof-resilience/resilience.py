#!/usr/bin/env python3
"""Check how an app behaves when the network or the backend misbehaves. Offline.

Standard library only, no network. This tool does not break anything itself. It
keeps a checklist of failure scenarios (offline, slow network, server errors,
timeouts, cold starts, double submits, retry storms, partial failures, a session
that expires mid-use), records the result of each one you run against your own
app with the evidence behind it, and reads HAR files captured during a scenario
for duplicate writes, retry storms, very slow requests and a slow first request.

    python3 resilience.py plan [--mobile]
    python3 resilience.py har capture.har --scenario double-submit
    python3 resilience.py record-har capture.har --scenario double-submit
    python3 resilience.py result offline pass --evidence evidence/proof-resilience/offline.png
    python3 resilience.py result timeout fail --evidence <path> --title "..." --verify
    python3 resilience.py na token-expiry-midway --note "the app has no sign-in"
    python3 resilience.py status
"""

import argparse
import datetime
import json
import os
import shutil
import statistics
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-resilience"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# What each scenario checks, how to cause it on your own app, and what a pass looks like.
SCENARIOS = [
    {"id": "offline", "title": "Offline",
     "simulate": "Turn the network off: DevTools Network > Offline, Playwright context.set_offline(True), "
                 "airplane mode on the device or emulator.",
     "pass": "A clear message instead of a blank or frozen screen; work in progress is kept or queued, not lost."},
    {"id": "slow-network", "title": "Slow network",
     "simulate": "Throttle to slow 3G: DevTools Network throttling, Playwright routes with a delay, "
                 "adb emu network speed gsm, or the iOS Network Link Conditioner.",
     "pass": "Loading states show, buttons cannot be pressed twice, nothing times out silently."},
    {"id": "server-error", "title": "Server error",
     "simulate": "Make the backend answer 500: Playwright route.fulfill(status=500), a local proxy, or a "
                 "feature flag on a staging copy.",
     "pass": "A friendly error with a way to retry; no raw error text, no crash, no stuck spinner."},
    {"id": "timeout", "title": "Request that never answers",
     "simulate": "Hold a request open: a Playwright route that never fulfils, or a proxy that delays forever.",
     "pass": "The app gives up after a sensible time, says so, and lets the user try again."},
    {"id": "cold-start", "title": "Cold start of a sleeping backend",
     "simulate": "Let a free-tier host sleep (idle 15+ minutes), then open the app and capture a HAR.",
     "pass": "A loading state covers the slow first request; the first action does not fail."},
    {"id": "double-submit", "title": "Double submit",
     "simulate": "Tap submit, pay or send twice quickly (or click twice in Playwright) and capture a HAR.",
     "pass": "Exactly one action happens: one order, one message, one charge."},
    {"id": "retry-storm", "title": "Retry storm",
     "simulate": "Make an endpoint fail (route.fulfill(status=503)) and watch the requests in a HAR.",
     "pass": "Retries are few and spaced out (backoff); the app does not hammer the backend."},
    {"id": "partial-failure", "title": "Partial failure",
     "simulate": "Fail one API the screen uses (route.abort on that URL only) and leave the rest working.",
     "pass": "Only that part of the screen shows an error; the rest of the screen still works."},
    {"id": "token-expiry-midway", "title": "Session expires mid-use",
     "simulate": "Expire or revoke the session during a task (shorten the token life on staging, or delete "
                 "the token from storage), then continue the task.",
     "pass": "The user signs in again and gets back to where they were, without losing what they typed."},
]
MOBILE_SCENARIOS = [
    {"id": "background-resume", "title": "Backgrounded during a request",
     "simulate": "Start a request, send the app to the background or lock the phone, come back after a minute.",
     "pass": "The request finishes or retries cleanly when the app returns; no duplicate action, no crash."},
    {"id": "network-switch", "title": "Network switch",
     "simulate": "Switch between Wi-Fi and mobile data during a request (adb shell svc wifi disable/enable, "
                 "or by hand on the device).",
     "pass": "The app recovers on the new network without a stuck screen or a duplicate action."},
]
ALL = SCENARIOS + MOBILE_SCENARIOS
BY_ID = dict((s["id"], s) for s in ALL)


def _valid(sid):
    if sid not in BY_ID:
        raise SystemExit("no scenario %r. See: resilience.py plan" % sid)
    return BY_ID[sid]


# ------------------------------------------------------------------- HAR

def _time(s):
    """HAR startedDateTime (ISO 8601) to seconds since the epoch."""
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    # Trim fractional seconds to 6 digits for fromisoformat on older Pythons.
    if "." in s:
        head, rest = s.split(".", 1)
        frac, tz = rest, ""
        for sign in ("+", "-"):
            if sign in rest:
                frac, tz = rest.split(sign, 1)
                tz = sign + tz
                break
        s = head + "." + (frac + "000000")[:6] + tz
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def load_har(path):
    with open(path, encoding="utf-8-sig") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            raise SystemExit("%s is not JSON; save the HAR from the browser's Network panel" % path)
    entries = ((data.get("log") or {}).get("entries")) if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise SystemExit("%s has no log.entries; is it a HAR file?" % path)
    out = []
    for e in entries:
        req, resp = e.get("request") or {}, e.get("response") or {}
        url = req.get("url") or ""
        p = urlparse(url)
        body = ((req.get("postData") or {}).get("text")) or ""
        out.append({"method": (req.get("method") or "GET").upper(), "url": url,
                    "endpoint": "%s %s%s" % ((req.get("method") or "GET").upper(), p.netloc, p.path),
                    "body": body, "status": int(resp.get("status") or 0),
                    "start": _time(e.get("startedDateTime")), "ms": float(e.get("time") or 0)})
    out = [x for x in out if x["start"] is not None]
    out.sort(key=lambda x: x["start"])
    return out


def analyse_har(entries, window=2.0, max_retries=5, slow_ms=10000.0):
    dups, storms, slow = [], [], []
    seen = {}
    for x in entries:
        if x["method"] in MUTATING:
            key = (x["method"], x["url"], x["body"])
            prev = seen.get(key)
            if prev is not None and x["start"] - prev <= window:
                dups.append({"endpoint": x["endpoint"], "gap_s": round(x["start"] - prev, 3)})
            seen[key] = x["start"]
        if x["ms"] > slow_ms:
            slow.append({"endpoint": x["endpoint"], "ms": round(x["ms"])})
    by_ep = {}
    for x in entries:
        by_ep.setdefault(x["endpoint"], []).append(x)
    for ep, xs in by_ep.items():
        for i, x in enumerate(xs):
            if x["status"] < 500 and x["status"] not in (0, 429):
                continue
            burst = [y for y in xs[i + 1:] if y["start"] - x["start"] <= 10.0]
            if len(burst) > max_retries:
                storms.append({"endpoint": ep, "requests_in_10s": len(burst), "after_status": x["status"]})
                break
    first, median = None, None
    timed = [x["ms"] for x in entries if x["ms"] > 0]
    if timed:
        first = round(timed[0])
        median = round(statistics.median(timed))
    return {"requests": len(entries), "duplicates": dups, "storms": storms, "slow": slow,
            "first_ms": first, "median_ms": median,
            "cold_start": bool(first and median and first > max(3 * median, 3000))}


# --------------------------------------------------------------- commands

def cmd_plan(a):
    scen = ALL if a.mobile else SCENARIOS
    ledger.init_coverage(a.dir, SKILL, [(s["id"], s["title"]) for s in scen])
    print("%s coverage ledger created with %d scenarios, all not run." % (ledger.OK, len(scen)))
    print("Run each against your own app only (a local or staging copy you are allowed to break):")
    for s in scen:
        print("\n[%s] %s" % (s["id"], s["title"]))
        print("   cause it: %s" % s["simulate"])
        print("   pass:     %s" % s["pass"])
    return 0


def _print_har(r):
    print("%d request(s). First request %s ms, median %s ms%s." % (
        r["requests"], r["first_ms"], r["median_ms"], " (looks like a cold start)" if r["cold_start"] else ""))
    for d in r["duplicates"]:
        print("  %s duplicate write %s, %.3fs apart" % (ledger.BAD, d["endpoint"], d["gap_s"]))
    for s in r["storms"]:
        print("  %s retry storm on %s: %d requests in 10 s after a %s" % (
            ledger.BAD, s["endpoint"], s["requests_in_10s"], s["after_status"] or "network error"))
    for s in r["slow"]:
        print("  %s slow request %s: %d ms" % (ledger.BAD, s["endpoint"], s["ms"]))
    if not (r["duplicates"] or r["storms"] or r["slow"]):
        print("  %s no duplicate writes, retry storms or very slow requests" % ledger.OK)


def cmd_har(a):
    if a.scenario:
        _valid(a.scenario)
    r = analyse_har(load_har(a.path), a.window, a.max_retries, a.slow_ms)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        _print_har(r)
    return 0


def _copy(d, path):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


def _ensure(d, sid):
    cov = ledger.load_coverage(d, SKILL)
    ids = [c["id"] for c in (cov or {}).get("checks", [])]
    if sid not in ids:
        checks = [(c["id"], c["title"]) for c in (cov or {}).get("checks", [])]
        ledger.init_coverage(d, SKILL, checks + [(sid, BY_ID[sid]["title"])])


def cmd_record_har(a):
    scen = _valid(a.scenario)
    entries = load_har(a.path)
    r = analyse_har(entries, a.window, a.max_retries, a.slow_ms)
    rel = _copy(a.dir, a.path)
    _ensure(a.dir, a.scenario)
    problems = []
    for x in r["duplicates"]:
        problems.append(("duplicate write: %s sent twice %.3fs apart" % (x["endpoint"], x["gap_s"]), "high",
                         "Disable the control while the request runs and make the endpoint idempotent "
                         "(an idempotency key) so a second tap cannot repeat the action."))
    for x in r["storms"]:
        problems.append(("retry storm: %d requests to %s in 10 s after a %s" % (
            x["requests_in_10s"], x["endpoint"], x["after_status"] or "network error"), "medium",
            "Retry a few times with exponential backoff and jitter, then stop and tell the user."))
    for x in r["slow"]:
        problems.append(("slow request: %s took %d ms" % (x["endpoint"], x["ms"]), "low",
                         "Add a timeout and a loading state, or speed the endpoint up."))
    if a.scenario == "cold-start" and r["cold_start"]:
        problems.append(("cold start: first request %d ms against a median of %d ms" % (r["first_ms"], r["median_ms"]),
                         "low", "Keep the host warm or show a loading state that covers the first slow request."))
    for title, sev, fix in problems:
        fid = ledger.slug("res-%s-%s" % (a.scenario, title))[:60]
        f = ledger.new_finding(SKILL, ("%s: %s" % (scen["title"], title))[:160], sev, id=fid,
                               category="resilience:%s" % a.scenario, evidence=[rel], fix=fix,
                               repro="Replay the scenario '%s' and capture a HAR" % a.scenario)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    ledger.mark_coverage(a.dir, SKILL, a.scenario, "covered", evidence=[rel],
                         note="HAR: %d request(s), %d problem(s)" % (r["requests"], len(problems)))
    _print_har(r)
    print("%s recorded %s from the HAR: %d finding(s)." % (ledger.BAD if problems else ledger.OK, a.scenario,
                                                          len(problems)))
    return 0


def cmd_result(a):
    scen = _valid(a.scenario)
    _ensure(a.dir, a.scenario)
    if a.outcome == "pass":
        try:
            ledger.mark_coverage(a.dir, SKILL, a.scenario, "covered", note=a.note, evidence=[a.evidence])
        except ledger.LedgerError as e:
            raise SystemExit("cannot record a pass: %s" % e)
        print("%s %s holds up, evidence recorded." % (ledger.OK, a.scenario))
        return 0
    if not a.title:
        raise SystemExit("a failing scenario needs --title saying what the app did")
    f = ledger.new_finding(SKILL, a.title, a.severity or "medium", category="resilience:%s" % a.scenario,
                           evidence=[a.evidence], repro=a.repro or scen["simulate"],
                           fix=a.fix or "Make it pass: %s" % scen["pass"])
    ledger.write_finding(a.dir, f)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.scenario, "covered", evidence=[a.evidence])
    except ledger.LedgerError as e:
        raise SystemExit("finding written but coverage failed: %s" % e)
    if a.verify:
        ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
        print("%s %s: finding %r written and verified." % (ledger.BAD, a.scenario, f["id"]))
    else:
        print("%s %s: finding %r written (unverified); re-run with --verify once the evidence is saved."
              % (ledger.BAD, a.scenario, f["id"]))
    return 0


def cmd_na(a):
    _valid(a.scenario)
    _ensure(a.dir, a.scenario)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.scenario, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked not applicable." % (ledger.OK, a.scenario))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no resilience plan yet. Run: resilience.py plan")
        return 0
    counts = dict((s, sum(1 for c in cov["checks"] if c["status"] == s)) for s in ledger.COVERAGE_STATUSES)
    print("scenarios: %d covered, %d not applicable, %d not run" % (
        counts["covered"], counts["not_applicable"], counts["not_run"]))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="create the scenario checklist")
    p.add_argument("--mobile", action="store_true", help="add the phone scenarios (backgrounding, network switch)")
    p.set_defaults(func=cmd_plan)
    for name, func, hlp in (("har", cmd_har, "analyse a HAR captured during a scenario"),
                            ("record-har", cmd_record_har, "analyse a HAR and record the scenario from it")):
        h = sub.add_parser(name, help=hlp)
        h.add_argument("path")
        h.add_argument("--scenario", required=(name == "record-har"))
        h.add_argument("--window", type=float, default=2.0, help="seconds within which a repeated write is a duplicate")
        h.add_argument("--max-retries", type=int, default=5, help="requests to one endpoint in 10 s after an error")
        h.add_argument("--slow-ms", type=float, default=10000.0, help="a request slower than this is flagged")
        if name == "har":
            h.add_argument("--json", action="store_true")
        h.set_defaults(func=func)
    r = sub.add_parser("result", help="record a scenario pass or fail")
    r.add_argument("scenario")
    r.add_argument("outcome", choices=["pass", "fail"])
    r.add_argument("--evidence", required=True)
    r.add_argument("--note")
    r.add_argument("--title")
    r.add_argument("--severity", choices=ledger.SEVERITIES)
    r.add_argument("--repro")
    r.add_argument("--fix")
    r.add_argument("--verify", action="store_true")
    r.set_defaults(func=cmd_result)
    n = sub.add_parser("na", help="mark a scenario not applicable")
    n.add_argument("scenario")
    n.add_argument("--note", required=True)
    n.set_defaults(func=cmd_na)
    sub.add_parser("status", help="coverage and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
