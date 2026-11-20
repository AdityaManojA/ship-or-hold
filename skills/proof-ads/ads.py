#!/usr/bin/env python3
"""Tear down the brand's live ads against their landing pages and the competitors' ads.

Standard library only, no network. Reads one ads.json that holds the ads Claude
copied word for word from the public Meta Ad Library, plus the landing pages saved
as HTML files. It checks each brand ad against the page it sends people to (do the
headline's words, the price, the offer and the button match what the page shows),
then lists the themes at least two competitor ads use that no brand ad mentions.
A missing input is an error with exit code 2.

    python3 ads.py template --out ads-input          # an example ads.json and landing pages
    python3 ads.py check ads-input/ads.json          # print the teardown, record nothing
    python3 ads.py record ads-input/ads.json         # write the section and the ledger
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-ads"
SECTION = os.path.join("business", "ads.md")
MARKER = "Written by proof-ads"
FORMATS = ("image", "video", "carousel")
MATCH_FLOOR = 40.0      # below this share of headline words found, the ad and page talk about different things
MATCH_BUDGET = 60.0     # the score bar for the average match
MAX_GAP_FINDINGS = 5
EXAMPLE = os.path.join(HERE, "ads.example.json")
EXAMPLE_PAGES = os.path.join(HERE, "landing")

COVERAGE = [
    ("brand-ads-read", "The brand's active ads were read from the public ad library"),
    ("competitor-ads-read", "Competitors' active ads were read from the public ad library"),
    ("landing-match-checked", "Each brand ad was checked against the landing page it links to"),
    ("gaps-listed", "Themes competitors use and the brand does not are listed"),
]

STOP = set("""
a an and are as at be by for from has have in into is it its of on or our ours so that the their them
this to up us was we were will with you your yours all any more most just only also than then too very
now new get got buy shop order here there what when where which who why how can do does not no yes
best top great perfect ultimate amazing online today click see now link bio offer sale rs inr
""".split())

# Fixed theme dictionary: what a buyer might hear in an ad. Matched on headline, copy and offer.
THEMES = [
    ("sizing/fit", ("size chart", "size guide", "sizing", "true to size", "fit", "fits", "fitting", "every size",
                    "all sizes", "plus size", "xs to", "measurement")),
    ("fabric/material", ("cotton", "linen", "silk", "fabric", "material", "rayon", "modal", "chanderi",
                         "breathable", "handloom", "khadi", "polyester", "wool")),
    ("reviews/social proof", ("review", "rated", "star", "customers love", "loved by", "bestseller",
                              "best seller", "testimonial", "happy customer")),
    ("price/discount", ("% off", "discount", "sale", "save", "price drop", "mrp", "deal", "starting at",
                        "starting from")),
    ("free delivery", ("free delivery", "free shipping", "ships free", "no delivery charge")),
    ("cash on delivery", ("cash on delivery", "cod", "pay on delivery")),
    ("returns/exchange", ("easy return", "return", "exchange", "no questions asked", "refund")),
    ("comfort", ("comfort", "comfortable", "comfy", "soft", "all day", "lightweight", "relaxed")),
    ("gifting", ("gift", "gifting", "for her", "for him", "festive", "diwali", "rakhi", "wedding season")),
    ("new arrivals", ("new arrival", "new collection", "just dropped", "just in", "new drop", "launch")),
    ("limited stock", ("limited stock", "limited edition", "few left", "selling fast", "only a few", "last chance",
                       "while stocks last")),
]


def _word_re(w):
    w = " ".join(w.replace("-", " ").split())
    pre = r"(?<![a-z0-9])" if w[0].isalnum() else ""
    post = r"(?:s|es)?(?![a-z0-9])" if w[-1].isalnum() else ""
    return pre + re.escape(w) + post


THEME_RES = [(name, re.compile("|".join(_word_re(w) for w in words))) for name, words in THEMES]

BUY_CTA = ("shop", "buy", "order", "get offer", "add to cart", "purchase", "get yours")
LEARN_CTA = ("learn", "sign up", "subscribe", "watch", "book", "contact", "send message", "see menu", "apply")
PAGE_BUY = ("add to cart", "add to bag", "add to basket", "buy now", "buy it now", "checkout", "order now",
            "shop now", "add to wishlist")

PRICE_RE = re.compile(r"(?:₹|rs\.?|inr|\$|£|€|usd|eur|gbp)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
PRICE_SUFFIX_RE = re.compile(r"\b([0-9][0-9,]*)\s*/-")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AdsError(ValueError):
    pass


# ------------------------------------------------------------------ helpers

def money(cur, x):
    if x == int(x):
        return "%s%s" % (cur, "{:,.0f}".format(x))
    return "%s%s" % (cur, "{:,.2f}".format(x))


def cell(text):
    return " ".join(str(text).split()).replace("|", "/")


def norm_text(text):
    s = text.lower().replace("’", "'")
    s = re.sub(r"[-_/]", " ", s)
    return " ".join(s.split())


def _stem(tok):
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok


def tokens(text):
    """Main nouns of a line, roughly: lower-case words, stop words and bare numbers dropped."""
    out = []
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if t in STOP or t.isdigit() or len(t) < 2:
            continue
        if t not in out:
            out.append(t)
    return out


def prices_in(text, currency):
    found = set()
    patterns = [PRICE_RE, PRICE_SUFFIX_RE]
    cur = (currency or "").strip()
    if cur and not re.match(r"^(?:₹|rs\.?|inr|\$|£|€|usd|eur|gbp)$", cur, re.I):
        patterns.append(re.compile(re.escape(cur) + r"\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I))
    for pat in patterns:
        for m in pat.finditer(text):
            try:
                found.add(round(float(m.group(1).replace(",", "")), 2))
            except ValueError:
                pass
    return found


def norm_url(url):
    s = (url or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("#")[0].split("?")[0]
    return s.rstrip("/")


class _Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.title, self.h1, self.text = [], [], []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "img", "meta", "link", "input", "hr"):
            return
        self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._stack:
            while self._stack:
                if self._stack.pop() == tag:
                    break

    def handle_data(self, data):
        if any(t in self.SKIP for t in self._stack):
            return
        if "title" in self._stack:
            self.title.append(data)
            return
        if "h1" in self._stack:
            self.h1.append(data)
        self.text.append(data)


def read_page(path):
    """Title, h1 and visible text of a saved HTML page, or None when it does not load."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    p = _Page()
    try:
        p.feed(raw)
        p.close()
    except Exception:  # a malformed page still gives what it could
        pass
    title = " ".join(" ".join(p.title).split())
    h1 = " ".join(" ".join(p.h1).split())
    text = " ".join(" ".join(p.text).split())
    if not (title or text):
        return None
    return {"title": title, "h1": h1, "text": text, "all": " ".join((title, h1, text))}


def themes_in(text):
    low = norm_text(text)
    return [name for name, pat in THEME_RES if pat.search(low)]


def cta_kind(cta):
    c = cta.lower()
    if any(w in c for w in BUY_CTA):
        return "buy"
    if any(w in c for w in LEARN_CTA):
        return "learn"
    return "unknown"


def offer_on_page(offer, page_text):
    o, page = norm_text(offer), norm_text(page_text)
    if not o:
        return True
    variants = {o, o.replace("delivery", "shipping"), o.replace("shipping", "delivery"),
                o.replace("cash on delivery", "cod"), o.replace("cod", "cash on delivery")}
    if any(v in page for v in variants):
        return True
    pct = re.search(r"(\d+)\s*%", o)
    if pct and re.search(r"\b%s\s*%%" % pct.group(1), page):
        return True
    return False


# ------------------------------------------------------------------ analysis

def _need(obj, key, label):
    if key not in obj or obj[key] is None or (isinstance(obj[key], str) and not obj[key].strip()):
        raise AdsError("%s: '%s' is missing" % (label, key))
    return obj[key]


def analyse(spec, base_dir):
    if not isinstance(spec, dict):
        raise AdsError("the input must be a JSON object")
    for key in ("business", "currency", "brand", "captured"):
        _need(spec, key, "ads.json")
    cur = spec["currency"]
    if not isinstance(cur, str):
        raise AdsError("'currency' must be the prefix money is printed with, such as \"Rs \"")
    captured = str(spec["captured"]).strip()
    if not DATE_RE.match(captured):
        raise AdsError("'captured' must be the date the library was read, as YYYY-MM-DD")
    try:
        datetime.date(*map(int, captured.split("-")))
    except ValueError:
        raise AdsError("'captured' %r is not a real date" % captured)
    if "ads" not in spec or not isinstance(spec["ads"], list):
        raise AdsError("'ads' is missing (a list; an empty list means the library showed no active ads)")
    if "landing_pages" not in spec or not isinstance(spec["landing_pages"], list):
        raise AdsError("'landing_pages' is missing (a list of {url, file}; it may be empty)")

    pages = {}
    for i, lp in enumerate(spec["landing_pages"]):
        label = "landing page %d" % (i + 1)
        if not isinstance(lp, dict):
            raise AdsError("%s is not an object" % label)
        url, rel = _need(lp, "url", label), _need(lp, "file", label)
        price = num.parse_number(lp["price"], field="%s.price" % label) if lp.get("price") is not None else None
        path = os.path.join(base_dir, rel)
        pages[norm_url(url)] = {"url": url, "file": rel, "path": path, "price": price, "page": read_page(path)}

    brand_ads, comp_ads = [], []
    for i, ad in enumerate(spec["ads"]):
        label = "ad %d" % (i + 1)
        if not isinstance(ad, dict):
            raise AdsError("%s is not an object" % label)
        for key in ("advertiser", "library_url", "started", "format", "headline", "copy", "cta"):
            _need(ad, key, label)
        if not isinstance(ad.get("is_brand"), bool):
            raise AdsError("%s: 'is_brand' must be true or false" % label)
        if ad["format"] not in FORMATS:
            raise AdsError("%s: format must be one of %s" % (label, ", ".join(FORMATS)))
        entry = dict(ad)
        entry["n"] = i + 1
        entry["themes"] = themes_in(" ".join((ad["headline"], ad["copy"], ad.get("offer") or "")))
        if ad.get("price") is not None:
            entry["ad_price"] = num.parse_number(ad["price"], field="%s.price" % label)
        else:
            found = sorted(prices_in(ad["headline"] + " " + ad["copy"], cur))
            entry["ad_price"] = found[0] if found else None
        (brand_ads if ad["is_brand"] else comp_ads).append(entry)

    findings = []
    matched = []
    for ad in brand_ads:
        lp = pages.get(norm_url(ad.get("landing_url") or "")) if ad.get("landing_url") else None
        ad["landing"] = lp
        page = lp["page"] if lp else None
        if page is None:
            ad["match_pct"] = None
            findings.append({
                "id": "ads-no-landing-%d" % ad["n"], "severity": "low",
                "title": "No landing page captured for the ad \"%s\"" % ad["headline"][:60],
                "description": "Ad %d links to %s but no saved copy of that page was found, so its match was not checked."
                               % (ad["n"], ad.get("landing_url") or "nowhere"),
                "fix": "Save the landing page's HTML and list it under landing_pages, then record again."})
            continue
        toks = tokens(ad["headline"])
        page_stems = set(_stem(t) for t in re.findall(r"[a-z0-9]+", page["all"].lower()))
        found = [t for t in toks if _stem(t) in page_stems]
        ad["tokens"], ad["found"] = toks, found
        ad["match_pct"] = round(100.0 * len(found) / len(toks), 1) if toks else 100.0
        matched.append(ad["match_pct"])
        page_prices = prices_in(page["all"], cur)
        if lp["price"] is not None:
            page_prices.add(round(lp["price"], 2))
        ad["page_prices"] = sorted(page_prices)
        ad["price_ok"] = None if ad["ad_price"] is None else (round(ad["ad_price"], 2) in page_prices)
        ad["offer_ok"] = None if not ad.get("offer") else offer_on_page(ad["offer"], page["all"])
        kind = cta_kind(ad["cta"])
        has_buy = any(w in page["all"].lower() for w in PAGE_BUY)
        ad["cta_ok"] = None if kind == "unknown" else (has_buy if kind == "buy" else True)
        if ad["offer_ok"] is False:
            findings.append({
                "id": "ads-offer-missing-%d" % ad["n"], "severity": "high",
                "title": "The ad promises an offer the landing page does not show",
                "description": "Ad %d (\"%s\") offers \"%s\", and the page it links to (%s) never mentions it."
                               % (ad["n"], ad["headline"], ad["offer"], lp["url"]),
                "fix": "Show the offer on the landing page, or stop running the ad until the page honours it."})
        if ad["match_pct"] < MATCH_FLOOR:
            findings.append({
                "id": "ads-mismatch-%d" % ad["n"], "severity": "medium",
                "title": "Ad and landing page talk about different things",
                "description": "Only %.0f%% of the headline's main words (%s) appear on %s."
                               % (ad["match_pct"], ", ".join(toks) or "none", lp["url"]),
                "fix": "Send the ad to the page that sells what the headline names, or rewrite the headline."})
        if ad["price_ok"] is False:
            findings.append({
                "id": "ads-price-differs-%d" % ad["n"], "severity": "medium",
                "title": "Price in the ad differs from the page",
                "description": "Ad %d says %s; the page shows %s." % (
                    ad["n"], money(cur, ad["ad_price"]),
                    ", ".join(money(cur, p) for p in ad["page_prices"]) or "no price at all"),
                "fix": "Make the ad and the page show the same price."})

    gaps = []
    for name, _ in THEMES:
        c = sum(1 for a in comp_ads if name in a["themes"])
        b = sum(1 for a in brand_ads if name in a["themes"])
        if c >= 2 and b == 0:
            gaps.append({"theme": name, "competitor_ads": c})
    gaps.sort(key=lambda g: -g["competitor_ads"])  # stable: ties keep the dictionary order
    for g in gaps[:MAX_GAP_FINDINGS]:
        findings.append({
            "id": "ads-gap-%s" % ledger.slug(g["theme"]), "severity": "medium",
            "title": "Competitors lean on %s; your ads never mention it" % g["theme"],
            "description": "%d competitor ads talk about %s; none of the brand's %d ads do."
                           % (g["competitor_ads"], g["theme"], len(brand_ads)),
            "fix": "Test an ad that answers the %s question, if it is true for your product." % g["theme"]})

    if not brand_ads:
        findings.append({
            "id": "ads-none-found", "severity": "low",
            "title": "No active ads found in the public library on %s" % captured,
            "description": "The public Meta Ad Library showed no active ads for %s on %s." % (spec["brand"], captured),
            "fix": "If you are paying for ads, check the page name searched and the country; if not, this is expected."})

    avg = round(sum(matched) / len(matched), 1) if matched else None
    return {"business": spec["business"], "brand": spec["brand"], "currency": cur, "captured": captured,
            "brand_ads": brand_ads, "competitor_ads": comp_ads, "gaps": gaps, "match_pct": avg,
            "findings": findings, "pages": pages}


# ------------------------------------------------------------------ render

def _yn(v):
    return "-" if v is None else ("yes" if v else "NO")


def render(r):
    cur = r["currency"]
    L = ["# Ad teardown", "",
         "%s. Ads read from the public Meta Ad Library on %s: %d from %s, %d from competitors."
         % (r["business"], r["captured"], len(r["brand_ads"]), r["brand"], len(r["competitor_ads"])), ""]
    if not r["brand_ads"] and not r["competitor_ads"]:
        L += ["No active ads were found in the public library on %s for %s or the competitors searched."
              % (r["captured"], r["brand"]), ""]
    elif not r["brand_ads"]:
        L += ["No active ads from %s were found in the public library on %s." % (r["brand"], r["captured"]), ""]

    if r["brand_ads"]:
        L += ["## Your ads and the pages they send people to", "",
              "| # | headline | landing page | headline words on the page | price matches | offer shown | button fits |",
              "| ---: | --- | --- | ---: | --- | --- | --- |"]
        for a in r["brand_ads"]:
            lp = a["landing"]
            if a["match_pct"] is None:
                L.append("| %d | %s | not captured | - | - | - | - |" % (a["n"], cell(a["headline"])))
                continue
            L.append("| %d | %s | %s | %.0f%% (%d of %d) | %s | %s | %s |" % (
                a["n"], cell(a["headline"]), cell(lp["url"]), a["match_pct"], len(a["found"]), len(a["tokens"]),
                _yn(a["price_ok"]), _yn(a["offer_ok"]), _yn(a["cta_ok"])))
        L.append("")
        if r["match_pct"] is not None:
            L += ["Average match: %.0f%% of the headline's main words appear on the landing page (the bar is %.0f%%)."
                  % (r["match_pct"], MATCH_BUDGET), ""]

    if r["competitor_ads"]:
        L += ["## Competitor ads", "", "| advertiser | format | running since | headline | themes |",
              "| --- | --- | --- | --- | --- |"]
        for a in r["competitor_ads"]:
            L.append("| %s | %s | %s | %s | %s |" % (cell(a["advertiser"]), a["format"], cell(a["started"]),
                                                    cell(a["headline"]), ", ".join(a["themes"]) or "-"))
        L.append("")

    L += ["## Creative gaps", ""]
    if r["gaps"]:
        L += ["Themes at least two competitor ads use and none of your ads mention:", "",
              "| theme | competitor ads using it |", "| --- | ---: |"]
        for g in r["gaps"]:
            L.append("| %s | %d |" % (g["theme"], g["competitor_ads"]))
    elif r["competitor_ads"]:
        L.append("No theme is used by two or more competitor ads and missing from yours.")
    else:
        L.append("No competitor ads were read, so no gaps can be listed.")
    L.append("")

    L += ["## Findings", ""]
    if r["findings"]:
        for f in r["findings"]:
            L.append("- %s: %s. %s" % (f["severity"], f["title"], f["description"]))
    else:
        L.append("None.")
    L += ["", "_%s from ads copied word for word from the public Meta Ad Library and the landing pages saved on "
              "the same day. The library shows what is running, not what it costs or how it performs. Prices "
              "are in %s._" % (MARKER, cur.strip() or cur)]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ commands

def _load(path):
    if not os.path.isfile(path):
        raise AdsError("input file not found: %s" % path)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AdsError("%s is not valid JSON: %s" % (path, e))


def _base(path):
    return os.path.dirname(os.path.abspath(path))


def cmd_template(a):
    out = a.out
    target = os.path.join(out, "ads.json")
    ledger.guard_out(target, a.dir, a.force)
    os.makedirs(os.path.join(out, "landing"), exist_ok=True)
    shutil.copyfile(EXAMPLE, target)
    for name in sorted(os.listdir(EXAMPLE_PAGES)):
        shutil.copyfile(os.path.join(EXAMPLE_PAGES, name), os.path.join(out, "landing", name))
    print("%s wrote an example ads.json and its landing pages to %s. Replace every ad with what the library shows."
          % (ledger.OK, out))
    return 0


def cmd_check(a):
    print(render(analyse(_load(a.file), _base(a.file))))
    print("%s nothing was recorded. Use 'record' to write the section and the ledger." % ledger.OK)
    return 0


def _remove_stale(d):
    folder = os.path.dirname(ledger.finding_path(d, SKILL, "x"))
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if name.startswith("ads-") and name.endswith(".json"):
            os.remove(os.path.join(folder, name))


def cmd_record(a):
    spec = _load(a.file)
    res = analyse(spec, _base(a.file))
    d, root = a.dir, ledger.root(a.dir)

    section = os.path.join(root, SECTION)
    ledger.guard_out(section, d, a.force, ours=MARKER)
    os.makedirs(os.path.dirname(section), exist_ok=True)
    with open(section, "w", encoding="utf-8") as fh:
        fh.write(render(res))
    ev = ledger.evidence_dir(d, SKILL)
    with open(os.path.join(ev, "ads.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    landing_dir = os.path.join(ev, "landing")
    if os.path.isdir(landing_dir):
        shutil.rmtree(landing_dir)
    copied = []
    for i, lp in enumerate(sorted(res["pages"].values(), key=lambda p: p["url"])):
        if lp["page"] is not None:
            os.makedirs(landing_dir, exist_ok=True)
            name = "%02d-%s" % (i + 1, os.path.basename(lp["file"]))
            shutil.copyfile(lp["path"], os.path.join(landing_dir, name))
            copied.append("evidence/%s/landing/%s" % (SKILL, name))
    rel_section = SECTION.replace(os.sep, "/")
    evidence = ["evidence/%s/ads.json" % SKILL, rel_section]

    ledger.init_coverage(d, SKILL, COVERAGE, keep_existing=False)
    nb, nc = len(res["brand_ads"]), len(res["competitor_ads"])
    ledger.mark_coverage(d, SKILL, "brand-ads-read", "covered", evidence=evidence,
                         note="%d active brand ad(s) on %s" % (nb, res["captured"]))
    if nc:
        ledger.mark_coverage(d, SKILL, "competitor-ads-read", "covered", evidence=evidence,
                             note="%d competitor ad(s)" % nc)
    else:
        ledger.mark_coverage(d, SKILL, "competitor-ads-read", "not_run", note="No competitor ads were recorded.")
    checked = [x for x in res["brand_ads"] if x["match_pct"] is not None]
    if not nb:
        ledger.mark_coverage(d, SKILL, "landing-match-checked", "not_applicable",
                             note="The brand has no active ads to check.")
    elif checked:
        ledger.mark_coverage(d, SKILL, "landing-match-checked", "covered", evidence=evidence + copied,
                             note="%d of %d brand ads had a saved landing page" % (len(checked), nb))
    else:
        ledger.mark_coverage(d, SKILL, "landing-match-checked", "not_run",
                             note="No landing page was saved for any brand ad.")
    if nc:
        ledger.mark_coverage(d, SKILL, "gaps-listed", "covered", evidence=evidence)
    else:
        ledger.mark_coverage(d, SKILL, "gaps-listed", "not_run", note="Gaps need competitor ads to compare with.")

    metrics = []
    if res["match_pct"] is not None:
        metrics.append({"id": "match_pct", "value": res["match_pct"], "unit": "percent", "budget": MATCH_BUDGET,
                        "op": ">=", "evidence": [rel_section]})
    ledger.write_scores(d, SKILL, metrics)

    _remove_stale(d)
    for f in res["findings"]:
        finding = ledger.new_finding(SKILL, f["title"], f["severity"], id=f["id"], category="ads",
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

    t = sub.add_parser("template", help="write an example ads.json and landing pages into a folder")
    t.add_argument("--out", required=True, help="the folder to write the example into")
    common(t)
    t.set_defaults(func=cmd_template)
    for name, func, text in (("check", cmd_check, "print the teardown and record nothing"),
                             ("record", cmd_record, "write the section file and the ledger")):
        p = sub.add_parser(name, help=text)
        p.add_argument("file", help="the ads JSON")
        common(p)
        p.set_defaults(func=func)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: template, check or record")
    try:
        return a.func(a)
    except ValueError as e:  # AdsError, num.MissingInput / num.BadNumber, ledger.LedgerError
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
