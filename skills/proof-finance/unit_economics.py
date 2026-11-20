#!/usr/bin/env python3
"""Work out the unit economics behind a product. Standard library only, no network.

Reads one JSON file of inputs and reports what one unit earns after its own costs,
the real margin at the planned volume, how many units a day break even, three
scenarios (status quo, break-even, target scale), the cash needed before it pays
for itself, and three what-ifs. Every number traces to an input. A missing field is
an error, never a silent zero, and numbers are read locale-safely.

    python3 unit_economics.py numbers.json --out proof/finance.md
    python3 unit_economics.py numbers.json --record      # write scores to the ledger
    python3 unit_economics.py --template numbers.json
    python3 unit_economics.py numbers.json --what-if "unit_cost=320,conversion=3,ad_spend=40000"

With "from_intake": true (or --intake path) the product cost, the monthly ad spend
and today's orders a day come from proof/business/intake.json. Optional "cod" models
cash on delivery and return to origin; "target_per_day" sets the target scenario.
"""

import argparse
import copy
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-finance"
INTAKE_REL = os.path.join("business", "intake.json")
INTAKE_COST_LABEL = "Landed unit cost (intake)"
INTAKE_ADS_LABEL = "Ads (intake)"
INTAKE_FIELDS = ("landed_unit_cost", "monthly_ad_spend", "daily_sessions", "daily_orders")
AD_LABEL = re.compile(r"\b(ad|ads|advert\w*|advertising)\b", re.I)


class NumbersError(ValueError):
    pass


def _sum_items(items, label):
    """Sum a list of {label, cost} entries. Missing cost is an error."""
    if items is None:
        return 0.0, []
    if not isinstance(items, list):
        raise NumbersError("'%s' must be a list of {label, cost}" % label)
    total = 0.0
    named = []
    for it in items:
        if not isinstance(it, dict) or "cost" not in it:
            raise NumbersError("each entry in '%s' needs a 'cost'" % label)
        c = num.parse_number(it["cost"], field="%s.cost" % label)
        total += c
        named.append((it.get("label", "item"), c))
    return total, named


# ------------------------------------------------------------------ the intake

def _intake_value(intake, key):
    f = intake.get(key)
    if isinstance(f, dict) and isinstance(f.get("value"), (int, float)) and not isinstance(f.get("value"), bool):
        return float(f["value"])
    return None


def load_intake(path):
    if not os.path.exists(path):
        raise NumbersError("the intake %s is missing; run /proof-intake first" % path)
    with open(path, encoding="utf-8") as fh:
        intake = json.load(fh)
    if not isinstance(intake, dict):
        raise NumbersError("the intake %s is not a JSON object" % path)
    gaps = [k for k in INTAKE_FIELDS if _intake_value(intake, k) is None]
    cur = intake.get("currency")
    if not isinstance(cur, str) or not cur.strip():
        gaps.insert(0, "currency")
    if gaps:
        raise NumbersError("the intake is incomplete, missing: %s; run /proof-intake" % ", ".join(gaps))
    return intake


def apply_intake(spec, intake):
    """A copy of ``spec`` with the intake's unit cost, ad spend and daily orders filled in.

    Refuses a spec that already carries an ads fixed cost or a plan_per_day, which
    would count the same money or volume twice.
    """
    spec = copy.deepcopy(spec)
    if "plan_per_day" in spec:
        raise NumbersError("with from_intake, leave out 'plan_per_day': today's orders a day come from the intake")
    for it in spec.get("fixed_monthly") or []:
        if isinstance(it, dict) and AD_LABEL.search(str(it.get("label", ""))):
            raise NumbersError("with from_intake, leave out the fixed cost %r: the ad spend comes from the intake"
                               % it.get("label"))
    cur = intake["currency"]
    if "currency" in spec and spec["currency"] != cur:
        raise NumbersError("the numbers say currency %r but the intake says %r" % (spec["currency"], cur))
    spec["currency"] = cur
    spec["variable"] = list(spec.get("variable") or []) + [
        {"label": INTAKE_COST_LABEL, "cost": _intake_value(intake, "landed_unit_cost")}]
    spec["fixed_monthly"] = list(spec.get("fixed_monthly") or []) + [
        {"label": INTAKE_ADS_LABEL, "cost": _intake_value(intake, "monthly_ad_spend")}]
    spec["plan_per_day"] = _intake_value(intake, "daily_orders")
    spec["_intake"] = {"period": intake.get("period"), "daily_sessions": _intake_value(intake, "daily_sessions"),
                       "daily_orders": _intake_value(intake, "daily_orders")}
    return spec


# ------------------------------------------------------------------ cash on delivery

def _fraction(value, field, below_one=False):
    v = num.parse_number(value, field=field)
    if v > 1:
        v = v / 100.0  # given as a percent
    if v < 0 or v > 1 or (below_one and v >= 1):
        raise NumbersError("'%s' must be a fraction from 0 to 1 or a percent up to 100%s" % (
            field, ", and below 100%" if below_one else ""))
    return v


def _cod(spec):
    """Cash on delivery and return to origin, as the extra cost each delivered order carries.

    Of the COD orders shipped, rto_rate come back. For each COD order delivered,
    rto_rate / (1 - rto_rate) orders were shipped and returned, each costing rto_cost.
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise NumbersError("'cod' must be an object: {share, rto_rate, rto_cost, cod_fee}")
    for k in ("share", "rto_rate", "rto_cost"):
        if k not in spec:
            raise NumbersError("'cod.%s' is missing" % k)
    share = _fraction(spec["share"], "cod.share")
    rto_rate = _fraction(spec["rto_rate"], "cod.rto_rate", below_one=True)
    rto_cost = num.parse_number(spec["rto_cost"], field="cod.rto_cost")
    cod_fee = num.parse_number(spec.get("cod_fee", 0), field="cod.cod_fee")
    returns_per_delivered = rto_rate / (1 - rto_rate)
    rto_per_order = share * rto_cost * returns_per_delivered
    fee_per_order = share * cod_fee
    return {"share": share, "rto_rate": rto_rate, "rto_cost": rto_cost, "cod_fee": cod_fee,
            "returns_per_delivered": returns_per_delivered, "rto_per_order": rto_per_order,
            "fee_per_order": fee_per_order, "per_order": rto_per_order + fee_per_order}


# ------------------------------------------------------------------ the model

def analyse(spec):
    if not isinstance(spec, dict):
        raise NumbersError("the input must be a JSON object")
    if "price" not in spec:
        raise NumbersError("'price' is missing")
    price = num.parse_number(spec["price"], field="price")
    currency = spec.get("currency", "$")
    if not isinstance(currency, str) or not currency.strip():
        raise NumbersError("'currency' must be the prefix money is printed with, such as \"$\" or \"Rs \"")
    unit = spec.get("unit", "unit")
    days = num.parse_number(spec.get("days_per_month", 30), field="days_per_month")

    base_variable, var_items = _sum_items(spec.get("variable"), "variable")
    fixed, fixed_items = _sum_items(spec.get("fixed_monthly"), "fixed_monthly")
    startup, startup_items = _sum_items(spec.get("startup"), "startup")

    if "plan_per_day" not in spec:
        raise NumbersError("'plan_per_day' is missing (how many %ss you expect to sell a day)" % unit)
    plan_per_day = num.parse_number(spec["plan_per_day"], field="plan_per_day")

    cod = _cod(spec.get("cod"))
    variable = base_variable + (cod["per_order"] if cod else 0.0)
    target = spec.get("target_per_day")
    target = num.parse_number(target, field="target_per_day") if target is not None else None

    contribution = price - variable
    contribution_pct = (contribution / price) if price else 0.0
    plan_per_month = plan_per_day * days
    revenue_at_plan = price * plan_per_month
    variable_at_plan = variable * plan_per_month
    operating_profit_month = revenue_at_plan - variable_at_plan - fixed
    margin_at_plan = (operating_profit_month / revenue_at_plan) if revenue_at_plan else 0.0

    breakeven_per_day = (fixed / contribution / days) if contribution > 0 else math.inf

    capacity = spec.get("capacity_per_day")
    capacity = num.parse_number(capacity, field="capacity_per_day") if capacity is not None else None

    # Year one, month by month, with an optional linear ramp up to plan.
    ramped = spec.get("ramp_months") is not None or spec.get("ramp_per_day") is not None
    ramp = spec.get("ramp_months")
    ramp = int(num.parse_number(ramp, field="ramp_months")) if ramp is not None else 0
    per_day = spec.get("ramp_per_day")
    if per_day is not None:
        if not isinstance(per_day, list) or len(per_day) != 12:
            raise NumbersError("'ramp_per_day' must list 12 numbers, one a month for year 1")
        per_day = [num.parse_number(x, field="ramp_per_day") for x in per_day]
    months = []
    cumulative = -startup
    low_water = cumulative
    payback_month = None
    for m in range(1, 13):
        frac = 1.0 if ramp <= 0 or m > ramp else (m / float(ramp))
        units = per_day[m - 1] * days if per_day is not None else plan_per_month * frac
        profit = units * contribution - fixed
        cumulative += profit
        low_water = min(low_water, cumulative)
        if payback_month is None and cumulative >= 0:
            payback_month = m
        months.append({"month": m, "units": units, "profit": profit, "cumulative": cumulative})

    year1_profit = sum(mo["profit"] for mo in months)
    cash_needed = -low_water if low_water < 0 else 0.0

    ads = [c for label, c in fixed_items if AD_LABEL.search(str(label))]
    return {
        "business": spec.get("business", ""), "unit": unit, "price": price, "currency": currency,
        "variable": variable, "variable_base": base_variable, "cod": cod, "ramped": ramped,
        "fixed_monthly": fixed, "startup": startup, "target_per_day": target,
        "ads_monthly": sum(ads) if ads else None, "intake": spec.get("_intake"),
        "contribution": contribution, "contribution_pct": contribution_pct,
        "plan_per_day": plan_per_day, "days_per_month": days,
        "operating_profit_month": operating_profit_month, "margin_at_plan": margin_at_plan,
        "breakeven_per_day": breakeven_per_day, "capacity_per_day": capacity,
        "year1_profit": year1_profit, "cash_needed": cash_needed, "payback_month": payback_month,
        "months": months, "variable_items": var_items, "fixed_items": fixed_items,
        "startup_items": startup_items,
        "whatifs": _whatifs(spec, price, variable, fixed, days, plan_per_day),
    }


def _whatifs(spec, price, variable, fixed, days, plan_per_day):
    def op_month(p, v, ppd):
        contrib = p - v
        return ppd * days * contrib - fixed
    return {
        "price_down_10pct": op_month(price * 0.9, variable, plan_per_day),
        "sales_down_25pct": op_month(price, variable, plan_per_day * 0.75),
        "variable_up_20pct": op_month(price, variable * 1.2, plan_per_day),
    }


def scenario(a, per_day):
    """One row of the scenario table at ``per_day`` orders a day."""
    month = per_day * a["days_per_month"]
    revenue = a["price"] * month
    profit = month * a["contribution"] - a["fixed_monthly"]
    ads = a.get("ads_monthly")
    return {"per_day": per_day, "revenue": revenue, "profit": profit,
            "margin": (profit / revenue) if revenue else None,
            "ad_per_order": (ads / month) if ads is not None and month > 0 else None}


def scenarios(a):
    """Status quo, break-even and target scale. A row is None when it cannot be computed."""
    be = a["breakeven_per_day"]
    t = a.get("target_per_day")
    return [("Status quo", scenario(a, a["plan_per_day"])),
            ("Break-even", None if math.isinf(be) else scenario(a, be)),
            ("Target scale", None if t is None else scenario(a, t))]


def investor_verdict(a):
    """Go only when each order earns something and year one makes a profit. A pure function of the inputs."""
    cur, u = a["currency"], a["unit"]
    if a["contribution"] <= 0:
        return "Investor: Hold, each %s loses %s before fixed costs" % (u, _money(-a["contribution"], cur))
    if a["year1_profit"] <= 0:
        return "Investor: Hold, year one loses %s" % _money(-a["year1_profit"], cur)
    return "Investor: Go, each %s earns %s and year one makes %s" % (
        u, _money(a["contribution"], cur), _money(a["year1_profit"], cur))


# ------------------------------------------------------------------ what-if

WHAT_IF_KEYS = ("unit_cost", "conversion", "ad_spend", "price", "orders", "target")


def parse_what_if(text):
    out = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise NumbersError("--what-if takes key=value pairs, such as unit_cost=320,conversion=3")
        k, v = [x.strip() for x in part.split("=", 1)]
        if k not in WHAT_IF_KEYS:
            raise NumbersError("--what-if knows %s, not %r" % (", ".join(WHAT_IF_KEYS), k))
        out[k] = num.parse_number(v, field="what-if %s" % k, decimal=".")
    if not out:
        raise NumbersError("--what-if is empty")
    return out


def apply_what_if(spec, w):
    """A copy of the (intake-merged) spec with slider values applied. Pure: writes nothing."""
    spec = copy.deepcopy(spec)
    from_intake = "_intake" in spec
    if "unit_cost" in w:
        if from_intake:
            spec["variable"] = [it for it in spec["variable"] if it.get("label") != INTAKE_COST_LABEL] + [
                {"label": INTAKE_COST_LABEL, "cost": w["unit_cost"]}]
        else:  # without the intake, the slider is the whole cost per unit
            spec["variable"] = [{"label": "unit cost (what-if)", "cost": w["unit_cost"]}]
    if "ad_spend" in w:
        kept = [it for it in (spec.get("fixed_monthly") or [])
                if not AD_LABEL.search(str(it.get("label", "")))]
        spec["fixed_monthly"] = kept + [{"label": INTAKE_ADS_LABEL if from_intake else "Ads (what-if)",
                                         "cost": w["ad_spend"]}]
    if "price" in w:
        spec["price"] = w["price"]
    if "orders" in w:
        spec["plan_per_day"] = w["orders"]
    if "conversion" in w:
        sessions = (spec.get("_intake") or {}).get("daily_sessions")
        if not sessions:
            raise NumbersError("conversion needs the intake's daily sessions: use from_intake")
        spec["plan_per_day"] = sessions * w["conversion"] / 100.0
    if "target" in w:
        spec["target_per_day"] = w["target"]
    return spec


# ------------------------------------------------------------------ render

CURRENCY = "$"  # the prefix money is printed with; set from the inputs' "currency" field


def _money(x, cur=None):
    cur = CURRENCY if cur is None else cur
    return "-%s%s" % (cur, "{:,.0f}".format(-x)) if x < 0 else "%s%s" % (cur, "{:,.0f}".format(x))


def _money2(x, cur=None):
    cur = CURRENCY if cur is None else cur
    return "-%s%s" % (cur, "{:,.2f}".format(-x)) if x < 0 else "%s%s" % (cur, "{:,.2f}".format(x))


def _count(x):
    return "{:,.2f}".format(x).rstrip("0").rstrip(".")


def render_scenarios(a):
    cur, u = a["currency"], a["unit"]
    L = ["## Three scenarios", "",
         "| scenario | %ss a day | revenue a month | operating profit a month | profit margin | ad spend per %s |" % (u, u),
         "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in scenarios(a):
        if row is None:
            why = ("never: each %s loses money before fixed costs" % u) if name == "Break-even" else "set target_per_day"
            L.append("| %s | %s | | | | |" % (name, why))
            continue
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            name, _count(row["per_day"]), _money(row["revenue"], cur), _money(row["profit"], cur),
            "-" if row["margin"] is None else "%.0f%%" % (row["margin"] * 100),
            "-" if row["ad_per_order"] is None else _money2(row["ad_per_order"], cur)))
    L.append("")
    be = a["breakeven_per_day"]
    if a.get("ads_monthly") is not None and not math.isinf(be) and be > 0:
        L += ["At break-even (%s %ss a day) the monthly ad spend of %s works out at %s per %s." % (
            _count(be), u, _money(a["ads_monthly"], cur), _money2(a["ads_monthly"] / (be * a["days_per_month"]), cur), u), ""]
    return L


def render_cod(a):
    c = a.get("cod")
    if not c:
        return []
    cur, u = a["currency"], a["unit"]
    per_cod = c["rto_cost"] * c["returns_per_delivered"]
    return ["## Cash on delivery and return to origin", "",
            "- %.0f%% of %ss are cash on delivery, and %.0f%% of COD %ss shipped come back (RTO)." % (
                c["share"] * 100, u, c["rto_rate"] * 100, u),
            "- Each delivered COD %s carries %.0f%% / (100%% - %.0f%%) = %.3f returned %ss at %s each: %s." % (
                u, c["rto_rate"] * 100, c["rto_rate"] * 100, c["returns_per_delivered"], u,
                _money2(c["rto_cost"], cur), _money2(per_cod, cur)),
            "- Spread over every delivered %s: %.0f%% x %s = %s of RTO cost, and %.0f%% x %s COD fee = %s." % (
                u, c["share"] * 100, _money2(per_cod, cur), _money2(c["rto_per_order"], cur),
                c["share"] * 100, _money2(c["cod_fee"], cur), _money2(c["fee_per_order"], cur)),
            "- Cost per delivered %s = %s variable + %s COD fee + %s RTO = %s." % (
                u, _money2(a["variable_base"], cur), _money2(c["fee_per_order"], cur),
                _money2(c["rto_per_order"], cur), _money2(a["variable"], cur)),
            ""]


def render(a):
    global CURRENCY
    CURRENCY = a.get("currency", "$")
    L = ["# %s - unit economics" % (a["business"] or "Your product"), ""]
    u = a["unit"]
    L += ["| key number | |", "| --- | ---: |",
          "| Price | %s%s per %s |" % (CURRENCY, "{:,.2f}".format(a["price"]), u),
          "| Cost per %s%s | %s |" % ("delivered " if a.get("cod") else "", u, _money(a["variable"])),
          "| Earns per %s before fixed costs | %s (%.0f%%) |" % (u, _money(a["contribution"]), a["contribution_pct"] * 100),
          "| Fixed costs a month | %s |" % _money(a["fixed_monthly"]),
          "| Break-even | %s %ss a day |" % ("never" if math.isinf(a["breakeven_per_day"]) else "{:,.0f}".format(math.ceil(a["breakeven_per_day"])), u),
          "| Profit margin at plan | %.0f%% |" % (a["margin_at_plan"] * 100),
          "| Year 1 operating profit | %s |" % _money(a["year1_profit"]),
          "| Startup spend | %s |" % _money(a["startup"]),
          "| Cash needed before it pays for itself | %s |" % _money(a["cash_needed"]),
          "| Startup money earned back | %s |" % ("month %d" % a["payback_month"] if a["payback_month"] else "not in year 1"),
          ""]
    if a["capacity_per_day"] is not None and not math.isinf(a["breakeven_per_day"]):
        fits = a["breakeven_per_day"] <= a["capacity_per_day"]
        L.append("Break-even needs %.0f %ss a day against a capacity of %.0f: %s" % (
            math.ceil(a["breakeven_per_day"]), u, a["capacity_per_day"], "fits" if fits else "DOES NOT FIT"))
        L.append("")
    if a.get("intake"):
        L += ["The product cost, the ad spend and today's %ss a day come from the intake (%s)." % (
            u, a["intake"].get("period") or "period not given"), ""]
    L += render_cod(a)
    L += render_scenarios(a)
    if a.get("ramped", True):
        L += ["## Year 1, month by month", "", "| month | %ss | profit | cumulative |" % u, "| ---: | ---: | ---: | ---: |"]
        for mo in a["months"]:
            L.append("| %d | %s | %s | %s |" % (mo["month"], "{:,.0f}".format(mo["units"]), _money(mo["profit"]), _money(mo["cumulative"])))
        L.append("")
    else:
        L += ["No ramp was given, so every month of year one is the same: %s %ss a day and %s operating "
              "profit a month, %s for the year." % (_count(a["plan_per_day"]), u, _money(a["operating_profit_month"]),
                                                   _money(a["year1_profit"])), ""]
    L += ["## What if", "",
          "- Price drops 10%%: operating profit a month becomes %s" % _money(a["whatifs"]["price_down_10pct"]),
          "- Sales come in 25%% low: %s" % _money(a["whatifs"]["sales_down_25pct"]),
          "- Variable costs rise 20%%: %s" % _money(a["whatifs"]["variable_up_20pct"]), ""]
    L.append("_Every number traces to an input you gave. Estimates are only estimates: confirm costs with "
             "real quotes and the structure with an accountant. Not financial advice._")
    return "\n".join(L) + "\n"


TEMPLATE = {
    "business": "Matcha cafe", "currency": "$",
    "unit": "cup", "price": "6.50", "days_per_month": 30,
    "variable": [{"label": "matcha + milk", "cost": "1.10"}, {"label": "cup + lid", "cost": "0.35"},
                 {"label": "card fee", "cost": "0.29"}],
    "fixed_monthly": [{"label": "rent", "cost": "3200"}, {"label": "one barista", "cost": "4200"},
                      {"label": "utilities + wifi", "cost": "450"}],
    "startup": [{"label": "espresso + fridge", "cost": "12000"}, {"label": "fit-out", "cost": "9000"},
                {"label": "licences", "cost": "1200"}],
    "plan_per_day": "383", "capacity_per_day": "500", "ramp_months": 4, "target_per_day": "450",
}


def build_spec(spec, a):
    """The spec the model runs on: the numbers file, merged with the intake when asked."""
    if not isinstance(spec, dict):
        raise NumbersError("the input must be a JSON object")
    if spec.get("from_intake") or a.intake:
        path = a.intake or os.path.join(ledger.root(a.dir), INTAKE_REL)
        spec = apply_intake(spec, load_intake(path))
    return spec


def cmd_main(a):
    if a.template:
        ledger.guard_out(a.template, a.dir, a.force)
        with open(a.template, "w", encoding="utf-8") as fh:
            json.dump(TEMPLATE, fh, indent=2)
        print("%s wrote an example numbers file to %s. Replace every value with your own." % (ledger.OK, a.template))
        return 0
    with open(a.numbers, encoding="utf-8") as fh:
        spec = json.load(fh)
    try:
        spec = build_spec(spec, a)
        if a.what_if is not None:
            result = analyse(apply_what_if(spec, parse_what_if(a.what_if)))
            print("\n".join(render_scenarios(result)).rstrip())
            print(investor_verdict(result))
            return 0
        result = analyse(spec)
    except ValueError as e:  # NumbersError, num.MissingInput / num.BadNumber, a bad intake file
        print("error: %s" % e, file=sys.stderr)
        return 2
    text = render(result)
    out = a.out or (os.path.join(ledger.root(a.dir), "finance.md") if a.record else None)
    if out:
        ledger.guard_out(out, a.dir, a.force, ours="- unit economics")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    if a.record:
        rel = os.path.relpath(out, ledger.root(a.dir)).replace(os.sep, "/")
        metrics = [
            {"id": "contribution_per_unit", "value": round(result["contribution"], 2), "unit": "currency",
             "budget": 0, "op": ">", "evidence": [rel]},
            {"id": "year1_operating_profit", "value": round(result["year1_profit"], 2), "unit": "currency",
             "budget": 0, "op": ">", "evidence": [rel]},
        ]
        if not math.isinf(result["breakeven_per_day"]):  # never breaking even is the negative-contribution finding
            metrics.append({"id": "breakeven_orders_per_day", "value": round(result["breakeven_per_day"], 2),
                            "unit": "orders/day", "budget": 0, "op": ">=", "evidence": [rel]})
        ledger.write_scores(a.dir, SKILL, metrics)
        ledger.init_coverage(a.dir, SKILL, [("unit-economics", "Unit economics computed from inputs")])
        ledger.mark_coverage(a.dir, SKILL, "unit-economics", "covered", evidence=[rel])
        if result["contribution"] <= 0:
            fid = ledger.slug("finance-negative-contribution")
            f = ledger.new_finding(SKILL, "Each %s loses money before fixed costs" % result["unit"], "high",
                                   id=fid, category="unit-economics", evidence=[rel],
                                   fix="Raise the price or cut the per-unit cost so each sale earns something.")
            ledger.write_finding(a.dir, f)
            ledger.verify(a.dir, SKILL, fid, by="evidence-file")
        print("%s wrote %s and recorded the numbers." % (ledger.OK, out))
    elif not out:
        print(text)
    else:
        print("%s wrote %s" % (ledger.OK, out))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("numbers", nargs="?", help="the inputs JSON")
    ap.add_argument("--dir", default="proof")
    ap.add_argument("--out", help="where to write the report")
    ap.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    ap.add_argument("--record", action="store_true", help="write scores to the ledger")
    ap.add_argument("--template", help="write an example numbers file to this path and exit")
    ap.add_argument("--intake", help="read the unit cost, ad spend and orders a day from this intake.json")
    ap.add_argument("--what-if", dest="what_if",
                    help='slider values, such as "unit_cost=320,conversion=3,ad_spend=40000": print the '
                         "scenarios and the Investor vote, write nothing")
    a = ap.parse_args(argv)
    if not a.numbers and not a.template:
        ap.error("give a numbers file, or --template <path>")
    if a.what_if is not None and (a.record or a.out):
        ap.error("--what-if writes nothing; leave out --record and --out")
    return cmd_main(a)


if __name__ == "__main__":
    sys.exit(main())
