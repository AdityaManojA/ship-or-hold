#!/usr/bin/env python3
"""Check how a business runs on day one and write it as one section of the business plan.

Standard library only, no network. Reads one JSON file (ops.json) and works out,
from those inputs alone:

* the monthly cost of keeping the doors open and how much of it is an estimate;
* the hours the routines need each week against the hours the team has;
* a risk score for every dependency (likelihood times impact), ranked;
* single points of failure: a dependency with a high impact and no fallback;
* platforms and data sources used without permission;
* routines nobody owns, and whether one person owns all of them.

    python3 ops.py template --out ops.json
    python3 ops.py check ops.json            # print, record nothing
    python3 ops.py record ops.json           # write proof/business/ops.md and the ledger
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-ops"
PREFIX = "ops-"  # every finding this tool generates; a re-record clears these first
SECTION = os.path.join("business", "ops.md")
MARKER = "Written by proof-ops"
EXAMPLE = os.path.join(HERE, "ops.example.json")

WHEN = ["daily", "weekly", "monthly", "on_event"]
PER_WEEK = {"daily": 7.0, "weekly": 1.0, "monthly": 1.0 / 4.3}  # on_event is not counted
SOURCES = ["invoice", "quote", "owner", "estimate"]
KINDS = ["platform", "data", "supplier", "person", "licence", "payment"]
PERMISSIONS = ["written", "public_terms", "unknown", "none"]
PERMISSION_WORDS = {"written": "written", "public_terms": "public terms", "unknown": "unknown", "none": "none"}
LEGAL_STATUS = ["done", "to_do", "unknown"]

COVERAGE = [
    ("team-and-hours", "The team is listed with hours a week each"),
    ("routines-written", "At least three routines are written with steps and an owner"),
    ("costs-listed", "Monthly running costs are listed with where each number comes from"),
    ("dependencies-ranked", "Dependencies the business does not control are listed and ranked by risk"),
    ("support-defined", "Customer support has a channel, a response target and a named person"),
    ("legal-listed", "Legal items are listed for the owner to take to a professional"),
    ("kpis-set", "At least three KPIs are set with a target and a way to measure each"),
]


class OpsError(ValueError):
    pass


# ------------------------------------------------------------------ helpers

def _text(v):
    return " ".join(v.split()) if isinstance(v, str) else ""


def _cell(v):
    return _text(v).replace("|", "/") or "-"


def _list(spec, key):
    v = spec.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        raise OpsError("'%s' must be a list" % key)
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            raise OpsError("%s[%d] must be an object" % (key, i))
    return v


def _named(item, key, where):
    name = _text(item.get(key))
    if not name:
        raise OpsError("%s needs a '%s'" % (where, key))
    return name


def _enum(item, key, allowed, where):
    if item.get(key) not in allowed:
        raise OpsError("%s needs '%s': one of %s" % (where, key, ", ".join(allowed)))
    return item[key]


def _scale(item, key, where):
    if key not in item:
        raise OpsError("%s needs '%s' (1 to 5)" % (where, key))
    v = num.parse_number(item[key], field="%s.%s" % (where, key))
    if v != int(v) or not 1 <= v <= 5:
        raise OpsError("%s: '%s' must be a whole number from 1 to 5" % (where, key))
    return int(v)


def money(cur, x):
    s = "{:,.2f}".format(abs(x))
    if s.endswith(".00"):
        s = s[:-3]
    return ("-" if x < 0 else "") + cur + s


def _hours(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-") or "item"


# ------------------------------------------------------------------ analyse

def analyse(spec):
    """Everything the tool computes. Raises OpsError (or a num error) on a bad input."""
    if not isinstance(spec, dict):
        raise OpsError("the input must be a JSON object")
    cur = spec.get("currency")
    if not isinstance(cur, str) or not cur.strip():
        raise OpsError("'currency' is missing (a prefix such as \"Rs \" or \"$\"); there is no default currency")
    findings = []
    cov = {}

    def finding(fid, title, severity, category, description, fix):
        findings.append({"id": PREFIX + fid, "title": title, "severity": severity, "category": category,
                         "description": description, "fix": fix})

    # team
    team = []
    for i, m in enumerate(_list(spec, "team")):
        who = _named(m, "who", "team[%d]" % i)
        if "hours_per_week" not in m:
            raise OpsError("team member '%s' needs 'hours_per_week'" % who)
        hours = num.parse_number(m["hours_per_week"], field="team[%d].hours_per_week" % i)
        if hours < 0:
            raise OpsError("team member '%s' has negative hours" % who)
        team.append({"who": who, "hours": hours, "does": _text(m.get("does"))})
    team_hours = sum(m["hours"] for m in team) if team else None
    cov["team-and-hours"] = ("covered", None) if team else ("not_run", "the input lists no team")

    # routines
    routines = []
    for i, r in enumerate(_list(spec, "routines")):
        name = _named(r, "name", "routines[%d]" % i)
        when = _enum(r, "when", WHEN, "routine '%s'" % name)
        steps = r.get("steps") or []
        if not isinstance(steps, list):
            raise OpsError("routine '%s': 'steps' must be a list" % name)
        steps = [_text(s) for s in steps if _text(s)]
        minutes = None
        if r.get("time_minutes") not in (None, ""):
            minutes = num.parse_number(r["time_minutes"], field="routines[%d].time_minutes" % i)
            if minutes < 0:
                raise OpsError("routine '%s' has a negative time" % name)
        elif when != "on_event":
            raise OpsError("routine '%s' needs 'time_minutes' (how long one run takes)" % name)
        weekly = (minutes / 60.0) * PER_WEEK[when] if when in PER_WEEK else 0.0
        routines.append({"name": name, "when": when, "steps": steps, "owner": _text(r.get("owner")),
                         "minutes": minutes, "weekly_hours": weekly})
    routine_hours = sum(r["weekly_hours"] for r in routines) if routines else None
    written = [r for r in routines if r["steps"] and r["owner"]]
    if len(written) >= 3:
        cov["routines-written"] = ("covered", None)
    else:
        cov["routines-written"] = ("not_run", "%d of the 3 routines needed have steps and an owner" % len(written))
    for r in routines:
        if not r["owner"]:
            finding("routine-no-owner-%s" % _slug(r["name"]), "Routine '%s' has no owner" % r["name"], "low", "routines",
                    "Nobody is named for this routine, so on a busy day it is the one that gets skipped.",
                    "Name one person who does it and one who covers when they are away.")
    hours_fit = None
    if team and routines:
        hours_fit = team_hours - routine_hours
        if hours_fit < 0:
            finding("routines-exceed-hours", "The routines need more hours than the team has", "medium", "routines",
                    "The routines add up to %s hours a week and the team has %s hours a week between them. That is "
                    "before any work that is not a routine." % (_hours(routine_hours), _hours(team_hours)),
                    "Cut or shorten a routine, do one less often, or add hours, and put the new numbers in ops.json.")
    owners = {r["owner"].lower() for r in routines}
    one_owner = len(team) == 1 or (len(routines) >= 2 and len(owners) == 1 and "" not in owners)
    if one_owner:
        who = team[0]["who"] if len(team) == 1 else routines[0]["owner"]
        finding("one-person-owns-every-routine", "One person owns every routine", "low", "routines",
                "Everything runs through %s. If they are ill or away for a week, nothing in this plan says who does "
                "what." % who,
                "Write down which routines can pause for a week, and who could run the ones that cannot.")

    # monthly costs
    costs = []
    for i, c in enumerate(_list(spec, "monthly_costs")):
        label = _named(c, "label", "monthly_costs[%d]" % i)
        if "cost" not in c:
            raise OpsError("monthly cost '%s' needs a 'cost'" % label)
        costs.append({"label": label, "cost": num.parse_number(c["cost"], field="monthly_costs[%d].cost" % i),
                      "source": _enum(c, "source", SOURCES, "monthly cost '%s'" % label)})
    cost_total = estimate_total = estimate_share = None
    if costs:
        cost_total = sum(c["cost"] for c in costs)
        estimate_total = sum(c["cost"] for c in costs if c["source"] == "estimate")
        estimate_share = (estimate_total / cost_total) if cost_total > 0 else 0.0
        cov["costs-listed"] = ("covered", None)
        if estimate_share > 0.5:
            finding("costs-mostly-estimates", "Most monthly costs are estimates", "medium", "costs",
                    "%s of the %s a month (%.0f%%) is marked as an estimate, not an invoice, a quote or a figure the "
                    "owner pays today." % (money(cur, estimate_total), money(cur, cost_total), estimate_share * 100),
                    "Get a quote or an invoice for the largest estimated costs and change their source.")
    else:
        cov["costs-listed"] = ("not_run", "the input lists no monthly costs")

    # dependencies
    deps = []
    for i, x in enumerate(_list(spec, "dependencies")):
        name = _named(x, "name", "dependencies[%d]" % i)
        where = "dependency '%s'" % name
        dep = {"name": name, "kind": _enum(x, "kind", KINDS, where),
               "permission": _enum(x, "permission", PERMISSIONS, where),
               "likelihood": _scale(x, "likelihood", where), "impact": _scale(x, "impact", where),
               "used_for": _text(x.get("used_for")), "if_lost": _text(x.get("if_lost")),
               "fallback": _text(x.get("fallback"))}
        dep["risk"] = dep["likelihood"] * dep["impact"]
        dep["spof"] = dep["impact"] >= 4 and not dep["fallback"]
        dep["no_permission"] = dep["kind"] in ("platform", "data") and dep["permission"] in ("unknown", "none")
        deps.append(dep)
    deps.sort(key=lambda x: (-x["risk"], -x["impact"], x["name"].lower()))
    spofs = [x for x in deps if x["spof"]]
    cov["dependencies-ranked"] = ("covered", None) if deps else ("not_run", "the input lists no dependencies")
    for x in deps:
        if x["no_permission"] and x["impact"] >= 4:
            finding("no-permission-%s" % _slug(x["name"]), "The business depends on %s without permission" % x["name"],
                    "high", "dependencies",
                    "%s is a %s the business uses for %s. Permission is '%s' and the impact of losing it is %d of 5%s"
                    % (x["name"], x["kind"], x["used_for"] or "something the input does not say",
                       PERMISSION_WORDS[x["permission"]], x["impact"],
                       ": %s." % x["if_lost"].rstrip(".") if x["if_lost"] else "."),
                    "Read the terms or ask the owner of %s in writing. Until there is a yes, plan as if it can be "
                    "taken away." % x["name"])
        if x["spof"]:
            finding("single-point-%s" % _slug(x["name"]), "%s is a single point of failure" % x["name"],
                    "high" if x["risk"] >= 16 else "medium", "dependencies",
                    "Losing %s has an impact of %d of 5 and there is no fallback written down (risk score %d of 25)%s"
                    % (x["name"], x["impact"], x["risk"], ": %s." % x["if_lost"].rstrip(".") if x["if_lost"] else "."),
                    "Write down what the business does the day %s is gone, and test that it works." % x["name"])

    # support
    sup = spec.get("support")
    if sup is not None and not isinstance(sup, dict):
        raise OpsError("'support' must be an object")
    support = None
    if sup:
        target = None
        if sup.get("response_target_hours") not in (None, ""):
            target = num.parse_number(sup["response_target_hours"], field="support.response_target_hours")
        support = {"channel": _text(sup.get("channel")), "target": target, "who": _text(sup.get("who"))}
        gaps = [k for k, v in (("channel", support["channel"]), ("response_target_hours", target is not None),
                               ("who", support["who"])) if not v]
        cov["support-defined"] = ("not_run", "missing: %s" % ", ".join(gaps)) if gaps else ("covered", None)
    else:
        cov["support-defined"] = ("not_run", "the input has no support section")

    # legal
    legal = []
    for i, x in enumerate(_list(spec, "legal")):
        item = _named(x, "item", "legal[%d]" % i)
        legal.append({"item": item, "status": _enum(x, "status", LEGAL_STATUS, "legal item '%s'" % item),
                      "note": _text(x.get("note"))})
        if legal[-1]["status"] == "unknown":
            finding("legal-unknown-%s" % _slug(item), "Nobody knows where '%s' stands" % item, "low", "legal",
                    "The status of this legal item is unknown. It may be fine; the plan does not say.",
                    "The owner finds out and takes it to a lawyer or an accountant if it is not done.")
    cov["legal-listed"] = ("covered", None) if legal else ("not_run", "the input lists no legal items")

    # kpis
    kpis = [{"name": _text(k.get("name")), "target": _text(str(k.get("target")) if k.get("target") is not None else ""),
             "how": _text(k.get("how_measured"))} for k in _list(spec, "kpis")]
    full = [k for k in kpis if k["name"] and k["target"] and k["how"]]
    cov["kpis-set"] = ("covered", None) if len(full) >= 3 else (
        "not_run", "%d of the 3 KPIs needed have a target and a way to measure it" % len(full))

    return {"business": _text(spec.get("business")), "currency": cur, "team": team, "team_hours": team_hours,
            "routines": routines, "routine_hours": routine_hours, "hours_fit": hours_fit, "one_owner": one_owner,
            "costs": costs, "cost_total": cost_total, "estimate_total": estimate_total,
            "estimate_share": estimate_share, "dependencies": deps, "spofs": spofs,
            "no_permission": [x for x in deps if x["no_permission"]], "support": support, "legal": legal,
            "kpis": kpis, "findings": findings, "coverage": cov}


# ------------------------------------------------------------------- render

WHEN_WORDS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "on_event": "When it happens"}


def render(a):
    cur = a["currency"]
    L = ["# Operations", "",
         "How %s runs on day one, what it costs each month to keep going, and what it depends on that it does not "
         "control. Every line comes from what the owner said." % (a["business"] or "the business")]

    L += ["", "## Team", ""]
    if a["team"]:
        L += ["| Who | Hours a week | Does |", "| --- | ---: | --- |"]
        L += ["| %s | %s | %s |" % (_cell(m["who"]), _hours(m["hours"]), _cell(m["does"])) for m in a["team"]]
        L += ["", "Total: %s hours a week." % _hours(a["team_hours"])]
    else:
        L.append("No team listed yet.")

    L += ["", "## Routines", ""]
    if a["routines"]:
        for r in a["routines"]:
            time = "%s minutes a run" % _hours(r["minutes"]) if r["minutes"] is not None else "time not given"
            L.append("**%s** (%s, %s, owner: %s)" % (r["name"], WHEN_WORDS[r["when"]].lower(), time,
                                                     r["owner"] or "NOBODY NAMED"))
            L.append("")
            L += ["%d. %s" % (i + 1, s) for i, s in enumerate(r["steps"])] or ["No steps written."]
            L.append("")
        line = "The routines take %s hours a week" % _hours(a["routine_hours"])
        if a["hours_fit"] is not None:
            line += " against %s team hours a week: %s." % (
                _hours(a["team_hours"]),
                "%s hours left for everything else" % _hours(a["hours_fit"]) if a["hours_fit"] >= 0
                else "%s hours SHORT" % _hours(-a["hours_fit"]))
        else:
            line += "."
        L.append(line + " A daily routine is counted seven times a week and a monthly one is divided by 4.3. "
                 "Routines that only run when something happens are not counted.")
        if a["one_owner"]:
            L += ["", "One person owns every routine."]
    else:
        L.append("No routines written yet.")

    L += ["", "## Monthly costs", ""]
    if a["costs"]:
        L += ["| Cost | A month | Source |", "| --- | ---: | --- |"]
        L += ["| %s | %s | %s |" % (_cell(c["label"]), money(cur, c["cost"]), c["source"]) for c in a["costs"]]
        L.append("| **Total** | **%s** | |" % money(cur, a["cost_total"]))
        L += ["", "%s of that (%.0f%%) is an estimate." % (money(cur, a["estimate_total"]), a["estimate_share"] * 100)]
    else:
        L.append("No monthly costs listed yet.")

    L += ["", "## Dependencies", ""]
    if a["dependencies"]:
        L += ["Ranked by risk: how likely it is to go (1 to 5) times how much it hurts (1 to 5).", "",
              "| Depends on | Kind | Used for | Risk | Permission | If lost | Fallback |",
              "| --- | --- | --- | ---: | --- | --- | --- |"]
        for x in a["dependencies"]:
            L.append("| %s | %s | %s | %d (%d x %d) | %s | %s | %s |" % (
                _cell(x["name"]), x["kind"], _cell(x["used_for"]), x["risk"], x["likelihood"], x["impact"],
                PERMISSION_WORDS[x["permission"]] + (" (NOT HELD)" if x["no_permission"] else ""), _cell(x["if_lost"]),
                _cell(x["fallback"]) if x["fallback"] else "NONE"))
        L += ["", "Single points of failure (high impact, no fallback): %s." % (
            ", ".join(x["name"] for x in a["spofs"]) or "none listed")]
    else:
        L.append("No dependencies listed yet.")

    L += ["", "## What could stop this", ""]
    high = [f for f in a["findings"] if f["severity"] == "high"]
    if high:
        L.append(" ".join(f["description"] for f in high))
    elif a["dependencies"]:
        L.append("Nothing in the inputs rises to a high risk. That is a statement about what was listed, not about "
                 "what was left out.")
    else:
        L.append("Not known yet: no dependencies were listed, so nothing was ranked.")

    L += ["", "## Support", ""]
    s = a["support"]
    if s:
        L += ["- Channel: %s" % (s["channel"] or "not said"),
              "- Reply within: %s" % ("%s hours" % _hours(s["target"]) if s["target"] is not None else "not said"),
              "- Who answers: %s" % (s["who"] or "not said")]
    else:
        L.append("Not defined yet.")

    L += ["", "## Legal list", "", "A list for the owner to take to a lawyer or an accountant. It is not legal advice.", ""]
    if a["legal"]:
        L += ["| Item | Status | Note |", "| --- | --- | --- |"]
        L += ["| %s | %s | %s |" % (_cell(x["item"]), x["status"].replace("_", " "), _cell(x["note"])) for x in a["legal"]]
    else:
        L.append("Nothing listed yet.")

    L += ["", "## KPIs", ""]
    if a["kpis"]:
        L += ["| KPI | Target | How it is measured |", "| --- | --- | --- |"]
        L += ["| %s | %s | %s |" % (_cell(k["name"]), _cell(k["target"]), _cell(k["how"])) for k in a["kpis"]]
    else:
        L.append("None set yet.")

    L += ["", "Launch-day readiness (alerts, backups, rollback, on-call) is checked next by `/proof-release`.",
          "", "_%s from the owner's inputs (evidence/%s/ops.json). It describes the plan, not a tested operation._" % (MARKER, SKILL)]
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
        raise OpsError("cannot read %s: %s" % (path, e))
    except ValueError as e:
        raise OpsError("%s is not valid JSON: %s" % (path, e))


def cmd_template(a):
    ledger.guard_out(a.out, a.dir, a.force)
    with open(EXAMPLE, encoding="utf-8") as fh:
        text = fh.read()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("%s wrote an example to %s. Replace every value with your own." % (ledger.OK, a.out))
    return 0


def cmd_check(a):
    result = analyse(_load(a.file))
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
    result = analyse(spec)
    d = a.dir
    section = os.path.join(ledger.root(d), SECTION)
    ledger.guard_out(section, d, a.force, ours=MARKER)
    os.makedirs(os.path.dirname(section), exist_ok=True)
    with open(section, "w", encoding="utf-8") as fh:
        fh.write(render(result))
    with open(os.path.join(ledger.evidence_dir(d, SKILL), "ops.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    inputs = "evidence/%s/ops.json" % SKILL
    section_rel = SECTION.replace(os.sep, "/")

    ledger.init_coverage(d, SKILL, COVERAGE, keep_existing=False)
    for cid, _ in COVERAGE:
        status, note = result["coverage"][cid]
        ledger.mark_coverage(d, SKILL, cid, status, note=note,
                             evidence=[inputs, section_rel] if status == "covered" else None)

    metrics = []
    if result["hours_fit"] is not None:
        metrics.append({"id": "routine_hours_fit", "value": round(result["hours_fit"], 2), "unit": "hours",
                        "budget": 0, "op": ">=", "evidence": [inputs],
                        "source": "ops.json: team hours a week minus routine hours a week"})
    if result["dependencies"]:
        metrics.append({"id": "single_points_of_failure", "value": len(result["spofs"]), "unit": "count",
                        "budget": 0, "op": "==", "evidence": [inputs],
                        "source": "ops.json: dependencies with impact 4 or 5 and no fallback"})
    ledger.write_scores(d, SKILL, metrics)

    _clear_generated(d)
    for f in result["findings"]:
        ledger.write_finding(d, ledger.new_finding(SKILL, f["title"], f["severity"], id=f["id"], category=f["category"],
                                                   description=f["description"], fix=f["fix"], evidence=[inputs],
                                                   location="ops.json"))
        ledger.verify(d, SKILL, f["id"], by="evidence-file")

    print("%s wrote %s" % (ledger.OK, section))
    print(summary(result))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # --dir and --force work before the command, like every other Proof tool, or after it.
    ap.add_argument("--dir", default="proof", help="the proof folder (default: proof)")
    ap.add_argument("--force", action="store_true", help="overwrite a file even if Proof did not write it")
    sub = ap.add_subparsers(dest="cmd")
    t = sub.add_parser("template", help="write an example ops.json")
    t.add_argument("--out", required=True, help="where to write the example")
    c = sub.add_parser("check", help="print the section and what would be recorded; write nothing")
    c.add_argument("file", help="the ops.json")
    r = sub.add_parser("record", help="write proof/business/ops.md and the ledger")
    r.add_argument("file", help="the ops.json")
    for p in (t, c, r):
        p.add_argument("--dir", default=argparse.SUPPRESS, help="the proof folder (default: proof)")
        p.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                       help="overwrite a file Proof did not write")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: template, check or record")
    try:
        return {"template": cmd_template, "check": cmd_check, "record": cmd_record}[a.cmd](a)
    except ValueError as e:  # OpsError, num.MissingInput, num.BadNumber, ledger.LedgerError
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
