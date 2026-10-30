#!/usr/bin/env python3
"""Find the tells of machine-made filler in a project's copy, UI and code. Standard library only.

Walks a folder (or reads single files) and reports wording, styling and code habits
that make a product read as generated rather than made: hype words, invented-looking
numbers, placeholder text left in, stock gradients and glass effects, banner
comments, comments that repeat the code, swallowed errors and assistant chatter left
in the source. Every hit carries a file and line, so each one can be checked.

    python3 slop.py scan src/
    python3 slop.py scan src/ --json
    python3 slop.py record src/ --budget 70
    python3 slop.py status

A line containing "slop: ignore" is skipped.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-slop"

MARKUP = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".astro"}
PROSE = {".md", ".mdx", ".txt"}
STYLE = {".css", ".scss", ".sass", ".less"}
CODE = {".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".go", ".rb", ".java", ".rs", ".php", ".cs",
        ".kt", ".swift", ".vue", ".svelte"}
SKIP_DIRS = {".git", "node_modules", "dist", "build", "out", ".next", ".nuxt", "vendor", "__pycache__", "venv",
             ".venv", "proof", "coverage", ".cache", "target", "bin", "obj"}
MAX_BYTES = 1024 * 1024
IGNORE = "slop: ignore"

# (id, title, group, severity, fix)
CHECKS = [
    ("hype", "Hype words in the copy", "copy", "low",
     "Say what the product does in plain words; cut adjectives that any product could claim."),
    ("dashes", "Em dashes in the copy", "copy", "low",
     "Use a full stop, comma or colon; long dash chains read as generated."),
    ("emoji", "Emoji used as decoration", "copy", "low",
     "Drop emoji from headings, buttons, bullets and log lines; use words or a real icon."),
    ("contrast-frame", "\"Not just X, it's Y\" framing", "copy", "low",
     "State the one thing it is."),
    ("claims", "Numbers that look invented", "copy", "medium",
     "Back each number with a source you can show, or remove it."),
    ("placeholders", "Placeholder text left in", "copy", "medium",
     "Replace stand-in names, lorem ipsum and example addresses with the real content."),
    ("gradients", "Stock purple-to-blue gradients", "ui", "low",
     "Pick colours from the brand, not the default violet-to-blue ramp."),
    ("gradient-text", "Gradient-filled headline text", "ui", "low",
     "Set headlines in a solid colour; gradient text is a stock look and hurts contrast."),
    ("glass", "Frosted-glass panels everywhere", "ui", "low",
     "Keep blur for one surface that needs it, if any."),
    ("stock-type", "Default stock typeface as the only voice", "ui", "low",
     "Choose type for the product, or pair the default with a face that carries the brand."),
    ("banners", "Banner comments", "code", "low",
     "Delete divider banners; a short heading comment, or none, is enough."),
    ("echo-comments", "Comments that repeat the code", "code", "low",
     "Delete comments that restate the next line; keep the ones that say why."),
    ("swallowed-errors", "Errors caught and ignored", "code", "medium",
     "Handle the error, log it with context, or let it propagate."),
    ("assistant-chatter", "Assistant chatter left in the source", "any", "medium",
     "Remove the conversational leftovers; they were written to a person, not for the product."),
]
CHECK_BY_ID = dict((c[0], c) for c in CHECKS)
WEIGHT = {"low": 3, "medium": 8}

HYPE = re.compile(
    r"\b(revolutioni[sz]e\w*|revolutionary|game[- ]chang\w+|cutting[- ]edge|next[- ]gen(?:eration)?|"
    r"seamless(?:ly)?|supercharg\w+|unleash\w*|unlock the (?:full )?power|harness the power|"
    r"elevate your|empower\w*|effortless(?:ly)?|world[- ]class|best[- ]in[- ]class|state[- ]of[- ]the[- ]art|"
    r"synerg\w+|blazing(?:ly)?[- ]fast|lightning[- ]fast|delve|tapestry|in today's fast[- ]paced|"
    r"look no further|to the next level|reimagin\w+|paradigm shift|10x your)\b", re.I)
EM_DASH = "\u2014"
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27bf\u2b50\u2b06\u2194-\u21aa\u2705\u274c\u2728]")
CONTRAST = re.compile(r"\b(?:is|are|isn't|aren't|it's|this is)\s+(?:not\s+)?(?:just|only|merely)\s+[^.!?]{1,60}?"
                      r"[,;:\u2014-]\s*(?:it's|it is|they're|this is|but)\b", re.I)
CLAIMS = [
    (re.compile(r"\b\d{1,3}(?:,\d{3})+\s?%|\b[1-9]\d{3,}\s?%"), "a percentage above 100"),
    (re.compile(r"\b0\.0+\d+\s?(?:ms|milliseconds?|s|seconds?)\b", re.I), "an implausibly precise time"),
    (re.compile(r"\b99\.9{2,}\s?%"), "a five-nines figure"),
    (re.compile(r"\b\d{2,}x\s+(?:faster|better|more|cheaper|quicker)\b", re.I), "a large multiple"),
    (re.compile(r"\b(?:trusted|loved|used)\s+by\s+\d[\d,.]*\s?[kKmM+]*\s+(?:teams|companies|developers|users|people|customers)\b",
                re.I), "a user-count boast"),
]
PLACEHOLDER = re.compile(r"\blorem ipsum\b|\bdolor sit amet\b|\bjohn doe\b|\bjane doe\b|\bacme(?: inc| corp)?\b|"
                         r"\byour company(?: name)?\b|\bcompany name\b|\[your [a-z ]+\]|\b[\w.]+@example\.(?:com|org)\b|"
                         r"\b(?:555)-\d{4}\b|\binsert (?:text|image|copy) here\b", re.I)
GRADIENT_CSS = re.compile(r"linear-gradient\([^)]*\)", re.I)
VIOLET_HEX = re.compile(r"#(?:8b5cf6|7c3aed|6d28d9|a855f7|9333ea|6366f1|4f46e5|818cf8|a78bfa|c084fc|764ba2|667eea)\b", re.I)
BLUE_PINK_HEX = re.compile(r"#(?:3b82f6|2563eb|06b6d4|0ea5e9|ec4899|db2777|f472b6|60a5fa|22d3ee|667eea|764ba2)\b", re.I)
TW_GRADIENT = re.compile(r"\bfrom-(?:purple|violet|indigo|fuchsia)-\d{2,3}\b[^\"'`]*\bto-(?:blue|pink|cyan|sky|purple|indigo)-\d{2,3}\b")
GRADIENT_TEXT = re.compile(r"background-clip\s*:\s*text|\bbg-clip-text\b", re.I)
GLASS = re.compile(r"backdrop-filter\s*:\s*blur|\bbackdrop-blur(?:-\w+)?\b", re.I)
STOCK_FONT = re.compile(r"font-family\s*:\s*['\"]?(Inter|Poppins)['\"]?\s*(?:,\s*(?:sans-serif|system-ui|-apple-system|"
                        r"BlinkMacSystemFont|['\"]?Segoe UI['\"]?|Roboto|Helvetica(?: Neue)?|Arial)\s*)*[;}\"']", re.I)
BANNER = re.compile(r"^\s*(?:#|//|/\*|\*|--|;)\s*([=\-#*~_+\u2500-\u257f])\1{9,}")
BANNER_BOX = re.compile(r"[\u2500-\u257f]{6,}")
COMMENT = re.compile(r"^\s*(?:#|//)\s*(.+?)\s*$")
SWALLOW = [
    re.compile(r"^\s*except(?:\s+\w[\w.]*(?:\s+as\s+\w+)?)?\s*:\s*(?:pass|\.\.\.)\s*$"),
    re.compile(r"\bcatch\s*(?:\(\s*\w*\s*\))?\s*\{\s*\}"),
    re.compile(r"\.catch\(\s*\(\s*\w*\s*\)\s*=>\s*\{\s*\}\s*\)"),
    re.compile(r"\bif\s+err\s*!=\s*nil\s*\{\s*\}"),
]
SWALLOW_NEXT = re.compile(r"^\s*except(?:\s+\w[\w.]*(?:\s+as\s+\w+)?)?\s*:\s*$")
CHATTER = re.compile(r"\bas an ai(?: language model)?\b|\bi hope this helps\b|\bcertainly!\s|\bhere(?:'s| is) the (?:updated|revised|complete) "
                     r"(?:code|version|file)\b|\blet me know if you(?:'d like| need| want)\b|\bgreat question\b", re.I)
STOP = {"the", "a", "an", "to", "of", "and", "or", "for", "in", "on", "with", "this", "that", "is", "it", "we", "set",
        "get", "new", "now", "then", "here", "by", "from", "as", "be", "our", "its", "add", "create", "return", "call"}


class _Text(HTMLParser):
    """Visible text of a markup file, with the line each piece starts on."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "code", "pre"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "code", "pre") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append((self.getpos()[0], data))


def _copy_lines(ext, text):
    """[(line, text)] of the words a visitor reads."""
    if ext in PROSE:
        out, fence = [], False
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                fence = not fence
                continue
            if not fence:
                out.append((n, line))
        return out
    if ext in MARKUP:
        p = _Text()
        try:
            p.feed(text)
            p.close()
        except Exception:  # malformed markup: fall back to the raw lines
            return list(enumerate(text.splitlines(), 1))
        out = []
        for start, data in p.parts:
            for k, line in enumerate(data.split("\n")):
                # Skip what is plainly code inside a component file.
                if line.strip() and not re.search(r"[{};=]\s*$|^\s*(import|export|const|let|return)\b", line):
                    out.append((start + k, line))
        return out
    return []


def _words(s):
    return [w for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in STOP]


def _echoes(comment, code):
    words = _words(comment)
    if not 1 <= len(words) <= 5 or not code.strip() or COMMENT.match(code):
        return False
    flat = re.sub(r"[^a-z]", "", code.lower())
    return all(w.rstrip("s") in flat for w in words)


def scan_text(path, text):
    """Return {check_id: [(line, excerpt)]} for one file."""
    ext = os.path.splitext(path)[1].lower()
    hits = {}

    def add(cid, n, s):
        hits.setdefault(cid, []).append((n, " ".join(s.split())[:100]))

    lines = text.splitlines()
    ignored = set(n for n, l in enumerate(lines, 1) if IGNORE in l)
    for n, line in _copy_lines(ext, text):
        if n in ignored:
            continue
        for m in HYPE.finditer(line):
            add("hype", n, m.group(0))
        if EM_DASH in line:
            add("dashes", n, line)
        if EMOJI.search(line):
            add("emoji", n, line)
        if CONTRAST.search(line):
            add("contrast-frame", n, line)
        for rx, what in CLAIMS:
            m = rx.search(line)
            if m:
                add("claims", n, "%s (%s)" % (m.group(0), what))
        m = PLACEHOLDER.search(line)
        if m:
            add("placeholders", n, m.group(0))
    style_like = ext in STYLE or ext in MARKUP
    is_code = ext in CODE
    glass = []
    for n, line in enumerate(lines, 1):
        if n in ignored:
            continue
        if style_like:
            for g in GRADIENT_CSS.findall(line):
                if VIOLET_HEX.search(g) and (BLUE_PINK_HEX.search(g) or len(VIOLET_HEX.findall(g)) > 1):
                    add("gradients", n, g)
            if TW_GRADIENT.search(line):
                add("gradients", n, TW_GRADIENT.search(line).group(0))
            if GRADIENT_TEXT.search(line):
                add("gradient-text", n, line)
            if GLASS.search(line):
                glass.append((n, line))
            if STOCK_FONT.search(line):
                add("stock-type", n, line)
        if is_code:
            if BANNER.match(line) or (COMMENT.match(line) and BANNER_BOX.search(line)):
                add("banners", n, line)
            if "KeyboardInterrupt" in line:
                pass  # stopping quietly on Ctrl+C is deliberate
            elif any(rx.search(line) for rx in SWALLOW):
                add("swallowed-errors", n, line)
            elif SWALLOW_NEXT.match(line) and n < len(lines) and lines[n].strip() in ("pass", "..."):
                add("swallowed-errors", n, line + " " + lines[n].strip())
            c = COMMENT.match(line)
            if c and n < len(lines) and _echoes(c.group(1), lines[n]):
                add("echo-comments", n, line.strip() + "  /  " + lines[n].strip())
            if ext not in MARKUP and EMOJI.search(line):
                add("emoji", n, line)
        if CHATTER.search(line):
            add("assistant-chatter", n, line)
    if len(glass) >= 3:
        for n, line in glass:
            add("glass", n, line)
    # A couple of echo comments is normal; flag a habit, not a slip.
    if len(hits.get("echo-comments", [])) < 3:
        hits.pop("echo-comments", None)
    return hits


def _files(paths, exclude=()):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for base, dirs, names in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(names):
                full = os.path.join(base, name)
                ext = os.path.splitext(name)[1].lower()
                if ext not in MARKUP | PROSE | STYLE | CODE or name.endswith((".min.js", ".min.css")):
                    continue
                rel = os.path.relpath(full, p).replace(os.sep, "/")
                if any(fnmatch.fnmatch(rel, x) or fnmatch.fnmatch(name, x) for x in exclude):
                    continue
                yield full


def _display(path):
    """The path relative to where the scan ran, or as given when that is on another drive."""
    try:
        return os.path.relpath(path).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def scan(paths, exclude=()):
    """Scan files and folders. Returns {"files": n, "kinds": set of groups seen, "hits": {cid: [(path, line, text)]}}."""
    out = {"files": 0, "kinds": set(), "hits": {}}
    for path in _files(paths, exclude):
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        out["files"] += 1
        ext = os.path.splitext(path)[1].lower()
        if ext in MARKUP or ext in PROSE:
            out["kinds"].add("copy")
        if ext in STYLE or ext in MARKUP:
            out["kinds"].add("ui")
        if ext in CODE:
            out["kinds"].add("code")
        for cid, found in scan_text(path, text).items():
            for n, s in found:
                out["hits"].setdefault(cid, []).append((path.replace(os.sep, "/"), n, s))
    return out


def score(hits):
    """100 less a weight per file and check with hits, so one noisy file cannot sink the score alone."""
    lost = 0
    for cid, found in hits.items():
        files = len(set(p for p, _, _ in found))
        lost += WEIGHT[CHECK_BY_ID[cid][3]] * min(files, 4)
    return max(0, 100 - lost)


# ---------------------------------------------------------------- commands

def _as_json(r):
    return {"files": r["files"], "kinds": sorted(r["kinds"]), "score": score(r["hits"]),
            "hits": dict((cid, [{"file": p, "line": n, "text": s} for p, n, s in v]) for cid, v in r["hits"].items())}


def cmd_scan(a):
    r = scan(a.paths, a.exclude)
    if a.json:
        print(json.dumps(_as_json(r), indent=2))
        return 0
    print("Scanned %d file(s). Score %d/100." % (r["files"], score(r["hits"])))
    for cid, title, group, sev, _ in CHECKS:
        found = r["hits"].get(cid, [])
        if group != "any" and group not in r["kinds"]:
            continue
        print("  %s %s%s" % (ledger.BAD if found else ledger.OK, title, (": %d" % len(found)) if found else ""))
        for p, n, s in found[:a.show]:
            print("      %s:%d  %s" % (p, n, s))
    return 0


def cmd_record(a):
    r = scan(a.paths, a.exclude)
    ledger.init_coverage(a.dir, SKILL, [(c[0], c[1]) for c in CHECKS])
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    ev_path = os.path.join(ev_dir, "scan.json")
    with open(ev_path, "w", encoding="utf-8") as fh:
        json.dump(_as_json(r), fh, indent=2)
    ev = os.path.relpath(ev_path, ledger.root(a.dir)).replace(os.sep, "/")
    n_findings = 0
    for cid, title, group, sev, fix in CHECKS:
        if group != "any" and group not in r["kinds"]:
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note="no %s files scanned" % group)
            continue
        found = r["hits"].get(cid, [])
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[ev], note="%d hit(s)" % len(found))
        by_file = {}
        for p, n, s in found:
            by_file.setdefault(p, []).append((n, s))
        for p, items in sorted(by_file.items()):
            rel = _display(p)
            examples = "; ".join("line %d: %s" % (n, s) for n, s in items[:3])
            fid = ledger.slug("slop-%s-%s" % (cid, rel))[:60]
            f = ledger.new_finding(SKILL, ("%s (%d in %s)" % (title, len(items), rel))[:160], sev, id=fid,
                                   category="slop:%s" % cid, evidence=[ev], fix=fix, description=examples[:400],
                                   location="%s:%d" % (rel.replace(os.sep, "/"), items[0][0]))
            ledger.write_finding(a.dir, f)
            ledger.verify(a.dir, SKILL, fid, by="evidence-file")
            n_findings += 1
    s = score(r["hits"])
    ledger.write_scores(a.dir, SKILL, [{"id": "slop-score", "value": s, "unit": "points", "budget": a.budget, "op": ">="}])
    ok = s >= a.budget
    print("%s recorded: %d file(s), %d finding(s), score %d/100 (budget %d)." % (
        ledger.OK if ok else ledger.BAD, r["files"], n_findings, s, a.budget))
    return 0


def cmd_status(a):
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, func, hlp in (("scan", cmd_scan, "print what was found"), ("record", cmd_record, "write it to the ledger")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("paths", nargs="+", help="files or folders to scan")
        p.add_argument("--exclude", action="append", default=[], help="glob to skip, e.g. 'docs/*' (repeatable)")
        if name == "scan":
            p.add_argument("--json", action="store_true")
            p.add_argument("--show", type=int, default=3, help="examples to print per check")
        else:
            p.add_argument("--budget", type=int, default=70, help="lowest passing score (default 70)")
        p.set_defaults(func=func)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
