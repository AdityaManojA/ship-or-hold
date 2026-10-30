#!/usr/bin/env python3
"""Score how readable and citable saved web pages are to AI search engines and assistants.

Standard library only, no network. Everything is measured on files the user saved:
the server HTML of a page, and the site's robots.txt, llms.txt and sitemap.xml.
Each signal traces to something countable in the file, and every weight is written
down in scoring.md, so the score can be reproduced by hand.

    python3 geo_score.py page saved/pricing.html [--json]
    python3 geo_score.py access --robots saved/robots.txt --llms saved/llms.txt [--sitemap saved/sitemap.xml]
    python3 geo_score.py record saved/pricing.html --robots saved/robots.txt --llms saved/llms.txt
    python3 geo_score.py record saved/about.html --name about --budget 75
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-geo"
DEFAULT_BUDGET = "70"

# (id, title, weight). The weights add up to 100; scoring.md explains each one.
SIGNALS = [
    ("topic", "One clear topic: title and h1 agree", 12),
    ("passages", "Self-contained passages that state a fact", 18),
    ("structured", "Structured data (ld+json) with a type", 12),
    ("eeat", "Author, organisation and date signals", 14),
    ("headings", "Subheadings that read as questions or topics", 10),
    ("density", "Factual density: numbers and named entities", 12),
    ("text", "Real text in the HTML, not an empty shell", 12),
    ("stable", "Primary content present without running scripts", 10),
]
WEIGHT = {sid: w for sid, _, w in SIGNALS}
ACCESS_CHECKS = [
    ("ai-crawlers", "robots.txt: which AI crawlers may fetch the site"),
    ("llms-txt", "llms.txt: present and well-formed"),
]

# User-agent tokens of crawlers run by AI search engines and assistants.
# "answers": fetches pages to answer a question, which is what produces a citation.
# "training": collects pages to train models; blocking it does not stop citation.
AI_CRAWLERS = [
    ("OAI-SearchBot", "answers", ""),
    ("ChatGPT-User", "answers", ""),
    ("Claude-SearchBot", "answers", ""),
    ("Claude-User", "answers", ""),
    ("PerplexityBot", "answers", ""),
    ("Perplexity-User", "answers", ""),
    ("DuckAssistBot", "answers", ""),
    ("YouBot", "answers", ""),
    ("Googlebot", "answers", "also the web search crawler"),
    ("Bingbot", "answers", "also the web search crawler"),
    ("GPTBot", "training", ""),
    ("ClaudeBot", "training", ""),
    ("anthropic-ai", "training", "older token"),
    ("Google-Extended", "training", "a control token, not a separate crawler"),
    ("Applebot-Extended", "training", "a control token, not a separate crawler"),
    ("CCBot", "training", "open crawl used by many training sets"),
    ("Bytespider", "training", ""),
    ("meta-externalagent", "training", ""),
    ("Amazonbot", "training", ""),
]

TRADE_OFF = ("Blocking a crawler keeps that company from reading the site; it also means that "
             "company's assistant cannot fetch or cite it. Which crawlers to allow is a business "
             "choice. This tool reports the state and does not decide it.")

# ------------------------------------------------------------- html parsing

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK_TAGS = HEADING_TAGS | {"p", "div", "section", "article", "main", "header", "footer",
                             "aside", "nav", "ul", "ol", "li", "table", "tr", "td", "th",
                             "blockquote", "pre", "figure", "figcaption", "form", "br", "hr",
                             "dl", "dt", "dd", "body", "summary", "details"}
VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "source", "track", "wbr", "area",
             "base", "col", "embed", "param"}
SHELL_IDS = {"root", "app", "__next", "__nuxt", "___gatsby", "app-root", "main-app", "svelte"}
DATE_META = ("article:published_time", "article:modified_time", "date", "dc.date",
             "dcterms.created", "dcterms.modified", "last-modified", "publish-date", "pubdate")
JS_WARNING = re.compile(r"(enable|requires?|turn on|need|needs|without) javascript|"
                        r"javascript (is |must be |to be )?(required|disabled|enabled|needed)", re.I)


def _clean(text):
    return re.sub(r"\s+", " ", text).strip()


class _Page(HTMLParser):
    """Collect the parts of a page the signals are measured on."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.title = None
        self.headings = []        # (level, text)
        self.blocks = []          # visible text blocks (paragraphs, list items, cells)
        self.text_parts = []      # every visible text chunk, in order
        self.ld_json = []         # raw ld+json strings
        self.meta = []            # (name or property, content)
        self.time_datetime = False
        self.author_marker = False
        self.noscript_text = []
        self.script_tags = 0
        self.script_chars = 0
        self.shell = None         # {"id", "chars"} for a client-render root such as <div id="root">
        self._stack = []
        self._skip = {"noscript": 0, "template": 0, "svg": 0, "head": 0}
        self._script = None       # type of the open <script>, or None
        self._style = False
        self._ld_buf = None
        self._title_buf = None
        self._block = []
        self._heading_level = None
        self._shell_depth = None

    # -- helpers
    def _skipping(self):
        return any(self._skip.values())

    def _flush(self):
        text = _clean(" ".join(self._block))
        self._block = []
        if not text:
            return
        if self._heading_level is not None:
            self.headings.append((self._heading_level, text))
        else:
            self.blocks.append(text)

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = {}
        for k, v in attrs:
            a[k.lower()] = v or ""
        if tag == "script":
            self._script = (a.get("type") or "text/javascript").strip().lower()
            self.script_tags += 1
            if self._script == "application/ld+json":
                self._ld_buf = []
            return
        if tag == "style":
            self._style = True
            return
        if tag == "body":
            self._skip["head"] = 0
        if tag in self._skip:
            self._skip[tag] += 1
        if tag == "title":
            if self.title is None and not self._skip["svg"]:
                self._title_buf = []
            return
        if tag == "meta":
            name = (a.get("name") or a.get("property") or a.get("itemprop") or "").strip().lower()
            if name:
                self.meta.append((name, a.get("content", "").strip()))
            return
        if tag == "time" and a.get("datetime", "").strip():
            self.time_datetime = True
        if "author" in a.get("rel", "").lower() or "author" in a.get("itemprop", "").lower():
            self.author_marker = True
        cls = a.get("class", "").lower()
        if "author" in cls or "byline" in cls:
            self.author_marker = True
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADING_TAGS:
                self._heading_level = int(tag[1])
        if tag in VOID_TAGS:
            return
        self._stack.append(tag)
        if self.shell is None and a.get("id", "").strip().lower() in SHELL_IDS:
            self.shell = {"id": a["id"].strip(), "chars": 0}
            self._shell_depth = len(self._stack)

    def handle_endtag(self, tag):
        if tag == "script":
            if self._ld_buf is not None:
                self.ld_json.append("".join(self._ld_buf))
                self._ld_buf = None
            self._script = None
            return
        if tag == "style":
            self._style = False
            return
        if tag == "title":
            if self._title_buf is not None:
                self.title = _clean(" ".join(self._title_buf))
                self._title_buf = None
            return
        if tag in self._skip and self._skip[tag] > 0:
            self._skip[tag] -= 1
        if tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADING_TAGS:
                self._heading_level = None
        if tag in self._stack:
            while self._stack:
                top = self._stack.pop()
                if self._shell_depth is not None and len(self._stack) < self._shell_depth:
                    self._shell_depth = None
                if top == tag:
                    break

    def handle_data(self, data):
        if self._script is not None:
            self.script_chars += len(data)
            if self._ld_buf is not None:
                self._ld_buf.append(data)
            return
        if self._style:
            return
        if self._title_buf is not None:
            self._title_buf.append(data)
            return
        if self._skip["noscript"]:
            self.noscript_text.append(data)
            return
        if self._skipping() or not data.strip():
            return
        self.text_parts.append(data)
        self._block.append(data)
        if self._shell_depth is not None:
            self.shell["chars"] += len(data.strip())

    def close(self):
        HTMLParser.close(self)
        self._flush()


def parse_html(text):
    p = _Page()
    p.feed(text)
    p.close()
    return p


# ---------------------------------------------------------- page signals

STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
        "your", "you", "our", "we", "by", "at", "from", "it", "this", "that", "vs"}
PRONOUN_START = {"it", "this", "that", "these", "those", "they", "he", "she", "we", "here", "there"}
PITCH = ["best", "leading", "amazing", "revolutionary", "seamless", "effortless", "unlock",
         "supercharge", "world-class", "cutting-edge", "game-changing", "next-generation",
         "award-winning", "sign up", "get started", "book a demo", "free trial", "try it free",
         "limited time", "trusted by", "join thousands", "start today", "start now", "learn more"]
QUESTION_WORDS = {"how", "what", "why", "when", "where", "which", "who", "can", "does", "do",
                  "is", "are", "should", "will"}
NUMBER = re.compile(r"(?<![A-Za-z])[$\u20ac\u00a3]?\d[\d,.]*%?")
ENTITY = re.compile(r"(?<=[a-z,;]\s)[A-Z][A-Za-z0-9&'-]+")
DEFINES = re.compile(r"\b(?:is|are|was|were) (?:a|an|the)\b|\b(?:means|refers to|consists of|"
                     r"is defined as|is measured)\b")


def _tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP}


def _title_segments(title):
    # Split a title on common separators (pipe, colon, dash, middle dot, en/em dash).
    # The non-ASCII separators are written as escapes so this source stays ASCII.
    return [s for s in re.split(r"\s+[|:\-]+\s+|\s+[\u00b7\u2013\u2014]\s+", title or "") if s.strip()]


def _agreement(title, h1):
    """How much the page title and the h1 share: 0 to 1, best over the title's segments."""
    b = _tokens(h1)
    best = 0.0
    for seg in _title_segments(title) or [title or ""]:
        a = _tokens(seg)
        if a and b:
            best = max(best, len(a & b) / float(min(len(a), len(b))))
    return round(best, 2)


def _is_passage(text):
    words = text.split()
    if len(words) < 40:
        return False
    first = re.sub(r"[^a-z]", "", words[0].lower())
    if first in PRONOUN_START:
        return False
    low = text.lower()
    pitch = sum(low.count(p) for p in PITCH)
    if pitch >= 2 or (pitch and len(words) < 60):
        return False
    return bool(NUMBER.search(text) or ENTITY.search(text) or DEFINES.search(text))


def _heading_quality(text):
    words = text.split()
    if not words:
        return 0.0
    if text.endswith("?") or words[0].lower().strip(":,") in QUESTION_WORDS:
        return 1.0
    if 2 <= len(words) <= 14:
        return 1.0
    return 0.5


def _walk_json(obj, found):
    """Collect @type values and the keys that carry author, publisher and date signals."""
    if isinstance(obj, dict):
        t = obj.get("@type")
        for item in (t if isinstance(t, list) else [t]):
            if isinstance(item, str) and item.strip():
                found["types"].append(item.strip())
        for key in ("author", "publisher", "datePublished", "dateModified", "dateCreated"):
            if key in obj and obj[key] not in (None, "", [], {}):
                found["keys"].add(key)
        for v in obj.values():
            _walk_json(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_json(v, found)


def _clamp(x):
    return max(0.0, min(1.0, x))


def analyse_page(html_text, name):
    """Measure every signal on one page. Returns a dict with signals, facts and the score."""
    p = parse_html(html_text)
    body = _clean(" ".join(p.text_parts))
    words = body.split()
    nwords = len(words)
    h1s = [t for lvl, t in p.headings if lvl == 1]
    subs = [t for lvl, t in p.headings if 2 <= lvl <= 4]
    meta = {}
    for k, v in p.meta:
        meta.setdefault(k, v)

    signals = []

    def add(sid, share, detail):
        share = round(_clamp(share), 3)
        title = next(t for s, t, _ in SIGNALS if s == sid)
        signals.append({"id": sid, "title": title, "weight": WEIGHT[sid], "share": share,
                        "points": round(WEIGHT[sid] * share, 1), "detail": detail})

    # topic: a title, exactly one h1, and the two agree
    share = 0.25 if p.title else 0.0
    share += 0.25 if len(h1s) == 1 else (0.1 if h1s else 0.0)
    overlap = _agreement(p.title, h1s[0]) if (p.title and h1s) else 0.0
    share += 0.5 if overlap >= 0.6 else (0.25 if overlap >= 0.3 else 0.0)
    if not p.title:
        detail = "no <title>"
    elif not h1s:
        detail = "no h1"
    else:
        detail = "title and h1 %s (overlap %.2f)%s" % (
            "agree" if overlap >= 0.6 else ("partly agree" if overlap >= 0.3 else "disagree"), overlap,
            "" if len(h1s) == 1 else "; %d h1 elements, one is clearer" % len(h1s))
    add("topic", share, detail)

    # passages: blocks of 40+ words that state a fact and are not a pitch
    passages = [b for b in p.blocks if _is_passage(b)]
    add("passages", min(len(passages), 3) / 3.0,
        "%d self-contained passage(s) of 40+ words with a fact in them (3 for full marks)" % len(passages))

    # structured data
    found = {"types": [], "keys": set()}
    invalid = 0
    for raw in p.ld_json:
        try:
            _walk_json(json.loads(raw), found)
        except ValueError:
            invalid += 1
    types = sorted(set(found["types"]))
    if not p.ld_json:
        share, detail = 0.0, "no ld+json block"
    elif types:
        share, detail = 1.0, "ld+json types: %s" % ", ".join(types)
    elif invalid == len(p.ld_json):
        share, detail = 0.25, "%d ld+json block(s), none is valid JSON" % invalid
    else:
        share, detail = 0.5, "ld+json present but no @type"
    add("structured", share, detail)

    # author / organisation / date
    author = bool(meta.get("author")) or p.author_marker or "author" in found["keys"]
    org = ("publisher" in found["keys"] or "Organization" in types or bool(meta.get("og:site_name"))
           or bool(meta.get("publisher")))
    date = (p.time_datetime or any(meta.get(k) for k in DATE_META)
            or bool(found["keys"] & {"datePublished", "dateModified", "dateCreated"}))
    have = [n for n, ok in (("author", author), ("organisation", org), ("date", date)) if ok]
    missing = [n for n, ok in (("author", author), ("organisation", org), ("date", date)) if not ok]
    add("eeat", len(have) / 3.0, "present: %s; missing: %s" % (", ".join(have) or "none", ", ".join(missing) or "none"))

    # subheadings
    if subs:
        quality = sum(_heading_quality(t) for t in subs) / len(subs)
        questions = sum(1 for t in subs if _heading_quality(t) == 1.0 and
                        (t.endswith("?") or t.split()[0].lower().strip(":,") in QUESTION_WORDS))
        share = quality * min(1.0, len(subs) / 2.0)
        detail = "%d subheading(s), %d read as a question, clarity %.2f" % (len(subs), questions, quality)
    else:
        share, detail = 0.0, "no h2-h4 subheadings"
    add("headings", share, detail)

    # factual density
    numbers = len(NUMBER.findall(body))
    entities = len(ENTITY.findall(body))
    per100 = (lambda n: round(100.0 * n / nwords, 2)) if nwords else (lambda n: 0.0)
    add("density", 0.5 * min(1.0, per100(numbers) / 2.0) + 0.5 * min(1.0, per100(entities) / 2.0),
        "%.1f numbers and %.1f named entities per 100 words (2 of each for full marks)" % (per100(numbers), per100(entities)))

    # real text
    add("text", (nwords - 50) / 250.0, "%d visible words (300 for full marks, under 50 is an empty shell)" % nwords)

    # stable primary content without scripts
    content = any(len(b.split()) >= 20 for b in p.blocks)
    shell_empty = bool(p.shell) and p.shell["chars"] < 100
    warning = bool(JS_WARNING.search(" ".join(p.noscript_text)))
    share = (0.5 if content else 0.0) + (0.0 if shell_empty else 0.25) + (0.0 if warning else 0.25)
    notes = []
    notes.append("a 20+ word block is in the HTML" if content else "no 20+ word block in the HTML")
    if shell_empty:
        notes.append("empty client-render root #%s" % p.shell["id"])
    if warning:
        notes.append("noscript says JavaScript is required")
    add("stable", share, "; ".join(notes))

    total = sum(s["points"] for s in signals)
    return {
        "name": name,
        "score": int(total + 0.5),
        "signals": signals,
        "facts": {
            "title": p.title, "h1": h1s, "subheadings": len(subs), "words": nwords,
            "blocks": len(p.blocks), "passages": len(passages), "ld_json_blocks": len(p.ld_json),
            "ld_json_invalid": invalid, "ld_json_types": types, "author": author,
            "organisation": org, "date": date, "numbers_per_100": per100(numbers),
            "entities_per_100": per100(entities), "script_tags": p.script_tags,
            "script_chars": p.script_chars, "shell": p.shell, "noscript_warning": warning,
        },
    }


def weak_signals(page):
    """Signals under half marks. Severity: medium under a quarter, low otherwise."""
    out = []
    for s in page["signals"]:
        if s["share"] < 0.5:
            out.append((s, "medium" if s["share"] < 0.25 else "low"))
    return out


# ---------------------------------------------------------- robots / llms

def parse_robots(text):
    """Return {ua_lower: {"disallow": [...], "allow": [...]}} from robots.txt."""
    groups = {}
    current = []
    expecting_ua = True
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if not expecting_ua:
                current = []
            ua = value.lower()
            groups.setdefault(ua, {"disallow": [], "allow": []})
            current.append(ua)
            expecting_ua = True
        elif field in ("disallow", "allow") and current:
            expecting_ua = False
            for ua in current:
                groups[ua][field].append(value)
    return groups


def crawler_allowed(groups, token):
    """Is a crawler whose UA contains token allowed to fetch '/'? (True when nothing blocks it.)"""
    tok = token.lower()
    group = None
    # Prefer an exact or substring UA match over the catch-all '*'.
    for ua in groups:
        if ua != "*" and (ua == tok or tok in ua or ua in tok):
            group = groups[ua]
            break
    if group is None:
        group = groups.get("*")
    if group is None:
        return True  # no matching group means no restriction
    # Blocked only when the root is disallowed and not re-allowed.
    blocks_root = any(d == "/" for d in group["disallow"])
    allows_root = any(a == "/" for a in group["allow"])
    return not blocks_root or allows_root


def analyse_access(robots_text, llms_text):
    """Report which AI crawlers may fetch the site, and whether llms.txt helps."""
    result = {"robots_present": robots_text is not None, "crawlers": [], "llms": None}
    groups = parse_robots(robots_text) if robots_text is not None else {}
    for token, kind, note in AI_CRAWLERS:
        allowed = crawler_allowed(groups, token) if robots_text is not None else True
        result["crawlers"].append({"name": token, "kind": kind, "note": note, "allowed": allowed})
    answers = [c for c in result["crawlers"] if c["kind"] == "answers"]
    result["answers_allowed"] = sum(1 for c in answers if c["allowed"])
    result["answers_total"] = len(answers)
    if llms_text is not None:
        has_title = bool(re.search(r"^\s*#\s+\S", llms_text, re.M))
        links = re.findall(r"\[[^\]]+\]\([^)]+\)", llms_text)
        descriptions = [ln for ln in llms_text.splitlines() if ":" in ln and "](" in ln]
        wellformed = has_title and len(links) >= 1
        result["llms"] = {"present": True, "wellformed": wellformed, "title": has_title,
                          "links": len(links), "described": len(descriptions)}
    else:
        result["llms"] = {"present": False, "wellformed": False, "title": False, "links": 0, "described": 0}
    return result


# ------------------------------------------------------------- rendering

def _render_page(page):
    L = ["GEO citability of %s: %d/100" % (page["name"], page["score"]), ""]
    for s in page["signals"]:
        mark = ledger.OK if s["share"] >= 0.5 else ledger.BAD
        L.append("  %s %-11s %4.1f/%-2d  %s" % (mark, s["id"], s["points"], s["weight"], s["detail"]))
    return "\n".join(L)


def _render_access(acc):
    L = ["AI crawler access (from robots.txt):" if acc["robots_present"] else
         "No robots.txt given; assuming every crawler is allowed.", ""]
    L.append("  Answer crawlers allowed: %d of %d (these are the ones that can cite you)"
             % (acc["answers_allowed"], acc["answers_total"]))
    for c in acc["crawlers"]:
        mark = ledger.OK if c["allowed"] else ledger.BAD
        extra = (" - " + c["note"]) if c["note"] else ""
        L.append("  %s %-20s %-9s%s" % (mark, c["name"], c["kind"], extra))
    llms = acc["llms"]
    if llms["present"]:
        L.append("")
        L.append("  llms.txt: present, %s (%d link(s), %d described)"
                 % ("well-formed" if llms["wellformed"] else "missing a title or links",
                    llms["links"], llms["described"]))
    else:
        L.append("")
        L.append("  llms.txt: not provided")
    L.append("")
    L.append("  " + TRADE_OFF)
    return "\n".join(L)


# ------------------------------------------------------------- commands

def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def cmd_page(a):
    page = analyse_page(_read(a.path), a.name or os.path.basename(a.path))
    if a.json:
        print(json.dumps(page, indent=2))
    else:
        print(_render_page(page))
    return 0


def cmd_access(a):
    robots = _read(a.robots) if a.robots else None
    llms = _read(a.llms) if a.llms else None
    acc = analyse_access(robots, llms)
    if a.json:
        print(json.dumps(acc, indent=2))
    else:
        print(_render_access(acc))
    return 0


def _ensure_under_proof(d, path):
    """Copy a page into proof/evidence/proof-geo/ if it is not already under proof/."""
    base = ledger.root(d)
    ap = os.path.abspath(path)
    try:
        if os.path.commonpath([ap, base]) == base:
            return os.path.relpath(ap, base).replace(os.sep, "/")
    except ValueError:
        pass
    ev = ledger.evidence_dir(d, SKILL)
    import shutil
    dest = os.path.join(ev, os.path.basename(path))
    shutil.copy2(path, dest)
    return os.path.relpath(dest, base).replace(os.sep, "/")


def cmd_record(a):
    page = analyse_page(_read(a.path), a.name or os.path.splitext(os.path.basename(a.path))[0])
    rel = _ensure_under_proof(a.dir, a.path)
    budget = num.parse_number(a.budget, field="budget")

    # Coverage: one check per signal (covered, since it was measured), plus access.
    checks = [(s["id"], s["title"]) for s in page["signals"]]
    acc = None
    if a.robots or a.llms:
        checks += list(ACCESS_CHECKS)
        acc = analyse_access(_read(a.robots) if a.robots else None,
                             _read(a.llms) if a.llms else None)
    ledger.init_coverage(a.dir, SKILL, checks)
    for s in page["signals"]:
        ledger.mark_coverage(a.dir, SKILL, s["id"], "covered", evidence=[rel],
                             note="%.1f/%d - %s" % (s["points"], s["weight"], s["detail"]))
    if acc is not None:
        ledger.mark_coverage(a.dir, SKILL, "ai-crawlers", "covered", evidence=[rel],
                             note="%d of %d answer crawlers allowed" % (acc["answers_allowed"], acc["answers_total"]))
        if acc["llms"]["present"]:
            ledger.mark_coverage(a.dir, SKILL, "llms-txt", "covered", evidence=[rel],
                                 note="well-formed" if acc["llms"]["wellformed"] else "present but missing a title or links")
        else:
            ledger.mark_coverage(a.dir, SKILL, "llms-txt", "not_applicable", note="no llms.txt provided")

    ledger.write_scores(a.dir, SKILL, [{"id": "geo_citability", "value": page["score"], "unit": "points",
                                        "budget": budget, "op": ">=", "source": "geo_score.py",
                                        "evidence": [rel]}])

    # Weak signals become findings, verified by the computed note.
    written = 0
    for s, severity in weak_signals(page):
        fid = ledger.slug("geo-%s-%s" % (page["name"], s["id"]))
        f = ledger.new_finding(SKILL, "Weak citability signal: %s" % s["title"], severity,
                               id=fid, category="geo", location=rel, evidence=[rel],
                               fix="Improve this signal; see scoring.md. Computed: %s" % s["detail"])
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="manual", note=s["detail"])
        written += 1
    mark = ledger.OK if page["score"] >= budget else ledger.BAD
    print("%s %s scored %d/100 (budget %d). %d weak-signal finding(s) recorded. Evidence: %s"
          % (mark, page["name"], page["score"], int(budget), written, rel))
    if acc is not None and acc["answers_allowed"] < acc["answers_total"]:
        print("Note: %d of %d answer crawlers are blocked in robots.txt. %s"
              % (acc["answers_total"] - acc["answers_allowed"], acc["answers_total"], TRADE_OFF))
    return 0


def cmd_status(a):
    scores = [s for s in ledger.load_all_scores(a.dir) if s["skill"] == SKILL]
    if not scores:
        print("no GEO record yet. Run: geo_score.py record <page.html>")
        return 0
    for m in scores[0]["metrics"]:
        print("  %s: %s %s (budget %s %s)" % (m["id"], m["value"], m["unit"], m["op"], m["budget"]))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("page", help="score one saved HTML page")
    p.add_argument("path")
    p.add_argument("--name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_page)
    ac = sub.add_parser("access", help="check AI-crawler access and llms.txt")
    ac.add_argument("--robots")
    ac.add_argument("--llms")
    ac.add_argument("--json", action="store_true")
    ac.set_defaults(func=cmd_access)
    r = sub.add_parser("record", help="write the citability score and findings to the ledger")
    r.add_argument("path")
    r.add_argument("--name")
    r.add_argument("--budget", default=DEFAULT_BUDGET)
    r.add_argument("--robots")
    r.add_argument("--llms")
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded scores and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
