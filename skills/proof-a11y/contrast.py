#!/usr/bin/env python3
"""WCAG contrast arithmetic, plus the accessibility coverage and findings ledger.

Standard library only, no network. The contrast checker is pure maths and always
works. A full rule engine (axe, Lighthouse) is optional; when it is installed its
report is recorded through the same ledger. The manual checks in checklist.md are
tracked as coverage because no tool can do them.

    python3 contrast.py ratio --fg "#767676" --bg "#ffffff" [--size normal|large|14pt|24px] [--bold]
    python3 contrast.py check --pairs pairs.json [--record]
    python3 contrast.py checklist [--record] [--full]
    python3 contrast.py cover focus-order --note "tabbed every page; order matches the reading order"
    python3 contrast.py na motion --note "nothing on the site moves or animates"
    python3 contrast.py finding add names-labels --title "..." --severity high --location "..." --evidence <path>
    python3 contrast.py finding verify <id> --by evidence-file
    python3 contrast.py finding reject <id> --reason "..."

A pairs file is a JSON list, or an object with a "pairs" list, of
{"id", "fg", "bg", "size", "bold", "where"}. Colours are #rgb, #rrggbb or
rgb(r, g, b). Size is "normal", "large", or a font size such as "14pt" or "24px";
bold (default false) is read with it, so 14pt bold counts as large. "where" says
where the pair appears and becomes the location of any finding.

Pass or fail is judged on the unrounded ratio; the printed value is rounded to two
decimals.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-a11y"
CHECKLIST_DOC = os.path.join(HERE, "checklist.md")
PAIR_PREFIX = "contrast-"
ENGINE_CHECK = ("engine", "Automated rule engine run (axe or the Lighthouse accessibility audit)")

# WCAG 1.4.3 (AA) and 1.4.6 (AAA) minimum ratios by text size.
AA = {"normal": 4.5, "large": 3.0}
AAA = {"normal": 7.0, "large": 4.5}
LARGE_PT = 18.0        # large text: 18pt and up ...
LARGE_BOLD_PT = 14.0   # ... or 14pt and up when bold
PX_PER_PT = 4.0 / 3.0

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_HEX_ALPHA = re.compile(r"^#?([0-9a-fA-F]{4}|[0-9a-fA-F]{8})$")
_RGB = re.compile(r"^rgba?\(\s*([^,\s]+)\s*,\s*([^,\s]+)\s*,\s*([^,\s)]+)\s*(?:,\s*([^,\s)]+)\s*)?\)$", re.I)


class ColorError(ValueError):
    """A colour could not be read."""


class SizeError(ValueError):
    """A text size could not be read."""


# ------------------------------------------------------------------ colours

def parse_color(text):
    """Return (r, g, b) as ints 0..255 from '#rgb', '#rrggbb' or 'rgb(r, g, b)'.

    Percent channels ('rgb(50%, 50%, 50%)') are accepted. A colour with an alpha
    channel is refused: blend it onto its background first, then pass the result.
    """
    if not isinstance(text, str) or not text.strip():
        raise ColorError("colour is missing")
    s = text.strip()
    m = _HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    if _HEX_ALPHA.match(s):
        raise ColorError("%r has an alpha channel; blend it onto the background first and pass the flat colour" % text)
    m = _RGB.match(s)
    if m:
        alpha = m.group(4)
        if alpha is not None and _channel_value(alpha, scale=1.0) != 1.0:
            raise ColorError("%r has an alpha channel; blend it onto the background first and pass the flat colour" % text)
        return tuple(_channel_value(part, scale=255.0) for part in m.groups()[:3])
    raise ColorError("cannot read colour %r; use #rgb, #rrggbb or rgb(r, g, b)" % text)


def _channel_value(part, scale):
    """One rgb() channel: '118' or '50%'. Returns an int 0..scale (or a float for alpha)."""
    try:
        if part.endswith("%"):
            value = num.parse_number(part[:-1], decimal=".", field="colour channel") * scale / 100.0
        else:
            value = num.parse_number(part, decimal=".", field="colour channel")
    except (num.MissingInput, num.BadNumber):
        raise ColorError("cannot read colour channel %r" % part)
    if scale == 1.0:
        return value
    if value < 0 or value > scale or (not part.endswith("%") and value != int(value)):
        raise ColorError("colour channel %r must be a whole number from 0 to 255, or a percentage" % part)
    return int(round(value))


def hex_of(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


# -------------------------------------------------------------------- maths

def _linear(channel):
    """One sRGB channel (0..255) to linear light, per the WCAG definition."""
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    """Relative luminance of an sRGB colour, 0.0 (black) to 1.0 (white)."""
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """The WCAG contrast ratio of two colours, 1.0 to 21.0. Order does not matter."""
    lighter, darker = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def points(size):
    """Read a font size as points. 'normal' and 'large' return None (no size given)."""
    if isinstance(size, bool):
        raise SizeError("size must be normal, large, or a font size such as 14pt or 24px")
    if isinstance(size, (int, float)):
        pt = float(size)
    elif isinstance(size, str):
        s = size.strip().lower()
        if s in ("normal", "large"):
            return None
        try:
            if s.endswith("pt"):
                pt = num.parse_number(s[:-2], decimal=".", field="size")
            elif s.endswith("px"):
                pt = num.parse_number(s[:-2], decimal=".", field="size") / PX_PER_PT
            else:
                pt = num.parse_number(s, decimal=".", field="size")
        except (num.MissingInput, num.BadNumber):
            raise SizeError("cannot read size %r; use normal, large, or a font size such as 14pt or 24px" % size)
    else:
        raise SizeError("size must be normal, large, or a font size such as 14pt or 24px")
    if pt <= 0:
        raise SizeError("size must be greater than zero, got %r" % size)
    return pt


def is_large(size, bold=False):
    """Large text is 18pt and up, or 14pt and up when bold (WCAG 1.4.3 note)."""
    if not isinstance(bold, bool):
        raise SizeError("bold must be true or false")
    pt = points(size)
    if pt is None:
        return size.strip().lower() == "large"
    return pt >= LARGE_PT or (bold and pt >= LARGE_BOLD_PT)


def describe_size(size, bold=False):
    pt = points(size)
    kind = "large" if is_large(size, bold) else "normal"
    if pt is None:
        return kind
    shown = str(size).strip() if isinstance(size, str) else ("%g" % size) + "pt"
    return "%s%s, counted as %s" % (shown, " bold" if bold else "", kind)


def judge(ratio, large):
    """Pass/fail at AA and AAA for one ratio and one text size."""
    kind = "large" if large else "normal"
    return {"text": kind, "aa_min": AA[kind], "aaa_min": AAA[kind],
            "aa": ratio >= AA[kind], "aaa": ratio >= AAA[kind]}


# -------------------------------------------------------------------- ratio

def cmd_ratio(a):
    fg, bg = parse_color(a.fg), parse_color(a.bg)
    ratio = contrast_ratio(fg, bg)
    if a.size is None:
        sizes = [judge(ratio, False), judge(ratio, True)]
        size_note = None
    else:
        sizes = [judge(ratio, is_large(a.size, a.bold))]
        size_note = describe_size(a.size, a.bold)
    if a.json:
        print(json.dumps({"fg": hex_of(fg), "bg": hex_of(bg), "ratio": round(ratio, 2),
                          "ratio_exact": ratio, "size": size_note, "sizes": sizes}, indent=2))
        return 0
    print("%s on %s: contrast %.2f:1" % (hex_of(fg), hex_of(bg), ratio))
    if size_note:
        print("text size: %s" % size_note)
    for s in sizes:
        for level, key, minimum in (("AA", "aa", s["aa_min"]), ("AAA", "aaa", s["aaa_min"])):
            print("  %s  %-3s  %-4s  %-11s  needs %.1f:1" % (
                ledger.OK if s[key] else ledger.BAD, level, "PASS" if s[key] else "FAIL", s["text"] + " text", minimum))
    if size_note is None:
        print("Large text is 18pt and up, or 14pt and up when bold. Pass --size to judge one size.")
    print("WCAG 1.4.3 (AA) and 1.4.6 (AAA). Informational only; nothing recorded. "
          "Use check --record to put pairs in the ledger.")
    return 0


# -------------------------------------------------------------------- check

def load_pairs(path):
    """Read and evaluate a pairs file. Returns a list of dicts with the ratio and verdicts."""
    try:
        with open(path, encoding="utf-8-sig") as fh:  # -sig: accept the BOM Windows editors add
            data = json.load(fh)
    except OSError as e:
        raise SystemExit("cannot read %s: %s" % (path, e))
    except ValueError as e:
        raise SystemExit("%s is not valid JSON: %s" % (path, e))
    if isinstance(data, list):
        data = {"pairs": data}
    pairs = num.require_list(data, "pairs")
    out, seen = [], set()
    for i, p in enumerate(pairs):
        label = "pair %d" % (i + 1)
        if not isinstance(p, dict):
            raise SystemExit("%s is not an object" % label)
        for key in ("id", "fg", "bg", "size", "where"):
            if key not in p or p[key] in (None, ""):
                raise num.MissingInput("%s: '%s' is missing (id, fg, bg, size and where are required)" % (label, key))
        pid = str(p["id"]).strip()
        if not _ID.match(pid):
            raise SystemExit("%s: id %r must be lowercase letters, digits and hyphens" % (label, pid))
        if pid in seen:
            raise SystemExit("duplicate pair id %r" % pid)
        seen.add(pid)
        bold = p.get("bold", False)
        try:
            fg, bg = parse_color(p["fg"]), parse_color(p["bg"])
            large = is_large(p["size"], bold)
        except (ColorError, SizeError) as e:
            raise SystemExit("pair %r: %s" % (pid, e))
        ratio = contrast_ratio(fg, bg)
        entry = judge(ratio, large)
        entry.update({"id": pid, "fg": hex_of(fg), "bg": hex_of(bg), "size": p["size"], "bold": bold,
                      "where": str(p["where"]), "ratio": ratio})
        out.append(entry)
    return out


def _print_table(pairs):
    idw = max(len(p["id"]) for p in pairs)
    print("    %-*s  %9s  %-6s  %-4s  %s" % (idw, "id", "ratio", "text", "AA", "where"))
    for p in pairs:
        print("%s  %-*s  %7.2f:1  %-6s  %-4s  %s" % (ledger.OK if p["aa"] else ledger.BAD, idw, p["id"],
                                                     p["ratio"], p["text"], "PASS" if p["aa"] else "FAIL", p["where"]))
    passing = sum(1 for p in pairs if p["aa"])
    aaa = sum(1 for p in pairs if p["aaa"])
    print("%d pair%s: %d pass, %d fail the AA bar (WCAG 1.4.3); %d also reach%s AAA." % (
        len(pairs), "" if len(pairs) == 1 else "s", passing, len(pairs) - passing, aaa, "es" if aaa == 1 else ""))


def _repro(p):
    return 'python3 contrast.py ratio --fg "%s" --bg "%s" --size %s%s' % (
        p["fg"], p["bg"], json.dumps(str(p["size"])), " --bold" if p["bold"] else "")


def _clear_pair_findings(d):
    """Remove findings this tool generated on an earlier run; they are rebuilt from the pairs file."""
    removed = []
    for f in ledger.load_findings(d, SKILL):
        if f["id"].startswith(PAIR_PREFIX) and f.get("category") == "contrast":
            os.remove(ledger.finding_path(d, SKILL, f["id"]))
            removed.append(f["id"])
    return removed


def record_pairs(d, pairs, source):
    """Coverage for every pair, a verified finding for every failing pair, one score."""
    existing = ledger.load_coverage(d, SKILL)
    kept = [(c["id"], c["title"]) for c in (existing["checks"] if existing else [])
            if not c["id"].startswith(PAIR_PREFIX)]
    checks = kept + [(PAIR_PREFIX + p["id"], "Contrast of %s (%s on %s)" % (p["where"], p["fg"], p["bg"]))
                     for p in pairs]
    ledger.init_coverage(d, SKILL, checks)
    replaced = _clear_pair_findings(d)
    written = []
    for p in pairs:
        note = "%.2f:1, %s text, AA %s (needs %.1f:1)" % (p["ratio"], p["text"], "pass" if p["aa"] else "FAIL", p["aa_min"])
        ledger.mark_coverage(d, SKILL, PAIR_PREFIX + p["id"], "covered", note=note)
        if p["aa"]:
            continue
        f = ledger.new_finding(
            SKILL, "Low contrast (%.2f:1) at %s" % (p["ratio"], p["where"]), "medium",
            id=PAIR_PREFIX + p["id"], category="contrast", location=p["where"],
            description="%s on %s measures %.2f:1; %s text needs at least %.1f:1 to meet WCAG 1.4.3 (AA)."
                        % (p["fg"], p["bg"], p["ratio"], p["text"], p["aa_min"]),
            repro=_repro(p),
            fix="Raise the contrast to at least %.1f:1 for %s text (darken the text or lighten the background, "
                "or make the text large enough to use the 3:1 bar)." % (p["aa_min"], p["text"]),
            references=["WCAG 1.4.3"])
        ledger.write_finding(d, f)
        ledger.verify(d, SKILL, f["id"], by="manual",
                      note="Ratio computed by contrast.py from the colours in the pairs file: %.2f:1 against the "
                           "%.1f:1 AA bar for %s text. The arithmetic is the proof; re-run the repro to check it."
                           % (p["ratio"], p["aa_min"], p["text"]))
        written.append(f["id"])
    passing = len(pairs) - len(written)
    ledger.write_scores(d, SKILL, [{"id": "contrast_pairs_passing", "value": passing, "unit": "pairs",
                                    "budget": len(pairs), "op": "==",
                                    "source": "contrast.py check %s" % os.path.basename(source)}])
    return written, replaced


def cmd_check(a):
    pairs = load_pairs(a.pairs)
    if a.json:
        print(json.dumps({"pairs": pairs, "passing": sum(1 for p in pairs if p["aa"]), "total": len(pairs),
                          "recorded": bool(a.record)}, indent=2))
    else:
        _print_table(pairs)
    if not a.record:
        if not a.json:
            print("Nothing recorded. Re-run with --record to write coverage, findings and the score.")
        return 0
    written, replaced = record_pairs(a.dir, pairs, a.pairs)
    if a.json:
        return 0
    print("%s %d pair%s marked covered in %s." % (ledger.OK, len(pairs), "" if len(pairs) == 1 else "s",
                                                  ledger.coverage_path(a.dir, SKILL)))
    if replaced:
        print("   replaced %d contrast finding%s from an earlier run." % (len(replaced), "" if len(replaced) == 1 else "s"))
    for fid in written:
        print("%s finding %r written and verified (the ratio is the proof)." % (ledger.BAD, fid))
    print("score contrast_pairs_passing: %d of %d (must equal %d, so one failing pair makes the verdict Not yet)."
          % (len(pairs) - len(written), len(pairs), len(pairs)))
    return 0


# ---------------------------------------------------------------- checklist

def items():
    """The manual items from checklist.md (single source of truth), and the full text."""
    with open(CHECKLIST_DOC, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S)
    if not m:
        raise SystemExit("checklist.md has no JSON item list")
    return json.loads(m.group(1)), text


def _known_ids(d):
    ids = {ENGINE_CHECK[0]} | {i["id"] for i in items()[0]}
    cov = ledger.load_coverage(d, SKILL)
    if cov:
        ids |= {c["id"] for c in cov["checks"]}
    return ids


def cmd_checklist(a):
    manual, text = items()
    if a.full:
        print(text)
    if a.record:
        existing = ledger.load_coverage(a.dir, SKILL)
        pairs = [(c["id"], c["title"]) for c in (existing["checks"] if existing else [])
                 if c["id"].startswith(PAIR_PREFIX)]
        ledger.init_coverage(a.dir, SKILL, [ENGINE_CHECK] + [(i["id"], i["title"]) for i in manual] + pairs)
    cov = ledger.load_coverage(a.dir, SKILL)
    status = {c["id"]: c["status"] for c in cov["checks"]} if cov else {}
    marks = {"covered": ledger.OK, "not_applicable": "na", "not_run": ".."}
    if a.full:
        print("")
    print("Accessibility review: %d manual items plus the engine run. Automated tools catch only a "
          "fraction of real barriers; a person does these." % len(manual))
    if status:
        print("(%s covered, na not applicable, .. not run)" % ledger.OK)
    print("")
    rows = [ENGINE_CHECK + ("",)] + [(i["id"], i["title"], "WCAG " + i["wcag"]) for i in manual]
    idw = max(len(r[0]) for r in rows)
    for cid, title, ref in rows:
        print("  %s  %-*s  %s%s" % (marks.get(status.get(cid), "  "), idw, cid, title, ("  (%s)" % ref) if ref else ""))
    print("")
    print("How to do each one: checklist.md in this skill folder (checklist --full prints it).")
    if a.record:
        not_run = sum(1 for c in cov["checks"] if c["status"] == "not_run")
        print("%s coverage ledger written: %s (%d not run)." % (ledger.OK, ledger.coverage_path(a.dir, SKILL), not_run))
    print("Record as you go: cover <id> --note ..., na <id> --note ..., finding add <id> ...")
    return 0


def _need_ledger(d):
    if ledger.load_coverage(d, SKILL) is None:
        raise SystemExit("no coverage ledger yet. Run: contrast.py checklist --record")


def cmd_cover(a):
    _need_ledger(a.dir)
    ledger.mark_coverage(a.dir, SKILL, a.check_id, "covered", note=a.note,
                         evidence=[a.evidence] if a.evidence else None)
    print("%s %s marked covered." % (ledger.OK, a.check_id))
    return 0


def cmd_na(a):
    _need_ledger(a.dir)
    ledger.mark_coverage(a.dir, SKILL, a.check_id, "not_applicable", note=a.note)
    print("%s %s marked not applicable." % (ledger.OK, a.check_id))
    return 0


# ----------------------------------------------------------------- findings

def _wcag_ref(text):
    s = re.sub(r"^\s*wcag\s*", "", text.strip(), flags=re.I)
    return "WCAG " + s


def _default_refs(check_id):
    if check_id.startswith(PAIR_PREFIX):
        return ["WCAG 1.4.3"]
    for i in items()[0]:
        if i["id"] == check_id:
            return ["WCAG " + i["wcag"]]
    return None


def cmd_finding_add(a):
    if a.check_id not in _known_ids(a.dir):
        raise SystemExit("no check %r. See: contrast.py checklist" % a.check_id)
    refs = [_wcag_ref(a.wcag)] if a.wcag else _default_refs(a.check_id)
    f = ledger.new_finding(SKILL, a.title, a.severity, category=a.check_id, location=a.location,
                           repro=a.repro, fix=a.fix, evidence=[a.evidence] if a.evidence else [],
                           references=refs)
    ledger.write_finding(a.dir, f)
    # Recording a finding means the check was looked at.
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov and any(c["id"] == a.check_id for c in cov["checks"]):
        try:
            ledger.mark_coverage(a.dir, SKILL, a.check_id, "covered",
                                 evidence=[a.evidence] if a.evidence else None)
        except ledger.LedgerError:
            pass
    print("%s finding %r written (unverified). Verify it before it counts:" % (ledger.BAD, f["id"]))
    print("   contrast.py finding verify %s --by evidence-file" % f["id"])
    return 0


def cmd_finding_verify(a):
    ledger.verify(a.dir, SKILL, a.id, by=a.by, note=a.note)
    print("%s finding %r verified by %s." % (ledger.OK, a.id, a.by))
    return 0


def cmd_finding_reject(a):
    ledger.reject(a.dir, SKILL, a.id, a.reason)
    print("%s finding %r marked a false positive." % (ledger.OK, a.id))
    return 0


# --------------------------------------------------------------------- main

def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("ratio", help="contrast ratio of one colour pair, with the AA and AAA verdicts")
    r.add_argument("--fg", required=True, help="text colour: #rgb, #rrggbb or rgb(r, g, b)")
    r.add_argument("--bg", required=True, help="background colour")
    r.add_argument("--size", help="normal, large, or a font size such as 14pt, 18 or 24px (default: show both)")
    r.add_argument("--bold", action="store_true", help="the text is bold (14pt bold and up counts as large)")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_ratio)

    c = sub.add_parser("check", help="judge every pair in a pairs file at AA; --record writes the ledger")
    c.add_argument("--pairs", required=True, help="a JSON file of {id, fg, bg, size, bold, where} pairs")
    c.add_argument("--record", action="store_true", help="write coverage, a finding per failing pair, and the score")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_check)

    k = sub.add_parser("checklist", help="the manual review items; --record puts them in the coverage ledger")
    k.add_argument("--record", action="store_true", help="create the coverage ledger with every item not run")
    k.add_argument("--full", action="store_true", help="print the whole checklist.md, with the how-to of each item")
    k.set_defaults(func=cmd_checklist)

    cv = sub.add_parser("cover", help="mark an item looked at (say what you did in --note)")
    cv.add_argument("check_id")
    cv.add_argument("--note")
    cv.add_argument("--evidence", help="a file under proof/ (screenshot, notes, engine report)")
    cv.set_defaults(func=cmd_cover)

    na = sub.add_parser("na", help="mark an item not applicable (with a reason)")
    na.add_argument("check_id")
    na.add_argument("--note", required=True)
    na.set_defaults(func=cmd_na)

    fd = sub.add_parser("finding", help="add, verify or reject a finding")
    fsub = fd.add_subparsers(dest="faction", required=True)
    fa = fsub.add_parser("add")
    fa.add_argument("check_id", help="the checklist item, engine, or contrast-<pair id> it belongs to")
    fa.add_argument("--title", required=True)
    fa.add_argument("--severity", required=True, choices=ledger.SEVERITIES)
    fa.add_argument("--location", help="URL, selector or screen")
    fa.add_argument("--evidence", help="a file under proof/")
    fa.add_argument("--repro")
    fa.add_argument("--fix")
    fa.add_argument("--wcag", help="the success criterion, e.g. 1.1.1 (default: the item's own)")
    fa.set_defaults(func=cmd_finding_add)
    fv = fsub.add_parser("verify")
    fv.add_argument("id")
    fv.add_argument("--by", required=True, choices=["evidence-file", "repro-run", "manual"])
    fv.add_argument("--note")
    fv.set_defaults(func=cmd_finding_verify)
    fr = fsub.add_parser("reject")
    fr.add_argument("id")
    fr.add_argument("--reason", required=True)
    fr.set_defaults(func=cmd_finding_reject)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (ColorError, SizeError, num.MissingInput, num.BadNumber, ledger.LedgerError) as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    sys.exit(main())
