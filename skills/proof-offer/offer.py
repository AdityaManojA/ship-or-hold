#!/usr/bin/env python3
"""Build an offer from the obstacles buyers named. Standard library only, no network.

Reads one JSON file that lists every problem standing between the buyer and the
result they want, and one or more scored solutions per problem. It works out which
problems have no answer, builds the stack (core, bonuses, guarantee) from solutions
that are worth a lot to the buyer and cheap to deliver, flags anything in the stack
that does not exist today, lists the big objections the offer cannot answer yet, and
costs the guarantee. Every line traces to an input. A missing input is an error.

    python3 offer.py template --out offer.json
    python3 offer.py check offer.json      # print, record nothing
    python3 offer.py record offer.json     # write the section and the ledger
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-offer"
SECTION = os.path.join("business", "offer.md")
MARKER = "Written by proof-offer"
STAGES = ("before", "during", "after")
STAGE_WORDS = {"before": "Before buying", "during": "While using it", "after": "After the result"}
SOURCES = ("panel", "competitors", "owner", "estimate")
EXISTS = ("yes", "partly", "no")
ROLES = ("core", "bonus", "guarantee", "later", "not_ours")
STACK_ROLES = ("core", "bonus", "guarantee")
SCORE_KEYS = ("outcome", "belief", "speed", "ease")
SCORE_WORDS = {"outcome": "How much the buyer wants the result", "belief": "How sure the buyer is it will work",
               "speed": "How soon the result arrives", "ease": "How little effort it takes"}
MIN_VALUE, MAX_COST, MIN_PROBLEMS = 4, 2, 10

COVERAGE = [
    ("problems-listed", "At least ten buyer problems are listed, by stage and source"),
    ("solutions-scored", "Each solution is scored for value to the buyer and cost to deliver"),
    ("stack-built", "The stack holds only high-value, low-cost solutions"),
    ("guarantee-costed", "The guarantee has an expected cost"),
    ("promises-exist", "Every stack item was checked for whether it exists today"),
    ("retest-planned", "The new pitch will be re-tested with buyers"),
]

# Findings this tool writes: these exact ids, and any id starting with one of the prefixes.
FIXED_IDS = ("offer-big-objections-unanswered", "offer-urgency-not-true", "offer-fewer-than-ten-problems")
ID_PREFIXES = ("offer-not-built-", "offer-partly-built-", "offer-no-solution-")


class OfferError(ValueError):
    pass


# ------------------------------------------------------------------ helpers

def money(cur, x):
    neg = x < 0
    x = abs(x)
    body = "{:,.0f}".format(x) if abs(x - round(x)) < 0.005 else "{:,.2f}".format(x)
    return ("-" if neg else "") + cur + body


def cell(text):
    return " ".join(str(text if text is not None else "").replace("|", "/").split())


def _text(obj, key, where):
    v = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str) or not v.strip():
        raise OfferError("%s needs '%s'" % (where, key))
    return v.strip()


def _choice(obj, key, allowed, where):
    v = obj.get(key) if isinstance(obj, dict) else None
    if v not in allowed:
        raise OfferError("%s: '%s' must be one of %s" % (where, key, ", ".join(allowed)))
    return v


def _scale(obj, key, where, top):
    if not isinstance(obj, dict) or obj.get(key) in (None, ""):
        raise OfferError("%s needs '%s' (1 to %d)" % (where, key, top))
    v = num.parse_number(obj[key], field="%s.%s" % (where, key))
    if v != int(v) or not 1 <= v <= top:
        raise OfferError("%s: '%s' must be a whole number from 1 to %d" % (where, key, top))
    return int(v)


def _number(obj, key, where):
    if not isinstance(obj, dict) or obj.get(key) in (None, ""):
        raise OfferError("%s needs '%s'" % (where, key))
    return num.parse_number(obj[key], field="%s.%s" % (where, key))


# ----------------------------------------------------------------- analyse

def analyse(spec):
    if not isinstance(spec, dict):
        raise OfferError("the input must be a JSON object")
    cur = spec.get("currency")
    if not isinstance(cur, str) or not cur.strip():
        raise OfferError("'currency' is missing. Give the prefix money is printed with, such as "
                         "\"Rs \" or \"$\". There is no default currency.")
    core = _text(spec, "core", "the input")
    name = _text(spec, "name", "the input")

    raw = spec.get("problems")
    if not isinstance(raw, list) or not raw:
        raise OfferError("'problems' is missing or empty. List every obstacle between the buyer and the result.")
    problems, by_id = [], {}
    for i, p in enumerate(raw, 1):
        where = "problems entry %d" % i
        pid = _text(p, "id", where)
        if pid in by_id:
            raise OfferError("%s: the id '%s' is used twice" % (where, pid))
        item = {"id": pid, "stage": _choice(p, "stage", STAGES, where), "problem": _text(p, "problem", where),
                "source": _choice(p, "source", SOURCES, where), "solutions": []}
        problems.append(item)
        by_id[pid] = item

    raw = spec.get("solutions")
    if not isinstance(raw, list):
        raise OfferError("'solutions' is missing. Give a list, one or more per problem (it may be empty at first).")
    solutions = []
    for i, s in enumerate(raw, 1):
        where = "solutions entry %d" % i
        pid = _text(s, "problem", where)
        if pid not in by_id:
            raise OfferError("%s: there is no problem with id '%s'" % (where, pid))
        item = {"n": i, "problem": pid, "solution": _text(s, "solution", where),
                "value": _scale(s, "value", where, 5), "cost": _scale(s, "cost", where, 5),
                "exists": _choice(s, "exists", EXISTS, where), "role": _choice(s, "role", ROLES, where)}
        item["clears_bar"] = item["value"] >= MIN_VALUE and item["cost"] <= MAX_COST
        item["in_stack"] = item["role"] in STACK_ROLES and item["clears_bar"]
        solutions.append(item)
        by_id[pid]["solutions"].append(item)

    unsolved_why = {}
    raw = spec.get("unsolved") or []
    if not isinstance(raw, list):
        raise OfferError("'unsolved' must be a list of {problem, why}")
    for i, u in enumerate(raw, 1):
        where = "unsolved entry %d" % i
        pid = u.get("problem", u.get("id")) if isinstance(u, dict) else None
        if pid not in by_id:
            raise OfferError("%s: 'problem' must be the id of a listed problem" % where)
        unsolved_why[pid] = _text(u, "why", where)

    stack = [s for s in solutions if s["in_stack"]]
    left_out = [s for s in solutions if s["role"] in STACK_ROLES and not s["clears_bar"]]
    not_built = [s for s in stack if s["exists"] == "no"]
    partly_built = [s for s in stack if s["exists"] == "partly"]
    cannot_fix = [s for s in solutions if s["role"] in ("later", "not_ours")]
    big_unanswered = [s for s in cannot_fix if s["value"] >= MIN_VALUE]
    biggest_unanswered = [s for s in cannot_fix if s["value"] == 5]
    no_solution = [p for p in problems if not p["solutions"]]
    answered = [p for p in problems
                if any(s["in_stack"] and s["exists"] in ("yes", "partly") for s in p["solutions"])]

    guarantee = spec.get("guarantee")
    if guarantee is not None:
        if not isinstance(guarantee, dict):
            raise OfferError("'guarantee' must be {promise, cost_if_claimed, expected_claim_rate}")
        cost = _number(guarantee, "cost_if_claimed", "guarantee")
        rate = _number(guarantee, "expected_claim_rate", "guarantee")
        if not 0 <= rate <= 1:
            raise OfferError("guarantee: 'expected_claim_rate' is a share between 0 and 1 (0.05 means 5 in 100)")
        guarantee = {"promise": _text(guarantee, "promise", "guarantee"), "cost_if_claimed": cost,
                     "expected_claim_rate": rate, "expected_cost": cost * rate}

    urgency = spec.get("urgency")
    if urgency is not None:
        if not isinstance(urgency, dict) or not isinstance(urgency.get("true"), bool):
            raise OfferError("'urgency' must be {what, true, ends} with 'true' set to true or false")
        urgency = {"what": _text(urgency, "what", "urgency"), "true": urgency["true"],
                   "ends": str(urgency.get("ends") or "").strip()}

    scores = spec.get("scores")
    if scores is not None:
        if not isinstance(scores, dict):
            raise OfferError("'scores' must be {before: {...}, after: {...}}")
        out = {}
        for when in ("before", "after"):
            if not isinstance(scores.get(when), dict):
                raise OfferError("scores needs '%s' with outcome, belief, speed and ease" % when)
            out[when] = {k: _scale(scores[when], k, "scores.%s" % when, 10) for k in SCORE_KEYS}
        out["change"] = {k: out["after"][k] - out["before"][k] for k in SCORE_KEYS}
        scores = out

    retest = spec.get("retest")
    retest = retest.strip() if isinstance(retest, str) else ""

    res = {
        "business": str(spec.get("business") or "").strip(), "currency": cur, "core": core, "name": name,
        "problems": problems, "solutions": solutions, "by_id": by_id,
        "by_stage": {st: [p for p in problems if p["stage"] == st] for st in STAGES},
        "by_source": {so: sum(1 for p in problems if p["source"] == so) for so in SOURCES},
        "stack": stack, "left_out": left_out, "not_built": not_built, "partly_built": partly_built,
        "cannot_fix": cannot_fix, "big_unanswered": big_unanswered, "biggest_unanswered": biggest_unanswered,
        "no_solution": no_solution, "unsolved_why": unsolved_why, "answered": answered,
        "answered_pct": 100.0 * len(answered) / len(problems),
        "guarantee": guarantee, "guarantee_in_solutions": any(s["role"] == "guarantee" for s in solutions),
        "urgency": urgency, "scores": scores, "retest": retest,
    }
    res["findings"] = findings_for(res)
    return res


def findings_for(r):
    out = []

    def add(fid, severity, title, description, fix):
        out.append({"id": fid, "severity": severity, "title": title, "description": description, "fix": fix})

    for s in r["not_built"]:
        add("offer-not-built-%d" % s["n"], "high",
            "The offer promises something that is not built: %s" % s["solution"],
            "This is in the stack as %s, answering \"%s\", and the input says it does not exist today."
            % (s["role"], r["by_id"][s["problem"]]["problem"]),
            "Build it before the offer goes out, or mark it 'later' and take it out of the pitch.")
    for s in r["partly_built"]:
        add("offer-partly-built-%d" % s["n"], "medium",
            "The offer promises something that is only partly built: %s" % s["solution"],
            "This is in the stack as %s and the input says it exists only in part." % s["role"],
            "Finish it, or word the promise so it covers only the part that exists.")
    if r["biggest_unanswered"]:
        add("offer-big-objections-unanswered", "medium", "The biggest objections are not answered by the offer",
            "Solutions scored 5 for value that are marked later or not ours: %s."
            % "; ".join("%s (%s)" % (s["solution"], s["role"]) for s in r["biggest_unanswered"]),
            "Say so plainly in the pitch, or find a version of the answer that can be delivered now.")
    if r["urgency"] and not r["urgency"]["true"]:
        add("offer-urgency-not-true", "medium", "Urgency is not true",
            "The offer uses this reason to act now, and the input says it is not true: %s." % r["urgency"]["what"],
            "Remove it. Use a deadline or a limit only when it is real.")
    for p in r["no_solution"]:
        why = r["unsolved_why"].get(p["id"])
        add("offer-no-solution-%s" % ledger.slug(p["id"]), "low", "No solution for: %s" % p["problem"],
            "Problem '%s' (%s) has no solution listed.%s" % (p["id"], p["stage"], " Reason given: %s" % why if why else ""),
            "Add a solution, or keep it under 'unsolved' with the reason so the pitch does not imply it is handled.")
    if len(r["problems"]) < MIN_PROBLEMS:
        add("offer-fewer-than-ten-problems", "low", "Fewer than ten problems listed",
            "%d problems are listed. A short list usually means the obstacles before buying or after the "
            "result were not walked through." % len(r["problems"]),
            "Go through before, during and after again, and pull objections from the buyer panel and competitor reviews.")
    return out


# ------------------------------------------------------------------ render

def render(r):
    cur = r["currency"]
    L = ["# The offer", ""]
    L += ["**%s**%s" % (r["name"], " from %s" % r["business"] if r["business"] else ""), "",
          "The result the buyer gets: %s" % r["core"], "",
          "%d problems listed, %d solutions scored, %d in the stack. The stack answers %.0f%% of the problems "
          "with something that exists at least in part."
          % (len(r["problems"]), len(r["solutions"]), len(r["stack"]), r["answered_pct"]), ""]

    L += ["## The problems, in the buyer's words", ""]
    for st in STAGES:
        L += ["### %s" % STAGE_WORDS[st], ""]
        if not r["by_stage"][st]:
            L += ["None listed.", ""]
            continue
        for p in r["by_stage"][st]:
            tag = "" if p["solutions"] else " **No solution yet.**"
            L.append("- %s (%s; source: %s)%s" % (p["problem"], p["id"], p["source"], tag))
        L.append("")
    L.append("Where the problems come from: %s." % ", ".join(
        "%d %s" % (r["by_source"][so], so) for so in SOURCES if r["by_source"][so]))
    if r["by_source"]["estimate"]:
        L.append("Estimates, not something a buyer said: %d." % r["by_source"]["estimate"])

    L += ["", "## The solutions, scored", "",
          "Value is what it is worth to the buyer and cost is what it takes to deliver, both from 1 to 5. "
          "A solution goes in the stack only at value %d or more and cost %d or less." % (MIN_VALUE, MAX_COST), ""]
    if r["solutions"]:
        L += ["| Problem | Solution | Value | Cost | Exists today | Role | In the stack |",
              "| --- | --- | ---: | ---: | --- | --- | --- |"]
        for s in r["solutions"]:
            L.append("| %s | %s | %d | %d | %s | %s | %s |" % (
                cell(s["problem"]), cell(s["solution"]), s["value"], s["cost"], s["exists"],
                s["role"].replace("_", " "), "yes" if s["in_stack"] else "no"))
    else:
        L.append("No solutions are listed yet.")

    L += ["", "## The stack", ""]
    if r["stack"]:
        for role, title in (("core", "Core"), ("bonus", "Bonuses"), ("guarantee", "Guarantee")):
            items = [s for s in r["stack"] if s["role"] == role]
            if not items:
                continue
            L += ["**%s**" % title, ""]
            for s in items:
                state = {"yes": "", "partly": " (only partly built today)", "no": " (**not built today**)"}[s["exists"]]
                L.append("- %s%s" % (s["solution"], state))
            L.append("")
    else:
        L += ["Nothing clears the bar yet, so there is no stack.", ""]
    if r["left_out"]:
        L.append("Left out because the value is too low or the cost too high: %s." % "; ".join(
            "%s (value %d, cost %d)" % (s["solution"], s["value"], s["cost"]) for s in r["left_out"]))
        L.append("")

    L += ["## The guarantee", ""]
    g = r["guarantee"]
    if g:
        L += [g["promise"], "",
              "If claimed it costs %s. Expected claims: %s%% of sales. Expected cost: %s on every sale."
              % (money(cur, g["cost_if_claimed"]), "{:g}".format(g["expected_claim_rate"] * 100),
                 money(cur, g["expected_cost"]))]
    elif r["guarantee_in_solutions"]:
        L.append("A guarantee is listed among the solutions but it has no cost and no expected claim rate yet.")
    else:
        L.append("No guarantee is offered.")

    L += ["", "## A reason to act now", ""]
    u = r["urgency"]
    if not u:
        L.append("None used.")
    else:
        L.append("%s%s %s" % (u["what"].rstrip(".") + ".", " Ends: %s." % u["ends"].rstrip(".") if u["ends"] else "",
                              "This is true." if u["true"] else "**This is not true and must not be used.**"))

    L += ["", "## Before and after", ""]
    if r["scores"]:
        sc = r["scores"]
        L += ["Scored from 1 to 10 by the person who filled in the input. A judgement, not a measurement.", "",
              "| | Before | After | Change |", "| --- | ---: | ---: | ---: |"]
        for k in SCORE_KEYS:
            L.append("| %s | %d | %d | %+d |" % (SCORE_WORDS[k], sc["before"][k], sc["after"][k], sc["change"][k]))
    else:
        L.append("Not scored.")

    L += ["", "## What the offer cannot fix", ""]
    wrote = False
    for s in r["cannot_fix"]:
        wrote = True
        L.append("- %s: %s. Answers \"%s\" (value %d)%s" % (
            "Later" if s["role"] == "later" else "Not ours to fix", s["solution"],
            r["by_id"][s["problem"]]["problem"], s["value"],
            ". **A big objection the offer does not answer yet.**" if s["value"] >= MIN_VALUE else "."))
    for p in r["no_solution"]:
        wrote = True
        why = r["unsolved_why"].get(p["id"])
        L.append("- No solution: \"%s\"%s" % (p["problem"], ". Why: %s" % why if why else ". No reason given."))
    for pid, why in r["unsolved_why"].items():
        if r["by_id"][pid]["solutions"]:
            wrote = True
            L.append("- Not answered: \"%s\". Why: %s" % (r["by_id"][pid]["problem"], why))
    if not wrote:
        L.append("Nothing is listed as out of reach.")

    L += ["", "## Re-testing the pitch", ""]
    L.append(r["retest"] or "No re-test is planned. The new pitch has not been put in front of buyers.")
    if r["findings"]:
        L += ["", "## What the numbers flag", ""]
        for f in r["findings"]:
            L.append("- **%s** (%s). %s" % (f["title"], f["severity"], f["description"]))
    L += ["", "_%s from the inputs given. Every promise must be one the business can deliver and afford. "
          "Not legal advice._" % MARKER]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- template

def template():
    problems = [
        ("p1", "before", "I do not know if it will taste like the one I had in Kyoto", "panel"),
        ("p2", "before", "It costs more than my usual coffee", "panel"),
        ("p3", "before", "I do not know where you are or when you open", "owner"),
        ("p4", "before", "The last place like this closed after two months", "competitors"),
        ("p5", "during", "The queue at lunch is too long", "competitors"),
        ("p6", "during", "I cannot tell which drink to order", "panel"),
        ("p7", "during", "There is nowhere to sit with a laptop", "panel"),
        ("p8", "during", "I want oat milk and they charge extra everywhere", "competitors"),
        ("p9", "after", "I forget to come back", "owner"),
        ("p10", "after", "I cannot make it this well at home", "estimate"),
        ("p11", "after", "Parking nearby is hard", "panel"),
    ]
    return {
        "business": "Matcha cafe",
        "currency": "Rs ",
        "name": "The first-cup promise",
        "core": "A proper cup of matcha in under four minutes, or the next one is free.",
        "problems": [{"id": i, "stage": s, "problem": p, "source": src} for i, s, p, src in problems],
        "solutions": [
            {"problem": "p1", "solution": "Free taster sip before you order", "value": 5, "cost": 1, "exists": "yes", "role": "core"},
            {"problem": "p2", "solution": "Loyalty card: sixth cup free", "value": 4, "cost": 2, "exists": "yes", "role": "bonus"},
            {"problem": "p3", "solution": "Map pin and opening hours on every post", "value": 4, "cost": 1, "exists": "yes", "role": "bonus"},
            {"problem": "p5", "solution": "Order ahead by message, collect at the hatch", "value": 5, "cost": 2, "exists": "partly", "role": "core"},
            {"problem": "p6", "solution": "Three-drink menu board with a picture of each", "value": 4, "cost": 1, "exists": "yes", "role": "core"},
            {"problem": "p8", "solution": "Oat milk at no extra charge", "value": 4, "cost": 2, "exists": "yes", "role": "bonus"},
            {"problem": "p1", "solution": "Next cup free if you do not like it", "value": 5, "cost": 2, "exists": "yes", "role": "guarantee"},
            {"problem": "p7", "solution": "Seating for twenty with sockets", "value": 4, "cost": 5, "exists": "no", "role": "later"},
            {"problem": "p9", "solution": "A reminder message on the day your card is nearly full", "value": 3, "cost": 2, "exists": "no", "role": "later"},
            {"problem": "p10", "solution": "Take-home tin and a one-page method", "value": 4, "cost": 2, "exists": "yes", "role": "bonus"},
            {"problem": "p11", "solution": "Street parking is the council's", "value": 3, "cost": 5, "exists": "no", "role": "not_ours"},
        ],
        "unsolved": [{"problem": "p4", "why": "Only staying open answers this. Nothing in the offer can."}],
        "guarantee": {"promise": "If you do not like your first cup, the next one is free.",
                      "cost_if_claimed": "62", "expected_claim_rate": "0.05"},
        "urgency": {"what": "Opening fortnight: the taster sip comes with a free refill", "true": True,
                    "ends": "14 days after opening"},
        "scores": {"before": {"outcome": 6, "belief": 4, "speed": 5, "ease": 5},
                   "after": {"outcome": 7, "belief": 7, "speed": 8, "ease": 7}},
        "retest": "Show the new pitch to the same buyer panel and compare how many say they would buy.",
    }


# ---------------------------------------------------------------- commands

def _load(path):
    if not os.path.isfile(path):
        raise OfferError("input file not found: %s" % path)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except ValueError as e:
        raise OfferError("%s is not valid JSON: %s" % (path, e))


def cmd_template(a):
    ledger.guard_out(a.out, a.dir, a.force)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(template(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("%s wrote an example offer file to %s. Replace every value with your own." % (ledger.OK, a.out))
    return 0


def cmd_check(a):
    print(render(analyse(_load(a.file))))
    print("%s nothing was recorded. Use 'record' to write the section and the ledger." % ledger.OK)
    return 0


def _remove_stale(d):
    folder = os.path.dirname(ledger.finding_path(d, SKILL, "x"))
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        fid = name[:-5] if name.endswith(".json") else None
        if fid and (fid in FIXED_IDS or fid.startswith(ID_PREFIXES)):
            os.remove(os.path.join(folder, name))


def cmd_record(a):
    spec = _load(a.file)
    res = analyse(spec)
    d, root = a.dir, ledger.root(a.dir)

    section = os.path.join(root, SECTION)
    ledger.guard_out(section, d, a.force, ours=MARKER)
    os.makedirs(os.path.dirname(section), exist_ok=True)
    with open(section, "w", encoding="utf-8") as fh:
        fh.write(render(res))
    with open(os.path.join(ledger.evidence_dir(d, SKILL), "offer.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    rel_section = SECTION.replace(os.sep, "/")
    evidence = ["evidence/%s/offer.json" % SKILL, rel_section]

    ledger.init_coverage(d, SKILL, COVERAGE, keep_existing=False)

    def cover(cid, yes, why_not=None):
        if yes:
            ledger.mark_coverage(d, SKILL, cid, "covered", evidence=evidence)
        elif why_not:
            ledger.mark_coverage(d, SKILL, cid, "not_run", note=why_not)

    n = len(res["problems"])
    cover("problems-listed", n >= MIN_PROBLEMS, "Only %d problems are listed; at least %d are needed." % (n, MIN_PROBLEMS))
    cover("solutions-scored", bool(res["solutions"]))
    cover("stack-built", bool(res["stack"]), "No solution reaches value %d with cost %d or less." % (MIN_VALUE, MAX_COST))
    if res["guarantee"]:
        cover("guarantee-costed", True)
    elif res["guarantee_in_solutions"]:
        cover("guarantee-costed", False, "A guarantee is listed among the solutions but has no cost or claim rate.")
    else:
        ledger.mark_coverage(d, SKILL, "guarantee-costed", "not_applicable", note="No guarantee is offered.")
    # Covered once the stack has been checked: an unbuilt promise is a high finding, not a gap.
    if res["stack"]:
        note = None
        if res["not_built"]:
            note = "Checked. Not built today: %s (each is a high finding)." % "; ".join(
                s["solution"] for s in res["not_built"])
        ledger.mark_coverage(d, SKILL, "promises-exist", "covered", note=note, evidence=evidence)
    cover("retest-planned", bool(res["retest"]))

    ledger.write_scores(d, SKILL, [
        {"id": "problems_answered_pct", "value": round(res["answered_pct"], 1), "unit": "percent",
         "budget": 0, "op": ">=", "evidence": [rel_section]},
        {"id": "stack_promises_missing", "value": len(res["not_built"]), "unit": "count",
         "budget": 0, "op": "==", "evidence": [rel_section]},
    ])

    _remove_stale(d)
    for f in res["findings"]:
        finding = ledger.new_finding(SKILL, f["title"], f["severity"], id=f["id"], category="offer",
                                     description=f["description"], fix=f["fix"], evidence=evidence,
                                     location=rel_section)
        ledger.write_finding(d, finding)
        ledger.verify(d, SKILL, f["id"], by="evidence-file")

    print("%s wrote %s and recorded coverage, 2 scores and %d finding(s)." % (ledger.OK, section, len(res["findings"])))
    for f in res["findings"]:
        print("%s %s: %s" % (ledger.BAD, f["severity"], f["title"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # --dir and --force work before the command, like every other Proof tool, or after it.
    ap.add_argument("--dir", default="proof", help="the proof folder (default: proof)")
    ap.add_argument("--force", action="store_true", help="overwrite a file even if Proof did not write it")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--dir", default=argparse.SUPPRESS, help="the proof folder (default: proof)")
        p.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                       help="overwrite a file even if Proof did not write it")

    t = sub.add_parser("template", help="write a runnable example input")
    t.add_argument("--out", required=True, help="where to write the example")
    common(t)
    t.set_defaults(func=cmd_template)
    for name, func, text in (("check", cmd_check, "print the offer and record nothing"),
                             ("record", cmd_record, "write the section file and the ledger")):
        p = sub.add_parser(name, help=text)
        p.add_argument("file", help="the offer JSON")
        common(p)
        p.set_defaults(func=func)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: template, check or record")
    try:
        return a.func(a)
    except ValueError as e:  # OfferError, and num.MissingInput / num.BadNumber
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
