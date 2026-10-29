#!/usr/bin/env python3
"""Score saved web pages for technical and on-page SEO. The score is computed, never an opinion.

Standard library only, no network. Save the rendered HTML of the pages that matter
(and, when the site has them, robots.txt and sitemap.xml) under
proof/evidence/proof-seo/, then point this tool at the files. Every check names the
element it looked at, the value it found and the points it was worth, so a score can
be re-derived from the file by hand. The tool audits; it writes no content.

    python3 seo_score.py checks
    python3 seo_score.py page proof/evidence/proof-seo/index.html [--url https://example.com/] [--json]
    python3 seo_score.py site [--pages proof/evidence/proof-seo] [--robots robots.txt] [--sitemap sitemap.xml] [--json]
    python3 seo_score.py record proof/evidence/proof-seo/index.html [--url U] [--lighthouse report.json]
"""

import argparse
import json
import os
import re
import shutil
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-seo"

# Budgets and thresholds. They live here and nowhere else.
BUDGET = 80                  # seo_score passes at or above this
LIGHTHOUSE_BUDGET = 90       # the Lighthouse SEO category, when a saved report is folded in
TITLE_RANGE = (10, 60)
DESCRIPTION_RANGE = (50, 160)
MIN_INTERNAL_LINKS = 3
MIN_WORDS = 300
MAX_SITEMAP_URLS = 50000
MAX_SITEMAP_BYTES = 52428800
FINDING_WEIGHT = 10          # a failed on-page check worth at least this becomes a finding
PAGE_SHARE = 0.75            # site score = PAGE_SHARE * mean page score + (1 - PAGE_SHARE) * technical score
WATCHED_AGENTS = ("*", "googlebot", "bingbot")

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
SKIP_TEXT = {"script", "style", "noscript", "template", "head", "svg"}


def _clean(text):
    return re.sub(r"\s+", " ", text).strip()


def _ascii(text):
    """Console output stays ASCII; files are written as UTF-8 regardless."""
    return str(text).encode("ascii", "replace").decode("ascii")


def _short(text, n=60):
    return text if len(text) <= n else text[:n - 3] + "..."


def _host(url):
    if not url:
        return None
    netloc = urllib.parse.urlparse(url if "//" in url else "//" + url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


# ------------------------------------------------------------------- parsing

class _Page(HTMLParser):
    """Collect the elements the checks look at, in document order."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.saw_html = False
        self.lang = None          # the html element's lang attribute (None when absent)
        self.title = None         # text of the first <title> (None when there is none)
        self.metas, self.links, self.anchors, self.imgs = [], [], [], []
        self.headings = []        # (level, text) in document order
        self.ldjson = []          # raw text of each application/ld+json script
        self.words = 0            # words of visible text
        self._skip = 0
        self._svg = 0
        self._title_buf = None
        self._heading = None
        self._ld = None

    def handle_starttag(self, tag, attrs):
        a = dict((k, "" if v is None else v) for k, v in attrs)
        if tag == "html":
            if not self.saw_html:
                self.saw_html = True
                self.lang = a.get("lang")
        elif tag == "title":
            if self.title is None and self._title_buf is None and not self._svg:
                self._title_buf = []
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "a":
            if "href" in a:
                self.anchors.append(a["href"])
        elif tag == "img":
            self.imgs.append(a)
        elif tag in HEADING_LEVEL:
            self._heading = [HEADING_LEVEL[tag], []]
        elif tag == "script" and a.get("type", "").strip().lower() == "application/ld+json":
            self._ld = []
        if tag == "svg":
            self._svg += 1
        if tag in SKIP_TEXT:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._title_buf is not None:
            self.title = _clean("".join(self._title_buf))
            self._title_buf = None
        elif tag in HEADING_LEVEL and self._heading is not None:
            self.headings.append((self._heading[0], _clean("".join(self._heading[1]))))
            self._heading = None
        elif tag == "script" and self._ld is not None:
            self.ldjson.append("".join(self._ld))
            self._ld = None
        if tag == "svg" and self._svg:
            self._svg -= 1
        if tag in SKIP_TEXT and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._title_buf is not None:
            self._title_buf.append(data)
            return
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._heading is not None:
            self._heading[1].append(data)
        if not self._skip:
            self.words += len(_WORD.findall(data))


def parse_html(text):
    p = _Page()
    p.feed(text)
    p.close()
    if p._title_buf is not None:          # unclosed <title>
        p.title = _clean("".join(p._title_buf))
    if p._heading is not None:            # unclosed heading
        p.headings.append((p._heading[0], _clean("".join(p._heading[1]))))
    return p


def _meta(p, name):
    """Content of the first <meta name=...> (or property=...), None when absent."""
    for m in p.metas:
        keys = (m.get("name", "").strip().lower(), m.get("property", "").strip().lower())
        if name in keys:
            return _clean(m.get("content", ""))
    return None


def _canonicals(p):
    return [l for l in p.links if "canonical" in l.get("rel", "").lower().split()]


# -------------------------------------------------------------------- checks
# Each check returns (status, found, problem, severity): status is pass, fail or na;
# found is the value seen (None when the problem says it all); problem is the fail
# phrase; severity is the finding severity when the check fails.

def _pass(found):
    return "pass", found, None, None


def _fail(found, problem, severity):
    return "fail", found, problem, severity


def _na(reason):
    return "na", reason, None, None


def chk_title(p, ctx):
    if not p.title:
        return _fail(None, "title missing", "medium")
    n = len(p.title)
    found = '"%s" (%d chars)' % (_short(p.title), n)
    if n < TITLE_RANGE[0] or n > TITLE_RANGE[1]:
        return _fail(found, "title is %d chars (want %d-%d)" % (n, TITLE_RANGE[0], TITLE_RANGE[1]), "low")
    return _pass(found)


def chk_description(p, ctx):
    d = _meta(p, "description")
    if not d:
        return _fail(None, "meta description missing", "medium")
    n = len(d)
    found = '"%s" (%d chars)' % (_short(d), n)
    if n < DESCRIPTION_RANGE[0] or n > DESCRIPTION_RANGE[1]:
        return _fail(found, "meta description is %d chars (want %d-%d)" % (n, DESCRIPTION_RANGE[0], DESCRIPTION_RANGE[1]), "low")
    return _pass(found)


def chk_h1(p, ctx):
    h1s = [t for lvl, t in p.headings if lvl == 1]
    if not h1s:
        return _fail(None, "no h1", "medium")
    if len(h1s) > 1:
        return _fail('first: "%s"' % _short(h1s[0]), "%d h1 elements (want one)" % len(h1s), "low")
    return _pass('1 h1: "%s"' % _short(h1s[0]))


def chk_heading_order(p, ctx):
    if not p.headings:
        return _na("no headings")
    outline = " ".join("h%d" % lvl for lvl, _ in p.headings[:12]) + (" ..." if len(p.headings) > 12 else "")
    found = "%d headings: %s" % (len(p.headings), outline)
    skips, prev = [], p.headings[0][0]
    for lvl, _ in p.headings[1:]:
        if lvl > prev + 1:
            skips.append("h%d to h%d" % (prev, lvl))
        prev = lvl
    if skips:
        return _fail(found, "heading levels skip (%s)" % ", ".join(skips[:3]), "low")
    return _pass(found)


def chk_img_alt(p, ctx):
    if not p.imgs:
        return _na("no img elements")
    missing = [i for i in p.imgs if "alt" not in i]
    found = "%d of %d images have alt" % (len(p.imgs) - len(missing), len(p.imgs))
    if missing:
        first = missing[0].get("src", "") or "(no src)"
        return _fail("%s; first without: %s" % (found, _short(first, 80)),
                     "%d of %d images have no alt" % (len(missing), len(p.imgs)),
                     "medium" if len(missing) == len(p.imgs) else "low")
    return _pass(found)


def chk_canonical(p, ctx):
    c = _canonicals(p)
    if not c:
        return _fail(None, "no canonical link", "low")
    if len(c) > 1:
        return _fail(None, "%d canonical links (want one)" % len(c), "low")
    href = c[0].get("href", "").strip()
    if not href:
        return _fail(None, "canonical link has an empty href", "low")
    return _pass(href)


def chk_lang(p, ctx):
    if not p.saw_html:
        return _fail(None, "no html element", "low")
    if not (p.lang or "").strip():
        return _fail(None, "html element has no lang attribute", "low")
    return _pass('lang="%s"' % p.lang.strip())


def chk_viewport(p, ctx):
    v = _meta(p, "viewport")
    if v is None:
        return _fail(None, "no meta viewport", "low")
    if "width" not in v.lower():
        return _fail('"%s"' % _short(v), "meta viewport has no width", "low")
    return _pass('"%s"' % _short(v))


def chk_open_graph(p, ctx):
    wanted = ("og:title", "og:description", "og:image")
    present = [k for k in wanted if _meta(p, k)]
    missing = [k for k in wanted if k not in present]
    found = "present: %s" % (", ".join(present) or "none")
    if missing:
        return _fail(found, "missing %s" % ", ".join(missing), "low")
    return _pass(found)


def _types(data, out):
    if isinstance(data, dict):
        t = data.get("@type")
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, list):
            out.extend(str(x) for x in t)
        for item in data.get("@graph", []) if isinstance(data.get("@graph"), list) else []:
            _types(item, out)
    elif isinstance(data, list):
        for item in data:
            _types(item, out)
    return out


def chk_structured_data(p, ctx):
    if not p.ldjson:
        return _fail(None, "no application/ld+json script", "low")
    types = []
    for raw in p.ldjson:
        text = raw.strip()
        if text.startswith("<!--") and text.endswith("-->"):
            text = text[4:-3].strip()
        try:
            _types(json.loads(text), types)
        except ValueError as e:
            return _fail("%d ld+json script(s)" % len(p.ldjson), "ld+json is not valid JSON (%s)" % e, "low")
    return _pass("%d ld+json script(s), @type %s" % (len(p.ldjson), ", ".join(types[:6]) or "not stated"))


def chk_internal_links(p, ctx):
    host = ctx.get("host")
    internal = external = 0
    for href in p.anchors:
        h = href.strip()
        if not h or h.startswith("#"):
            continue
        if h.lower().startswith(("mailto:", "tel:", "sms:", "javascript:", "data:")):
            continue
        parsed = urllib.parse.urlparse(h)
        if parsed.netloc:
            if host and _host(h) == host:
                internal += 1
            else:
                external += 1
        elif parsed.scheme:
            external += 1
        else:
            internal += 1
    found = "%d internal, %d external" % (internal, external)
    found += (" (host %s)" % host) if host else " (no host known; absolute links counted as external, pass --url)"
    if internal < MIN_INTERNAL_LINKS:
        return _fail(found, "%d internal links (want at least %d)" % (internal, MIN_INTERNAL_LINKS), "low")
    return _pass(found)


def chk_word_count(p, ctx):
    found = "%d words of visible text" % p.words
    if p.words < MIN_WORDS:
        return _fail(found, "%d words of visible text (want at least %d)" % (p.words, MIN_WORDS), "low")
    return _pass(found)


# id, title, weight, function, fix. The weights sum to 100.
CHECKS = [
    ("title", "Title present and %d-%d chars" % TITLE_RANGE, 12, chk_title,
     "Give the page one <title> of %d-%d characters that names the page and the site." % TITLE_RANGE),
    ("meta-description", "Meta description present and %d-%d chars" % DESCRIPTION_RANGE, 10, chk_description,
     "Add <meta name=\"description\"> of %d-%d characters that says what the page offers." % DESCRIPTION_RANGE),
    ("h1", "Exactly one h1", 10, chk_h1,
     "Use one h1 that states the page topic; demote the others to h2."),
    ("heading-order", "Heading levels do not skip", 6, chk_heading_order,
     "Step headings down one level at a time (h2 then h3, not h2 then h4)."),
    ("img-alt", "Every img has an alt attribute", 10, chk_img_alt,
     "Add alt text to every img; use alt=\"\" for purely decorative images."),
    ("canonical", "One canonical link", 8, chk_canonical,
     "Add one <link rel=\"canonical\"> with the page's preferred absolute URL."),
    ("lang", "lang attribute on the html element", 6, chk_lang,
     "Set <html lang=\"...\"> to the page language, for example lang=\"en\"."),
    ("viewport", "Meta viewport with a width", 8, chk_viewport,
     "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."),
    ("open-graph", "Open Graph title, description and image", 8, chk_open_graph,
     "Add og:title, og:description and og:image meta tags for link previews."),
    ("structured-data", "A valid application/ld+json script", 8, chk_structured_data,
     "Add a <script type=\"application/ld+json\"> block with valid JSON describing the page."),
    ("internal-links", "At least %d internal links" % MIN_INTERNAL_LINKS, 6, chk_internal_links,
     "Link to related pages on the same site from the body and navigation."),
    ("word-count", "At least %d words of visible text" % MIN_WORDS, 8, chk_word_count,
     "Write out what the page is for; thin pages rarely earn a ranking."),
]
assert sum(c[2] for c in CHECKS) == 100

TECHNICAL = [
    ("robots-present", "robots.txt saved and readable", 20),
    ("robots-open", "robots.txt does not block all crawling", 20),
    ("sitemap-present", "sitemap saved", 20),
    ("sitemap-valid", "sitemap is well-formed XML with at most %d urls" % MAX_SITEMAP_URLS, 20),
    ("sitemap-covers-pages", "every saved page is listed in the sitemap", 20),
]
LIGHTHOUSE_CHECK = ("lighthouse-seo", "Lighthouse SEO category score (saved report)")


def _tally(checks):
    applicable = sum(c["weight"] for c in checks if c["status"] != "na")
    earned = sum(c["points"] for c in checks)
    score = int(100.0 * earned / applicable + 0.5) if applicable else 0
    return earned, applicable, score


def _entry(cid, title, weight, status, found, problem=None, severity=None, fix=None):
    return {"id": cid, "title": title, "weight": weight, "status": status,
            "points": weight if status == "pass" else 0, "found": found,
            "problem": problem, "severity": severity, "fix": fix if status == "fail" else None}


def analyse_page(path, url=None):
    """Run every on-page check against one saved HTML file."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        raise SystemExit("cannot read %s: %s" % (path, e))
    p = parse_html(text)
    canonicals = _canonicals(p)
    canonical = canonicals[0].get("href", "").strip() if len(canonicals) == 1 else None
    host = _host(url) or _host(canonical)
    ctx = {"host": host, "url": url}
    checks = []
    for cid, title, weight, fn, fix in CHECKS:
        status, found, problem, severity = fn(p, ctx)
        checks.append(_entry(cid, title, weight, status, found, problem, severity, fix))
    earned, applicable, score = _tally(checks)
    return {"file": path, "page": os.path.basename(path), "url": url, "host": host,
            "canonical": canonical, "words": p.words, "checks": checks,
            "earned": earned, "applicable": applicable, "score": score, "budget": BUDGET}


# ------------------------------------------------------------- robots, sitemap

def parse_robots(text):
    groups, sitemaps, current = [], [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if current is None or current["rules"]:
                current = {"agents": [], "rules": []}
                groups.append(current)
            current["agents"].append(value.lower())
        elif key in ("disallow", "allow") and current is not None:
            current["rules"].append((key, value))
        elif key == "sitemap":
            sitemaps.append(value)
    blocked = []
    for g in groups:
        shut = any(k == "disallow" and v in ("/", "/*") for k, v in g["rules"])
        opened = any(k == "allow" and v in ("/", "/*") for k, v in g["rules"])
        if shut and not opened:
            blocked.extend(g["agents"])
    return {"groups": groups, "sitemaps": sitemaps, "blocked": blocked,
            "rules": sum(len(g["rules"]) for g in groups)}


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def read_sitemap(path):
    try:
        size = os.path.getsize(path)
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return {"error": "not well-formed XML (%s)" % e}
    except OSError as e:
        return {"error": "cannot read (%s)" % e}
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        return {"error": "root element is <%s>, expected <urlset> or <sitemapindex>" % kind}
    child = "url" if kind == "urlset" else "sitemap"
    entries = [e for e in root if _local(e.tag) == child]
    locs, without_loc = [], 0
    for e in entries:
        loc = next((c.text.strip() for c in e if _local(c.tag) == "loc" and c.text and c.text.strip()), None)
        if loc is None:
            without_loc += 1
        else:
            locs.append(loc)
    return {"error": None, "kind": kind, "locs": locs, "count": len(entries),
            "without_loc": without_loc, "bytes": size}


def _norm_url(u):
    parsed = urllib.parse.urlparse(u.strip())
    return (_host(u) or "", (parsed.path.rstrip("/") or "/").lower())


def page_in_sitemap(result, locs):
    """Match a saved page to a sitemap entry by its canonical URL, else by file name."""
    norm = set(_norm_url(l) for l in locs)
    if result.get("canonical") and _norm_url(result["canonical"]) in norm:
        return True
    name = result["page"].lower()
    stem = os.path.splitext(name)[0]
    for _, path in norm:
        last = path.rsplit("/", 1)[-1]
        if last in (name, stem):
            return True
        if stem == "index" and path == "/":
            return True
    return False


def technical_checks(robots_path, sitemap_path, pages, absent="fail"):
    """The site-level checks. ``absent`` says what a file not given means: "fail"
    (site: it was not saved, so it is not shown to exist) or "na" (record: not passed)."""
    titles = dict((cid, (title, weight)) for cid, title, weight in TECHNICAL)
    out = []

    def add(cid, status, found, problem=None, severity=None, fix=None):
        title, weight = titles[cid]
        out.append(_entry(cid, title, weight, status, found, problem, severity, fix))

    if robots_path is None:
        if absent == "na":
            add("robots-present", "na", "no robots.txt passed (--robots)")
        else:
            add("robots-present", "fail", None, "robots.txt not saved next to the pages (save it, or pass --robots)",
                "low", "Save the site's robots.txt next to the pages so it can be checked.")
        add("robots-open", "na", "no robots.txt to read")
    else:
        try:
            with open(robots_path, encoding="utf-8-sig", errors="replace") as fh:
                robots = parse_robots(fh.read())
        except OSError as e:
            raise SystemExit("cannot read %s: %s" % (robots_path, e))
        add("robots-present", "pass", "%s: %d group(s), %d rule(s), %d sitemap line(s)"
            % (os.path.basename(robots_path), len(robots["groups"]), robots["rules"], len(robots["sitemaps"])))
        watched = [a for a in robots["blocked"] if a == "*" or a.startswith(WATCHED_AGENTS[1:])]
        if watched:
            add("robots-open", "fail", "Disallow: / applies to %s" % ", ".join(sorted(set(watched))),
                "robots.txt blocks all crawling for %s" % ", ".join(sorted(set(watched))), "medium",
                "Remove the blanket Disallow: / (or scope it to the paths that must stay out of search).")
        else:
            extra = "; fully blocked agents: %s" % ", ".join(robots["blocked"]) if robots["blocked"] else ""
            add("robots-open", "pass", "no blanket Disallow: / for %s%s" % (", ".join(WATCHED_AGENTS), extra))

    missing_pages = []
    if sitemap_path is None:
        if absent == "na":
            add("sitemap-present", "na", "no sitemap passed (--sitemap)")
        else:
            add("sitemap-present", "fail", None, "sitemap.xml not saved next to the pages (save it, or pass --sitemap)",
                "low", "Save the site's sitemap.xml next to the pages so it can be checked.")
        add("sitemap-valid", "na", "no sitemap to read")
        add("sitemap-covers-pages", "na", "no sitemap to read")
    else:
        sm = read_sitemap(sitemap_path)
        add("sitemap-present", "pass", os.path.basename(sitemap_path))
        fix = "Publish a well-formed sitemap (urlset of url/loc entries, at most %d urls and 50 MB per file)." % MAX_SITEMAP_URLS
        if sm["error"]:
            add("sitemap-valid", "fail", None, "sitemap %s" % sm["error"], "low", fix)
            add("sitemap-covers-pages", "na", "sitemap is not readable")
        elif sm["count"] > MAX_SITEMAP_URLS:
            add("sitemap-valid", "fail", "%d entries" % sm["count"], "sitemap has %d entries (limit %d)" % (sm["count"], MAX_SITEMAP_URLS), "low", fix)
            add("sitemap-covers-pages", "na", "sitemap over the entry limit")
        elif sm["bytes"] > MAX_SITEMAP_BYTES:
            add("sitemap-valid", "fail", "%d bytes" % sm["bytes"], "sitemap is %d bytes (limit %d)" % (sm["bytes"], MAX_SITEMAP_BYTES), "low", fix)
            add("sitemap-covers-pages", "na", "sitemap over the size limit")
        elif sm["without_loc"]:
            add("sitemap-valid", "fail", "%d entries" % sm["count"], "%d sitemap entries have no <loc>" % sm["without_loc"], "low", fix)
            add("sitemap-covers-pages", "na", "sitemap has entries without <loc>")
        else:
            add("sitemap-valid", "pass", "%s with %d %s, %d bytes"
                % (sm["kind"], sm["count"], "urls" if sm["kind"] == "urlset" else "child sitemaps", sm["bytes"]))
            if sm["kind"] == "sitemapindex":
                add("sitemap-covers-pages", "na", "sitemap index; pass a child sitemap with --sitemap to check page coverage")
            else:
                missing_pages = [r["page"] for r in pages if not page_in_sitemap(r, sm["locs"])]
                found = "%d of %d saved pages listed (matched by canonical URL or file name)" % (len(pages) - len(missing_pages), len(pages))
                if missing_pages:
                    add("sitemap-covers-pages", "fail", found,
                        "%d saved page(s) not in the sitemap: %s" % (len(missing_pages), ", ".join(missing_pages[:5]) + (" ..." if len(missing_pages) > 5 else "")),
                        "low", "List every page that should be indexed in the sitemap, by its canonical URL.")
                else:
                    add("sitemap-covers-pages", "pass", found)
    return out, missing_pages


# ---------------------------------------------------------------- lighthouse

def read_lighthouse(path):
    """Read the SEO category out of a Lighthouse JSON report the user saved."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit("cannot read %s as JSON: %s" % (path, e))
    cat = data.get("categories", {}).get("seo") if isinstance(data, dict) else None
    score = cat.get("score") if isinstance(cat, dict) else None
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise SystemExit("%s has no categories.seo.score; save a report with: lighthouse <url> --only-categories=seo --output json" % path)
    audits = data.get("audits") or {}
    failing = []
    for ref in cat.get("auditRefs") or []:
        aud = audits.get(ref.get("id")) if isinstance(ref, dict) else None
        if not isinstance(aud, dict):
            continue
        s, mode = aud.get("score"), aud.get("scoreDisplayMode", "")
        if isinstance(s, (int, float)) and not isinstance(s, bool) and s < 1 \
                and mode not in ("notApplicable", "informative", "manual"):
            failing.append({"id": ref["id"], "title": aud.get("title", "")})
    return {"score": int(score * 100 + 0.5), "version": data.get("lighthouseVersion", ""),
            "url": data.get("finalDisplayedUrl") or data.get("finalUrl") or data.get("requestedUrl"),
            "failing": failing, "budget": LIGHTHOUSE_BUDGET}


# ------------------------------------------------------------------ printing

def _print_checks(checks, indent="  "):
    for c in checks:
        if c["status"] == "na":
            mark, pts, text = "--", "  n/a", "not applicable: %s" % c["found"]
        elif c["status"] == "pass":
            mark, pts, text = ledger.OK, "%2d/%-2d" % (c["points"], c["weight"]), c["found"]
        else:
            mark, pts = ledger.BAD, "%2d/%-2d" % (0, c["weight"])
            text = c["problem"] + (" [%s]" % c["found"] if c["found"] else "")
        print("%s%s  %-21s %s  %s" % (indent, mark, c["id"], pts, _ascii(text)))


def _print_page(r):
    print("Page: %s%s" % (_ascii(r["page"]), (" (%s)" % _ascii(r["url"])) if r["url"] else ""))
    if r.get("url_source") == "target.json":
        print("  (url taken from proof/target.json)")
    _print_checks(r["checks"])
    scaled = "" if r["applicable"] == 100 else " (%d of %d applicable points, scaled to 100)" % (r["earned"], r["applicable"])
    print("Score: %d/100%s. Budget %d: %s" % (r["score"], scaled, BUDGET, ledger.OK if r["score"] >= BUDGET else ledger.BAD))
    if r.get("lighthouse"):
        _print_lighthouse(r["lighthouse"])


def _print_lighthouse(lh):
    ver = (" v%s" % lh["version"]) if lh["version"] else ""
    print("Lighthouse SEO%s: %d/100. Budget %d: %s" % (ver, lh["score"], LIGHTHOUSE_BUDGET,
                                                        ledger.OK if lh["score"] >= LIGHTHOUSE_BUDGET else ledger.BAD))
    for f in lh["failing"][:12]:
        print("  %s  %s: %s" % (ledger.BAD, f["id"], _ascii(f["title"])))


# ----------------------------------------------------------------- commands

def _target_url(d):
    try:
        t = ledger.load_target(d)
    except (ledger.LedgerError, ValueError):
        return None
    urls = (t or {}).get("urls") or []
    return urls[0] if urls else None


def _resolve_url(flag, d):
    if flag:
        return flag, "flag"
    t = _target_url(d)
    return (t, "target.json") if t else (None, None)


def cmd_checks(a):
    print("On-page checks (points out of 100; a failed check worth %d or more becomes a finding in record):" % FINDING_WEIGHT)
    for cid, title, weight, _, _ in CHECKS:
        print("  %-21s %3d  %s" % (cid, weight, title))
    print("\nTechnical checks (site; each is %d points of the technical score):" % TECHNICAL[0][2])
    for cid, title, weight in TECHNICAL:
        print("  %-21s %3d  %s" % (cid, weight, title))
    print("\nA check that does not apply (no images, no headings) is left out and the rest is scaled to 100.")
    print("Site score = %.2f x mean page score + %.2f x technical score." % (PAGE_SHARE, 1 - PAGE_SHARE))
    print("Budgets: seo_score >= %d, lighthouse_seo >= %d (only when a saved Lighthouse report is passed)." % (BUDGET, LIGHTHOUSE_BUDGET))
    return 0


def cmd_page(a):
    url, source = _resolve_url(a.url, a.dir)
    r = analyse_page(a.page, url)
    r["url_source"] = source
    if a.lighthouse:
        r["lighthouse"] = read_lighthouse(a.lighthouse)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    _print_page(r)
    return 0


def _html_files(folder):
    out = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            if name.lower().endswith((".html", ".htm")):
                out.append(os.path.join(base, name))
    return out


def _sibling(folder, name, flag):
    if flag:
        if not os.path.isfile(flag):
            raise SystemExit("%s does not exist" % flag)
        return flag
    path = os.path.join(folder, name)
    return path if os.path.isfile(path) else None


def cmd_site(a):
    folder = a.pages or os.path.join(ledger.root(a.dir), "evidence", SKILL)
    if not os.path.isdir(folder):
        raise SystemExit("%s is not a folder. Save the rendered pages there first (see SKILL.md)." % folder)
    files = _html_files(folder)
    if not files:
        raise SystemExit("no .html files under %s. Save the rendered pages there first (see SKILL.md)." % folder)
    url, source = _resolve_url(a.url, a.dir)
    pages = [analyse_page(f, url) for f in files]
    robots_path = _sibling(folder, "robots.txt", a.robots)
    sitemap_path = _sibling(folder, "sitemap.xml", a.sitemap)
    tech, missing = technical_checks(robots_path, sitemap_path, pages, absent="fail")
    page_mean = int(sum(p["score"] for p in pages) / float(len(pages)) + 0.5)
    _, _, tech_score = _tally(tech)
    score = int(PAGE_SHARE * page_mean + (1 - PAGE_SHARE) * tech_score + 0.5)
    result = {"pages_dir": folder, "url": url, "url_source": source, "pages": pages,
              "robots": robots_path, "sitemap": sitemap_path, "technical": tech,
              "missing_from_sitemap": missing, "page_mean": page_mean,
              "technical_score": tech_score, "score": score, "budget": BUDGET}
    if a.json:
        print(json.dumps(result, indent=2))
        return 0
    print("Pages under %s (%d):" % (_ascii(folder), len(pages)))
    if source == "target.json":
        print("  (url taken from proof/target.json)")
    for p in pages:
        failed = [c["id"] for c in p["checks"] if c["status"] == "fail"]
        print("  %s  %-32s %3d/100  %s" % (ledger.OK if p["score"] >= BUDGET else ledger.BAD,
                                           _ascii(_short(p["page"], 32)), p["score"],
                                           ("fails: " + ", ".join(failed)) if failed else "all checks pass"))
    print("  mean page score: %d" % page_mean)
    print("Technical:")
    _print_checks(tech)
    print("Site score: %d/100 = %.2f x %d (pages) + %.2f x %d (technical). Budget %d: %s"
          % (score, PAGE_SHARE, page_mean, 1 - PAGE_SHARE, tech_score, BUDGET, ledger.OK if score >= BUDGET else ledger.BAD))
    print("Detail per page: seo_score.py page <file>. To write a page to the ledger: seo_score.py record <file>.")
    return 0


# ------------------------------------------------------------------- record

def _into_evidence(d, path):
    """Return a ledger-relative path for ``path``, copying it under proof/evidence/proof-seo/ when it lives elsewhere."""
    base, full = ledger.root(d), os.path.abspath(path)
    try:
        inside = os.path.commonpath([full, base]) == base
    except ValueError:
        inside = False
    if inside:
        return os.path.relpath(full, base).replace(os.sep, "/")
    dest = os.path.join(ledger.evidence_dir(d, SKILL), os.path.basename(full))
    shutil.copyfile(full, dest)
    rel = "evidence/%s/%s" % (SKILL, os.path.basename(full))
    print("  copied %s to %s" % (_ascii(path), rel))
    return rel


def _sync_finding(d, fid, failing, build, note):
    """Write and verify a finding when the check fails; drop a stale one when it no longer does.
    A finding the user rejected as a false positive is left alone either way."""
    path = ledger.finding_path(d, SKILL, fid)
    existing = ledger.load_finding(d, SKILL, fid) if os.path.exists(path) else None
    if existing and existing["status"] == "false_positive":
        print("  kept %s as the false positive you ruled out (not rewritten)" % fid)
        return None
    if not failing:
        if existing:
            os.remove(path)
            print("  removed stale finding %s (the check passes now)" % fid)
        return None
    f = build()
    ledger.write_finding(d, f)
    ledger.verify(d, SKILL, fid, by="manual", note=note)
    print("  %s  finding %s written and verified (%s)" % (ledger.BAD, fid, f["severity"]))
    return f


def _merge_metric(metrics, prefix, pslug, value, budget, source, evidence):
    """Keep one metric per recorded page under ``prefix:<page>`` and ``prefix`` as the lowest of them."""
    metrics["%s:%s" % (prefix, pslug)] = {"id": "%s:%s" % (prefix, pslug), "value": value, "unit": "points",
                                          "budget": budget, "op": ">=", "source": source, "evidence": evidence}
    per_page = [m for k, m in metrics.items() if k.startswith(prefix + ":")]
    lowest = min(per_page, key=lambda m: (m["value"], m["id"]))
    metrics[prefix] = {"id": prefix, "value": lowest["value"], "unit": "points", "budget": budget, "op": ">=",
                       "source": "lowest of %d recorded page(s): %s" % (len(per_page), lowest["id"].split(":", 1)[1]),
                       "evidence": list(lowest.get("evidence", []))}
    return len(per_page)


def cmd_record(a):
    url, source = _resolve_url(a.url, a.dir)
    r = analyse_page(a.page, url)
    r["url_source"] = source
    lh = read_lighthouse(a.lighthouse) if a.lighthouse else None
    if a.robots and not os.path.isfile(a.robots):
        raise SystemExit("%s does not exist" % a.robots)
    if a.sitemap and not os.path.isfile(a.sitemap):
        raise SystemExit("%s does not exist" % a.sitemap)
    tech, _ = technical_checks(a.robots, a.sitemap, [r], absent="na")
    _print_page(r)
    if lh:
        _print_lighthouse(lh)
    if a.robots or a.sitemap:
        print("Technical:")
        _print_checks(tech)

    print("Ledger:")
    rel = _into_evidence(a.dir, a.page)
    lh_rel = _into_evidence(a.dir, a.lighthouse) if a.lighthouse else None
    tech_rel = {"robots": _into_evidence(a.dir, a.robots) if a.robots else None,
                "sitemap": _into_evidence(a.dir, a.sitemap) if a.sitemap else None}
    page, pslug = r["page"], ledger.slug(os.path.splitext(r["page"])[0])
    location = url or page

    # Coverage: every on-page item, the Lighthouse fold-in, and the technical checks.
    checks = [(cid, title) for cid, title, _, _, _ in CHECKS] + [LIGHTHOUSE_CHECK] + [(cid, title) for cid, title, _ in TECHNICAL]
    ledger.init_coverage(a.dir, SKILL, checks)
    for c in r["checks"] + tech:
        label = c["problem"] if c["status"] == "fail" else c["found"]
        evidence = tech_rel[c["id"].split("-", 1)[0]] if c["id"].split("-", 1)[0] in tech_rel else rel
        if c["status"] == "na":
            ledger.mark_coverage(a.dir, SKILL, c["id"], "not_applicable", note="%s: %s" % (page, c["found"]))
        else:
            ledger.mark_coverage(a.dir, SKILL, c["id"], "covered", note="%s: %s" % (page, label), evidence=[evidence])
    if lh:
        ledger.mark_coverage(a.dir, SKILL, LIGHTHOUSE_CHECK[0], "covered",
                             note="%s: Lighthouse SEO %d/100, %d failing audit(s)" % (page, lh["score"], len(lh["failing"])),
                             evidence=[lh_rel])
    else:
        ledger.mark_coverage(a.dir, SKILL, LIGHTHOUSE_CHECK[0], "not_applicable",
                             note="%s: no Lighthouse report passed (--lighthouse)" % page)

    # Findings: failed high-weight on-page checks, plus the two technical facts that matter.
    written = 0
    for c in r["checks"]:
        if c["weight"] < FINDING_WEIGHT:
            continue
        fid = "seo-%s-%s" % (pslug, c["id"])

        def build(c=c):
            return ledger.new_finding(SKILL, "%s on %s" % (c["problem"], page), c["severity"], id=fid,
                                      category="seo", location=location, evidence=[rel], fix=c["fix"],
                                      description="Check '%s' (%d points) failed. Found: %s."
                                      % (c["title"], c["weight"], c["found"] or c["problem"]))
        note = "computed by seo_score.py from %s: %s. The parse is the proof; re-run 'seo_score.py page' to see it." % (rel, c["problem"] or c["found"])
        if _sync_finding(a.dir, fid, c["status"] == "fail", build, note):
            written += 1
    tech_by = dict((c["id"], c) for c in tech)
    for cid, fid in (("robots-open", "seo-robots-blocks-all"), ("sitemap-valid", "seo-sitemap-invalid"),
                     ("sitemap-covers-pages", "seo-%s-not-in-sitemap" % pslug)):
        c = tech_by[cid]
        if c["status"] == "na":
            continue  # not checked this run; leave whatever an earlier run recorded
        src = tech_rel["robots"] if cid == "robots-open" else tech_rel["sitemap"]

        def build(c=c, src=src):
            return ledger.new_finding(SKILL, c["problem"], c["severity"], id=fid, category="seo",
                                      location=src, fix=c["fix"], evidence=[src],
                                      description="Check '%s' failed. Found: %s." % (c["title"], c["found"] or c["problem"]))
        note = "computed by seo_score.py from %s: %s" % (src, c["problem"] or c["found"])
        if _sync_finding(a.dir, fid, c["status"] == "fail", build, note):
            written += 1

    # Scores: one entry per recorded page, and seo_score as the lowest of them.
    metrics = {}
    for s in ledger.load_all_scores(a.dir):
        if s["skill"] == SKILL:
            metrics = dict((m["id"], m) for m in s["metrics"])
    n = _merge_metric(metrics, "seo_score", pslug, r["score"], BUDGET,
                      "seo_score.py page checks on %s (%d of %d applicable points)" % (page, r["earned"], r["applicable"]), [rel])
    if lh:
        _merge_metric(metrics, "lighthouse_seo", pslug, lh["score"], LIGHTHOUSE_BUDGET,
                      "Lighthouse %s SEO category, saved report" % (lh["version"] or "(version not stated)"), [lh_rel])
    ledger.write_scores(a.dir, SKILL, [metrics[k] for k in sorted(metrics)])
    print("%s  seo_score %d (budget >= %d)%s; coverage written for %d checks; %d finding(s) this run."
          % (ledger.OK if r["score"] >= BUDGET else ledger.BAD, r["score"], BUDGET,
             (", lowest of %d recorded pages: %d" % (n, metrics["seo_score"]["value"])) if n > 1 else "",
             len(checks), written))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("checks", help="list the checks and their weights").set_defaults(func=cmd_checks)

    pg = sub.add_parser("page", help="score one saved HTML page")
    pg.add_argument("page", help="a saved .html file")
    pg.add_argument("--url", help="the page's URL (used to tell internal links from external)")
    pg.add_argument("--lighthouse", help="a saved Lighthouse JSON report for the same page")
    pg.add_argument("--json", action="store_true")
    pg.set_defaults(func=cmd_page)

    st = sub.add_parser("site", help="score every saved page plus robots.txt and the sitemap")
    st.add_argument("--pages", help="folder of saved .html pages (default: <dir>/evidence/proof-seo)")
    st.add_argument("--robots", help="saved robots.txt (default: robots.txt next to the pages, when present)")
    st.add_argument("--sitemap", help="saved sitemap.xml (default: sitemap.xml next to the pages, when present)")
    st.add_argument("--url", help="the site's URL (used to tell internal links from external)")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_site)

    rc = sub.add_parser("record", help="score one page and write coverage, score and findings to the ledger")
    rc.add_argument("page", help="a saved .html file")
    rc.add_argument("--url")
    rc.add_argument("--lighthouse", help="a saved Lighthouse JSON report; folds its SEO score in as lighthouse_seo")
    rc.add_argument("--robots", help="saved robots.txt to check alongside the page")
    rc.add_argument("--sitemap", help="saved sitemap.xml to check alongside the page")
    rc.set_defaults(func=cmd_record)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
