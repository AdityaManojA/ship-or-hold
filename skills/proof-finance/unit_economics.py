#!/usr/bin/env python3
"""Work out the unit economics behind a product. Standard library only, no network.

Reads one JSON file of inputs and reports what one unit earns after its own costs,
the real margin at the planned volume, how many units a day break even, a month by
month year one, the cash needed before it pays for itself, and three what-ifs.
Every number traces to an input. A missing field is an error, never a silent zero,
and numbers are read locale-safely.

    python3 unit_economics.py numbers.json --out proof/finance.md
    python3 unit_economics.py numbers.json --record      # write scores to the ledger
    python3 unit_economics.py --template numbers.json
"""

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-finance"


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


def analyse(spec):
    if not isinstance(spec, dict):
        raise NumbersError("the input must be a JSON object")
    if "price" not in spec:
        raise NumbersError("'price' is missing")
    price = num.parse_number(spec["price"], field="price")
    unit = spec.get("unit", "unit")
    days = num.parse_number(spec.get("days_per_month", 30), field="days_per_month")

    variable, var_items = _sum_items(spec.get("variable"), "variable")
    fixed, fixed_items = _sum_items(spec.get("fixed_monthly"), "fixed_monthly")
    startup, startup_items = _sum_items(spec.get("startup"), "startup")

    if "plan_per_day" not in spec:
        raise NumbersError("'plan_per_day' is missing (how many %ss you expect to sell a day)" % unit)
    plan_per_day = num.parse_number(spec["plan_per_day"], field="plan_per_day")

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
    ramp = spec.get("ramp_months")
    ramp = int(num.parse_number(ramp, field="ramp_months")) if ramp is not None else 0
    months = []
    cumulative = -startup
    low_water = cumulative
    payback_month = None
    for m in range(1, 13):
        frac = 1.0 if ramp <= 0 or m > ramp else (m / float(ramp))
        units = plan_per_month * frac
        profit = units * contribution - fixed
        cumulative += profit
        low_water = min(low_water, cumulative)
        if payback_month is None and cumulative >= 0:
            payback_month = m
        months.append({"month": m, "units": units, "profit": profit, "cumulative": cumulative})

    year1_profit = sum(mo["profit"] for mo in months)
    cash_needed = -low_water if low_water < 0 else 0.0

    return {
        "business": spec.get("business", ""), "unit": unit, "price": price,
        "variable": variable, "fixed_monthly": fixed, "startup": startup,
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


def _money(x):
    return "-$%s" % "{:,.0f}".format(-x) if x < 0 else "$%s" % "{:,.0f}".format(x)


def render(a):
    L = ["# %s - unit economics" % (a["business"] or "Your product"), ""]
    u = a["unit"]
    L += ["| key number | |", "| --- | ---: |",
          "| Price | $%.2f per %s |" % (a["price"], u),
          "| Cost per %s | %s |" % (u, _money(a["variable"])),
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
    L += ["## Year 1, month by month", "", "| month | %ss | profit | cumulative |" % u, "| ---: | ---: | ---: | ---: |"]
    for mo in a["months"]:
        L.append("| %d | %s | %s | %s |" % (mo["month"], "{:,.0f}".format(mo["units"]), _money(mo["profit"]), _money(mo["cumulative"])))
    L += ["", "## What if", "",
          "- Price drops 10%%: operating profit a month becomes %s" % _money(a["whatifs"]["price_down_10pct"]),
          "- Sales come in 25%% low: %s" % _money(a["whatifs"]["sales_down_25pct"]),
          "- Variable costs rise 20%%: %s" % _money(a["whatifs"]["variable_up_20pct"]), ""]
    L.append("_Every number traces to an input you gave. Estimates are only estimates: confirm costs with "
             "real quotes and the structure with an accountant. Not financial advice._")
    return "\n".join(L) + "\n"


TEMPLATE = {
    "business": "Matcha cafe",
    "unit": "cup", "price": "6.50", "days_per_month": 30,
    "variable": [{"label": "matcha + milk", "cost": "1.10"}, {"label": "cup + lid", "cost": "0.35"},
                 {"label": "card fee", "cost": "0.29"}],
    "fixed_monthly": [{"label": "rent", "cost": "3200"}, {"label": "one barista", "cost": "4200"},
                      {"label": "utilities + wifi", "cost": "450"}],
    "startup": [{"label": "espresso + fridge", "cost": "12000"}, {"label": "fit-out", "cost": "9000"},
                {"label": "licences", "cost": "1200"}],
    "plan_per_day": "383", "capacity_per_day": "500", "ramp_months": 4,
}


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
        result = analyse(spec)
    except NumbersError as e:
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
        ledger.write_scores(a.dir, SKILL, [
            {"id": "contribution_per_unit", "value": round(result["contribution"], 2), "unit": "currency",
             "budget": 0, "op": ">", "evidence": [rel]},
            {"id": "year1_operating_profit", "value": round(result["year1_profit"], 2), "unit": "currency",
             "budget": 0, "op": ">", "evidence": [rel]},
        ])
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
    a = ap.parse_args(argv)
    if not a.numbers and not a.template:
        ap.error("give a numbers file, or --template <path>")
    return cmd_main(a)


if __name__ == "__main__":
    sys.exit(main())
