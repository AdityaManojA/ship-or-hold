#!/usr/bin/env python3
"""Check a go-to-market plan and write it as one section of the business plan.

Standard library only, no network, and it never reads the clock: the plan says what
"today" is. Reads one JSON file (gtm.json) and works out, from those inputs alone:

* whether the positioning, the brand basics and the channels are all there;
* what the channels cost a month against the budget;
* whether the first real-world test has a pass mark and a fail mark with a number in
  each, and dates that are in order and not in the past;
* whether the countdown is dated sensibly against today and the launch date;
* the promotion gate: every verified high or critical finding in the evidence ledger
  needs a dated fix, due on or before the day the first test starts.

    python3 gtm.py template --out gtm.json
    python3 gtm.py check gtm.json            # print, record nothing
    python3 gtm.py record gtm.json           # write proof/business/go-to-market.md and the ledger
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-go-to-market"
PREFIX = "gtm-"  # every finding this tool generates; a re-record clears these first
SECTION = os.path.join("business", "go-to-market.md")
MARKER = "Written by proof-go-to-market"
EXAMPLE = os.path.join(HERE, "gtm.example.json")
VERDICT_PY = os.path.join(HERE, "..", "proof-report", "verdict.py")

POSITIONING = ["for_who", "problem", "what_it_is", "unlike"]
NAME_CHECKS = ["trademark", "domain", "handle", "meaning"]
CHECK_STATUS = ["done", "to_do"]
CHANNEL_EVIDENCE = ["panel", "competitors", "owner", "estimate"]
BLOCKING = ("high", "critical")

COVERAGE = [
    ("positioning-stated", "Positioning stated: who it is for, the problem, what it is, unlike what"),
    ("channels-chosen", "At least two channels chosen, each with a cost, a first action and a measure"),
    ("brand-basics", "Brand basics: the promise, three voice traits and all four name checks listed"),
    ("first-test-defined", "A first real-world test with dates and a pass and fail mark set before it runs"),
    ("countdown-dated", "A dated countdown with nothing overdue and no critical task after launch"),
    ("promotion-gate", "Every open blocker in the ledger has a fix due before promotion starts"),
]


class GtmError(ValueError):
    pass


# ------------------------------------------------------------------ helpers

def _text(v):
    return " ".join(v.split()) if isinstance(v, str) else ""


def _cell(v):
    return _text(v).replace("|", "/") or "-"


def _date(value, field):
    if value is None or value == "":
        raise GtmError("'%s' is missing (an ISO date such as 2026-11-30)" % field)
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise GtmError("'%s' must be an ISO date (YYYY-MM-DD), got %r" % (field, value))
    try:
        return datetime.date.fromisoformat(value.strip())
    except ValueError:
        raise GtmError("'%s' is not a real date: %r" % (field, value))


def _list(spec, key):
    v = spec.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        raise GtmError("'%s' must be a list" % key)
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            raise GtmError("%s[%d] must be an object" % (key, i))
    return v


def _has_number(text):
    return bool(re.search(r"\d", _text(text)))


def money(cur, x):
    s = "{:,.2f}".format(abs(x))
    if s.endswith(".00"):
        s = s[:-3]
    return ("-" if x < 0 else "") + cur + s


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:44] or "item"


# --------------------------------------------------------------------- gate

def _load_verdict():
    spec = importlib.util.spec_from_file_location("proof_verdict_gtm", VERDICT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ledger_blockers(d):
    """Verified high and critical findings across every skill.

    Returns (list, None) when the ledger could be read, or (None, reason) when it
    could not. A ledger that cannot be read is never treated as a clean one.
    """
    if d is None or not os.path.isdir(ledger.root(d)):
        return None, "there is no proof folder to read"
    try:
        r = _load_verdict().compute(d)
    except (Exception, SystemExit) as e:  # noqa: B014 - any failure means "not checked"
        return None, "the ledger could not be read (%s)" % e
    if r.get("validation"):
        return None, "the ledger does not validate; run /proof-report to see why"
    if r.get("target") is None:
        return None, "the proof folder has no target.json, so there is no ledger to read; run /proof-plan"
    out = []
    for f in r["findings"]:
        if f["status"] != "verified" or f["severity"] not in BLOCKING:
            continue
        if f["skill"] == SKILL and f["id"].startswith(PREFIX):
            continue  # this tool's own output, rebuilt on every run
        out.append({"ref": "%s/%s" % (f["skill"], f["id"]), "severity": f["severity"], "title": f["title"]})
    out.sort(key=lambda b: b["ref"])
    return out, None


# ------------------------------------------------------------------ analyse

def analyse(spec, d=None):
    """Everything the tool computes. Raises GtmError (or a num error) on a bad input."""
    if not isinstance(spec, dict):
        raise GtmError("the input must be a JSON object")
    cur = spec.get("currency")
    if not isinstance(cur, str) or not cur.strip():
        raise GtmError("'currency' is missing (a prefix such as \"Rs \" or \"$\"); there is no default currency")
    today = _date(spec.get("today"), "today")
    findings = []
    cov = {}

    def finding(fid, title, severity, category, description, fix):
        findings.append({"id": PREFIX + fid, "title": title, "severity": severity, "category": category,
                         "description": description, "fix": fix})

    # positioning
    pos = spec.get("positioning")
    if pos is not None and not isinstance(pos, dict):
        raise GtmError("'positioning' must be an object")
    pos = {k: _text((pos or {}).get(k)) for k in POSITIONING}
    pos_missing = [k for k in POSITIONING if not pos[k]]
    business = _text(spec.get("business"))
    statement = None
    if not pos_missing:
        statement = "For %s, who %s, %s is %s, unlike %s." % (
            pos["for_who"], pos["problem"], business or "this product", pos["what_it_is"], pos["unlike"])
        cov["positioning-stated"] = ("covered", None)
    else:
        cov["positioning-stated"] = ("not_run", "missing: %s" % ", ".join(pos_missing))

    # brand basics
    promise = _text(spec.get("promise"))
    voice = [{"trait": _text(v.get("trait")), "do": _text(v.get("do")), "do_not": _text(v.get("do_not"))}
             for v in _list(spec, "voice")]
    voice_full = [v for v in voice if v["trait"] and v["do"] and v["do_not"]]
    checks = []
    for i, c in enumerate(_list(spec, "name_checks")):
        if c.get("check") not in NAME_CHECKS:
            raise GtmError("name_checks[%d].check must be one of %s" % (i, ", ".join(NAME_CHECKS)))
        if c.get("status") not in CHECK_STATUS:
            raise GtmError("name_checks[%d].status must be one of %s" % (i, ", ".join(CHECK_STATUS)))
        checks.append({"check": c["check"], "status": c["status"], "note": _text(c.get("note"))})
    checks_missing = [k for k in NAME_CHECKS if k not in {c["check"] for c in checks}]
    brand_gaps = []
    if not promise:
        brand_gaps.append("no promise")
    if len(voice_full) < 3:
        brand_gaps.append("%d of 3 voice traits with a do and a do-not" % len(voice_full))
    if checks_missing:
        brand_gaps.append("name checks not listed: %s" % ", ".join(checks_missing))
    cov["brand-basics"] = ("not_run", "; ".join(brand_gaps)) if brand_gaps else ("covered", None)
    for c in checks:
        if c["status"] == "to_do" and c["check"] in ("trademark", "domain"):
            finding("name-check-to-do-%s" % c["check"], "The %s check on the name is still to do" % c["check"],
                    "medium", "brand",
                    "The name is being promoted before the owner has run the %s check. A clash found after "
                    "launch means renaming with customers already using the old name." % c["check"],
                    "The owner runs the %s check and writes the date and the result in the note." % c["check"])

    # channels
    channels = []
    for i, c in enumerate(_list(spec, "channels")):
        name = _text(c.get("name"))
        if not name:
            raise GtmError("channels[%d] needs a 'name'" % i)
        if "cost_per_month" not in c:
            raise GtmError("channel '%s' needs 'cost_per_month' (0 if it costs nothing)" % name)
        cost = num.parse_number(c["cost_per_month"], field="channels[%d].cost_per_month" % i)
        if cost < 0:
            raise GtmError("channel '%s' has a negative cost_per_month" % name)
        if c.get("evidence") not in CHANNEL_EVIDENCE:
            raise GtmError("channel '%s' needs 'evidence': one of %s" % (name, ", ".join(CHANNEL_EVIDENCE)))
        channels.append({"name": name, "cost": cost, "first_action": _text(c.get("first_action")),
                         "measure": _text(c.get("measure")), "evidence": c["evidence"]})
    budget = total = headroom = None
    if channels:
        if "budget_per_month" not in spec:
            raise GtmError("'budget_per_month' is missing (0 is allowed and means the plan must work unpaid)")
        budget = num.parse_number(spec["budget_per_month"], field="budget_per_month")
        if budget < 0:
            raise GtmError("'budget_per_month' cannot be negative")
        total = sum(c["cost"] for c in channels)
        headroom = budget - total
    elif spec.get("budget_per_month") not in (None, ""):
        budget = num.parse_number(spec["budget_per_month"], field="budget_per_month")
    complete = [c for c in channels if c["first_action"] and c["measure"]]
    if len(complete) >= 2:
        cov["channels-chosen"] = ("covered", None)
    else:
        cov["channels-chosen"] = ("not_run", "%d of the 2 channels needed have a cost, a first action and a measure"
                                  % len(complete))
    if channels:
        if total > budget:
            finding("channels-over-budget", "Channels cost more than the budget", "medium", "channels",
                    "The channels add up to %s a month against a budget of %s a month." % (money(cur, total), money(cur, budget)),
                    "Drop or shrink a channel, or have the owner confirm a larger budget.")
        if budget == 0 and any(c["cost"] > 0 for c in channels):
            finding("paid-reach-no-budget", "The plan depends on paid reach with no budget", "low", "channels",
                    "The budget is %s a month but these channels cost money: %s." % (
                        money(cur, 0), ", ".join("%s (%s)" % (c["name"], money(cur, c["cost"])) for c in channels if c["cost"] > 0)),
                    "Replace each paid channel with an unpaid one, or have the owner set a budget.")
        if not any(c["evidence"] != "estimate" for c in channels):
            finding("channels-all-estimates", "Every channel rests on an estimate", "medium", "channels",
                    "No channel is backed by what the buyer panel said, where competitors' customers are, or the "
                    "owner's own experience. The channel list is a guess.",
                    "Ask real buyers where they look for this, or read where competitors' customers talk, and "
                    "pick at least one channel from that.")
        for c in channels:
            if not c["measure"]:
                finding("channel-no-measure-%s" % _slug(c["name"]), "Channel '%s' has no measure" % c["name"], "low",
                        "channels", "Nothing says how the owner will know whether this channel worked.",
                        "Name one thing to count for this channel and where the count comes from.")

    # the first test
    test = spec.get("first_test")
    if test is not None and not isinstance(test, dict):
        raise GtmError("'first_test' must be an object")
    ft = None
    if test:
        starts, ends = _date(test.get("starts"), "first_test.starts"), _date(test.get("ends"), "first_test.ends")
        people = None
        if test.get("people") not in (None, ""):
            people = num.parse_number(test["people"], field="first_test.people")
        ft = {"what": _text(test.get("what")), "where": _text(test.get("where")), "starts": starts, "ends": ends,
              "people": people, "pass": _text(test.get("pass")), "fail": _text(test.get("fail")),
              "measure": _text(test.get("measure"))}
        ft["marks_ok"] = _has_number(ft["pass"]) and _has_number(ft["fail"])
        ft["in_order"] = starts <= ends
        ft["in_past"] = starts < today
        gaps = [k for k in ("what", "where", "measure") if not ft[k]]
        if people is None or people <= 0:
            gaps.append("people (how many are asked)")
        if not ft["marks_ok"]:
            gaps.append("a pass and a fail mark with a number in each")
            weak = [k for k in ("pass", "fail") if not _has_number(ft[k])]
            finding("first-test-no-pass-mark", "The first test has no pass mark", "high", "first-test",
                    "The %s mark%s missing or has no number in it, so any result can be read as a success after "
                    "the fact." % (" and ".join(weak), " is" if len(weak) == 1 else "s are"),
                    "Before the test runs, write the pass mark and the fail mark as plain words with a number, "
                    "for example '12 of 60 people asked pay'.")
        if not ft["in_order"]:
            gaps.append("the end date is before the start date")
        if ft["in_past"]:
            gaps.append("it starts before today (%s)" % today.isoformat())
        cov["first-test-defined"] = ("not_run", "missing or wrong: %s" % "; ".join(gaps)) if gaps else ("covered", None)
    else:
        cov["first-test-defined"] = ("not_run", "the input has no first_test")

    # countdown
    launch = _date(spec["launch_date"], "launch_date") if spec.get("launch_date") not in (None, "") else None
    countdown = []
    for i, t in enumerate(_list(spec, "countdown")):
        task = _text(t.get("task"))
        if not task:
            raise GtmError("countdown[%d] needs a 'task'" % i)
        if not isinstance(t.get("critical"), bool):
            raise GtmError("countdown task '%s' needs 'critical': true or false" % task)
        countdown.append({"date": _date(t.get("date"), "countdown[%d].date" % i), "task": task,
                          "critical": t["critical"], "owner": _text(t.get("owner"))})
    in_order = all(countdown[i]["date"] <= countdown[i + 1]["date"] for i in range(len(countdown) - 1))
    overdue = [t for t in countdown if t["date"] < today]
    late = [t for t in countdown if t["critical"] and launch is not None and t["date"] > launch]
    if not countdown:
        cov["countdown-dated"] = ("not_run", "the input has no countdown")
    elif overdue or late:
        notes = []
        if overdue:
            notes.append("%d task(s) dated before today" % len(overdue))
        if late:
            notes.append("%d critical task(s) dated after the launch date" % len(late))
        cov["countdown-dated"] = ("not_run", "; ".join(notes))
    else:
        cov["countdown-dated"] = ("covered", None)

    # before any promotion, and the gate
    fixes = []
    for i, b in enumerate(_list(spec, "before_promotion")):
        fix = _text(b.get("fix"))
        if not fix:
            raise GtmError("before_promotion[%d] needs a 'fix'" % i)
        fixes.append({"fix": fix, "ref": _text(b.get("ref")), "due": _date(b.get("due"), "before_promotion[%d].due" % i)})
    gate = {"checked": False, "reason": None, "blockers": [], "ungated": []}
    blockers, reason = ledger_blockers(d)
    if blockers is None:
        gate["reason"] = reason
    elif ft is None:
        gate["reason"] = "the input has no first_test, so there is no start date to hold the fixes against"
        gate["blockers"] = [dict(b, gated=None, due=None) for b in blockers]
    else:
        gate["checked"] = True
        for b in blockers:
            dues = sorted(f["due"] for f in fixes if f["ref"] == b["ref"])
            b = dict(b, due=dues[0].isoformat() if dues else None, gated=bool(dues) and dues[0] <= ft["starts"])
            gate["blockers"].append(b)
            if not b["gated"]:
                gate["ungated"].append(b)
                why = ("its fix is due %s, after the first test starts on %s" % (b["due"], ft["starts"].isoformat())
                       if dues else "no entry in before_promotion names it")
                tag = "%s-%s" % (_slug(b["ref"])[:34].strip("-"), hashlib.sha256(b["ref"].encode("utf-8")).hexdigest()[:6])
                finding("blocker-open-%s" % tag, "Promotion is planned while a blocker is open: %s" % b["ref"],
                        "high", "promotion-gate",
                        "The ledger holds a verified %s finding (%s) and %s." % (b["severity"], b["title"], why),
                        "Add a before_promotion entry with ref \"%s\" and a due date on or before %s, or move the "
                        "first test." % (b["ref"], ft["starts"].isoformat()))
    cov["promotion-gate"] = ("covered", None) if gate["checked"] else ("not_run", "not checked: %s" % gate["reason"])

    access = []
    for i, p in enumerate(_list(spec, "accessibility")):
        if not isinstance(p.get("passes"), bool):
            raise GtmError("accessibility[%d] needs 'passes': true or false" % i)
        access.append({"pair": _text(p.get("pair")), "passes": p["passes"],
                       "ratio": num.parse_number(p.get("ratio"), field="accessibility[%d].ratio" % i)})

    return {"business": business, "currency": cur, "today": today, "positioning": pos, "pos_missing": pos_missing,
            "statement": statement, "promise": promise, "voice": voice, "name_checks": checks,
            "name_checks_missing": checks_missing, "channels": channels, "budget": budget, "total": total,
            "headroom": headroom, "first_test": ft, "countdown": countdown, "in_order": in_order,
            "overdue": overdue, "late": late, "launch": launch, "fixes": fixes, "gate": gate,
            "accessibility": access, "findings": findings, "coverage": cov}


# ------------------------------------------------------------------- render

def render(a):
    cur = a["currency"]
    L = ["# Go to market", ""]
    L.append("%s, as planned on %s. Every line below comes from the plan the owner gave; nothing here has "
             "happened yet." % (a["business"] or "This business", a["today"].isoformat()))
    L += ["", "## Positioning", ""]
    if a["statement"]:
        L.append(a["statement"])
    else:
        L.append("Not stated yet. Missing: %s." % ", ".join(a["pos_missing"]))

    L += ["", "## Promise and voice", ""]
    L.append("Promise: %s" % (a["promise"] or "not written yet."))
    L.append("")
    if a["voice"]:
        L += ["| Trait | Do | Do not |", "| --- | --- | --- |"]
        L += ["| %s | %s | %s |" % (_cell(v["trait"]), _cell(v["do"]), _cell(v["do_not"])) for v in a["voice"]]
    else:
        L.append("No voice traits listed yet.")

    L += ["", "## Name checks", "", "The owner runs these. Proof records the answer and does not check a register itself.", ""]
    if a["name_checks"]:
        L += ["| Check | Status | Note |", "| --- | --- | --- |"]
        L += ["| %s | %s | %s |" % (c["check"], "done" if c["status"] == "done" else "to do", _cell(c["note"]))
              for c in a["name_checks"]]
    if a["name_checks_missing"]:
        L += ["", "Not listed: %s." % ", ".join(a["name_checks_missing"])]

    L += ["", "## Channels", ""]
    if a["channels"]:
        L += ["| Channel | Cost a month | First action | How it is measured | Rests on |", "| --- | ---: | --- | --- | --- |"]
        L += ["| %s | %s | %s | %s | %s |" % (_cell(c["name"]), money(cur, c["cost"]), _cell(c["first_action"]),
                                             _cell(c["measure"]), c["evidence"]) for c in a["channels"]]
        L.append("")
        state = "%s left" % money(cur, a["headroom"]) if a["headroom"] >= 0 else "%s over" % money(cur, -a["headroom"])
        L.append("Total %s a month against a budget of %s a month: %s." % (money(cur, a["total"]), money(cur, a["budget"]), state))
        if a["budget"] == 0:
            L.append("There is no budget, so the plan has to work unpaid.")
    else:
        L.append("No channels chosen yet.")

    L += ["", "## Before any promotion", ""]
    g = a["gate"]
    if g["checked"]:
        if g["ungated"]:
            L.append("The evidence ledger holds %d blocker(s) with no fix due before the first test. Nothing is "
                     "promoted until each has one." % len(g["ungated"]))
        elif g["blockers"]:
            L.append("The evidence ledger holds %d blocker(s); each has a fix due before the first test starts." % len(g["blockers"]))
        else:
            L.append("The evidence ledger holds no verified high or critical finding as of this run.")
    else:
        L.append("The promotion gate was not checked: %s. This is not a pass." % g["reason"])
    L.append("")
    if g["blockers"]:
        L += ["| Blocker in the ledger | Severity | Fix due | State |", "| --- | --- | --- | --- |"]
        for b in g["blockers"]:
            state = "not checked" if b["gated"] is None else ("fix dated in time" if b["gated"] else "OPEN, promotion waits")
            L.append("| %s: %s | %s | %s | %s |" % (b["ref"], _cell(b["title"]), b["severity"], b.get("due") or "none", state))
        L.append("")
    if a["fixes"]:
        L += ["| Fix | Ledger ref | Due |", "| --- | --- | --- |"]
        L += ["| %s | %s | %s |" % (_cell(f["fix"]), f["ref"] or "-", f["due"].isoformat())
              for f in sorted(a["fixes"], key=lambda f: f["due"])]
    else:
        L.append("No fixes are listed.")

    L += ["", "## The first test", ""]
    ft = a["first_test"]
    if ft:
        L += ["- What: %s" % (ft["what"] or "not said"),
              "- Where: %s" % (ft["where"] or "not said"),
              "- When: %s to %s" % (ft["starts"].isoformat(), ft["ends"].isoformat()),
              "- People asked: %s" % ("{:,.0f}".format(ft["people"]) if ft["people"] is not None else "not said"),
              "- Measured by: %s" % (ft["measure"] or "not said"),
              "- Pass mark: %s" % (ft["pass"] or "not written"),
              "- Fail mark: %s" % (ft["fail"] or "not written"), ""]
        if not ft["marks_ok"]:
            L.append("The pass and fail marks must each hold a number, written before the test runs.")
        if not ft["in_order"]:
            L.append("The end date is before the start date.")
        if ft["in_past"]:
            L.append("The test is dated to start before today (%s)." % a["today"].isoformat())
        if ft["marks_ok"] and ft["in_order"] and not ft["in_past"]:
            L.append("Both marks were written down before the test. The result is read against them, not against hope.")
    else:
        L.append("No first test is defined yet.")

    L += ["", "## Countdown", ""]
    if a["countdown"]:
        if a["launch"]:
            L += ["Launch date: %s." % a["launch"].isoformat(), ""]
        L += ["| Date | Task | Critical | Owner | Note |", "| --- | --- | --- | --- | --- |"]
        for t in sorted(a["countdown"], key=lambda t: t["date"]):
            note = []
            if t["date"] < a["today"]:
                note.append("dated before today")
            if t in a["late"]:
                note.append("critical but after launch")
            L.append("| %s | %s | %s | %s | %s |" % (t["date"].isoformat(), _cell(t["task"]),
                                                    "CRITICAL" if t["critical"] else "", t["owner"] or "nobody named",
                                                    ", ".join(note)))
        if not a["in_order"]:
            L += ["", "The tasks were given out of date order; they are sorted here."]
        L += ["", "Launch-day readiness (alerts, backups, rollback, on-call) is checked by `/proof-release`."]
    else:
        L.append("No countdown yet.")

    if a["accessibility"]:
        L += ["", "## Brand colours and contrast", "", "| Pair | Contrast ratio | Passes |", "| --- | ---: | --- |"]
        L += ["| %s | %.2f | %s |" % (_cell(p["pair"]), p["ratio"], "yes" if p["passes"] else "NO") for p in a["accessibility"]]

    L += ["", "_%s from the owner's inputs (evidence/%s/gtm.json). It is a plan, not a result._" % (MARKER, SKILL)]
    return "\n".join(L) + "\n"


def summary(a):
    L = ["", "Coverage:"]
    for cid, _ in COVERAGE:
        status, note = a["coverage"][cid]
        L.append("  %s %s%s" % (ledger.OK if status == "covered" else ledger.BAD, cid, " (%s)" % note if note else ""))
    L.append("Findings: %d" % len(a["findings"]))
    L += ["  %s [%s] %s" % (ledger.BAD, f["severity"], f["title"]) for f in a["findings"]]
    return "\n".join(L)


# ----------------------------------------------------------------- commands

def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as e:
        raise GtmError("cannot read %s: %s" % (path, e))
    except ValueError as e:
        raise GtmError("%s is not valid JSON: %s" % (path, e))


def cmd_template(a):
    ledger.guard_out(a.out, a.dir, a.force)
    with open(EXAMPLE, encoding="utf-8") as fh:
        text = fh.read()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("%s wrote an example plan to %s. Replace every value with your own, and set 'today'." % (ledger.OK, a.out))
    return 0


def cmd_check(a):
    result = analyse(_load(a.file), a.dir)
    print(render(result) + summary(result))
    print("\nNothing was recorded. Use 'record' to write the section and the ledger.")
    return 0


def _clear_generated(d):
    folder = os.path.join(ledger.root(d), "findings", SKILL)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith(PREFIX) and name.endswith(".json"):
                os.remove(os.path.join(folder, name))


def cmd_record(a):
    spec = _load(a.file)
    result = analyse(spec, a.dir)
    d = a.dir
    section = os.path.join(ledger.root(d), SECTION)
    ledger.guard_out(section, d, a.force, ours=MARKER)
    os.makedirs(os.path.dirname(section), exist_ok=True)
    with open(section, "w", encoding="utf-8") as fh:
        fh.write(render(result))
    ev = ledger.evidence_dir(d, SKILL)
    with open(os.path.join(ev, "gtm.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    g = result["gate"]
    with open(os.path.join(ev, "promotion-gate.json"), "w", encoding="utf-8") as fh:
        json.dump({"checked": g["checked"], "not_checked_because": g["reason"], "blockers": g["blockers"],
                   "first_test_starts": result["first_test"]["starts"].isoformat() if result["first_test"] else None},
                  fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    inputs = "evidence/%s/gtm.json" % SKILL
    gate_ev = "evidence/%s/promotion-gate.json" % SKILL
    section_rel = SECTION.replace(os.sep, "/")

    ledger.init_coverage(d, SKILL, COVERAGE, keep_existing=False)
    for cid, _ in COVERAGE:
        status, note = result["coverage"][cid]
        evidence = [inputs, gate_ev] if cid == "promotion-gate" else [inputs, section_rel]
        ledger.mark_coverage(d, SKILL, cid, status, note=note, evidence=evidence if status == "covered" else None)

    metrics = []
    if result["headroom"] is not None:
        metrics.append({"id": "channel_cost_within_budget", "value": round(result["headroom"], 2), "unit": "currency",
                        "budget": 0, "op": ">=", "evidence": [inputs],
                        "source": "gtm.json: budget a month minus channel costs a month, in %s" % result["currency"].strip()})
    if g["checked"]:
        metrics.append({"id": "blockers_without_a_fix_date", "value": len(g["ungated"]), "unit": "count",
                        "budget": 0, "op": "==", "evidence": [gate_ev],
                        "source": "verified high and critical findings in the ledger against before_promotion"})
    ledger.write_scores(d, SKILL, metrics)

    _clear_generated(d)
    for f in result["findings"]:
        evidence = [inputs, gate_ev] if f["category"] == "promotion-gate" else [inputs]
        ledger.write_finding(d, ledger.new_finding(SKILL, f["title"], f["severity"], id=f["id"], category=f["category"],
                                                   description=f["description"], fix=f["fix"], evidence=evidence,
                                                   location="gtm.json"))
        ledger.verify(d, SKILL, f["id"], by="evidence-file")

    print("%s wrote %s" % (ledger.OK, section))
    print(summary(result))
    if not g["checked"]:
        print("%s promotion gate not checked: %s" % (ledger.BAD, g["reason"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # --dir and --force work before the command, like every other Proof tool, or after it.
    ap.add_argument("--dir", default="proof", help="the proof folder (default: proof)")
    ap.add_argument("--force", action="store_true", help="overwrite a file even if Proof did not write it")
    sub = ap.add_subparsers(dest="cmd")
    t = sub.add_parser("template", help="write an example gtm.json")
    t.add_argument("--out", required=True, help="where to write the example")
    c = sub.add_parser("check", help="print the section and what would be recorded; write nothing")
    c.add_argument("file", help="the gtm.json")
    r = sub.add_parser("record", help="write proof/business/go-to-market.md and the ledger")
    r.add_argument("file", help="the gtm.json")
    for p in (t, c, r):
        p.add_argument("--dir", default=argparse.SUPPRESS, help="the proof folder (default: proof)")
        p.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                       help="overwrite a file Proof did not write")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: template, check or record")
    try:
        return {"template": cmd_template, "check": cmd_check, "record": cmd_record}[a.cmd](a)
    except ValueError as e:  # GtmError, num.MissingInput, num.BadNumber, ledger.LedgerError
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
