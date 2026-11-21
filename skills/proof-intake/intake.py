#!/usr/bin/env python3
"""Record the four real numbers a business audit starts from. Standard library only, no network.

Landed unit cost, monthly ad spend, daily sessions and daily orders (plus the
currency and the period they cover), each with where it came from: the owner, a
Shopify export or an analytics export. Nothing here is ever estimated: a missing
field stays missing and `check` exits 2 naming it.

    python3 intake.py set --currency "Rs " --unit-cost 300 --ad-spend 50000 --sessions 20 --orders 0.4
    python3 intake.py import-shopify --orders-csv orders.csv --sessions-csv sessions.csv --days 30
    python3 intake.py check
    python3 intake.py record
    python3 intake.py status
"""

import argparse
import csv
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-intake"
INTAKE = os.path.join("business", "intake.json")
SECTION = os.path.join("business", "intake.md")
MARKER = "# The numbers we started from"
SOURCES = ("owner", "shopify-export", "analytics-export")
DAYS_PER_MONTH = 30.0

# (flag, intake key, label, money?)
FIELDS = [
    ("unit-cost", "landed_unit_cost", "Landed unit cost", True),
    ("ad-spend", "monthly_ad_spend", "Monthly ad spend", True),
    ("sessions", "daily_sessions", "Daily sessions (visits)", False),
    ("orders", "daily_orders", "Daily orders", False),
]
OPTIONAL = [("aov", "average_order_value", "Average order value", True)]
REQUIRED_KEYS = [f[1] for f in FIELDS]
COVERAGE = [
    ("unit-cost", "The landed unit cost is known, with its source"),
    ("ad-spend", "The monthly ad spend is known, with its source"),
    ("sessions", "Daily sessions are known, with their source"),
    ("orders", "Daily orders are known, with their source"),
]
FINDING_PREFIX = "intake-"
CANCELLED = ("voided", "refunded")


class IntakeError(ValueError):
    pass


# ------------------------------------------------------------------ the file

def intake_path(d):
    return os.path.join(ledger.root(d), INTAKE)


def load(d):
    path = intake_path(d)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise IntakeError("%s is not a JSON object" % path)
    return data


def value(intake, key):
    f = intake.get(key)
    if isinstance(f, dict) and isinstance(f.get("value"), (int, float)) and not isinstance(f.get("value"), bool):
        return float(f["value"])
    return None


def missing(intake):
    """The required fields that are not set, as their flag names."""
    out = [flag for flag, key, _, _ in FIELDS if value(intake, key) is None]
    cur = intake.get("currency")
    if not isinstance(cur, str) or not cur.strip():
        out.insert(0, "currency")
    return out


def compute(intake):
    """Conversion and ad spend per order, from whatever is present."""
    s, o, ad = value(intake, "daily_sessions"), value(intake, "daily_orders"), value(intake, "monthly_ad_spend")
    out = {}
    if s is not None and o is not None and s > 0:
        out["conversion_pct"] = round(o / s * 100, 4)
    if ad is not None and o is not None and o > 0:
        out["ad_spend_per_order"] = round(ad / (o * DAYS_PER_MONTH), 2)
    return out


def save(d, intake):
    intake["computed"] = compute(intake)
    path = intake_path(d)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(intake, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def flags(intake):
    """Consistency problems, each a dict(id, severity, title, description, fix)."""
    out = []
    s, o = value(intake, "daily_sessions"), value(intake, "daily_orders")
    aov = value(intake, "average_order_value")
    cur = intake.get("currency") or ""
    comp = compute(intake)
    if s is not None and o is not None and o > s:
        out.append(dict(
            id="intake-orders-exceed-sessions", severity="high",
            title="More orders a day (%g) than sessions a day (%g), which is impossible" % (o, s),
            description="Every order needs at least one visit, so these two numbers cannot both be right.",
            fix="Check whether sessions and orders were swapped or cover different periods, then set them again."))
    elif "conversion_pct" in comp and (comp["conversion_pct"] > 20 or comp["conversion_pct"] < 0.1):
        pct = comp["conversion_pct"]
        out.append(dict(
            id="intake-conversion-%s" % ("high" if pct > 20 else "low"), severity="medium",
            title="Conversion of %s%% is %s for a store" % ("{:.2f}".format(pct), "unusually high" if pct > 20 else "unusually low"),
            description="Orders divided by sessions is %s%%, outside 0.1%% to 20%%." % "{:.2f}".format(pct),
            fix="Check whether sessions and orders were swapped or cover different periods."))
    if aov is not None and "ad_spend_per_order" in comp and comp["ad_spend_per_order"] > aov:
        out.append(dict(
            id="intake-ad-spend-over-aov", severity="medium",
            title="Ad spend per order (%s%s) is more than the average order (%s%s)" % (
                cur, "{:,.2f}".format(comp["ad_spend_per_order"]), cur, "{:,.2f}".format(aov)),
            description="Each order costs more in ads than the buyer pays, before the product or shipping.",
            fix="Confirm the ad spend and orders cover the same period; if they do, the ads lose money on every order."))
    return out


# ------------------------------------------------------------------ commands

def _set_field(intake, key, raw, source, flag):
    v = num.parse_number(raw, field="--" + flag)
    if v < 0:
        raise IntakeError("--%s cannot be negative" % flag)
    intake[key] = {"value": v, "source": source}


def cmd_set(a):
    if a.source not in SOURCES:
        raise IntakeError("--source must be one of %s" % ", ".join(SOURCES))
    intake = load(a.dir)
    given = False
    if a.currency is not None:
        if not a.currency.strip():
            raise IntakeError("--currency must be the prefix money is printed with, such as \"Rs \" or \"$\"")
        intake["currency"] = a.currency
        given = True
    if a.period is not None:
        intake["period"] = a.period
        given = True
    for flag, key, _, money in FIELDS + OPTIONAL:
        raw = getattr(a, flag.replace("-", "_"))
        if raw is None:
            continue
        _set_field(intake, key, raw, a.source, flag)
        given = True
    if not given:
        raise IntakeError("nothing to set: give at least one of --currency, --period, --unit-cost, "
                          "--ad-spend, --sessions, --orders, --aov")
    path = save(a.dir, intake)
    gaps = missing(intake)
    print("%s wrote %s%s" % (ledger.OK, path, (". Still missing: %s" % ", ".join(gaps)) if gaps else ""))
    return 0


def _date(text):
    text = (text or "").strip()
    if len(text) < 10:
        return None
    try:
        return datetime.date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return None


def _rows(path, need):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = [c.strip() for c in (reader.fieldnames or [])]
        absent = [c for c in need if c not in cols]
        if absent:
            raise IntakeError("%s has no %s column (found: %s)" % (path, ", ".join(absent), ", ".join(cols) or "none"))
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def read_orders(path):
    """Distinct orders that were not voided or refunded: count, subtotal sum, first and last date.

    One order spans several rows (one per line item) sharing the same Name; the
    status, totals and date sit on the first row that carries them.
    """
    orders = {}
    for row in _rows(path, ("Name", "Created at", "Financial Status", "Subtotal", "Total")):
        name = row["Name"]
        if not name:
            continue
        o = orders.setdefault(name, {"status": "", "amount": None, "date": None})
        if not o["status"] and row["Financial Status"]:
            o["status"] = row["Financial Status"].lower()
        if o["amount"] is None:
            raw = row["Subtotal"] or row["Total"]
            if raw:
                o["amount"] = num.parse_number(raw, decimal=".", field="Subtotal of %s" % name)
        if o["date"] is None:
            o["date"] = _date(row["Created at"])
    kept = [o for o in orders.values() if o["status"] not in CANCELLED]
    dates = sorted(o["date"] for o in kept if o["date"])
    amounts = [o["amount"] for o in kept if o["amount"] is not None]
    return {"count": len(kept), "skipped": len(orders) - len(kept),
            "aov": (sum(amounts) / len(amounts)) if amounts else None,
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None}


def read_sessions(path):
    total = 0.0
    for row in _rows(path, ("Sessions",)):
        if row["Sessions"]:
            total += num.parse_number(row["Sessions"], decimal=".", field="Sessions")
    return total


def cmd_import_shopify(a):
    o = read_orders(a.orders_csv)
    if a.days is not None:
        days = num.parse_number(a.days, field="--days")
        if days <= 0:
            raise IntakeError("--days must be more than 0")
        period = "%g days" % days
    else:
        if not o["first"]:
            raise IntakeError("no order carries a 'Created at' date; give --days")
        days = float((o["last"] - o["first"]).days + 1)
        period = "%s to %s, %g days" % (o["first"].isoformat(), o["last"].isoformat(), days)
    intake = load(a.dir)
    intake["daily_orders"] = {"value": round(o["count"] / days, 4), "source": "shopify-export"}
    if o["aov"] is not None:
        intake["average_order_value"] = {"value": round(o["aov"], 2), "source": "shopify-export"}
    if a.sessions_csv:
        intake["daily_sessions"] = {"value": round(read_sessions(a.sessions_csv) / days, 4), "source": "shopify-export"}
    intake.setdefault("period", period)
    path = save(a.dir, intake)
    print("%s %d order(s) over %g days (%d voided or refunded left out) -> %s" % (
        ledger.OK, o["count"], days, o["skipped"], path))
    print("   Unit cost and ad spend never come from these files: set them with 'set --unit-cost --ad-spend'.")
    gaps = missing(intake)
    if gaps:
        print("%s still missing: %s" % (ledger.BAD, ", ".join(gaps)))
    return 0


def _show(intake):
    cur = intake.get("currency") or ""
    lines = []
    for flag, key, label, money in FIELDS + OPTIONAL:
        v = value(intake, key)
        if v is None:
            if (flag, key, label, money) in OPTIONAL:
                continue
            lines.append((label, "missing", ""))
        else:
            shown = "%s%s" % (cur, "{:,.2f}".format(v)) if money else "{:,.2f}".format(v).rstrip("0").rstrip(".")
            lines.append((label, shown, intake[key].get("source", "")))
    return lines


def cmd_check(a):
    intake = load(a.dir)
    gaps = missing(intake)
    if gaps:
        for g in gaps:
            print("%s missing: %s" % (ledger.BAD, g), file=sys.stderr)
        print("error: the intake is incomplete (%s); ask the owner, nothing here is estimated" % ", ".join(gaps),
              file=sys.stderr)
        return 2
    comp = compute(intake)
    cur = intake["currency"]
    if "conversion_pct" in comp:
        print("conversion: %.2f%% of sessions order" % comp["conversion_pct"])
    if "ad_spend_per_order" in comp:
        print("ad spend per order: %s%s" % (cur, "{:,.2f}".format(comp["ad_spend_per_order"])))
    fl = flags(intake)
    for f in fl:
        print("%s %s: %s. %s" % (ledger.BAD, f["severity"], f["title"], f["fix"]))
    print("%s the intake is complete%s." % (ledger.OK, (" with %d flag(s)" % len(fl)) if fl else ""))
    return 0


def render(intake):
    L = [MARKER, ""]
    if intake.get("period"):
        L += ["Period: %s." % intake["period"], ""]
    L += ["| number | value | source |", "| --- | ---: | --- |"]
    for label, shown, source in _show(intake):
        L.append("| %s | %s | %s |" % (label, shown, source or "-"))
    comp = compute(intake)
    cur = intake.get("currency") or ""
    if "conversion_pct" in comp:
        L.append("| Conversion (orders / sessions) | %.2f%% | computed |" % comp["conversion_pct"])
    if "ad_spend_per_order" in comp:
        L.append("| Ad spend per order | %s%s | computed |" % (cur, "{:,.2f}".format(comp["ad_spend_per_order"])))
    L += ["", "_Every value came from the owner or an export, as its source says. None is estimated._", ""]
    return "\n".join(L)


def _remove_stale(d):
    folder = os.path.dirname(ledger.finding_path(d, SKILL, "x"))
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if name.endswith(".json") and name.startswith(FINDING_PREFIX):
            os.remove(os.path.join(folder, name))


def cmd_record(a):
    d, root = a.dir, ledger.root(a.dir)
    if not os.path.exists(intake_path(d)):
        raise IntakeError("no %s yet; run 'set' or 'import-shopify' first" % intake_path(d))
    intake = load(d)
    save(d, intake)  # refresh the computed numbers
    section = os.path.join(root, SECTION)
    ledger.guard_out(section, d, a.force, ours=MARKER)
    with open(section, "w", encoding="utf-8") as fh:
        fh.write(render(intake))
    rel_json, rel_md = INTAKE.replace(os.sep, "/"), SECTION.replace(os.sep, "/")
    evidence = [rel_json, rel_md]

    ledger.init_coverage(d, SKILL, COVERAGE, keep_existing=False)
    for flag, key, label, _ in FIELDS:
        if value(intake, key) is not None:
            ledger.mark_coverage(d, SKILL, flag, "covered", evidence=evidence,
                                 note="source: %s" % intake[key].get("source", "unknown"))
        else:
            ledger.mark_coverage(d, SKILL, flag, "not_run", note="%s is missing; ask the owner." % label)

    comp = compute(intake)
    metrics = []
    if "conversion_pct" in comp:
        metrics.append({"id": "conversion_pct", "value": comp["conversion_pct"], "unit": "percent",
                        "budget": 0, "op": ">=", "evidence": [rel_json]})
    if "ad_spend_per_order" in comp:
        metrics.append({"id": "ad_spend_per_order", "value": comp["ad_spend_per_order"], "unit": "currency",
                        "budget": 0, "op": ">=", "evidence": [rel_json]})
    ledger.write_scores(d, SKILL, metrics)

    _remove_stale(d)
    fl = flags(intake)
    for f in fl:
        finding = ledger.new_finding(SKILL, f["title"], f["severity"], id=f["id"], category="intake",
                                     description=f["description"], fix=f["fix"], evidence=[rel_json],
                                     location=rel_json)
        ledger.write_finding(d, finding)
        ledger.verify(d, SKILL, f["id"], by="evidence-file")
    gaps = missing(intake)
    print("%s wrote %s and recorded coverage, %d score(s) and %d finding(s)." % (ledger.OK, section, len(metrics), len(fl)))
    if gaps:
        print("%s still missing: %s" % (ledger.BAD, ", ".join(gaps)))
    return 0


def cmd_status(a):
    intake = load(a.dir)
    if not intake:
        print("%s no intake yet at %s" % (ledger.BAD, intake_path(a.dir)))
        return 0
    print("currency: %r   period: %s" % (intake.get("currency", "missing"), intake.get("period", "not given")))
    for label, shown, source in _show(intake):
        print("  %-26s %-16s %s" % (label, shown, source))
    gaps = missing(intake)
    print("%s %s" % ((ledger.BAD, "missing: " + ", ".join(gaps)) if gaps else (ledger.OK, "complete")))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof", help="the proof folder (default: proof)")
    ap.add_argument("--force", action="store_true", help="overwrite a file even if Proof did not write it")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--dir", default=argparse.SUPPRESS, help="the proof folder (default: proof)")
        p.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                       help="overwrite a file even if Proof did not write it")

    s = sub.add_parser("set", help="set one or more numbers, each with its source")
    s.add_argument("--currency", help='the prefix money is printed with, such as "Rs "')
    s.add_argument("--unit-cost", help="landed cost of one unit (product, packing, inbound freight)")
    s.add_argument("--ad-spend", help="ad spend a month")
    s.add_argument("--sessions", help="visits (sessions) a day")
    s.add_argument("--orders", help="orders a day")
    s.add_argument("--aov", help="average order value (optional)")
    s.add_argument("--period", help='the period the numbers cover, such as "last 30 days"')
    s.add_argument("--source", default="owner", help="owner, shopify-export or analytics-export (default owner)")
    common(s)
    s.set_defaults(func=cmd_set)

    i = sub.add_parser("import-shopify", help="fill orders, sessions and AOV from Shopify Admin exports")
    i.add_argument("--orders-csv", required=True, help="Admin > Orders > Export, as CSV")
    i.add_argument("--sessions-csv", help="Analytics > Reports > Sessions over time > Export")
    i.add_argument("--days", help="the period length in days (default: first to last order date)")
    common(i)
    i.set_defaults(func=cmd_import_shopify)

    for name, func, text in (("check", cmd_check, "exit 2 naming each missing field; show the flags"),
                             ("record", cmd_record, "write the section and the ledger"),
                             ("status", cmd_status, "show what is set and where it came from")):
        p = sub.add_parser(name, help=text)
        common(p)
        p.set_defaults(func=func)

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: set, import-shopify, check, record or status")
    try:
        return a.func(a)
    except (ValueError, OSError) as e:  # IntakeError, num.MissingInput / num.BadNumber, a missing file
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
