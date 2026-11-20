#!/usr/bin/env python3
"""Set a price from three things that can be checked. Standard library only, no network.

Reads one JSON file and reports where a candidate price stands against what buyers
said (a four-answer price-sensitivity survey), what competitors charge, and what one
unit costs. A product that is free to its user and earns from someone else is handled
as its own model: the "price" is what 1,000 visits earn, counted from live revenue
lines only, with unproven lines shown as upside and paused lines counted as zero.
Every number traces to an input. A missing input is an error, never a silent default.

    python3 pricing.py template --out pricing.json          # a paid example
    python3 pricing.py template --out pricing.json --free   # a free-to-user example
    python3 pricing.py sensitivity pricing.json             # the four survey points only
    python3 pricing.py check pricing.json                   # print, record nothing
    python3 pricing.py record pricing.json                  # write the section and the ledger
"""

import argparse
import csv
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-pricing"
SECTION = os.path.join("business", "pricing.md")
MARKER = "Written by proof-pricing"
KEYS = ("too_cheap", "cheap", "expensive", "too_expensive")
MODELS = ("paid", "free_to_user")
KINDS = ("per_1000_pageviews", "per_action", "fixed_monthly")
STATUSES = ("live", "unproven", "paused")
MIN_USABLE = 10
MAX_DROPPED_SHARE = 0.25
DAYS = 30  # break-even a day is the monthly figure over a 30-day month; the report says so

COVERAGE = [
    ("buyers-asked", "Buyers were asked what they would pay and a usable range came out"),
    ("competitors-compared", "The price was compared with what competitors charge"),
    ("margin-checked", "What is left after the cost of serving one unit or 1,000 visits"),
    ("ladder-or-lines", "The price ladder, or the revenue lines with their status"),
    ("real-test-planned", "A price test with real buyers is planned"),
]

# Every finding id this tool can write. A re-record deletes these before writing again.
FINDING_IDS = ("pricing-below-cost", "pricing-no-live-revenue", "pricing-answers-unusable",
               "pricing-outside-range", "pricing-rests-on-estimate", "pricing-no-real-test")


class PricingError(ValueError):
    pass


# ------------------------------------------------------------------ helpers

def money(cur, x):
    neg = x < 0
    x = abs(x)
    body = "{:,.0f}".format(x) if abs(x - round(x)) < 0.005 else "{:,.2f}".format(x)
    return ("-" if neg else "") + cur + body


def cell(text):
    """Make free text safe inside a Markdown table cell."""
    return " ".join(str(text if text is not None else "").replace("|", "/").split())


def _text(obj, key, where):
    v = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(v, str) or not v.strip():
        raise PricingError("%s needs '%s'" % (where, key))
    return v.strip()


def _number(obj, key, where):
    if not isinstance(obj, dict) or obj.get(key) in (None, ""):
        raise PricingError("%s needs '%s'" % (where, key))
    return num.parse_number(obj[key], field="%s.%s" % (where, key))


def _list(spec, key):
    v = spec.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        raise PricingError("'%s' must be a list" % key)
    return v


def currency_of(spec):
    cur = spec.get("currency")
    if not isinstance(cur, str) or not cur.strip():
        raise PricingError("'currency' is missing. Give the prefix money is printed with, "
                           "such as \"Rs \" or \"$\". There is no default currency.")
    return cur


def model_of(spec):
    model = spec.get("model")
    if model not in MODELS:
        raise PricingError("'model' must be \"paid\" or \"free_to_user\"")
    return model


# ------------------------------------------------------------- the survey

# The buyer panel (proof-buyers) writes the same four answers under longer names.
PANEL_KEYS = {"price_too_cheap": "too_cheap", "price_bargain": "cheap",
              "price_getting_expensive": "expensive", "price_too_expensive": "too_expensive"}


def _as_answer(d):
    """A dict's four price answers under the survey's own key names, or None."""
    if any(k in d for k in KEYS):
        return d
    if any(k in d for k in PANEL_KEYS):
        return {short: d.get(long) for long, short in PANEL_KEYS.items()}
    return None


def _find_answer(obj):
    """The dict that holds the four keys: the object itself or one level down."""
    if not isinstance(obj, dict):
        return None
    found = _as_answer(obj)
    if found is not None:
        return found
    for v in obj.values():
        if isinstance(v, dict):
            found = _as_answer(v)
            if found is not None:
                return found
    return None


def load_answers(spec, base):
    """Return (rows, source label, files skipped), or (None, None, 0) when no survey was given."""
    given = [k for k in ("answers", "answers_file", "answers_dir") if spec.get(k)]
    if len(given) > 1:
        raise PricingError("give only one of answers, answers_file and answers_dir (got %s)" % ", ".join(given))
    if not given:
        return None, None, 0
    key = given[0]
    if key == "answers":
        if not isinstance(spec["answers"], list):
            raise PricingError("'answers' must be a list of {too_cheap, cheap, expensive, too_expensive}")
        return list(spec["answers"]), "the answers in the input file", 0
    path = spec[key] if os.path.isabs(str(spec[key])) else os.path.join(base, str(spec[key]))
    if key == "answers_file":
        if not os.path.isfile(path):
            raise PricingError("answers_file not found: %s" % spec[key])
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            cols = [(c or "").strip().lower() for c in (reader.fieldnames or [])]
            missing = [k for k in KEYS if k not in cols]
            if missing:
                raise PricingError("answers_file %s has no column %s" % (spec[key], ", ".join(missing)))
            rows = [{(k or "").strip().lower(): v for k, v in row.items()} for row in reader]
        if not rows:
            raise PricingError("answers_file %s holds no answers" % spec[key])
        return rows, os.path.basename(path), 0
    if not os.path.isdir(path):
        raise PricingError("answers_dir not found: %s" % spec[key])
    rows, skipped = [], 0
    for name in sorted(os.listdir(path)):
        if not name.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(path, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            skipped += 1
            continue
        found = [_find_answer(x) for x in (data if isinstance(data, list) else [data])]
        found = [f for f in found if f is not None]
        if not found:
            skipped += 1
        rows.extend(found)
    if not rows:
        raise PricingError("answers_dir %s holds no JSON file with the keys %s" % (spec[key], ", ".join(KEYS)))
    return rows, "the folder %s" % os.path.basename(os.path.normpath(path)), skipped


def _share_at_least(values, p):
    return sum(1 for v in values if v >= p) / float(len(values))


def _share_at_most(values, p):
    return sum(1 for v in values if v <= p) / float(len(values))


def _crossing(falling, rising, grid):
    """Price where the falling share meets the rising share, by straight line between grid points."""
    prev = None
    for p in grid:
        diff = _share_at_least(falling, p) - _share_at_most(rising, p)
        if diff <= 0:
            if prev is None or diff == 0:
                return p
            p0, d0 = prev
            return p0 + (p - p0) * d0 / (d0 - diff)
        prev = (p, diff)
    return None


def shares_at(usable, p):
    """Share of usable answers that call price p too cheap, cheap, expensive, too expensive."""
    col = lambda i: [u[i] for u in usable]  # noqa: E731
    return {"too_cheap": _share_at_least(col(0), p), "cheap": _share_at_least(col(1), p),
            "expensive": _share_at_most(col(2), p), "too_expensive": _share_at_most(col(3), p)}


def sensitivity(rows):
    """Work out the four crossing points, or say why they cannot be reported."""
    usable, dropped = [], 0
    for r in rows:
        try:
            vals = [num.parse_number(r.get(k) if isinstance(r, dict) else None, field=k) for k in KEYS]
        except ValueError:
            dropped += 1
            continue
        if vals[0] < 0 or any(vals[i] > vals[i + 1] for i in range(3)):
            dropped += 1
            continue
        usable.append(vals)
    total = len(rows)
    out = {"total": total, "usable": len(usable), "dropped": dropped,
           "usable_pct": (100.0 * len(usable) / total) if total else 0.0,
           "ok": False, "why": "", "points": None, "rows": usable}
    if total and dropped / float(total) > MAX_DROPPED_SHARE:
        out["why"] = ("%d of %d answers were dropped, which is more than a quarter. That usually means buyers "
                      "answered in mixed units (a month against a year, or one item against a pack)."
                      % (dropped, total))
        return out
    if len(usable) < MIN_USABLE:
        out["why"] = "only %d usable answers were left and at least %d are needed." % (len(usable), MIN_USABLE)
        return out
    grid = sorted(set(v for u in usable for v in u))
    col = lambda i: [u[i] for u in usable]  # noqa: E731
    points = {
        "marginal_cheapness": _crossing(col(0), col(2), grid),
        "optimal": _crossing(col(0), col(3), grid),
        "indifference": _crossing(col(1), col(2), grid),
        "marginal_expensiveness": _crossing(col(1), col(3), grid),
    }
    if any(v is None for v in points.values()):
        out["why"] = "the answers do not cross, so no range can be read from them."
        return out
    out.update(ok=True, points=points)
    return out


POINT_WORDS = [
    ("marginal_cheapness", "Point of marginal cheapness",
     "below this, more buyers call it too cheap to trust than call it expensive"),
    ("optimal", "Optimal point", "as many buyers call it too cheap as call it too expensive; the fewest reject it"),
    ("indifference", "Indifference point", "as many buyers call it cheap as call it expensive"),
    ("marginal_expensiveness", "Point of marginal expensiveness",
     "above this, more buyers call it too expensive than call it a bargain"),
]


# ------------------------------------------------------------------- paid

def _margin(price, cost):
    m = price - cost
    return {"price": price, "margin": m, "margin_pct": (100.0 * m / price) if price else 0.0,
            "below_cost": price <= cost}


def analyse_paid(spec, cur, base):
    price = _number(spec, "price", "the input")
    price_for = _text(spec, "price_for", "the input")
    cost = _number(spec, "variable_cost", "the input")
    if price <= 0:
        raise PricingError("'price' must be above zero for a paid product; use model \"free_to_user\" "
                           "when the user pays nothing")

    rows, source, skipped = load_answers(spec, base)
    sens = sensitivity(rows) if rows is not None else None
    position = None
    if sens and sens["ok"]:
        lo, hi = sens["points"]["marginal_cheapness"], sens["points"]["marginal_expensiveness"]
        position = "below" if price < lo else ("above" if price > hi else "inside")

    competitors = []
    for i, c in enumerate(_list(spec, "competitor_prices"), 1):
        where = "competitor_prices entry %d" % i
        competitors.append({"name": _text(c, "name", where), "price": _number(c, "price", where),
                            "includes": c.get("includes", ""), "url": c.get("url", "")})
    comp = None
    if competitors:
        ps = [c["price"] for c in competitors]
        med = statistics.median(ps)
        comp = {"lowest": min(ps), "median": med, "highest": max(ps),
                "where": "below the lowest" if price < min(ps) else (
                    "above the highest" if price > max(ps) else "between the lowest and the highest"),
                "vs_median_pct": (100.0 * (price - med) / med) if med else None}

    candidates = [price]
    for i, c in enumerate(_list(spec, "candidates"), 1):
        v = num.parse_number(c, field="candidates entry %d" % i)
        if v not in candidates:
            candidates.append(v)
    margins = [_margin(p, cost) for p in candidates]

    ladder = []
    for i, s in enumerate(_list(spec, "ladder"), 1):
        where = "ladder entry %d" % i
        ladder.append({"name": _text(s, "name", where), "price": _number(s, "price", where),
                       "why_step_up": s.get("why_step_up", "")})
        ladder[-1].update(_margin(ladder[-1]["price"], cost))

    tests = []
    for i, t in enumerate(_list(spec, "tests"), 1):
        where = "tests entry %d" % i
        tests.append({"price": _number(t, "price", where), "how": _text(t, "how", where)})

    return {"model": "paid", "price": price, "price_for": price_for, "variable_cost": cost,
            "sens": sens, "answers_source": source, "answers_skipped": skipped, "position": position,
            "competitors": competitors, "comp": comp, "margins": margins, "ladder": ladder,
            "tests": tests}


# ------------------------------------------------------------ free to user

def analyse_free(spec, cur, base):
    raw = spec.get("revenue_lines")
    if not isinstance(raw, list) or not raw:
        raise PricingError("'revenue_lines' is missing or empty. A product that is free to its user "
                           "needs at least one line saying who pays instead.")
    fixed_cost = _number(spec, "fixed_monthly_cost", "the input")
    var_cost = _number(spec, "variable_cost_per_1000_visits", "the input")
    lines = []
    for i, r in enumerate(raw, 1):
        where = "revenue_lines entry %d" % i
        name = _text(r, "name", where)
        kind, status = r.get("kind"), r.get("status")
        if kind not in KINDS:
            raise PricingError("%s: 'kind' must be one of %s" % (where, ", ".join(KINDS)))
        if status not in STATUSES:
            raise PricingError("%s: 'status' must be one of %s" % (where, ", ".join(STATUSES)))
        source = _text(r, "source", where)
        amount = _number(r, "amount", where)
        per_1000, monthly, how = 0.0, 0.0, ""
        if kind == "per_1000_pageviews":
            pages = _number(r, "pages_per_visit", where)
            per_1000 = amount * pages
            how = "%s per 1,000 pageviews x %s pages a visit" % (money(cur, amount), "{:g}".format(pages))
        elif kind == "per_action":
            rate = _number(r, "rate", where)
            if not 0 <= rate <= 1:
                raise PricingError("%s: 'rate' is the share of visits that take the action, "
                                   "between 0 and 1 (0.02 means 2 in 100)" % where)
            per_1000 = amount * rate * 1000.0
            how = "%s per action x %s%% of visits" % (money(cur, amount), "{:g}".format(rate * 100))
        else:
            monthly = amount
            how = "%s a month whatever the visits" % money(cur, amount)
        lines.append({"name": name, "kind": kind, "status": status, "source": source,
                      "estimate": source.lower() == "estimate", "per_1000": per_1000,
                      "monthly": monthly, "how": how})

    def case(statuses):
        rev = sum(x["per_1000"] for x in lines if x["status"] in statuses)
        fixed_rev = sum(x["monthly"] for x in lines if x["status"] in statuses)
        contribution = rev - var_cost
        left = fixed_cost - fixed_rev
        if left <= 0:
            month = 0.0
        elif contribution <= 0:
            month = math.inf
        else:
            month = left / contribution * 1000.0
        return {"per_1000": rev, "fixed_revenue": fixed_rev, "contribution": contribution,
                "fixed_left": left, "breakeven_month": month,
                "breakeven_day": month / DAYS if not math.isinf(month) else math.inf}

    base_case, upside = case(("live",)), case(("live", "unproven"))
    live = [x for x in lines if x["status"] == "live"]
    # Share of base-case revenue that rests on an estimate. Per-visit revenue is judged first;
    # when no per-visit line is live, the fixed monthly lines are judged instead.
    if base_case["per_1000"] > 0:
        est = sum(x["per_1000"] for x in live if x["estimate"]) / base_case["per_1000"]
    elif base_case["fixed_revenue"] > 0:
        est = sum(x["monthly"] for x in live if x["estimate"]) / base_case["fixed_revenue"]
    else:
        est = 0.0

    competitors = []
    for i, c in enumerate(_list(spec, "competitor_prices"), 1):
        where = "competitor_prices entry %d" % i
        competitors.append({"name": _text(c, "name", where), "price": _number(c, "price", where),
                            "includes": c.get("includes", ""), "url": c.get("url", "")})
    tests = []
    for i, t in enumerate(_list(spec, "tests"), 1):
        tests.append({"price": None, "how": _text(t, "how", "tests entry %d" % i)})

    return {"model": "free_to_user", "lines": lines, "fixed_monthly_cost": fixed_cost,
            "variable_cost_per_1000": var_cost, "base": base_case, "upside": upside,
            "live_count": len(live), "estimate_share": est, "competitors": competitors,
            "tests": tests}


def analyse(spec, base="."):
    if not isinstance(spec, dict):
        raise PricingError("the input must be a JSON object")
    cur = currency_of(spec)
    model = model_of(spec)
    res = analyse_paid(spec, cur, base) if model == "paid" else analyse_free(spec, cur, base)
    offer = spec.get("opening_offer")
    if offer is not None:
        if not isinstance(offer, dict):
            raise PricingError("'opening_offer' must be {what, ends}")
        offer = {"what": _text(offer, "what", "opening_offer"), "ends": _text(offer, "ends", "opening_offer")}
    res.update(currency=cur, business=str(spec.get("business") or "").strip(), opening_offer=offer)
    res["findings"] = findings_for(res)
    return res


# ---------------------------------------------------------------- findings

def findings_for(r):
    cur, out = r["currency"], []

    def add(fid, severity, title, description, fix):
        out.append({"id": fid, "severity": severity, "title": title, "description": description, "fix": fix})

    if r["model"] == "paid":
        if r["price"] <= r["variable_cost"]:
            add("pricing-below-cost", "high", "The price does not cover the cost of one unit",
                "The price is %s for %s and one unit costs %s to serve, so each sale earns %s."
                % (money(cur, r["price"]), r["price_for"], money(cur, r["variable_cost"]),
                   money(cur, r["price"] - r["variable_cost"])),
                "Raise the price or cut the cost of one unit. If selling below cost is a deliberate opening "
                "offer, say so and say when it ends.")
        s = r["sens"]
        if s is not None and not s["ok"]:
            add("pricing-answers-unusable", "medium", "The price-sensitivity answers cannot be used",
                "No range is reported because %s" % s["why"],
                "Ask again with the unit written into every question (for example \"for one cup\"), "
                "and collect at least %d complete answers." % MIN_USABLE)
        if r["position"] in ("below", "above"):
            p = s["points"]
            add("pricing-outside-range", "medium", "The price sits outside what buyers called acceptable",
                "The price of %s is %s the range of %s to %s that the answers give."
                % (money(cur, r["price"]), r["position"], money(cur, p["marginal_cheapness"]),
                   money(cur, p["marginal_expensiveness"])),
                "Move the price into the range, or test the current price with real buyers before relying on it.")
    else:
        if r["live_count"] == 0:
            add("pricing-no-live-revenue", "high", "No revenue line is live",
                "None of the %d revenue lines has status live, so the base case earns nothing per visit."
                % len(r["lines"]),
                "Get one line earning real money before counting on any of them.")
        if r["estimate_share"] > 0.5:
            add("pricing-rests-on-estimate", "medium", "Most revenue rests on an estimate",
                "%.0f%% of the base-case revenue comes from lines whose source is an estimate."
                % (r["estimate_share"] * 100),
                "Replace each estimate with a figure from the payer's own report or rate card.")
    if not r["tests"]:
        add("pricing-no-real-test", "low", "No real-buyer price test is planned",
            "The input lists no test of the price with real people.",
            "Add a 'tests' entry saying which price will be shown to real buyers and how.")
    return out


# ------------------------------------------------------------------ render

def render_points(r):
    cur, s = r["currency"], r["sens"]
    L = []
    if s is None:
        return ["No price-sensitivity answers were given, so there is no range from buyers."]
    L.append("%d answers from %s. %d usable, %d dropped (a value missing, or the four not in rising order)."
             % (s["total"], r["answers_source"], s["usable"], s["dropped"]))
    if r.get("answers_skipped"):
        L.append("%d files in the folder held no price answers and were skipped." % r["answers_skipped"])
    L.append("")
    if not s["ok"]:
        L.append("**No range is reported:** %s" % s["why"])
        return L
    L += ["| Point | Price | In plain words |", "| --- | ---: | --- |"]
    for key, label, words in POINT_WORDS:
        L.append("| %s | %s | %s |" % (label, money(cur, s["points"][key]), words))
    L += ["", "Acceptable range: %s to %s for %s." % (
        money(cur, s["points"]["marginal_cheapness"]), money(cur, s["points"]["marginal_expensiveness"]),
        r["price_for"])]
    return L


def _breakeven(v):
    return "never, at these numbers" if math.isinf(v) else "{:,.0f}".format(math.ceil(v))


def render(r):
    cur = r["currency"]
    L = ["# Pricing", ""]
    if r["business"]:
        L += ["For %s." % r["business"], ""]
    if r["model"] == "paid":
        L += _render_paid(r)
    else:
        L += _render_free(r)
    L += ["", "## Opening offer", ""]
    if r["opening_offer"]:
        L.append("%s Ends: %s." % (r["opening_offer"]["what"].rstrip(".") + ".", r["opening_offer"]["ends"].rstrip(".")))
    else:
        L.append("None planned.")
    L += ["", "## What to test with real people", ""]
    if r["tests"]:
        for t in r["tests"]:
            L.append("- %s%s" % ("%s: " % money(cur, t["price"]) if t["price"] is not None else "", t["how"]))
    else:
        L.append("Nothing is planned yet. Until real buyers have seen a price, every figure above is a starting point.")
    if r["findings"]:
        L += ["", "## What the numbers flag", ""]
        for f in r["findings"]:
            L.append("- **%s** (%s). %s" % (f["title"], f["severity"], f["description"]))
    L += ["", "_%s from the inputs given. Every figure traces to an input. Simulated answers only pick "
          "what to test; real buyers decide the price._" % MARKER]
    return "\n".join(L) + "\n"


def _render_paid(r):
    cur, s, comp = r["currency"], r["sens"], r["comp"]
    main = r["margins"][0]
    L = ["## The decision", "", "| Question | Answer |", "| --- | --- |",
         "| Candidate price | %s for %s |" % (money(cur, r["price"]), cell(r["price_for"]))]
    if s is None:
        L.append("| What buyers said | not asked |")
    elif not s["ok"]:
        L.append("| What buyers said | answers given but not usable |")
    else:
        L.append("| What buyers said | acceptable from %s to %s; the candidate is %s the range |" % (
            money(cur, s["points"]["marginal_cheapness"]), money(cur, s["points"]["marginal_expensiveness"]),
            r["position"]))
    if comp:
        L.append("| What competitors charge | %s to %s, median %s; the candidate is %s |" % (
            money(cur, comp["lowest"]), money(cur, comp["highest"]), money(cur, comp["median"]), comp["where"]))
    else:
        L.append("| What competitors charge | not given |")
    L.append("| What one unit earns | %s after a cost of %s (%.0f%%)%s |" % (
        money(cur, main["margin"]), money(cur, r["variable_cost"]), main["margin_pct"],
        " **does not cover the cost**" if main["below_cost"] else ""))
    L.append("| Tested with real buyers | %s |" % ("%d test(s) planned" % len(r["tests"]) if r["tests"] else "no test planned"))

    L += ["", "## What buyers said", ""] + render_points(r)
    if s is not None and s["ok"]:
        L += ["", "| Price | Too cheap | Cheap | Expensive | Too expensive |", "| ---: | ---: | ---: | ---: | ---: |"]
        for m in r["margins"]:
            sh = shares_at(s["rows"], m["price"])
            L.append("| %s | %.0f%% | %.0f%% | %.0f%% | %.0f%% |" % (
                money(cur, m["price"]), sh["too_cheap"] * 100, sh["cheap"] * 100,
                sh["expensive"] * 100, sh["too_expensive"] * 100))
        L += ["", "Each figure is the share of usable answers that would call that price too cheap, "
              "cheap, expensive or too expensive."]

    L += ["", "## What competitors charge", ""]
    if comp:
        L += ["| Competitor | Price | What it includes | Source |", "| --- | ---: | --- | --- |"]
        for c in r["competitors"]:
            L.append("| %s | %s | %s | %s |" % (cell(c["name"]), money(cur, c["price"]), cell(c["includes"]),
                                                cell(c["url"]) or "no link given"))
        L += ["", "Lowest %s, median %s, highest %s. The candidate of %s is %s%s." % (
            money(cur, comp["lowest"]), money(cur, comp["median"]), money(cur, comp["highest"]),
            money(cur, r["price"]), comp["where"],
            "" if comp["vs_median_pct"] is None else " and %+.0f%% against the median" % comp["vs_median_pct"])]
    else:
        L.append("No competitor prices were given.")

    L += ["", "## What one unit earns at each price", "",
          "One unit costs %s to serve." % money(cur, r["variable_cost"]), "",
          "| Price | Earns per unit | As a share of the price | |", "| ---: | ---: | ---: | --- |"]
    for i, m in enumerate(r["margins"]):
        note = "the candidate" if i == 0 else ""
        if m["below_cost"]:
            note = (note + "; " if note else "") + "does not cover the cost"
        L.append("| %s | %s | %.0f%% | %s |" % (money(cur, m["price"]), money(cur, m["margin"]), m["margin_pct"], note))

    L += ["", "## The price ladder", ""]
    if r["ladder"]:
        L += ["| Step | Price | Earns per unit | Why step up |", "| --- | ---: | ---: | --- |"]
        for s_ in r["ladder"]:
            L.append("| %s | %s | %s%s | %s |" % (cell(s_["name"]), money(cur, s_["price"]), money(cur, s_["margin"]),
                                                  " (below cost)" if s_["below_cost"] else "", cell(s_["why_step_up"])))
    else:
        L.append("One price, no ladder given.")
    return L


def _render_free(r):
    cur, b, u = r["currency"], r["base"], r["upside"]
    L = ["The user pays nothing. Someone else pays, so the price that matters is what one visit earns.", "",
         "## The decision", "", "| Question | Live lines only | Live plus unproven |", "| --- | ---: | ---: |",
         "| Earned per 1,000 visits | %s | %s |" % (money(cur, b["per_1000"]), money(cur, u["per_1000"])),
         "| Cost of serving 1,000 visits | %s | %s |" % (money(cur, r["variable_cost_per_1000"]), money(cur, r["variable_cost_per_1000"])),
         "| Left per 1,000 visits | %s | %s |" % (money(cur, b["contribution"]), money(cur, u["contribution"])),
         "| Fixed revenue a month | %s | %s |" % (money(cur, b["fixed_revenue"]), money(cur, u["fixed_revenue"])),
         "| Fixed cost a month | %s | %s |" % (money(cur, r["fixed_monthly_cost"]), money(cur, r["fixed_monthly_cost"])),
         "| Break-even visits a month | %s | %s |" % (_breakeven(b["breakeven_month"]), _breakeven(u["breakeven_month"])),
         "| Break-even visits a day | %s | %s |" % (_breakeven(b["breakeven_day"]), _breakeven(u["breakeven_day"])),
         "",
         "The live column is the base case. The second column is the upside and is not money yet. "
         "A day is the monthly figure over a %d-day month. Fixed monthly lines are set against the fixed "
         "cost, not spread over visits." % DAYS,
         "", "## Revenue lines", "",
         "| Line | Status | Source | Worked out as | Adds per 1,000 visits | Adds a month | Counted |",
         "| --- | --- | --- | --- | ---: | ---: | --- |"]
    counted = {"live": "base case and upside", "unproven": "upside only", "paused": "no, paused: counts as zero"}
    for x in r["lines"]:
        fixed = x["kind"] == "fixed_monthly"
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            cell(x["name"]), x["status"], "estimate (not a quoted figure)" if x["estimate"] else cell(x["source"]),
            x["how"], "-" if fixed else money(cur, x["per_1000"]), money(cur, x["monthly"]) if fixed else "-",
            counted[x["status"]]))
    paused = [x["name"] for x in r["lines"] if x["status"] == "paused"]
    if paused:
        L += ["", "Paused and counted as zero: %s. The figure shown is what the line would add if switched on." % ", ".join(paused)]
    if any(x["estimate"] for x in r["lines"]):
        L += ["", "%.0f%% of the base-case revenue rests on estimates." % (r["estimate_share"] * 100)]
    L += ["", "## What others charge", ""]
    if r["competitors"]:
        L += ["| Competitor | Price | What it includes | Source |", "| --- | ---: | --- | --- |"]
        for c in r["competitors"]:
            L.append("| %s | %s | %s | %s |" % (cell(c["name"]), money(cur, c["price"]), cell(c["includes"]),
                                                cell(c["url"]) or "no link given"))
    else:
        L.append("No competitor prices were given. The user pays nothing, so there is no price to compare.")
    return L


# ---------------------------------------------------------------- templates

def _template_answers():
    rows = []
    for i in range(14):
        a = 60 + 10 * i
        rows.append({"too_cheap": a, "cheap": a + 30, "expensive": a + 70 + 5 * (i % 3), "too_expensive": a + 120 + 10 * (i % 4)})
    return rows


def template(free):
    if free:
        return {
            "business": "City events guide",
            "currency": "Rs ",
            "model": "free_to_user",
            "revenue_lines": [
                {"name": "Display adverts", "kind": "per_1000_pageviews", "amount": "60", "pages_per_visit": "2.5",
                 "status": "live", "source": "owner"},
                {"name": "Ticket referral fee", "kind": "per_action", "amount": "40", "rate": "0.02",
                 "status": "unproven", "source": "estimate"},
                {"name": "Venue of the month sponsor", "kind": "fixed_monthly", "amount": "15000",
                 "status": "paused", "source": "owner"},
            ],
            "fixed_monthly_cost": "45000",
            "variable_cost_per_1000_visits": "12",
            "tests": [{"how": "Run the referral link on ten event pages for four weeks and count paid referrals."}],
        }
    return {
        "business": "Matcha cafe",
        "currency": "Rs ",
        "model": "paid",
        "price": "180",
        "price_for": "one cup",
        "variable_cost": "62",
        "answers": _template_answers(),
        "competitor_prices": [
            {"name": "Cafe on the corner", "price": "160", "includes": "one cup, no loyalty card",
             "url": "https://example.com/corner-menu"},
            {"name": "Chain store", "price": "220", "includes": "one cup, larger size",
             "url": "https://example.com/chain-menu"},
            {"name": "Tea stall", "price": "90", "includes": "one small cup, takeaway only",
             "url": "https://example.com/stall"},
        ],
        "candidates": ["160", "200"],
        "ladder": [
            {"name": "Single cup", "price": "180", "why_step_up": ""},
            {"name": "Cup and a pastry", "price": "290", "why_step_up": "Breakfast in one order"},
        ],
        "opening_offer": {"what": "Second cup half price in the opening fortnight", "ends": "14 days after opening"},
        "tests": [{"price": "180", "how": "Sell at the weekend market for two Saturdays and count cups sold."},
                  {"price": "200", "how": "Same stall, the following two Saturdays."}],
    }


# ---------------------------------------------------------------- commands

def _load(path):
    if not os.path.isfile(path):
        raise PricingError("input file not found: %s" % path)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except ValueError as e:
        raise PricingError("%s is not valid JSON: %s" % (path, e))


def cmd_template(a):
    ledger.guard_out(a.out, a.dir, a.force)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(template(a.free), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("%s wrote an example %s pricing file to %s. Replace every value with your own."
          % (ledger.OK, "free-to-user" if a.free else "paid", a.out))
    return 0


def cmd_sensitivity(a):
    spec = _load(a.file)
    if not isinstance(spec, dict):
        raise PricingError("the input must be a JSON object")
    cur = currency_of(spec)
    if model_of(spec) != "paid":
        raise PricingError("the product is free to its user, so there is no price-sensitivity survey to read")
    rows, source, skipped = load_answers(spec, os.path.dirname(os.path.abspath(a.file)))
    if rows is None:
        raise PricingError("the input has no answers, answers_file or answers_dir")
    r = {"currency": cur, "sens": sensitivity(rows), "answers_source": source, "answers_skipped": skipped,
         "price_for": str(spec.get("price_for") or "one unit")}
    print("\n".join(render_points(r)))
    return 0 if r["sens"]["ok"] else 1


def cmd_check(a):
    spec = _load(a.file)
    res = analyse(spec, os.path.dirname(os.path.abspath(a.file)))
    print(render(res))
    print("%s nothing was recorded. Use 'record' to write the section and the ledger." % ledger.OK)
    return 0


def _remove_stale(d):
    for fid in FINDING_IDS:
        path = ledger.finding_path(d, SKILL, fid)
        if os.path.isfile(path):
            os.remove(path)


def cmd_record(a):
    spec = _load(a.file)
    res = analyse(spec, os.path.dirname(os.path.abspath(a.file)))
    d, root = a.dir, ledger.root(a.dir)

    section = os.path.join(root, SECTION)
    ledger.guard_out(section, d, a.force, ours=MARKER)
    os.makedirs(os.path.dirname(section), exist_ok=True)
    with open(section, "w", encoding="utf-8") as fh:
        fh.write(render(res))

    ev_dir = ledger.evidence_dir(d, SKILL)
    with open(os.path.join(ev_dir, "pricing.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    answers_copy = os.path.join(ev_dir, "answers.json")
    if os.path.isfile(answers_copy):
        os.remove(answers_copy)
    rel_in = "evidence/%s/pricing.json" % SKILL
    rel_section = SECTION.replace(os.sep, "/")
    evidence = [rel_in, rel_section]
    sens = res.get("sens")
    if sens is not None and (spec.get("answers_file") or spec.get("answers_dir")):
        rows, _, _ = load_answers(spec, os.path.dirname(os.path.abspath(a.file)))
        with open(answers_copy, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        evidence.append("evidence/%s/answers.json" % SKILL)

    ledger.init_coverage(d, SKILL, COVERAGE, keep_existing=False)

    def cover(cid, yes, why_not=None):
        if yes:
            ledger.mark_coverage(d, SKILL, cid, "covered", evidence=evidence)
        elif why_not:
            ledger.mark_coverage(d, SKILL, cid, "not_run", note=why_not)

    metrics = []
    if res["model"] == "paid":
        cover("buyers-asked", bool(sens and sens["ok"]),
              None if sens is None else "Answers were given but no usable range came out of them.")
        cover("competitors-compared", bool(res["competitors"]))
        cover("margin-checked", True)
        cover("ladder-or-lines", bool(res["ladder"]))
        metrics.append({"id": "margin_per_unit", "value": round(res["margins"][0]["margin"], 2),
                        "unit": "currency", "budget": 0, "op": ">", "evidence": [rel_section]})
        if sens is not None:
            metrics.append({"id": "usable_answers_pct", "value": round(sens["usable_pct"], 1), "unit": "percent",
                            "budget": 75, "op": ">=", "evidence": [rel_section]})
    else:
        ledger.mark_coverage(d, SKILL, "buyers-asked", "not_applicable",
                             note="The user pays nothing, so there is no price to ask buyers about.")
        if res["competitors"]:
            cover("competitors-compared", True)
        else:
            ledger.mark_coverage(d, SKILL, "competitors-compared", "not_applicable",
                                 note="The user pays nothing and no competitor prices were given.")
        cover("margin-checked", True)
        cover("ladder-or-lines", True)
        metrics.append({"id": "live_revenue_per_1000_visits", "value": round(res["base"]["per_1000"], 2),
                        "unit": "currency", "budget": 0, "op": ">", "evidence": [rel_section]})
    cover("real-test-planned", bool(res["tests"]))
    ledger.write_scores(d, SKILL, metrics)

    _remove_stale(d)
    for f in res["findings"]:
        finding = ledger.new_finding(SKILL, f["title"], f["severity"], id=f["id"], category="pricing",
                                     description=f["description"], fix=f["fix"], evidence=evidence,
                                     location=rel_section)
        ledger.write_finding(d, finding)
        ledger.verify(d, SKILL, f["id"], by="evidence-file")

    print("%s wrote %s and recorded coverage, %d score(s) and %d finding(s)."
          % (ledger.OK, section, len(metrics), len(res["findings"])))
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
    t.add_argument("--free", action="store_true", help="write the free-to-user example instead of the paid one")
    common(t)
    t.set_defaults(func=cmd_template)
    for name, func, text in (("sensitivity", cmd_sensitivity, "print the four price-sensitivity points only"),
                             ("check", cmd_check, "print the report and record nothing"),
                             ("record", cmd_record, "write the section file and the ledger")):
        p = sub.add_parser(name, help=text)
        p.add_argument("file", help="the pricing JSON")
        common(p)
        p.set_defaults(func=func)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: template, sensitivity, check or record")
    try:
        return a.func(a)
    except ValueError as e:  # PricingError, and num.MissingInput / num.BadNumber
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
