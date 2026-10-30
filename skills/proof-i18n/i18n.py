#!/usr/bin/env python3
"""Check translation files and source for internationalisation gaps. Offline.

Standard library only, no network. Loads one translation file per locale (JSON,
nested objects flattened to dotted keys, Java-style .properties, or gettext .po)
and compares each locale with a base locale: missing keys, extra keys, empty
values, values left identical to the base, and placeholders that differ (which
break at runtime). It also writes a pseudo-locale for testing text expansion and
hardcoded strings, and scans .jsx/.tsx/.vue/.html for likely hardcoded
user-facing text.

    python3 i18n.py keys locales/ --base en
    python3 i18n.py pseudo locales/en.json --out locales/pseudo.json --expand 0.35
    python3 i18n.py hardcoded src/
    python3 i18n.py record locales/ --base en --src src/ --min 95
    python3 i18n.py status
"""

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-i18n"
CATEGORY = "i18n"
FINDING_PREFIX = "i18n-"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".nuxt"}
TRANSLATION_EXTS = (".json", ".properties", ".po")
SOURCE_EXTS = (".jsx", ".tsx", ".vue", ".html", ".htm")
MIN_IDENTICAL_LEN = 4
LIST_CAP = 20


class I18nError(ValueError):
    """The input files cannot be used as asked."""


# ------------------------------------------------------------- locale detection

_LOCALE = re.compile(r"(?:^|[_.\-])([a-z]{2,3}(?:[_\-](?:[A-Za-z]{2}|[A-Z][a-z]{3}(?:[_\-][A-Za-z]{2})?))?)$")


def locale_from_name(name):
    """The locale code at the end of a file stem or folder name, or None.

    en, fr, pt_BR, en-US, zh-Hant, messages_fr, messages_fr_FR all work.
    """
    m = _LOCALE.search(name)
    return m.group(1) if m else None


def norm_locale(code):
    return code.replace("_", "-").lower()


def detect_locale(path, root):
    """(locale, namespace) for a translation file under ``root``.

    The file stem is tried first (en.json, messages_fr.properties). When the stem
    is not a locale, up to two parent folders are tried (en/common.json,
    fr/LC_MESSAGES/messages.po) and the stem becomes a key namespace.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    loc = locale_from_name(stem)
    if loc:
        return loc, None
    parent = os.path.dirname(os.path.abspath(path))
    root = os.path.abspath(root)
    for _ in range(2):
        if os.path.normcase(parent) == os.path.normcase(root) or len(parent) < len(root):
            break
        loc = locale_from_name(os.path.basename(parent))
        if loc:
            return loc, stem
        parent = os.path.dirname(parent)
    return None, None


# ------------------------------------------------------------------ parsers

def flatten(obj, prefix=""):
    """Nested JSON to {dotted.key: string}. Lists use their index as a key part."""
    out = {}
    if isinstance(obj, dict):
        items = obj.items()
    elif isinstance(obj, list):
        items = ((str(i), v) for i, v in enumerate(obj))
    else:
        out[prefix] = "" if obj is None else (obj if isinstance(obj, str) else json.dumps(obj))
        return out
    for k, v in items:
        key = "%s.%s" % (prefix, k) if prefix else str(k)
        if isinstance(v, (dict, list)) and v:
            out.update(flatten(v, key))
        elif isinstance(v, (dict, list)):
            out[key] = ""
        else:
            out.update(flatten(v, key))
    return out


def parse_json(text):
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise I18nError("not valid JSON: %s" % e)
    if not isinstance(obj, dict):
        raise I18nError("a translation JSON file must hold an object at the top")
    return flatten(obj)


_U_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def _prop_unescape(s):
    s = _U_ESC.sub(lambda m: chr(int(m.group(1), 16)), s)
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", "f": "\f"}.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_properties(text):
    """Simple .properties: key=value, key: value or key value; # and ! comments;
    trailing backslash continues a line; \\uXXXX escapes decoded."""
    out = {}
    logical, buf = [], ""
    for raw in text.splitlines():
        line = raw.lstrip() if buf else raw.strip()
        if not buf and (not line or line[0] in "#!"):
            continue
        trailing = len(line) - len(line.rstrip("\\"))
        if trailing % 2 == 1:
            buf += line[:-1]
            continue
        logical.append(buf + line)
        buf = ""
    if buf:
        logical.append(buf)
    for line in logical:
        m = re.match(r"((?:\\.|[^=:\s\\])+)\s*[=:\s]\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2)
        else:
            key, value = line, ""
        out[_prop_unescape(key)] = _prop_unescape(value.rstrip())
    return out


def _po_string(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_po(text, base=False):
    """gettext .po: msgid is the key, msgstr (or msgstr[0]) the value.

    Entries flagged fuzzy count as empty (gettext does not use them). The header
    entry (empty msgid) is skipped. msgctxt, when present, prefixes the key as
    ``context|msgid``. In the base locale an empty msgstr falls back to msgid.
    """
    entries = []
    entry, field = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        starts_new = line.startswith("#") or re.match(r"(msgctxt|msgid)\s", line)
        if starts_new and field and field.startswith("msgstr"):
            entries.append(entry)  # the previous entry is complete
            entry, field = {}, None
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                entry["fuzzy"] = True
            continue
        m = re.match(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(\".*\")$", line)
        if m:
            field = m.group(1)
            entry[field] = _po_string(m.group(2))
        elif line.startswith('"') and field:
            entry[field] = entry.get(field, "") + _po_string(line)
    entries.append(entry)

    out = {}
    for e in entries:
        msgid = e.get("msgid")
        if not msgid:
            continue
        value = "" if e.get("fuzzy") else e.get("msgstr", e.get("msgstr[0]", ""))
        if base and not value:
            value = msgid
        key = "%s|%s" % (e["msgctxt"], msgid) if e.get("msgctxt") else msgid
        out[key] = value
    return out


def parse_file(path, base=False):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return parse_json(text)
    if ext == ".properties":
        return parse_properties(text)
    if ext == ".po":
        return parse_po(text, base=base)
    raise I18nError("unsupported translation file: %s" % path)


def load_locales(root, base):
    """{locale: {"keys": {...}, "files": [...]}} for every translation file under root."""
    if not os.path.isdir(root):
        raise I18nError("not a folder: %s" % root)
    found = {}
    names = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if not f.lower().endswith(TRANSLATION_EXTS):
                continue
            path = os.path.join(folder, f)
            loc, ns = detect_locale(path, root)
            if not loc:
                continue
            key = norm_locale(loc)
            names.setdefault(key, loc)
            keys = parse_file(path, base=(key == norm_locale(base)))
            if ns:
                keys = {"%s:%s" % (ns, k): v for k, v in keys.items()}
            slot = found.setdefault(key, {"locale": names[key], "keys": {}, "files": []})
            slot["keys"].update(keys)
            slot["files"].append(path)
    return found


# -------------------------------------------------------------- placeholders

_PH_DOUBLE = re.compile(r"\{\{\s*[-\w.$]+\s*\}\}")
_PH_PATTERNS = [
    re.compile(r"\$\{\s*[\w.]+\s*\}"),                                   # ${name}
    re.compile(r"\{\s*\w+\s*(?=[,}])"),                                   # {name} or {count, plural ...
    re.compile(r"%\(\w+\)[-+0#]*\d*(?:\.\d+)?[sdifrxXeEgGc]"),           # %(name)s
    re.compile(r"%(?:\d+\$)?[-+0#]*\d*(?:\.\d+)?(?:l|ll|h)?[sdifuxXeEgGc@]"),  # %s %d %1$s
]


def placeholders(text):
    """Sorted list of placeholder tokens in ``text``, normalised for comparison."""
    if not text:
        return []
    found = [re.sub(r"\s+", "", m.group(0)) for m in _PH_DOUBLE.finditer(text)]
    rest = _PH_DOUBLE.sub(" ", text).replace("%%", "  ")
    for rx in _PH_PATTERNS:
        for m in rx.finditer(rest):
            tok = re.sub(r"\s+", "", m.group(0))
            if tok.startswith("{") and not tok.startswith("{{"):
                tok += "}"
            found.append(tok)
        rest = rx.sub(lambda m: " " * len(m.group(0)), rest)
    return sorted(found)


# --------------------------------------------------------------- comparison

def looks_like_brand_or_number(value):
    """True for values that are reasonably the same in every language."""
    v = value.strip()
    stripped = v
    for tok in placeholders(v):
        stripped = stripped.replace(tok, " ")
    if not re.search(r"[^\W\d_]", stripped):
        return True  # numbers, punctuation, placeholders only
    if re.match(r"^(https?://|www\.|mailto:)\S+$", v) or re.match(r"^\S+@\S+\.\S+$", v):
        return True
    if not re.search(r"\s", v):
        if v.isupper() or re.search(r"\d", v) or re.search(r".[A-Z]", v):
            return True  # NASA, H2O, GitHub, iPhone
    return False


def compare(base_keys, loc_keys):
    """Per-locale report against the base. Pure function on two {key: value} maps."""
    missing = sorted(k for k in base_keys if k not in loc_keys)
    extra = sorted(k for k in loc_keys if k not in base_keys)
    empty, identical, mismatched = [], [], []
    for k in sorted(base_keys):
        if k not in loc_keys:
            continue
        b, v = base_keys[k], loc_keys[k]
        if not (v or "").strip():
            if (b or "").strip():
                empty.append(k)
            continue
        if v == b and len(v.strip()) >= MIN_IDENTICAL_LEN and not looks_like_brand_or_number(v):
            identical.append(k)
        pb, pv = placeholders(b), placeholders(v)
        if pb != pv:
            mismatched.append({"key": k, "base": pb, "translation": pv})
    total = len(base_keys)
    translated = total - len(missing) - len(empty)
    pct = round(100.0 * translated / total, 1) if total else 0.0
    return {"total": total, "translated": translated, "completeness": pct,
            "missing": missing, "extra": extra, "empty": empty,
            "identical": identical, "placeholder_mismatch": mismatched}


def analyse(root, base):
    locales = load_locales(root, base)
    bkey = norm_locale(base)
    if bkey not in locales:
        raise I18nError("no translation file for base locale %r under %s (found: %s)"
                        % (base, root, ", ".join(sorted(v["locale"] for v in locales.values())) or "none"))
    base_keys = locales[bkey]["keys"]
    if not base_keys:
        raise I18nError("the base locale %r has no keys" % base)
    others = sorted(k for k in locales if k != bkey)
    if not others:
        raise I18nError("only the base locale %r was found under %s; nothing to compare" % (base, root))
    reports = []
    for k in others:
        rep = compare(base_keys, locales[k]["keys"])
        rep["locale"] = locales[k]["locale"]
        rep["files"] = [os.path.relpath(p, root).replace(os.sep, "/") for p in locales[k]["files"]]
        reports.append(rep)
    return {"base": locales[bkey]["locale"], "base_keys": len(base_keys), "locales": reports}


# ------------------------------------------------------------------- pseudo

_ACCENTS = [
    ("a", "\u00e1"), ("b", "\u0180"), ("c", "\u00e7"), ("d", "\u00f0"), ("e", "\u00e9"),
    ("g", "\u011d"), ("h", "\u0125"), ("i", "\u00ee"), ("j", "\u0135"), ("k", "\u0137"),
    ("l", "\u013c"), ("n", "\u00f1"), ("o", "\u00f6"), ("r", "\u0155"), ("s", "\u0161"),
    ("t", "\u0163"), ("u", "\u00fb"), ("w", "\u0175"), ("y", "\u00fd"), ("z", "\u017e"),
    ("A", "\u00c5"), ("C", "\u00c7"), ("D", "\u00d0"), ("E", "\u00c9"), ("G", "\u011c"),
    ("H", "\u0124"), ("I", "\u00ce"), ("J", "\u0134"), ("K", "\u0136"), ("L", "\u013b"),
    ("N", "\u00d1"), ("O", "\u00d6"), ("R", "\u0154"), ("S", "\u0160"), ("T", "\u0162"),
    ("U", "\u00db"), ("W", "\u0174"), ("Y", "\u00dd"), ("Z", "\u017d"),
]
_ACCENT_TABLE = str.maketrans(dict(_ACCENTS))
_PROTECTED = re.compile(
    r"\{\{\s*[-\w.$]+\s*\}\}|\$\{\s*[\w.]+\s*\}|\{[^{}]*\}|%\(\w+\)[-+0#]*\d*(?:\.\d+)?[sdifrxXeEgGc]"
    r"|%(?:\d+\$)?[-+0#]*\d*(?:\.\d+)?(?:l|ll|h)?[sdifuxXeEgGc@]|%%|<[^<>]+>|&\w+;")


def pseudo_string(text, expand):
    """Accent the letters, keep placeholders/tags untouched, pad, wrap in brackets."""
    parts, pos, visible = [], 0, 0
    for m in _PROTECTED.finditer(text):
        chunk = text[pos:m.start()]
        parts.append(chunk.translate(_ACCENT_TABLE))
        visible += len(chunk)
        parts.append(m.group(0))
        pos = m.end()
    chunk = text[pos:]
    parts.append(chunk.translate(_ACCENT_TABLE))
    visible += len(chunk)
    pad = int(math.ceil(visible * expand)) if visible else 0
    return "[" + "".join(parts) + ("~" * pad) + "]"


def pseudo_tree(obj, expand):
    if isinstance(obj, dict):
        return {k: pseudo_tree(v, expand) for k, v in obj.items()}
    if isinstance(obj, list):
        return [pseudo_tree(v, expand) for v in obj]
    if isinstance(obj, str):
        return pseudo_string(obj, expand)
    return obj


def _is_pseudo(path):
    """True when path is a pseudo-locale an earlier run wrote (every string bracketed), so it may be replaced."""
    try:
        with open(path, encoding="utf-8") as fh:
            values = [v for _, v in flatten(json.load(fh)) if isinstance(v, str)]
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    return bool(values) and all(v.startswith("[") and v.endswith("]") for v in values)


# ---------------------------------------------------------------- hardcoded

_BLOCKS = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>|<!--.*?-->", re.S | re.I)
_JS_COMMENTS = re.compile(r"/\*.*?\*/|(?<![:\"'\w])//[^\n]*", re.S)
_TEXT_NODE = re.compile(r">([^<>]+)<")
_ATTR = re.compile(r"(?<![\w:.\-@])(placeholder|title|alt|aria-label)\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)
_EXPR = re.compile(r"\{\{.*?\}\}|\{[^{}]*\}", re.S)
_CODE_HINT = re.compile(r"[;=]|&&|\|\||\)\s*\{|\bif\s*\(|\breturn\b|\bconst\b|\bfunction\b|=>")
_WORD = re.compile(r"[^\W\d_]{2,}")


def _blank(match):
    """Replace a match with spaces, keeping newlines so line numbers survive."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _source_files(root):
    if os.path.isfile(root):
        yield root
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f.lower().endswith(SOURCE_EXTS):
                yield os.path.join(base, f)


def scan_text(text, is_js):
    """List of (line, kind, text) for likely hardcoded strings in one file's text."""
    text = _BLOCKS.sub(_blank, text)
    if is_js:
        text = _JS_COMMENTS.sub(_blank, text)
    hits = []
    for m in _TEXT_NODE.finditer(text):
        raw = m.group(1)
        if len(raw) > 300 or _CODE_HINT.search(raw):
            continue
        literal = _EXPR.sub(" ", raw)
        if "{" in literal or "}" in literal:
            continue
        if len(_WORD.findall(literal)) >= 2:
            offset = m.start(1) + (len(raw) - len(raw.lstrip()))
            hits.append((text.count("\n", 0, offset) + 1, "text", " ".join(raw.split())))
    for m in _ATTR.finditer(text):
        value = m.group(3) if m.group(3) is not None else m.group(4)
        if "{" in value or "}" in value or not _WORD.search(value):
            continue
        hits.append((text.count("\n", 0, m.start()) + 1, "attr:%s" % m.group(1).lower(), value.strip()))
    hits.sort()
    return hits


def hardcoded(root):
    """Likely hardcoded user-facing strings under ``root``. Heuristic."""
    if not os.path.exists(root):
        raise I18nError("no such path: %s" % root)
    out = []
    base = root if os.path.isdir(root) else os.path.dirname(root)
    for path in _source_files(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        is_js = path.lower().endswith((".jsx", ".tsx"))
        for line, kind, snippet in scan_text(text, is_js):
            out.append({"file": os.path.relpath(path, base).replace(os.sep, "/"), "line": line,
                        "kind": kind, "text": snippet[:120]})
    return out


# ----------------------------------------------------------------- commands

def _print_list(label, items, fmt=str):
    if not items:
        return
    print("    %s (%d):" % (label, len(items)))
    for it in items[:LIST_CAP]:
        print("      %s" % fmt(it))
    if len(items) > LIST_CAP:
        print("      ... and %d more" % (len(items) - LIST_CAP))


def _fmt_mismatch(m):
    return "%s  base %s  vs  %s" % (m["key"], " ".join(m["base"]) or "(none)", " ".join(m["translation"]) or "(none)")


def cmd_keys(a):
    try:
        res = analyse(a.locales_dir, a.base)
    except I18nError as e:
        print("%s %s" % (ledger.BAD, e))
        return 2
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=True))
        return 0
    print("Base locale %s: %d key(s)" % (res["base"], res["base_keys"]))
    for r in res["locales"]:
        clean = not (r["missing"] or r["empty"] or r["placeholder_mismatch"])
        print("%s %s: %.1f%% translated (%d of %d), %d missing, %d empty, %d extra, %d identical, "
              "%d placeholder mismatch(es)" % (
                  ledger.OK if clean else ledger.BAD, r["locale"], r["completeness"], r["translated"],
                  r["total"], len(r["missing"]), len(r["empty"]), len(r["extra"]), len(r["identical"]),
                  len(r["placeholder_mismatch"])))
        _print_list("missing", r["missing"])
        _print_list("empty", r["empty"])
        _print_list("extra (not in base)", r["extra"])
        _print_list("identical to base (likely untranslated)", r["identical"])
        _print_list("placeholder mismatch", r["placeholder_mismatch"], _fmt_mismatch)
    return 0


def cmd_pseudo(a):
    try:
        expand = num.parse_number(a.expand, field="--expand")
    except ValueError as e:
        print("%s %s" % (ledger.BAD, e))
        return 2
    if expand < 0 or expand > 3:
        print("%s --expand must be between 0 and 3 (a ratio, 0.35 means 35%% longer)" % ledger.BAD)
        return 2
    try:
        with open(a.base_json, encoding="utf-8-sig") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as e:
        print("%s cannot read %s: %s" % (ledger.BAD, a.base_json, e))
        return 2
    if not isinstance(obj, dict):
        print("%s %s must hold a JSON object" % (ledger.BAD, a.base_json))
        return 2
    out = pseudo_tree(obj, expand)
    folder = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(folder, exist_ok=True)
    ledger.guard_out(a.out, a.dir, a.force or _is_pseudo(a.out))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("%s wrote pseudo-locale %s (%d string(s), +%d%% padding, placeholders kept)"
          % (ledger.OK, a.out, len(flatten(obj)), int(round(expand * 100))))
    print("Load it as a locale in the app. Text without brackets and accents is hardcoded;")
    print("text cut off or overflowing will break in longer languages.")
    return 0


def cmd_hardcoded(a):
    try:
        hits = hardcoded(a.src_dir)
    except I18nError as e:
        print("%s %s" % (ledger.BAD, e))
        return 2
    if a.json:
        print(json.dumps(hits, indent=2, ensure_ascii=True))
        return 0
    if not hits:
        print("%s no likely hardcoded user-facing strings found in %s" % (ledger.OK, a.src_dir))
        return 0
    for h in hits:
        print("%s:%d  [%s]  %s" % (h["file"], h["line"], h["kind"], h["text"]))
    print("\n%s %d candidate(s). Heuristic: confirm each, then move it into the translation files."
          % (ledger.BAD, len(hits)))
    return 0


def _clear_findings(d):
    for f in ledger.load_findings(d, SKILL):
        if f["id"].startswith(FINDING_PREFIX) and f.get("category") == CATEGORY:
            os.remove(ledger.finding_path(d, SKILL, f["id"]))


def _metric_id(locale):
    return "i18n_completeness_%s" % re.sub(r"[^a-z0-9]+", "_", locale.lower()).strip("_")


def cmd_record(a):
    try:
        minimum = num.parse_number(a.min, field="--min")
        res = analyse(a.locales_dir, a.base)
        hits = hardcoded(a.src) if a.src else None
    except (I18nError, ValueError) as e:
        print("%s %s" % (ledger.BAD, e))
        return 2
    if minimum < 0 or minimum > 100:
        print("%s --min is a percent between 0 and 100" % ledger.BAD)
        return 2

    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "i18n-report.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("i18n check of %s, base locale %s (%d keys)\n" % (a.locales_dir, res["base"], res["base_keys"]))
        for r in res["locales"]:
            fh.write("\n== %s (%s): %.1f%% translated, %d of %d\n" % (
                r["locale"], ", ".join(r["files"]), r["completeness"], r["translated"], r["total"]))
            for label, items in [("missing", r["missing"]), ("empty", r["empty"]),
                                 ("extra (not in base)", r["extra"]),
                                 ("identical to base (likely untranslated)", r["identical"])]:
                fh.write("%s: %d\n" % (label, len(items)))
                for k in items:
                    fh.write("  %s\n" % k)
            fh.write("placeholder mismatch: %d\n" % len(r["placeholder_mismatch"]))
            for m in r["placeholder_mismatch"]:
                fh.write("  %s\n" % _fmt_mismatch(m))
        if hits is not None:
            fh.write("\n== hardcoded strings in %s: %d candidate(s)\n" % (a.src, len(hits)))
            for h in hits:
                fh.write("  %s:%d  [%s]  %s\n" % (h["file"], h["line"], h["kind"], h["text"]))
    rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")

    checks = [(r["locale"], "Translation completeness and placeholders for %s" % r["locale"])
              for r in res["locales"]]
    if hits is not None:
        checks.append(("hardcoded", "Hardcoded user-facing strings in source"))
    ledger.init_coverage(a.dir, SKILL, checks, keep_existing=False)
    for r in res["locales"]:
        ledger.mark_coverage(a.dir, SKILL, r["locale"], "covered", evidence=[rel],
                             note="%.1f%% translated, %d placeholder mismatch(es)"
                             % (r["completeness"], len(r["placeholder_mismatch"])))
    if hits is not None:
        ledger.mark_coverage(a.dir, SKILL, "hardcoded", "covered", evidence=[rel],
                             note="%d candidate(s)" % len(hits))

    _clear_findings(a.dir)
    written = []

    def add(fid, title, severity, **fields):
        f = ledger.new_finding(SKILL, title, severity, id=fid, category=CATEGORY, evidence=[rel], **fields)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
        written.append((severity, title))

    for r in res["locales"]:
        loc_slug = ledger.slug(r["locale"])
        gap = r["missing"] + r["empty"]
        if gap:
            share = float(len(gap)) / r["total"]
            add(ledger.slug("%smissing-%s" % (FINDING_PREFIX, loc_slug)),
                "%s: %d of %d keys missing or empty" % (r["locale"], len(gap), r["total"]),
                "medium" if share > 0.05 else "low",
                location=", ".join(r["files"]),
                description="Keys in the base locale with no usable translation: %s%s"
                % (", ".join(gap[:LIST_CAP]), " ..." if len(gap) > LIST_CAP else ""),
                fix="Translate the listed keys, or confirm the fallback to %s is acceptable." % res["base"])
        mm = r["placeholder_mismatch"]
        if mm:
            add(ledger.slug("%splaceholders-%s" % (FINDING_PREFIX, loc_slug)),
                "%s: %d placeholder mismatch%s" % (r["locale"], len(mm), "" if len(mm) == 1 else "es"),
                "high", location=", ".join(r["files"]),
                description="Placeholders differ from the base, which breaks at runtime: %s"
                % "; ".join(_fmt_mismatch(m) for m in mm[:LIST_CAP]),
                fix="Make each translation carry exactly the placeholders of the base string.")
    if hits:
        add(FINDING_PREFIX + "hardcoded",
            "Likely hardcoded user-facing strings (%d candidate%s)" % (len(hits), "" if len(hits) == 1 else "s"),
            "low", location=a.src,
            fix="Move each confirmed string into the translation files and look it up by key.")

    metrics = [{"id": _metric_id(r["locale"]), "value": r["completeness"], "unit": "%",
                "budget": minimum, "op": ">=", "source": "i18n.py record", "evidence": [rel]}
               for r in res["locales"]]
    ledger.write_scores(a.dir, SKILL, metrics)

    for r in res["locales"]:
        ok = r["completeness"] >= minimum and not r["placeholder_mismatch"]
        print("%s %s: %.1f%% translated (budget >= %g%%), %d placeholder mismatch(es)"
              % (ledger.OK if ok else ledger.BAD, r["locale"], r["completeness"], minimum,
                 len(r["placeholder_mismatch"])))
    if hits is not None:
        print("%s %d likely hardcoded string(s) in %s" % (ledger.OK if not hits else ledger.BAD, len(hits), a.src))
    print("Recorded %d finding(s), %d coverage check(s), %d score(s). Report: %s"
          % (len(written), len(checks), len(metrics), rel))
    return 0


def cmd_status(a):
    findings = ledger.load_findings(a.dir, SKILL)
    print("i18n: %d finding(s)" % len(findings))
    for f in findings:
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    for s in ledger.load_all_scores(a.dir):
        if s["skill"] != SKILL:
            continue
        for m in s["metrics"]:
            print("  %s %s = %g%s (budget %s %g)" % (ledger.OK if ledger.metric_passes(m) else ledger.BAD,
                                                      m["id"], m["value"], m["unit"], m["op"], m["budget"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof", help="the proof/ folder (default proof)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keys", help="compare every locale with the base")
    k.add_argument("locales_dir")
    k.add_argument("--base", default="en", help="base locale (default en)")
    k.add_argument("--json", action="store_true")
    k.set_defaults(func=cmd_keys)

    p = sub.add_parser("pseudo", help="write a pseudo-locale from a base JSON file")
    p.add_argument("base_json")
    p.add_argument("--out", required=True, help="where to write the pseudo-locale JSON")
    p.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    p.add_argument("--expand", default="0.35", help="padding ratio, default 0.35 (35%% longer)")
    p.set_defaults(func=cmd_pseudo)

    h = sub.add_parser("hardcoded", help="likely hardcoded strings in .jsx/.tsx/.vue/.html")
    h.add_argument("src_dir")
    h.add_argument("--json", action="store_true")
    h.set_defaults(func=cmd_hardcoded)

    r = sub.add_parser("record", help="write coverage, findings and scores to the ledger")
    r.add_argument("locales_dir")
    r.add_argument("--base", default="en", help="base locale (default en)")
    r.add_argument("--src", help="also scan this source folder for hardcoded strings")
    r.add_argument("--min", default="95", help="completeness budget in percent (default 95)")
    r.set_defaults(func=cmd_record)

    sub.add_parser("status", help="recorded findings and scores").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
