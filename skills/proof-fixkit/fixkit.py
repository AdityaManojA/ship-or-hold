#!/usr/bin/env python3
"""Write the 14-Day Fix Kit: paste-ready files and a day-by-day plan. Standard library only, no network.

Reads one fixkit.json and the verified findings in the ledger. Writes exact page
titles and meta descriptions within length limits, replacement colours for every
failing contrast pair (the nearest colour of the same hue that passes), WhatsApp
review-request and size-help scripts, a centimetre size chart to fill in, a draft
size exchange policy for a lawyer or CA to check, and the 14-Day Fix Kit itself:
every verified finding and kit item ordered by severity and effort, at most three a
day. The start date comes from the input; the tool never reads the clock.

    python3 fixkit.py template --out fixkit.json
    python3 fixkit.py check fixkit.json      # print the plan, write nothing
    python3 fixkit.py record fixkit.json     # write proof/fix-kit/ and the ledger
"""

import argparse
import csv
import datetime
import importlib.util
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-fixkit"
SECTION = os.path.join("business", "fix-kit.md")
KIT_DIR = "fix-kit"
MARKER = "Written by proof-fixkit"
EXAMPLE = os.path.join(HERE, "fixkit.example.json")
KINDS = ("home", "collection", "product", "policy")
STAGES = ("solo", "team", "company")
EFFORTS = ("S", "M", "L")
SEVERITY_ORDER = ("critical", "high", "medium", "low")
BUCKETS = {"do_today": "Do today", "freelancer": "Hire a freelancer", "professional": "Ask a CA or lawyer"}
SEATS = ("investor", "growth", "builder", "operator")
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 120, 155
DAYS, PER_DAY = 14, 3

COVERAGE = [
    ("titles-written", "An exact title and meta description for every audited page"),
    ("colours-fixed", "A passing replacement for every failing colour pair"),
    ("whatsapp-scripts", "WhatsApp review requests (day 3 and day 10) and a size-help reply"),
    ("size-chart", "A centimetre size chart to fill in for each product type"),
    ("exchange-policy", "A draft size exchange policy for a lawyer or CA to check"),
    ("schedule", "Every verified finding and kit item placed on a day from 1 to 14"),
]

# The skill a finding came from decides who owns it (team and company) and, with the
# words in its title, which bucket it falls in (solo).
SEAT_OF = {
    "investor": ("proof-finance", "proof-pricing", "proof-business-plan", "proof-llm-cost"),
    "growth": ("proof-seo", "proof-geo", "proof-ads", "proof-market", "proof-buyers", "proof-offer",
               "proof-go-to-market", "proof-voice", "proof-slop", "proof-email", "proof-i18n"),
    "operator": ("proof-ops", "proof-privacy", "proof-licenses", "proof-release", "proof-store-ready",
                 "proof-audit-trail"),
}
PROFESSIONAL_SKILLS = ("proof-privacy", "proof-licenses")
PROFESSIONAL_WORDS = ("policy", "legal", "lawyer", "tax", "gst", "terms", "licence", "license", "trademark",
                      "consent", "gdpr", "invoice", "contract", "compliance", "refund")
FREELANCER_SKILLS = ("proof-a11y", "proof-web-perf", "proof-sec-web", "proof-sec-deps", "proof-sec-secrets",
                     "proof-sec-diff", "proof-security", "proof-auth", "proof-api", "proof-backend",
                     "proof-database", "proof-infra", "proof-pwa", "proof-visual", "proof-design-match",
                     "proof-mobile", "proof-mobile-a11y", "proof-mobile-audit", "proof-mobile-e2e",
                     "proof-react-native", "proof-resilience", "proof-web-e2e", "proof-compat",
                     "proof-load", "proof-ai", "proof-rag", "proof-restraint")
EFFORT_L_SKILLS = ("proof-sec-web", "proof-security", "proof-auth", "proof-backend", "proof-database",
                   "proof-infra", "proof-resilience", "proof-load", "proof-web-perf")

KIT_FILES = {
    "titles": "fix-kit/titles.md", "colours": "fix-kit/colours.md", "whatsapp": "fix-kit/whatsapp.md",
    "size": "fix-kit/size-chart.md", "exchange": "fix-kit/exchange-policy.md",
}
HOW_TO_MEASURE = {
    "bust": "Around the fullest part of the chest, tape level under the arms.",
    "chest": "Around the fullest part of the chest, tape level under the arms.",
    "waist": "Around the natural waist, the narrowest part above the navel.",
    "hip": "Around the fullest part of the hips, feet together.",
    "length": "From the highest point of the shoulder, next to the collar, straight down to the hem.",
    "shoulder": "Across the back from one shoulder point to the other.",
    "sleeve": "From the shoulder point down to the end of the sleeve.",
    "inseam": "From the crotch seam down the inside of the leg to the hem.",
    "armhole": "Around the armhole seam, laid flat and doubled.",
}


class KitError(ValueError):
    pass


# ------------------------------------------------------------------ contrast maths (proof-a11y)

def _load_contrast():
    path = os.path.join(HERE, "..", "proof-a11y", "contrast.py")
    spec = importlib.util.spec_from_file_location("proof_fixkit_contrast", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


contrast = _load_contrast()


def rgb_to_hsl(rgb):
    r, g, b = (c / 255.0 for c in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    light = (hi + lo) / 2.0
    if hi == lo:
        return 0.0, 0.0, light
    d = hi - lo
    sat = d / (2.0 - hi - lo) if light > 0.5 else d / (hi + lo)
    if hi == r:
        hue = ((g - b) / d) % 6.0
    elif hi == g:
        hue = (b - r) / d + 2.0
    else:
        hue = (r - g) / d + 4.0
    return hue / 6.0, sat, light


def hsl_to_rgb(h, s, l):
    def channel(p, q, t):
        t %= 1.0
        if t < 1 / 6.0:
            return p + (q - p) * 6 * t
        if t < 0.5:
            return q
        if t < 2 / 3.0:
            return p + (q - p) * (2 / 3.0 - t) * 6
        return p
    if s == 0:
        v = int(round(l * 255))
        return v, v, v
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    return tuple(int(round(channel(p, q, h + off) * 255)) for off in (1 / 3.0, 0.0, -1 / 3.0))


def nearest_passing(fg, bg, target):
    """The colour closest to fg in lightness, same hue and saturation, that reaches target on bg.

    Steps lightness by 0.001 in both directions and keeps the first step that passes
    after rounding to a hex colour; the smaller step wins, darker on a tie.
    """
    h, s, l = rgb_to_hsl(fg)
    best = None
    for direction in (-1, 1):
        for k in range(1, 1001):
            nl = l + direction * k / 1000.0
            if nl < 0 or nl > 1:
                break
            rgb = hsl_to_rgb(h, s, nl)
            if contrast.contrast_ratio(rgb, bg) >= target:
                if best is None or k < best[0]:
                    best = (k, rgb)
                break
    if best is None:  # cannot happen for 4.5 or 3.0, black or white always reaches it
        raise KitError("no colour of this hue reaches %.1f:1 on %s" % (target, contrast.hex_of(bg)))
    return best[1], best[0] / 1000.0


# ------------------------------------------------------------------ helpers

def money(cur, x):
    if x == int(x):
        return "%s%s" % (cur, "{:,.0f}".format(x))
    return "%s%s" % (cur, "{:,.2f}".format(x))


def cell(text):
    return " ".join(str(text).split()).replace("|", "/")


def _need(obj, key, label):
    if not isinstance(obj, dict) or key not in obj or obj[key] is None or \
            (isinstance(obj[key], str) and not obj[key].strip()):
        raise KitError("%s: '%s' is missing" % (label, key))
    return obj[key]


def _date(text):
    if not isinstance(text, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", text.strip()):
        raise KitError("'start' must be the first day of the plan as YYYY-MM-DD (the tool never reads the clock)")
    try:
        return datetime.date(*map(int, text.strip().split("-")))
    except ValueError:
        raise KitError("'start' %r is not a real date" % text)


def _words_fit(text, limit):
    """Cut text back to whole words so it is at most limit characters. Never cuts mid-word."""
    if len(text) <= limit:
        return text
    out = ""
    for w in text.split():
        nxt = (out + " " + w) if out else w
        if len(nxt) > limit:
            break
        out = nxt
    return out.rstrip(" ,;:-|")


def _cap(s):
    return s[:1].upper() + s[1:] if s else s


# ------------------------------------------------------------------ titles

def make_title(page, brand, cur):
    name = page["name"].strip()
    cat = (page.get("category") or "").strip()
    kind = page["kind"]
    feats = [f.strip() for f in page.get("features") or [] if f and f.strip()]
    if kind == "home":
        core = [cat, feats[0] if feats else ""]
        variants = ["%s | %s" % (brand, " and ".join(x for x in core if x)) if any(core) else "",
                    "%s | %s" % (brand, cat) if cat else "",
                    "%s | %s" % (name, brand) if name.lower() != brand.lower() else "",
                    "%s | Shop %s online" % (brand, cat) if cat else "",
                    "%s | Official online store" % brand]
    elif kind == "policy":
        variants = ["%s | %s" % (name, brand), "%s | %s online store" % (name, brand),
                    "%s for orders | %s" % (name, brand)]
    elif kind == "collection":
        variants = ["%s - %s | %s" % (name, cat, brand) if cat and cat.lower() not in name.lower() else "",
                    "%s | %s" % (name, brand), "Shop %s | %s" % (name, brand),
                    "Shop %s online | %s" % (name, brand)]
    else:
        price = page.get("price")
        p = (" at %s" % money(cur, price)) if price is not None else ""
        variants = ["%s - %s | %s" % (name, cat, brand) if cat and cat.lower() not in name.lower() else "",
                    "%s | %s" % (name, brand),
                    "%s%s | %s" % (name, p, brand) if p else "",
                    "Shop %s online | %s" % (name, brand),
                    "Buy %s online | %s" % (name, brand)]
    for v in variants:
        v = " ".join(v.split())
        if v and TITLE_MIN <= len(v) <= TITLE_MAX:
            return v
    # Too long: trim the page name to whole words and keep the brand.
    tail = " | %s" % brand
    room = TITLE_MAX - len(tail)
    if room >= 10:
        t = _words_fit(name, room) + tail
        if len(t) >= TITLE_MIN:
            return t
    t = _words_fit(" ".join(v for v in variants if v)[:400], TITLE_MAX)
    if TITLE_MIN <= len(t) <= TITLE_MAX:
        return t
    raise KitError("page %s: cannot write a title of %d to %d characters from its name, category and brand; "
                   "add a category or features" % (page["url"], TITLE_MIN, TITLE_MAX))


def make_description(page, brand, cur):
    name = page["name"].strip()
    cat = (page.get("category") or "").strip()
    kind = page["kind"]
    feats = [f.strip().rstrip(".") for f in page.get("features") or [] if f and f.strip()]
    price = page.get("price")
    if kind == "product":
        first = name
        if cat and cat.lower() not in name.lower():
            first += " (%s)" % cat
        first += " from %s" % brand
        if price is not None:
            first += " at %s" % money(cur, price)
        first += "."
        fillers = ["Order online from %s." % brand, "See every size and detail on the page.",
                   "Browse the full range at %s." % brand]
    elif kind == "collection":
        first = "Shop %s%s from %s." % (name, (" in %s" % cat.lower()) if cat and cat.lower() not in name.lower()
                                         else "", brand)
        fillers = ["Browse the full range at %s." % brand, "Order online from %s." % brand,
                   "New pieces are added to this page as they arrive."]
    elif kind == "policy":
        first = "The %s of %s, in plain words." % (name if name.isupper() else name.lower(), brand)
        fillers = ["Read it before you order.", "Questions about it? Contact %s." % brand,
                   "It applies to orders from the %s online store." % brand]
    else:
        first = "%s%s." % (brand, (": %s" % cat.lower()) if cat else "")
        fillers = ["Order online from %s." % brand, "Browse the full range at %s." % brand,
                   "See what is new at %s." % brand]
    out = _words_fit(first, DESC_MAX - 1)
    if not out.endswith("."):
        out = out.rstrip(",;:") + "."
    if feats:
        chosen = []
        for f in feats:
            trial = chosen + [f]
            sentence = _feature_sentence(trial)
            if len(out) + 1 + len(sentence) <= DESC_MAX:
                chosen = trial
        if chosen:
            out += " " + _feature_sentence(chosen)
    for f in fillers:
        if len(out) >= DESC_MIN:
            break
        if len(out) + 1 + len(f) <= DESC_MAX:
            out += " " + f
    if not (DESC_MIN <= len(out) <= DESC_MAX):
        raise KitError("page %s: cannot write a meta description of %d to %d characters; add features "
                       "(short phrases about the product) so there is enough true to say"
                       % (page["url"], DESC_MIN, DESC_MAX))
    return out


def _feature_sentence(feats):
    items = [feats[0][:1].upper() + feats[0][1:]] + [f[:1].lower() + f[1:] if not f[:2].isupper() else f
                                                      for f in feats[1:]]
    if len(items) == 1:
        return items[0] + "."
    return ", ".join(items[:-1]) + " and " + items[-1] + "."


# ------------------------------------------------------------------ colours

def pairs_from_ledger(d):
    """Contrast pairs proof-a11y recorded: colours from its coverage, rejected findings left out."""
    cov = ledger.load_coverage(d, "proof-a11y")
    rejected = set(f["id"] for f in ledger.load_findings(d, "proof-a11y") if f["status"] == "false_positive")
    pairs = []
    if cov:
        for c in cov["checks"]:
            if not c["id"].startswith("contrast-"):
                continue
            m = re.match(r"^Contrast of (.*) \((#[0-9a-fA-F]{3,6}) on (#[0-9a-fA-F]{3,6})\)$", c["title"])
            if not m or c["id"] in rejected:
                continue
            size = "large" if re.search(r"\blarge text\b", c.get("note", "")) else "normal"
            pairs.append({"id": c["id"][len("contrast-"):], "where": m.group(1), "fg": m.group(2),
                          "bg": m.group(3), "size": size, "bold": False})
    if not pairs:
        for f in ledger.load_findings(d, "proof-a11y"):
            if f["status"] != "verified" or not f["id"].startswith("contrast-"):
                continue
            m = re.search(r'--fg "([^"]+)" --bg "([^"]+)" --size "([^"]+)"', f.get("repro", ""))
            if m:
                pairs.append({"id": f["id"][len("contrast-"):], "where": f.get("location", f["title"]),
                              "fg": m.group(1), "bg": m.group(2), "size": m.group(3),
                              "bold": "--bold" in f.get("repro", "")})
    if not pairs:
        raise KitError("colours_from is \"proof\" but the ledger holds no contrast results; run the "
                       "accessibility check with --record first, or list the colours in fixkit.json")
    return pairs


GREY_SATURATION = 0.15


def fix_colours(pairs):
    out = []
    for i, p in enumerate(pairs):
        label = "colour %d" % (i + 1)
        for key in ("id", "fg", "bg", "where", "size"):
            _need(p, key, label)
        bold = p.get("bold", False)
        try:
            fg, bg = contrast.parse_color(p["fg"]), contrast.parse_color(p["bg"])
            large = contrast.is_large(p["size"], bold)
        except (contrast.ColorError, contrast.SizeError) as e:
            raise KitError("%s: %s" % (label, e))
        target = contrast.AA["large" if large else "normal"]
        ratio = contrast.contrast_ratio(fg, bg)
        entry = {"id": str(p["id"]), "where": str(p["where"]), "fg": contrast.hex_of(fg), "bg": contrast.hex_of(bg),
                 "text": "large" if large else "normal", "target": target, "ratio": ratio, "passes": ratio >= target}
        if not entry["passes"]:
            # Neutral text (white, black, grey) on a coloured background: the brand colour is what
            # moves, because a button's white label turning black is not a fix anyone would ship.
            if rgb_to_hsl(fg)[1] < GREY_SATURATION and rgb_to_hsl(bg)[1] >= GREY_SATURATION:
                new, step = nearest_passing(bg, fg, target)
                entry.update({"change": "background", "new": contrast.hex_of(new),
                              "new_ratio": contrast.contrast_ratio(fg, new), "lightness_change": step})
            else:
                new, step = nearest_passing(fg, bg, target)
                entry.update({"change": "text", "new": contrast.hex_of(new),
                              "new_ratio": contrast.contrast_ratio(new, bg), "lightness_change": step})
        out.append(entry)
    return out


# ------------------------------------------------------------------ the plan

def _bucket(skill, title, kit):
    low = title.lower()
    if kit == "exchange" or skill in PROFESSIONAL_SKILLS or any(re.search(r"\b%s" % w, low)
                                                               for w in PROFESSIONAL_WORDS):
        return "professional"
    if kit in ("titles", "whatsapp", "size"):
        return "do_today"
    if kit == "colours" or skill in FREELANCER_SKILLS:
        return "freelancer"
    return "do_today"


def _seat(skill, kit):
    if kit in ("titles", "whatsapp"):
        return "growth"
    if kit == "colours":
        return "builder"
    if kit in ("size", "exchange"):
        return "operator"
    for seat, skills in SEAT_OF.items():
        if skill in skills:
            return seat
    return "builder" if skill.startswith(("proof-sec", "proof-mobile", "proof-web")) or \
        skill in FREELANCER_SKILLS else "operator"


def _kit_for(f):
    low = (f["title"] + " " + f.get("category", "")).lower()
    if f["skill"] == "proof-a11y" and f.get("category") == "contrast":
        return "colours"
    if re.search(r"\btitle|meta description|\bdescription\b", low) and f["skill"] in ("proof-seo", "proof-geo"):
        return "titles"
    if re.search(r"\bsize (chart|guide)|\bsizing\b", low):
        return "size"
    if re.search(r"\bexchange|\breturns? policy|\brefund", low):
        return "exchange"
    if re.search(r"\breview", low):
        return "whatsapp"
    return None


def _effort(f, kit):
    if kit in ("titles", "whatsapp", "colours"):
        return "S"
    if kit in ("size", "exchange"):
        return "M"
    return "L" if f["skill"] in EFFORT_L_SKILLS else "M"


PAGE_NAMES = {"index": "home", "home": "home"}
# Finding titles that read as a diagnosis, turned into the job to do. First match wins.
PLAIN_RULES = [
    (r"^(\d+) of (\d+) pass because of (\w+)$", r'Answer the "\3" objection that \1 of \2 simulated buyers raised'),
    (r"^(.+?) is a single point of failure$", r"Line up a backup for \1"),
    # The brand check and operations both ask for the trade mark search: one job, so one wording.
    (r"^Nobody knows where 'Trade ?mark\b.*' stands$", r"Search the trade mark register for the brand name"),
    (r"^Nobody knows where '(.+)' stands$", r"Find out where this stands: \1"),
    (r"^One person owns every routine$", r"Write down each routine so someone else can run it for a week"),
    (r"^Weak citability signal: (.+)$", r"Add short, quotable facts to the main pages"),
    (r"^The offer promises something that is not built: (.+)$", r"Build it: \1"),
    (r"^The offer promises something that is only partly built: (.+)$", r"Finish it: \1"),
    (r"^Low contrast \(([\d.]+):1\) at (.+)$", r"Change the colour of the \2 (now \1:1) to the one in the kit"),
    (r"^The trademark check on the name is still to do$", r"Search the trade mark register for the brand name"),
    (r"^meta description missing on the (.+) page$", r"Paste the meta description from the kit on the \1 page"),
    (r"^meta description is (\d+) chars.* on the (.+) page$", r"Paste the shorter meta description from the kit on the \2 page"),
    (r"^no h1 on the (.+) page$", r"Make the page's name its main heading (h1) on the \1 page"),
    (r"^\d+ h1 elements.* on the (.+) page$", r"Keep one main heading (h1) on the \1 page"),
    (r"^title is \d+ chars.* on the (.+) page$", r"Paste the new title from the kit on the \1 page"),
    (r"^The price sits outside what buyers called acceptable$", r"Test a lower price or a two-set bundle"),
    (r"^Trackers are present but no consent banner was found$",
     r"Add a cookie consent banner and hold the trackers until people agree"),
    (r"^\d+ tracker\(s\) load before consent: (.+)$",
     r"Add a cookie consent banner and hold the trackers until people agree"),
    (r"^Personal data in the URL at (.+): parameter '(.+)'$",
     r"Find out what the '\2' value in the \1 address holds, and keep personal data out of web addresses"),
    (r"^Ad and landing page talk about different things$",
     r"Send each ad to a page that shows what its headline promises"),
    (r"^Competitors lean on (.+); your ads never mention it$", r"Test an ad that mentions \1"),
    (r"^The loudest complaints are ones this product does not fix$",
     r"Answer the loudest complaint in the market on the product page"),
]
# Not jobs for the owner: the blocker itself is already a task, and a page the audit did not save is
# a gap in the audit, listed in the appendix.
SKIP_RULES = [r"^Promotion is planned while a blocker is open", r"^No landing page captured for the ad"]


def _page_name(m):
    stem = m.group(1)
    if stem in PAGE_NAMES:
        return "on the %s page" % PAGE_NAMES[stem]
    # Shopify-style captures are named <kind>-<handle>: "collection-pj-set" is the pj set collection page.
    kind, _, handle = stem.partition("-")
    if kind in ("collection", "product", "page", "blog") and handle:
        return "on the %s %s page" % (handle.replace("-", " "), kind)
    return "on the %s page" % stem.replace("-", " ")


def _plain(title, ids):
    """A finding title as a plain job, pages named in words, no ledger code or file name left in it."""
    t = " ".join(title.split()).rstrip(".")
    for fid in ids:
        if "-" in fid and fid in t:
            t = t.replace(fid, fid.replace("-", " "))
    t = re.sub(r"\bon ([\w-]+)\.html?\b", _page_name, t)
    for pattern, job in PLAIN_RULES:
        if re.match(pattern, t, re.I):
            out = re.sub(pattern, job, t, flags=re.I)
            return re.sub(r"\b(for|of) The ", lambda m: m.group(1) + " the ", out)
    return "Fix: " + t


# The same job on several pages or things is one task that names them all: (pattern, plural).
# The pattern's first group is the job, the second the thing it is done to.
GROUP_RULES = [
    (r"^(Paste the meta description from the kit) on the (.+) page$", "{job} on the {items} pages"),
    (r"^(Paste the shorter meta description from the kit) on the (.+) page$", "{job} on the {items} pages"),
    (r"^(Paste the new title from the kit) on the (.+) page$", "{job} on the {items} pages"),
    (r"^(Make the page's name its main heading \(h1\)) on the (.+) page$", "{job} on the {items} pages"),
    (r"^(Keep one main heading \(h1\)) on the (.+) page$", "{job} on the {items} pages"),
    (r"^(Line up a backup) for (.+)$", "{job} for each of these: {items}"),
    (r"^(Test an ad that mentions) (.+)$", "Test ads that mention {items}"),
    (r"^(Change the colour) of the (.+?) \(now [\d.]+:1\) to the one in the kit$",
     "Change the colours of the {items} to the ones in the kit"),
]
# More things than this and the task gives the count and points at the kit, not a long list.
GROUP_LIST_MAX = 4
GROUP_MANY = {
    "Change the colour": "Change the colours of {n} pieces of text and buttons to the ones in the kit (each one is "
                         "listed there)",
}


def _and(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _group(tasks):
    """Merge tasks that are one job done to several things; the merged task keeps the worst severity."""
    out, groups = [], {}
    for t in tasks:
        for n, (pattern, _) in enumerate(GROUP_RULES):
            m = re.match(pattern, t["task"])
            if m:
                key = (n, m.group(1))
                if key in groups:
                    g = groups[key]
                    g["items"].append(m.group(2))
                    g["task"]["source"] += ", " + t["source"]
                    if SEVERITY_ORDER.index(t["severity"]) < SEVERITY_ORDER.index(g["task"]["severity"]):
                        g["task"]["severity"] = t["severity"]
                    if EFFORTS.index(t["effort"]) > EFFORTS.index(g["task"]["effort"]):
                        g["task"]["effort"] = t["effort"]
                else:
                    groups[key] = {"items": [m.group(2)], "task": t, "rule": n, "job": m.group(1)}
                    out.append(t)
                break
        else:
            out.append(t)
    for g in groups.values():
        n = len(g["items"])
        if n > GROUP_LIST_MAX and g["job"] in GROUP_MANY:
            g["task"]["task"] = GROUP_MANY[g["job"]].format(n=n)
        elif n > 1:
            g["task"]["task"] = GROUP_RULES[g["rule"]][1].format(job=g["job"], items=_and(g["items"]))
    return out


def build_tasks(findings, kit_items, extras):
    ids = [f["id"] for f in findings]
    tasks, helped, seen = [], set(), {}
    for f in findings:
        if any(re.match(p, f["title"]) for p in SKIP_RULES):
            continue
        kit = _kit_for(f)
        if kit:
            helped.add(kit)
        text = _plain(f["title"], ids)
        if text in seen:  # the same job found in several places is one task
            t = seen[text]
            t["places"] += 1
            t["source"] += ", %s/%s" % (f["skill"], f["id"])
            if SEVERITY_ORDER.index(f["severity"]) < SEVERITY_ORDER.index(t["severity"]):
                t["severity"] = f["severity"]
            continue
        t = {"task": text, "severity": f["severity"], "effort": _effort(f, kit),
             "kit": KIT_FILES.get(kit, ""), "bucket": _bucket(f["skill"], f["title"], kit),
             "seat": _seat(f["skill"], kit), "source": "%s/%s" % (f["skill"], f["id"]), "places": 1}
        seen[text] = t
        tasks.append(t)
    for t in tasks:
        if t.pop("places", 1) > 1:
            t["task"] += " (found in %d places)" % len(t["source"].split(", "))
    tasks = _group(tasks)
    for k in kit_items:
        if k["kit"] in helped:
            continue  # a finding already points at this file
        tasks.append({"task": k["task"], "severity": k["severity"], "effort": k["effort"],
                      "kit": KIT_FILES[k["kit"]], "bucket": _bucket("", k["task"], k["kit"]),
                      "seat": _seat("", k["kit"]), "source": None})
    for i, e in enumerate(extras):
        label = "extra task %d" % (i + 1)
        _need(e, "task", label)
        if e.get("effort") not in EFFORTS:
            raise KitError("%s: effort must be S, M or L" % label)
        sev = e.get("severity", "medium")
        if sev not in SEVERITY_ORDER:
            raise KitError("%s: severity must be one of %s" % (label, ", ".join(SEVERITY_ORDER)))
        bucket = e.get("bucket") or _bucket("", e["task"], None)
        if bucket not in BUCKETS:
            raise KitError("%s: bucket must be one of %s" % (label, ", ".join(BUCKETS)))
        seat = e.get("owner") or "operator"
        if seat not in SEATS:
            raise KitError("%s: owner must be one of %s" % (label, ", ".join(SEATS)))
        tasks.append({"task": " ".join(str(e["task"]).split()), "severity": sev, "effort": e["effort"],
                      "kit": "", "bucket": bucket, "seat": seat, "source": None})
    # Stable sort: severity, then effort; ties keep findings first, then kit items, then extras.
    tasks.sort(key=lambda t: (SEVERITY_ORDER.index(t["severity"]), EFFORTS.index(t["effort"])))
    for i, t in enumerate(tasks):
        t["day"] = i // PER_DAY + 1 if i < DAYS * PER_DAY else None
    return tasks


# ------------------------------------------------------------------ analysis

def _stage(spec, d):
    if spec.get("stage") is not None:
        stage = spec["stage"]
    else:
        stage = "team"
        path = ledger.target_path(d)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    stage = json.load(fh).get("stage") or "team"
            except ValueError:
                stage = "team"
    if stage not in STAGES:
        raise KitError("stage must be one of %s" % ", ".join(STAGES))
    return stage


def analyse(spec, d):
    if not isinstance(spec, dict):
        raise KitError("the input must be a JSON object")
    for key in ("business", "brand", "currency"):
        _need(spec, key, "fixkit.json")
    if "start" not in spec:
        raise KitError("'start' is missing (the first day of the plan, YYYY-MM-DD)")
    start = _date(spec["start"])
    cur, brand = spec["currency"], spec["brand"].strip()
    stage = _stage(spec, d)

    if not isinstance(spec.get("pages"), list):
        raise KitError("'pages' is missing (a list of the audited pages; it may be empty)")
    pages = []
    for i, p in enumerate(spec["pages"]):
        label = "page %d" % (i + 1)
        for key in ("url", "kind", "name"):
            _need(p, key, label)
        if p["kind"] not in KINDS:
            raise KitError("%s: kind must be one of %s" % (label, ", ".join(KINDS)))
        if not isinstance(p.get("features", []), list):
            raise KitError("%s: features must be a list of short phrases" % label)
        page = dict(p)
        page["price"] = num.parse_number(p["price"], field="%s.price" % label) if p.get("price") is not None else None
        page["title"] = make_title(page, brand, cur)
        page["description"] = make_description(page, brand, cur)
        pages.append(page)

    if "colours" in spec:
        if not isinstance(spec["colours"], list):
            raise KitError("'colours' must be a list of {id, fg, bg, where, size, bold}")
        raw_pairs = spec["colours"]
    elif spec.get("colours_from") == "proof":
        raw_pairs = pairs_from_ledger(d)
    else:
        raise KitError("give 'colours' (a list, empty when none were checked) or \"colours_from\": \"proof\"")
    colours = fix_colours(raw_pairs)

    wa = _need(spec, "whatsapp", "fixkit.json")
    _need(wa, "sign_off", "whatsapp")
    if not isinstance(spec.get("size_chart"), list):
        raise KitError("'size_chart' is missing (a list of {product_type, sizes, measurements})")
    for i, s in enumerate(spec["size_chart"]):
        label = "size_chart %d" % (i + 1)
        _need(s, "product_type", label)
        for key in ("sizes", "measurements"):
            if not isinstance(s.get(key), list) or not s[key]:
                raise KitError("%s: '%s' must be a non-empty list" % (label, key))
    ex = _need(spec, "exchange", "fixkit.json")
    days = int(num.parse_number(_need(ex, "days", "exchange"), field="exchange.days"))
    if days <= 0:
        raise KitError("exchange.days must be more than zero")
    _need(ex, "contact", "exchange")
    if not isinstance(ex.get("conditions", []), list):
        raise KitError("exchange.conditions must be a list")

    findings = [f for f in ledger.load_findings(d) if f["status"] == "verified" and f["skill"] != SKILL
                and f["severity"] in SEVERITY_ORDER]
    failing = [c for c in colours if not c["passes"]]
    kit_items = []
    if pages:
        kit_items.append({"kit": "titles", "severity": "medium", "effort": "S",
                          "task": "Paste the new page titles and meta descriptions into each page's search settings"})
    if failing:
        kit_items.append({"kit": "colours", "severity": "medium", "effort": "S",
                          "task": "Change the failing text colours to the new hex codes"})
    if spec["size_chart"]:
        kit_items.append({"kit": "size", "severity": "medium", "effort": "M",
                          "task": "Measure each size, fill in the size chart and add it to every product page"})
    kit_items.append({"kit": "exchange", "severity": "medium", "effort": "M",
                      "task": "Have the exchange policy draft checked, then publish it and link it from the footer"})
    kit_items.append({"kit": "whatsapp", "severity": "low", "effort": "S",
                      "task": "Save the review request and size-help messages as WhatsApp quick replies"})
    tasks = build_tasks(findings, kit_items, spec.get("extra_tasks") or [])

    return {"business": spec["business"], "brand": brand, "currency": cur, "start": start, "stage": stage,
            "pages": pages, "colours": colours, "whatsapp": wa, "size_chart": spec["size_chart"],
            "exchange": {"days": days, "conditions": ex.get("conditions") or [], "contact": ex["contact"]},
            "tasks": tasks, "findings": findings}


# ------------------------------------------------------------------ render

def render_titles_md(r):
    L = ["# Page titles and meta descriptions", "",
         "Paste each new title into the page's title field and each description into its meta description "
         "field. Titles are %d to %d characters and descriptions %d to %d, so search results show them whole."
         % (TITLE_MIN, TITLE_MAX, DESC_MIN, DESC_MAX), ""]
    if not r["pages"]:
        L.append("No pages were listed in fixkit.json.")
    for p in r["pages"]:
        cur_t, cur_d = p.get("current_title") or "", p.get("current_description") or ""
        L += ["## %s" % p["url"], "",
              "- Current title (%d chars): %s" % (len(cur_t), cur_t or "none"),
              "- New title (%d chars):" % len(p["title"]), "", "```text", p["title"], "```", "",
              "- Current description (%d chars): %s" % (len(cur_d), cur_d or "none"),
              "- New description (%d chars):" % len(p["description"]), "", "```text", p["description"], "```", ""]
    L.append("_%s._" % MARKER)
    return "\n".join(L) + "\n"


def render_titles_csv(r):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["url", "kind", "current_title", "current_title_chars", "new_title", "new_title_chars",
                "current_description", "current_description_chars", "new_description", "new_description_chars"])
    for p in r["pages"]:
        ct, cd = p.get("current_title") or "", p.get("current_description") or ""
        w.writerow([p["url"], p["kind"], ct, len(ct), p["title"], len(p["title"]), cd, len(cd),
                    p["description"], len(p["description"])])
    return buf.getvalue()


def render_colours_md(r):
    L = ["# Replacement colours", "",
         "Each failing pair gets the nearest colour of the same hue, lighter or darker, that reaches the WCAG "
         "AA bar: 4.5:1 for normal text, 3:1 for large text. Coloured text moves; white, black or grey text on "
         "a brand colour keeps its colour and the brand colour moves instead. Pairs that already pass are left "
         "alone.", ""]
    failing = [c for c in r["colours"] if not c["passes"]]
    if failing:
        L += ["| where | change | text colour | background | old ratio | new colour | new ratio | needs |",
              "| --- | --- | --- | --- | ---: | --- | ---: | ---: |"]
        for c in failing:
            change = "the background (the text stays)" if c.get("change") == "background" else "the text colour"
            L.append("| %s | %s | %s | %s | %.2f:1 | %s | %.2f:1 | %.1f:1 |" % (
                cell(c["where"]), change, c["fg"], c["bg"], c["ratio"], c["new"], c["new_ratio"], c["target"]))
        L.append("")
    elif r["colours"]:
        L += ["Every pair checked already passes. Nothing to change.", ""]
    else:
        L += ["No colour pairs were checked.", ""]
    passing = [c for c in r["colours"] if c["passes"]]
    if passing:
        L += ["Already passing, unchanged:", ""]
        for c in passing:
            L.append("- %s: %s on %s, %.2f:1" % (c["where"], c["fg"], c["bg"], c["ratio"]))
        L.append("")
    L.append("_%s. Ratios use the WCAG relative luminance formula; check the new colour still suits the "
             "brand before you change it everywhere._" % MARKER)
    return "\n".join(L) + "\n"


def render_whatsapp_md(r):
    wa, brand, days = r["whatsapp"], r["brand"], r["exchange"]["days"]
    sign = wa["sign_off"]
    L = ["# WhatsApp scripts", "",
         "Plain messages to send by hand or save as quick replies. Replace {name} with the customer's first "
         "name, {order} with the order number and {review_link} with your review page. Ask every customer the "
         "same way, whatever you expect them to say, and never offer anything in return for a review: a "
         "rewarded or filtered review is misleading and against most platforms' rules.", ""]
    if wa.get("number_display"):
        L += ["Send from: %s" % wa["number_display"], ""]
    L += ["## Day 3 after delivery: review request", "", "```text",
          "Hi {name}, this is %s from %s. Your order {order} reached you a few days ago. We hope it is "
          "working out for you." % (sign, brand), "",
          "If you have a minute, would you leave an honest review? Good or bad, it helps the next person "
          "choose: {review_link}", "",
          "If something is not right, just reply here and we will help.", "", "%s, %s" % (sign, brand), "```", "",
          "## Day 10 after delivery: one gentle reminder", "", "```text",
          "Hi {name}, %s from %s again. A short note about order {order}: if you have not had the chance, "
          "an honest review would help us a lot: {review_link}" % (sign, brand), "",
          "No worries if not. This is the last reminder we will send.", "", "%s, %s" % (sign, brand), "```", "",
          "Send each message once. If the customer has already reviewed, or has asked not to be messaged, "
          "skip the reminder.", "",
          "## Size help: reply when someone asks which size to pick", "", "```text",
          "Hi {name}, happy to help with the size for {order}.", "",
          "Could you tell us the size you usually wear, and if you have them, your bust, waist and hip in "
          "centimetres? Our size chart has the measurements of each size: {size_chart_link}", "",
          "If you are between two sizes, tell us how you like the fit (relaxed or close) and we will suggest one.",
          "", "%s, %s" % (sign, brand), "```", "",
          "Add this line only once the exchange policy is published:", "", "```text",
          "If it does not fit, message us within %d days of delivery and we will help you exchange it for "
          "another size." % days, "```", "", "_%s._" % MARKER]
    return "\n".join(L) + "\n"


def _measures(r):
    out = []
    for s in r["size_chart"]:
        for m in s["measurements"]:
            if m not in out:
                out.append(m)
    return out


def render_size_csv(r):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    cols = _measures(r)
    w.writerow(["product_type", "size"] + ["%s (cm)" % m for m in cols])
    for s in r["size_chart"]:
        for size in s["sizes"]:
            w.writerow([s["product_type"], size] + ["" if m in s["measurements"] else "-" for m in cols])
    return buf.getvalue()


def render_size_md(r):
    L = ["# Size chart", "",
         "Fill in each blank cell in centimetres. "
         "The same table is in size-chart.csv for a spreadsheet.", ""]
    if not r["size_chart"]:
        L.append("No product types were listed in fixkit.json.")
    for s in r["size_chart"]:
        ms = s["measurements"]
        L += ["## %s" % s["product_type"], "", "| size | " + " | ".join("%s (cm)" % m for m in ms) + " |",
              "| --- | " + " | ".join("---:" for _ in ms) + " |"]
        for size in s["sizes"]:
            L.append("| %s | " % cell(size) + " | ".join("  " for _ in ms) + " |")
        L.append("")
    if r["size_chart"]:
        L += ["## How to measure", "",
              "Say on the page whether the numbers are body measurements or garment measurements. These notes "
              "are for body measurements, with a soft tape, snug but not tight:", ""]
        for m in _measures(r):
            note = HOW_TO_MEASURE.get(m.strip().lower(), "Measure along the line the name describes, in centimetres.")
            L.append("- %s: %s" % (m, note))
        L.append("")
    L.append("_%s._" % MARKER)
    return "\n".join(L) + "\n"


def render_exchange_md(r):
    ex, brand = r["exchange"], r["brand"]
    L = ["# Draft: have a lawyer or CA check this before publishing", "",
         "## %d-day size exchange policy" % ex["days"], "",
         "If something you ordered from %s does not fit, you can ask to exchange it for another size within "
         "%d days of delivery." % (brand, ex["days"]), "", "### Conditions", ""]
    if ex["conditions"]:
        for c in ex["conditions"]:
            L.append("- %s" % " ".join(str(c).split()))
    else:
        L.append("- (List the conditions here, for example that the piece is unworn with its tags on.)")
    L += ["", "### How to ask for an exchange", "",
          "1. Contact us at %s within %d days of delivery, with your order number and the size you would "
          "like instead." % (ex["contact"], ex["days"]),
          "2. We will reply to confirm the exchange and tell you how to send the piece back.",
          "3. Once the piece reaches us and meets the conditions above, we send the new size.", "",
          "If the size you want is out of stock, we will tell you what we can offer instead.", "",
          "### Questions for your lawyer or CA before this goes live", "",
          "- Does this policy meet the consumer protection and e-commerce rules where you sell?",
          "- Who pays for shipping each way, and does that need to be stated here?",
          "- Do you also offer refunds, and if not, does the law where you sell require them in some cases?",
          "- How are exchanges shown in your accounts and on your invoices?", "",
          "_%s. This is a draft, not legal advice._" % MARKER]
    return "\n".join(L) + "\n"


def _who(r, t):
    return BUCKETS[t["bucket"]] if r["stage"] == "solo" else t["seat"]


def render_plan(r):
    stage = r["stage"]
    head = "Bucket" if stage == "solo" else "Owner"
    L = ["# 14-Day Fix Kit", "",
         "%s. The plan starts on %s. Every verified finding and every item in the kit is placed on a day, "
         "most serious first, quick jobs before long ones, at most %d a day." % (r["business"],
                                                                                  r["start"].isoformat(), PER_DAY), ""]
    if stage == "solo":
        L += ["Each task carries a bucket: Do today (you can do it yourself), Hire a freelancer (design, code or "
              "copy work that needs a specialist) or Ask a CA or lawyer (legal, tax and policy).", ""]
    else:
        L += ["Each task has an owner seat: investor (money), growth (getting customers), builder (the site and "
              "the code) or operator (how the business runs).", ""]
    L += ["| Day | Date | Task | Kit file | Priority | Effort | %s |" % head,
          "| ---: | --- | --- | --- | --- | --- | --- |"]
    placed = [t for t in r["tasks"] if t["day"]]
    for t in placed:
        date = r["start"] + datetime.timedelta(days=t["day"] - 1)
        L.append("| %d | %s | %s | %s | %s | %s | %s |" % (
            t["day"], date.isoformat(), cell(t["task"]), t["kit"] or "-", t["severity"], t["effort"], _who(r, t)))
    if not placed:
        L.append("| - | - | Nothing to schedule | - | - | - | - |")
    later = [t for t in r["tasks"] if not t["day"]]
    if later:
        L += ["", "## After day 14", "", "| Task | Priority | Effort | %s |" % head, "| --- | --- | --- | --- |"]
        for t in later:
            L.append("| %s | %s | %s | %s |" % (cell(t["task"]), t["severity"], t["effort"], _who(r, t)))
    L += ["", "## The kit", "",
          "- `fix-kit/titles.md` and `titles.csv`: a title and meta description for each page.",
          "- `fix-kit/colours.md`: replacement colours for the failing pairs.",
          "- `fix-kit/whatsapp.md`: review requests for day 3 and day 10, and a size-help reply.",
          "- `fix-kit/size-chart.md` and `size-chart.csv`: the size chart to fill in.",
          "- `fix-kit/exchange-policy.md`: a draft exchange policy for a lawyer or CA to check.", ""]
    sources = [t for t in r["tasks"] if t["source"]]
    if sources:
        L += ["## Sources", ""]
        for t in sources:
            where = "day %d" % t["day"] if t["day"] else "after day 14"
            L.append("- %s: %s" % (where, t["source"]))
        L.append("")
    L.append("_%s from the verified findings in the ledger and fixkit.json._" % MARKER)
    return "\n".join(L) + "\n"


def outputs(r):
    return {
        os.path.join(KIT_DIR, "titles.md"): render_titles_md(r),
        os.path.join(KIT_DIR, "titles.csv"): render_titles_csv(r),
        os.path.join(KIT_DIR, "colours.md"): render_colours_md(r),
        os.path.join(KIT_DIR, "whatsapp.md"): render_whatsapp_md(r),
        os.path.join(KIT_DIR, "size-chart.csv"): render_size_csv(r),
        os.path.join(KIT_DIR, "size-chart.md"): render_size_md(r),
        os.path.join(KIT_DIR, "exchange-policy.md"): render_exchange_md(r),
        SECTION: render_plan(r),
    }


# ------------------------------------------------------------------ commands

def _load(path):
    if not os.path.isfile(path):
        raise KitError("input file not found: %s" % path)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except ValueError as e:
        raise KitError("%s is not valid JSON: %s" % (path, e))


def cmd_template(a):
    ledger.guard_out(a.out, a.dir, a.force)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(EXAMPLE, encoding="utf-8") as src, open(a.out, "w", encoding="utf-8") as fh:
        fh.write(src.read())
    print("%s wrote an example fixkit.json to %s. Replace every value with your own." % (ledger.OK, a.out))
    return 0


def cmd_check(a):
    r = analyse(_load(a.file), a.dir)
    print(render_plan(r))
    print("%s nothing was written. Use 'record' to write proof/fix-kit/ and the ledger." % ledger.OK)
    return 0


def cmd_record(a):
    spec = _load(a.file)
    r = analyse(spec, a.dir)
    d, root = a.dir, ledger.root(a.dir)
    files = outputs(r)
    for rel in files:
        ledger.guard_out(os.path.join(root, rel), d, a.force, ours=MARKER)
    for rel, text in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    with open(os.path.join(ledger.evidence_dir(d, SKILL), "fixkit.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    inp = "evidence/%s/fixkit.json" % SKILL

    def rel(*parts):
        return "/".join(parts)

    ledger.init_coverage(d, SKILL, COVERAGE, keep_existing=False)
    if r["pages"]:
        ledger.mark_coverage(d, SKILL, "titles-written", "covered",
                             evidence=[inp, rel(KIT_DIR, "titles.md"), rel(KIT_DIR, "titles.csv")],
                             note="%d page(s)" % len(r["pages"]))
    else:
        ledger.mark_coverage(d, SKILL, "titles-written", "not_run", note="No pages were listed.")
    failing = [c for c in r["colours"] if not c["passes"]]
    if failing:
        ledger.mark_coverage(d, SKILL, "colours-fixed", "covered", evidence=[inp, rel(KIT_DIR, "colours.md")],
                             note="%d failing pair(s) given a passing colour" % len(failing))
    else:
        ledger.mark_coverage(d, SKILL, "colours-fixed", "not_applicable",
                             note="No colour pair fails." if r["colours"] else "No colour pairs were checked.")
    ledger.mark_coverage(d, SKILL, "whatsapp-scripts", "covered", evidence=[inp, rel(KIT_DIR, "whatsapp.md")])
    if r["size_chart"]:
        ledger.mark_coverage(d, SKILL, "size-chart", "covered",
                             evidence=[inp, rel(KIT_DIR, "size-chart.md"), rel(KIT_DIR, "size-chart.csv")])
    else:
        ledger.mark_coverage(d, SKILL, "size-chart", "not_run", note="No product types were listed.")
    ledger.mark_coverage(d, SKILL, "exchange-policy", "covered", evidence=[inp, rel(KIT_DIR, "exchange-policy.md")])
    section = SECTION.replace(os.sep, "/")
    ledger.mark_coverage(d, SKILL, "schedule", "covered", evidence=[inp, section],
                         note="%d verified finding(s) read from the ledger" % len(r["findings"]))
    placed = sum(1 for t in r["tasks"] if t["day"])
    ledger.write_scores(d, SKILL, [{"id": "tasks_scheduled", "value": placed, "unit": "count", "budget": 0,
                                    "op": ">=", "evidence": [section]}])
    print("%s wrote %s and %d files under %s; %d task(s) on the 14 days, %d after."
          % (ledger.OK, os.path.join(root, SECTION), len(files) - 1, os.path.join(root, KIT_DIR), placed,
             len(r["tasks"]) - placed))
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

    t = sub.add_parser("template", help="write a runnable example fixkit.json")
    t.add_argument("--out", required=True, help="where to write the example")
    common(t)
    t.set_defaults(func=cmd_template)
    for name, func, text in (("check", cmd_check, "print the 14-day plan and write nothing"),
                             ("record", cmd_record, "write proof/fix-kit/, the section and the ledger")):
        p = sub.add_parser(name, help=text)
        p.add_argument("file", help="the fixkit JSON")
        common(p)
        p.set_defaults(func=func)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: template, check or record")
    try:
        return a.func(a)
    except ValueError as e:  # KitError, num.MissingInput / num.BadNumber, ledger.LedgerError
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
