#!/usr/bin/env python3
"""Tally a market and competition scan. Standard library only, no network.

The research is done by reading public pages. This tool does the counting: how many
competitors of each type, the price range, how many rows carry a link and a date,
how well each complaint theme is evidenced, and whether a gap was found. It writes
the "Market and competition" section of the business plan and records coverage, a
score and computed findings in the Proof ledger. It never fetches anything and it
never fills a gap in the inputs.

    python3 market.py template --out DIR
    python3 market.py check competitors.csv market.json     # print only
    python3 market.py record competitors.csv market.json    # write section + ledger

    Options: --dir proof (the proof folder), --force (overwrite a file Proof did not write).
"""

import argparse
import csv
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-market"
TITLE = "# Market and competition"
MARKER = "Recorded by proof-market"
COLUMNS = ["name", "type", "url", "where", "price", "what_it_sells", "their_line",
           "rating", "review_count", "source_date"]
TYPES = ["direct", "indirect", "substitute"]
NOT_FOUND = {"", "not found", "n/a", "na", "none", "unknown", "-"}
THIN = 3
MAX_QUOTE = 300
CHECKS = [
    ("competitors-listed", "At least one direct or indirect competitor is listed"),
    ("substitutes-listed", "What a buyer would use instead is listed"),
    ("prices-compared", "Prices are compared across at least two rows"),
    ("complaints-themed", "Customer complaints are grouped into themes with linked quotes"),
    ("gap-stated", "The gap, or the lack of one, is stated"),
    ("threat-stated", "The main threat is stated"),
    ("unverified-listed", "What could not be verified is listed"),
]
FINDING_PREFIX = "market-"


class MarketError(ValueError):
    pass


def _blank(v):
    return v is None or str(v).strip().lower() in NOT_FOUND


def _is_link(v):
    return (not _blank(v)) and str(v).strip().lower().startswith(("http://", "https://"))


# ------------------------------------------------------------------ inputs

def _price(cell, currency, where):
    """None when no price was found, 0.0 for free, else the number."""
    if _blank(cell):
        return None
    text = str(cell).strip()
    if text.lower() == "free":
        return 0.0
    prefix = currency.strip()
    if prefix and text.lower().startswith(prefix.lower()):
        text = text[len(prefix):].strip()
    try:
        value = num.parse_number(text, field="price")
    except (num.BadNumber, num.MissingInput) as e:
        raise MarketError("%s: %s. Write the number only, 'free', or 'not found'; put anything "
                          "else about the price in what_it_sells." % (where, e))
    if value < 0:
        raise MarketError("%s: a price cannot be negative" % where)
    return value


def read_competitors(path, currency):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        names = [(n or "").strip().lower() for n in (reader.fieldnames or [])]
        missing = [c for c in COLUMNS if c not in names]
        if missing:
            raise MarketError("competitors.csv is missing column(s): %s" % ", ".join(missing))
        rows = []
        for i, raw in enumerate(reader, start=2):
            cells = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            if not any(cells.get(c) for c in COLUMNS):
                continue
            name = cells["name"]
            if not name:
                raise MarketError("line %d: 'name' is empty" % i)
            where = "line %d (%s)" % (i, name)
            kind = cells["type"].lower()
            if kind not in TYPES:
                raise MarketError("%s: type must be direct, indirect or substitute, got %r" % (where, cells["type"]))
            row = {c: cells[c] for c in COLUMNS}
            row["type"] = kind
            row["line"] = i
            row["price_value"] = _price(cells["price"], currency, where)
            row["has_url"] = _is_link(cells["url"])
            row["has_date"] = not _blank(cells["source_date"])
            rows.append(row)
    if not rows:
        raise MarketError("competitors.csv has no rows. If the research found nobody, say so in "
                          "market.json and list the substitutes a buyer uses today.")
    return rows


def read_market(path):
    with open(path, encoding="utf-8") as fh:
        try:
            m = json.load(fh)
        except ValueError as e:
            raise MarketError("market.json is not valid JSON: %s" % e)
    if not isinstance(m, dict):
        raise MarketError("market.json must be a JSON object")
    if not isinstance(m.get("business"), str) or not m["business"].strip():
        raise MarketError("'business' is missing from market.json")
    if not isinstance(m.get("currency"), str) or not m["currency"].strip():
        raise MarketError("'currency' is missing from market.json. Give the prefix every price is in, "
                          "such as \"Rs \" or \"$\". There is no default currency.")
    for key in ("complaints", "gap", "threat", "could_not_verify"):
        if key not in m:
            raise MarketError("'%s' is missing from market.json. Leave it empty if the research found "
                              "nothing, but never leave it out." % key)
    if not isinstance(m["complaints"], list):
        raise MarketError("'complaints' must be a list")
    for key in ("gap", "threat"):
        if not isinstance(m[key], str):
            raise MarketError("'%s' must be text" % key)
    if not isinstance(m["could_not_verify"], list) or not all(isinstance(x, str) for x in m["could_not_verify"]):
        raise MarketError("'could_not_verify' must be a list of text lines")
    for n, c in enumerate(m["complaints"], start=1):
        label = "complaint %d" % n
        if not isinstance(c, dict) or not isinstance(c.get("theme"), str) or not c["theme"].strip():
            raise MarketError("%s needs a 'theme'" % label)
        label = "complaint %r" % c["theme"]
        for flag in ("product_fixes", "core"):
            if not isinstance(c.get(flag), bool):
                raise MarketError("%s needs '%s' set to true or false" % (label, flag))
        if not isinstance(c.get("sources"), list):
            raise MarketError("%s needs a 'sources' list (it may be empty)" % label)
        for s in c["sources"]:
            if not isinstance(s, dict) or _blank(s.get("quote")):
                raise MarketError("%s has a source with no quote. Remove it rather than describe it." % label)
            if not _is_link(s.get("url")):
                raise MarketError("%s has a quote with no link. A quote that cannot be linked is not "
                                  "evidence; remove it or move it to could_not_verify." % label)
            if len(str(s["quote"])) > MAX_QUOTE:
                raise MarketError("%s has a quote over %d characters. Keep quotes short and word for word."
                                  % (label, MAX_QUOTE))
    facts = m.get("revenue_facts")
    if facts is not None:
        if not isinstance(facts, list):
            raise MarketError("'revenue_facts' must be a list of {claim, url, status}")
        for f in facts:
            if not isinstance(f, dict) or _blank(f.get("claim")):
                raise MarketError("each entry in 'revenue_facts' needs a 'claim'")
            if _blank(f.get("status")):
                raise MarketError("revenue fact %r needs a 'status' (for example confirmed, reported, estimate)"
                                  % f["claim"])
    return m


# ---------------------------------------------------------------- analysis

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def analyse(rows, market):
    counts = {t: sum(1 for r in rows if r["type"] == t) for t in TYPES}
    priced = [r["price_value"] for r in rows if r["price_value"] is not None]
    linked = [r for r in rows if r["has_url"] and r["has_date"]]
    pct = 100.0 * len(linked) / len(rows)
    pct = 100.0 if len(linked) == len(rows) else min(round(pct, 1), 99.9)

    complaints = []
    for order, c in enumerate(market["complaints"]):
        complaints.append({"theme": c["theme"].strip(), "product_fixes": c["product_fixes"], "core": c["core"],
                           "sources": c["sources"], "count": len(c["sources"]),
                           "thin": len(c["sources"]) < THIN, "order": order})
    complaints.sort(key=lambda c: (-c["count"], c["order"]))

    gap = market["gap"].strip()
    a = {
        "business": market["business"].strip(), "currency": market["currency"],
        "price_for": str(market.get("price_for") or "").strip(),
        "rows": sorted(rows, key=lambda r: (TYPES.index(r["type"]), r["line"])),
        "counts": counts, "total": len(rows),
        "priced": len(priced), "unpriced": len(rows) - len(priced),
        "price_low": min(priced) if priced else None,
        "price_median": _median(priced) if priced else None,
        "price_high": max(priced) if priced else None,
        "all_free": bool(priced) and all(p == 0 for p in priced),
        "linked": len(linked), "sources_linked_pct": pct,
        "complaints": complaints,
        "gap": gap, "gap_found": bool(gap) and gap.lower() != "none",
        "threat": market["threat"].strip(),
        "could_not_verify": [x.strip() for x in market["could_not_verify"] if x.strip()],
        "revenue_facts": market.get("revenue_facts") or [],
    }
    a["findings"] = _findings(a)
    return a


def _findings(a):
    out = []
    core = [c for c in a["complaints"] if c["core"]]
    thin_core = [c for c in core if c["thin"]]
    if not core or thin_core:
        if not core:
            why = "No complaint theme is marked as the core problem this business solves."
        else:
            why = "; ".join("'%s' has %d source(s)" % (c["theme"], c["count"]) for c in thin_core) + \
                  ". Under %d linked sources is too few to say people have this problem." % THIN
        out.append({"id": "market-core-thin", "severity": "medium", "category": "complaints",
                    "title": "The core problem is thinly evidenced", "description": why,
                    "fix": "Find at least %d public, linked complaints about the core problem, or talk to "
                           "real buyers before building on it." % THIN})
    with_sources = [c for c in a["complaints"] if c["count"] > 0]
    if with_sources:
        top = with_sources[0]["count"]
        loudest = [c for c in with_sources if c["count"] == top]
        if all(not c["product_fixes"] for c in loudest):
            out.append({"id": "market-loudest-unfixed", "severity": "medium", "category": "complaints",
                        "title": "The loudest complaints are ones this product does not fix",
                        "description": "The most-evidenced theme (%s, %d sources) is marked product_fixes: false."
                                       % (", ".join("'%s'" % c["theme"] for c in loudest), top),
                        "fix": "Either aim the product at what people complain about most, or show why the "
                               "quieter problem is worth more."})
    if a["all_free"]:
        out.append({"id": "market-all-free", "severity": "low", "category": "prices",
                    "title": "Every alternative is free to the buyer",
                    "description": "All %d rows with a price found cost the buyer nothing." % a["priced"],
                    "fix": "Say plainly why someone would pay when the alternatives are free."})
    seen = set()
    for r in a["rows"]:
        if r["has_url"] and r["has_date"]:
            continue
        lacks = [w for w, ok in (("a source link", r["has_url"]), ("a source date", r["has_date"])) if not ok]
        fid = ("market-unlinked-" + ledger.slug(r["name"]))[:70].rstrip("-")
        if fid in seen:
            fid = "%s-line-%d" % (fid, r["line"])
        seen.add(fid)
        out.append({"id": fid, "severity": "low", "category": "sources",
                    "title": "%s has no %s" % (r["name"], " or ".join(w.replace("a source ", "source ") for w in lacks)),
                    "description": "Line %d of competitors.csv lacks %s, so its facts cannot be checked."
                                   % (r["line"], " and ".join(lacks)),
                    "location": "competitors.csv:%d" % r["line"],
                    "fix": "Add the page the facts came from (starting with http) and the date it was read, "
                           "or remove the row."})
    if not a["gap_found"]:
        out.append({"id": "market-no-gap", "severity": "medium", "category": "gap",
                    "title": "No gap was found",
                    "description": "The gap is %s." % ("written as 'none'" if a["gap"] else "empty"),
                    "fix": "A crowded market can still work, but the plan must say what this business does "
                           "that the others do not."})
    return out


# ------------------------------------------------------------------ report

def _money(currency, x):
    s = "{:,.2f}".format(x)
    if s.endswith(".00"):
        s = s[:-3]
    return currency + s


def _cell(v):
    text = " ".join(str(v).split()).replace("|", "\\|")
    return text if not _blank(text) else "not found"


def _price_text(a, r):
    if r["price_value"] is None:
        return "not found"
    return "free" if r["price_value"] == 0 else _money(a["currency"], r["price_value"])


def render(a):
    L = [TITLE, "", "For: %s. Public sources only. Every row links to the page it came from." % a["business"], ""]
    c = a["counts"]
    L += ["%d listed: %d direct, %d indirect, %d substitute." % (a["total"], c["direct"], c["indirect"], c["substitute"]),
          "", "## Who already solves this", "",
          "| Name | Type | Where | Price | What it sells | Their line | Rating | Reviews | Source |",
          "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |"]
    for r in a["rows"]:
        src = "[link](%s)" % r["url"] if r["has_url"] else "no link"
        src += ", read %s" % r["source_date"] if r["has_date"] else ", no date"
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            _cell(r["name"]), r["type"], _cell(r["where"]), _price_text(a, r), _cell(r["what_it_sells"]),
            _cell(r["their_line"]), _cell(r["rating"]), _cell(r["review_count"]), src))
    L += ["", "Sources linked and dated: %d of %d rows (%s%%)." % (
        a["linked"], a["total"], ("%.1f" % a["sources_linked_pct"]).rstrip("0").rstrip(".")), ""]

    L += ["## What they charge", ""]
    if a["priced"]:
        what = " Prices are for %s." % a["price_for"] if a["price_for"] else ""
        L.append("Lowest %s, median %s, highest %s, across %d of %d rows.%s" % (
            _money(a["currency"], a["price_low"]), _money(a["currency"], a["price_median"]),
            _money(a["currency"], a["price_high"]), a["priced"], a["total"], what))
    else:
        L.append("No price was found for any row.")
    if a["unpriced"]:
        L.append("%d row(s) had no price found and are left out of the range." % a["unpriced"])
    if a["all_free"]:
        L.append("Every alternative with a price found is free to the buyer.")
    L.append("")

    L += ["## What their customers complain about", ""]
    if not a["complaints"]:
        L += ["No complaint themes were recorded.", ""]
    for i, t in enumerate(a["complaints"], start=1):
        mark = " (thin: under %d sources)" % THIN if t["thin"] else ""
        L.append("%d. **%s**: %d source(s)%s. %s %s" % (
            i, t["theme"], t["count"], mark,
            "This is the core problem." if t["core"] else "Not the core problem.",
            "This product fixes it." if t["product_fixes"] else "This product does not fix it."))
        for s in t["sources"]:
            when = ", %s" % str(s["date"]).strip() if not _blank(s.get("date")) else ""
            L.append("   - \"%s\" ([source](%s)%s)" % (" ".join(str(s["quote"]).split()), str(s["url"]).strip(), when))
    if a["complaints"]:
        L.append("")

    L += ["## The gap", "", a["gap"] if a["gap_found"] else "No gap was found.", "",
          "## The main threat", "", a["threat"] or "Not stated.", ""]

    if a["revenue_facts"]:
        L += ["## Revenue facts", ""]
        for f in a["revenue_facts"]:
            src = "[source](%s)" % str(f["url"]).strip() if _is_link(f.get("url")) else "no link"
            L.append("- %s (%s; %s)" % (" ".join(str(f["claim"]).split()), str(f["status"]).strip(), src))
        L.append("")

    L += ["## What could not be verified", ""]
    if a["could_not_verify"]:
        L += ["- %s" % x for x in a["could_not_verify"]]
    else:
        L.append("Nothing was listed. That is unusual for desk research; treat it as not yet checked.")
    L += ["", "_%s from competitors.csv and market.json. A page read on one day can change the next: "
              "open the links before relying on a price or a rating._" % MARKER]
    return "\n".join(L) + "\n"


def _summary(a):
    L = ["%s rows: %d direct, %d indirect, %d substitute. Sources linked: %s%%." % (
        a["total"], a["counts"]["direct"], a["counts"]["indirect"], a["counts"]["substitute"],
        ("%.1f" % a["sources_linked_pct"]).rstrip("0").rstrip("."))]
    if not a["findings"]:
        L.append("%s no problems computed from these inputs." % ledger.OK)
    for f in a["findings"]:
        L.append("%s [%s] %s" % (ledger.BAD, f["severity"], f["title"]))
    return "\n".join(L)


# ---------------------------------------------------------------- commands

def _load(a):
    for p in (a.competitors, a.market):
        if not os.path.isfile(p):
            raise MarketError("input file not found: %s" % p)
    market = read_market(a.market)
    rows = read_competitors(a.competitors, market["currency"])
    return analyse(rows, market)


def cmd_template(a):
    os.makedirs(a.out, exist_ok=True)
    for src, name in (("competitors.example.csv", "competitors.csv"), ("market.example.json", "market.json")):
        dest = os.path.join(a.out, name)
        ledger.guard_out(dest, a.dir, a.force)
        shutil.copyfile(os.path.join(HERE, src), dest)
        print("%s wrote %s" % (ledger.OK, dest))
    print("These are made-up rows. Replace every one with what the public pages really say.")
    return 0


def cmd_check(a):
    result = _load(a)
    print(render(result))
    print(_summary(result))
    print("Nothing was recorded. Use 'record' to write the section and the ledger.")
    return 0


def _clear_findings(d):
    folder = os.path.dirname(ledger.finding_path(d, SKILL, "x"))
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith(FINDING_PREFIX) and name.endswith(".json"):
                os.remove(os.path.join(folder, name))


def cmd_record(a):
    result = _load(a)
    base = ledger.root(a.dir)
    out = os.path.join(base, "business", "market.md")
    ledger.guard_out(out, a.dir, a.force, ours=MARKER)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    evidence = []
    for src, name in ((a.competitors, "competitors.csv"), (a.market, "market.json")):
        dest = os.path.join(ev_dir, name)
        if os.path.abspath(src) != os.path.abspath(dest):
            shutil.copyfile(src, dest)
        evidence.append(os.path.relpath(dest, base).replace(os.sep, "/"))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(result))
    section = os.path.relpath(out, base).replace(os.sep, "/")

    ledger.write_scores(a.dir, SKILL, [
        {"id": "sources_linked_pct", "value": result["sources_linked_pct"], "unit": "percent",
         "budget": 100, "op": "==", "source": "market.py", "evidence": evidence[:1]},
    ])

    ledger.init_coverage(a.dir, SKILL, CHECKS, keep_existing=False)
    c = result["counts"]
    covered = {
        "competitors-listed": c["direct"] + c["indirect"] >= 1,
        "substitutes-listed": c["substitute"] >= 1,
        "prices-compared": result["priced"] >= 2,
        "complaints-themed": any(t["count"] for t in result["complaints"]),
        "gap-stated": bool(result["gap"]),
        "threat-stated": bool(result["threat"]),
        "unverified-listed": bool(result["could_not_verify"]),
    }
    for cid, _ in CHECKS:
        if covered[cid]:
            ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=evidence)

    _clear_findings(a.dir)
    for f in result["findings"]:
        finding = ledger.new_finding(SKILL, f["title"], f["severity"], id=f["id"], category=f["category"],
                                     description=f["description"], fix=f["fix"], location=f.get("location"),
                                     evidence=evidence + [section])
        ledger.write_finding(a.dir, finding)
        ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")

    print(_summary(result))
    not_run = [cid for cid, _ in CHECKS if not covered[cid]]
    if not_run:
        print("Not covered by these inputs: %s" % ", ".join(not_run))
    print("%s wrote %s and recorded coverage, 1 score and %d finding(s)." % (ledger.OK, out, len(result["findings"])))
    return 0


def main(argv=None):
    ledger.console()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=argparse.SUPPRESS, help="the proof folder (default: proof)")
    common.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                        help="overwrite a file even if Proof did not write it")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof", help="the proof folder (default: proof)")
    ap.add_argument("--force", action="store_true", help="overwrite a file even if Proof did not write it")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", parents=[common], help="write example inputs into a folder")
    t.add_argument("--out", required=True, help="folder to write competitors.csv and market.json into")
    t.set_defaults(func=cmd_template)

    for name, func, text in (("check", cmd_check, "print the section and the problems, record nothing"),
                             ("record", cmd_record, "write the section file and the ledger")):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("competitors", help="competitors.csv")
        p.add_argument("market", help="market.json")
        p.set_defaults(func=func)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (MarketError, ledger.LedgerError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
