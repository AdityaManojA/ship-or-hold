#!/usr/bin/env python3
"""Check that a built app screen matches its design, and flag new or missing UI elements.

Standard library only, no network, no design-tool account. Two layers:

* Screen against design: an app screenshot PNG against a design export PNG (for
  example a Figma frame exported at the device's scale). A design exported at a
  different scale is resampled to the screenshot's size. Regions that legitimately
  differ (status bar, live data) can be ignored. The result is the percent of
  pixels that differ, a diff image, and a score against a budget.
* Elements against the last build: two UI element trees (an Android
  ``uiautomator dump`` XML, or a JSON list of elements) are matched by id, then by
  label and type. Elements that appeared, disappeared, moved or changed size beyond
  a tolerance are reported. An optional design spec (the same JSON shape) lists the
  elements the design expects.

    python3 design.py compare --screen shots/login.png --design figma/login@3x.png --name login
    python3 design.py elements --current trees/login.xml --previous trees/login-old.xml
    python3 design.py record --name login --screen shots/login.png --design figma/login@3x.png \\
        --current trees/login.xml --previous trees/login-old.xml --spec spec/login.json
    python3 design.py status

Element JSON: a list (or {"elements": [...]}) of {"id", "label", "type", "x", "y",
"w", "h"}. Keys from iOS accessibility dumps also work: AXUniqueId or identifier for
id, AXLabel or text for label, AXRole or role for type, and a "frame" of
{x, y, width, height}.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

_spec = importlib.util.spec_from_file_location("proof_visual", os.path.join(HERE, "..", "proof-visual", "visual.py"))
visual = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(visual)

SKILL = "proof-design-match"
PREFIX = "design-"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


# ------------------------------------------------------------ screen layer

def resample(img, width, height):
    """Nearest-neighbour resample of a decoded image (w, h, rgba) to width x height."""
    w, h, px = img
    if (w, h) == (width, height):
        return img
    out = bytearray(width * height * 4)
    xs = [min(w - 1, x * w // width) for x in range(width)]
    for y in range(height):
        sy = min(h - 1, y * h // height)
        src = sy * w * 4
        dst = y * width * 4
        for x, sx in enumerate(xs):
            o = src + sx * 4
            out[dst + x * 4:dst + x * 4 + 4] = px[o:o + 4]
    return width, height, out


def parse_region(text):
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 4:
        raise SystemExit("--ignore takes x,y,w,h in screenshot pixels, got %r" % text)
    try:
        x, y, w, h = (int(p) for p in parts)
    except ValueError:
        raise SystemExit("--ignore takes whole numbers x,y,w,h, got %r" % text)
    if w <= 0 or h <= 0:
        raise SystemExit("--ignore needs a positive width and height, got %r" % text)
    return x, y, w, h


def mask(design, screen, regions):
    """Copy the screen's pixels into the design inside each ignored region, so they never count."""
    w, h, dpx = design
    spx = screen[2]
    dpx = bytearray(dpx)
    for x, y, rw, rh in regions:
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + rw), min(h, y + rh)
        for row in range(y0, y1):
            a, b = (row * w + x0) * 4, (row * w + x1) * 4
            if a < b:
                dpx[a:b] = spx[a:b]
    return w, h, dpx


def compare_screen(screen, design, regions=(), threshold=0.1):
    """Compare a screenshot with a design export. Returns visual's result plus the scale used."""
    sw, sh = screen[0], screen[1]
    dw, dh = design[0], design[1]
    if abs(dw * sh - dh * sw) > max(dw, dh, sw, sh):  # aspect ratios differ by more than a pixel
        raise SystemExit("the design (%dx%d) and the screenshot (%dx%d) have different shapes. Export the frame "
                         "for the same device, or crop the screenshot to the frame." % (dw, dh, sw, sh))
    scaled = resample(design, sw, sh)
    if regions:
        scaled = mask(scaled, screen, regions)
    result = visual.compare_images(scaled, screen, threshold)
    result["scale"] = round(float(sw) / dw, 4)
    return result


# ----------------------------------------------------------- element layer

def _short_type(cls):
    return (cls or "").rsplit(".", 1)[-1]


def parse_uiautomator(text):
    """Elements from an Android ``uiautomator dump`` XML. Containers with no id or label are skipped."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise SystemExit("the element tree is not valid XML: %s" % e)
    out = []
    for node in root.iter("node"):
        a = node.attrib
        m = _BOUNDS.match(a.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = (int(v) for v in m.groups())
        el = {"id": a.get("resource-id", ""), "label": a.get("text") or a.get("content-desc") or "",
              "type": _short_type(a.get("class")), "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}
        if el["id"] or el["label"]:
            out.append(el)
    return out


def _first(d, keys, default=""):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def parse_json_elements(obj):
    """Elements from the generic JSON shape, accepting common iOS accessibility keys."""
    items = obj.get("elements", []) if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        raise SystemExit("element JSON must be a list, or an object with an \"elements\" list")
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        frame = raw.get("frame") or raw.get("rect") or {}
        try:
            x = float(_first(raw, ["x"], frame.get("x", 0)))
            y = float(_first(raw, ["y"], frame.get("y", 0)))
            w = float(_first(raw, ["w", "width"], frame.get("width", frame.get("w", 0))))
            h = float(_first(raw, ["h", "height"], frame.get("height", frame.get("h", 0))))
        except (TypeError, ValueError):
            raise SystemExit("element %r has a position or size that is not a number" % raw)
        el = {"id": str(_first(raw, ["id", "identifier", "AXUniqueId", "resource-id"])),
              "label": str(_first(raw, ["label", "AXLabel", "text", "title", "name"])),
              "type": _short_type(str(_first(raw, ["type", "role", "AXRole", "class"]))),
              "x": x, "y": y, "w": w, "h": h}
        if el["id"] or el["label"]:
            out.append(el)
    return out


def load_elements(path):
    if not os.path.isfile(path):
        raise SystemExit("no such element file: %s" % path)
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    if text.lstrip().startswith("<"):
        return parse_uiautomator(text)
    try:
        return parse_json_elements(json.loads(text))
    except ValueError as e:
        raise SystemExit("%s is neither a uiautomator XML dump nor element JSON: %s" % (path, e))


def name_of(el):
    """How an element is shown in a report."""
    bits = [b for b in (el["id"], '"%s"' % el["label"] if el["label"] else "", el["type"]) if b]
    return " ".join(bits)


def _key(el):
    return ("id", el["id"]) if el["id"] else ("lt", el["label"], el["type"])


def match(expected, actual):
    """Pair elements by id, then by label and type, in order for repeated ones.

    Returns (pairs, missing, new): pairs of (expected, actual), elements only in
    expected, elements only in actual.
    """
    pool = {}
    for el in actual:
        pool.setdefault(_key(el), []).append(el)
    pairs, missing, used = [], [], set()
    leftovers = []
    for el in expected:
        bucket = pool.get(_key(el))
        if bucket:
            got = bucket.pop(0)
            pairs.append((el, got))
            used.add(id(got))
        else:
            leftovers.append(el)
    # Second pass: an element whose id changed may still match on label and type.
    rest = {}
    for el in actual:
        if id(el) not in used and el["label"]:
            rest.setdefault((el["label"], el["type"]), []).append(el)
    for el in leftovers:
        bucket = rest.get((el["label"], el["type"])) if el["label"] else None
        if bucket:
            got = bucket.pop(0)
            pairs.append((el, got))
            used.add(id(got))
        else:
            missing.append(el)
    new = [el for el in actual if id(el) not in used]
    return pairs, missing, new


def drifted(a, b, tol_px, tol_pct):
    """Reasons an element moved or changed size beyond tolerance, or an empty list."""
    out = []
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    if abs(dx) > tol_px or abs(dy) > tol_px:
        out.append("moved by (%+g, %+g) px" % (dx, dy))
    for dim, word in (("w", "width"), ("h", "height")):
        limit = max(tol_px, a[dim] * tol_pct / 100.0)
        if abs(b[dim] - a[dim]) > limit:
            out.append("%s %g -> %g px" % (word, a[dim], b[dim]))
    return out


def diff_elements(current, previous=None, spec=None, tol_px=4, tol_pct=2):
    """Compare the current tree with the last build and/or a design spec."""
    report = {"current": len(current)}
    for label, other in (("previous", previous), ("spec", spec)):
        if other is None:
            continue
        pairs, missing, new = match(other, current)
        report[label] = {
            "compared": len(other),
            "missing": missing,
            "new": new,
            "drifted": [(a, b, why) for a, b in pairs for why in [drifted(a, b, tol_px, tol_pct)] if why],
        }
    return report


# --------------------------------------------------------------- helpers

def _check_name(name):
    if not _NAME.match(name or ""):
        raise SystemExit("--name %r must be lowercase letters, digits and hyphens (for example login)" % name)
    return name


def _rel(d, path):
    return os.path.relpath(path, ledger.root(d)).replace(os.sep, "/")


def _copy_in(d, src, dest_name):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), dest_name)
    if os.path.abspath(src) != os.path.abspath(dest):
        shutil.copyfile(src, dest)
    return _rel(d, dest)


def _read_png(path):
    try:
        return visual.read_png(path)
    except visual.PngError as e:
        raise SystemExit(str(e))


def _pct(a, field):
    v = num.parse_number(a, field=field)
    if not 0 <= v <= 100:
        raise SystemExit("%s is a percent from 0 to 100, got %s" % (field, a))
    return v


def _print_elements(rep):
    for label in ("previous", "spec"):
        r = rep.get(label)
        if r is None:
            continue
        against = "the last build" if label == "previous" else "the design spec"
        bad = r["missing"] or r["new"] or r["drifted"]
        print("%s against %s: %d expected, %d on screen, %d missing, %d new, %d moved or resized"
              % (ledger.BAD if bad else ledger.OK, against, r["compared"], rep["current"], len(r["missing"]),
                 len(r["new"]), len(r["drifted"])))
        for el in r["missing"]:
            print("   missing: %s" % name_of(el))
        for el in r["new"]:
            print("   new:     %s" % name_of(el))
        for a, b, why in r["drifted"]:
            print("   changed: %s, %s" % (name_of(b), "; ".join(why)))


# --------------------------------------------------------------- commands

def cmd_compare(a):
    threshold = num.parse_number(a.threshold, field="--threshold")
    regions = [parse_region(r) for r in a.ignore]
    r = compare_screen(_read_png(a.screen), _read_png(a.design), regions, threshold)
    if a.name:
        name = _check_name(a.name)
        out = os.path.join(ledger.evidence_dir(a.dir, SKILL), name + "-diff.png")
        w, h, rgb = r["diff"]
        visual.write_png(out, w, h, rgb, 3)
        print("   diff image: %s" % _rel(a.dir, out))
    budget = _pct(a.max_diff, "--max-diff")
    over = r["percent"] > budget
    print("%s %d of %d pixels differ from the design (%.4f%%, budget %g%%), design scaled x%g, %s."
          % (ledger.BAD if over else ledger.OK, r["differing"], r["total"], r["percent"], budget, r["scale"],
             visual.describe_bbox(r["bbox"])))
    return 1 if over else 0


def cmd_elements(a):
    if not (a.previous or a.spec):
        raise SystemExit("give --previous (the last build's tree), --spec (the design's elements), or both")
    rep = diff_elements(load_elements(a.current), load_elements(a.previous) if a.previous else None,
                        load_elements(a.spec) if a.spec else None, a.tolerance, a.tolerance_pct)
    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    _print_elements(rep)
    return 0


def _clear(d, name):
    """Drop the findings an earlier record of this screen wrote, so a re-run starts clean."""
    for f in ledger.load_findings(d, SKILL):
        if f["id"].startswith(PREFIX + name + "-"):
            os.remove(ledger.finding_path(d, SKILL, f["id"]))


def _finding(d, fid, title, sev, evidence, location, fix, desc=""):
    f = ledger.new_finding(SKILL, title[:160], sev, id=ledger.slug(fid)[:60], category="design", evidence=evidence,
                           location=location, fix=fix, description=desc or None)
    ledger.write_finding(d, f)
    ledger.verify(d, SKILL, f["id"], by="evidence-file")
    return f


def cmd_record(a):
    name = _check_name(a.name)
    if bool(a.screen) != bool(a.design):
        raise SystemExit("--screen and --design go together")
    if not a.screen and not a.current:
        raise SystemExit("give --screen with --design, and/or --current with --previous or --spec")
    if a.current and not (a.previous or a.spec):
        raise SystemExit("--current needs --previous, --spec, or both")
    checks = []
    if a.screen:
        checks.append((name + "-screen", "%s screen matches the design" % name))
    if a.current:
        checks.append((name + "-elements", "%s elements match the last build or the design spec" % name))
    existing = ledger.load_coverage(a.dir, SKILL) if os.path.exists(ledger.coverage_path(a.dir, SKILL)) else None
    keep = [(c["id"], c["title"]) for c in (existing or {}).get("checks", []) if c["id"] not in dict(checks)]
    ledger.init_coverage(a.dir, SKILL, keep + checks)
    _clear(a.dir, name)
    written = []

    if a.screen:
        threshold = num.parse_number(a.threshold, field="--threshold")
        budget = _pct(a.max_diff, "--max-diff")
        regions = [parse_region(r) for r in a.ignore]
        r = compare_screen(_read_png(a.screen), _read_png(a.design), regions, threshold)
        diff_path = os.path.join(ledger.evidence_dir(a.dir, SKILL), name + "-diff.png")
        w, h, rgb = r["diff"]
        visual.write_png(diff_path, w, h, rgb, 3)
        ev = [_rel(a.dir, diff_path), _copy_in(a.dir, a.screen, name + "-screen.png"),
              _copy_in(a.dir, a.design, name + "-design.png")]
        pct = round(r["percent"], 4)
        metrics = [m for m in _scores(a.dir) if m["id"] != "design-diff-pct-" + name]
        metrics.append({"id": "design-diff-pct-" + name, "value": pct, "unit": "%", "budget": budget, "op": "<=",
                        "source": "design.py, threshold %s, design scaled x%g" % (threshold, r["scale"]),
                        "evidence": ev[:1]})
        ledger.write_scores(a.dir, SKILL, sorted(metrics, key=lambda m: m["id"]))
        ledger.mark_coverage(a.dir, SKILL, name + "-screen", "covered", evidence=ev[:1],
                             note="%.4f%% differs; %s" % (pct, visual.describe_bbox(r["bbox"])))
        if pct > budget:
            written.append(_finding(
                a.dir, "%s%s-screen" % (PREFIX, name), "%s screen differs from the design by %.2f%% of pixels" % (name, pct),
                "medium", ev, "%s (%s)" % (name, visual.describe_bbox(r["bbox"])),
                "Compare the diff image with the design and fix the build, or export the design again if it changed.",
                "Budget %g%%; a pixel counts when a channel moves more than %s of its range. Red in the diff image "
                "marks where the build and the design disagree." % (budget, threshold)))

    if a.current:
        cur = load_elements(a.current)
        prev = load_elements(a.previous) if a.previous else None
        spec = load_elements(a.spec) if a.spec else None
        ev = [_copy_in(a.dir, a.current, name + "-tree-current" + os.path.splitext(a.current)[1])]
        if a.previous:
            ev.append(_copy_in(a.dir, a.previous, name + "-tree-previous" + os.path.splitext(a.previous)[1]))
        if a.spec:
            ev.append(_copy_in(a.dir, a.spec, name + "-spec" + os.path.splitext(a.spec)[1]))
        rep = diff_elements(cur, prev, spec, a.tolerance, a.tolerance_pct)
        counts = []
        for label in ("previous", "spec"):
            res = rep.get(label)
            if res is None:
                continue
            against = "the last build" if label == "previous" else "the design"
            counts.append("%s: %d missing, %d new, %d changed" % (label, len(res["missing"]), len(res["new"]),
                                                                  len(res["drifted"])))
            for el in res["missing"]:
                written.append(_finding(
                    a.dir, "%s%s-%s-missing-%s" % (PREFIX, name, label, name_of(el)),
                    "%s: %s is in %s but not on screen" % (name, name_of(el), against), "medium", ev,
                    "%s at (%g, %g)" % (name, el["x"], el["y"]),
                    "Put the element back, or update %s if it was removed on purpose." % against))
            for el in res["new"]:
                written.append(_finding(
                    a.dir, "%s%s-%s-new-%s" % (PREFIX, name, label, name_of(el)),
                    "%s: %s is on screen but not in %s" % (name, name_of(el), against), "low", ev,
                    "%s at (%g, %g)" % (name, el["x"], el["y"]),
                    "Confirm the new element is intended and designed; then update %s." % against))
            for old, new, why in res["drifted"]:
                written.append(_finding(
                    a.dir, "%s%s-%s-moved-%s" % (PREFIX, name, label, name_of(new)),
                    "%s: %s %s compared with %s" % (name, name_of(new), "; ".join(why), against), "low", ev,
                    "%s at (%g, %g)" % (name, new["x"], new["y"]),
                    "Check the layout against %s; tolerance %g px or %g%%." % (against, a.tolerance, a.tolerance_pct)))
        ledger.mark_coverage(a.dir, SKILL, name + "-elements", "covered", evidence=ev, note="; ".join(counts))

    print("%s recorded %s: %d finding(s)." % (ledger.OK if not written else ledger.BAD, name, len(written)))
    for f in written:
        print("  [%s] %s" % (f["severity"], f["title"]))
    return 0


def _scores(d):
    path = ledger.scores_path(d, SKILL)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("metrics", [])


def cmd_status(a):
    for m in _scores(a.dir):
        print("  %s %s: %.4f%% (budget %g%%)" % (ledger.OK if ledger.metric_passes(m) else ledger.BAD, m["id"],
                                                 m["value"], m["budget"]))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def screen_args(p, required):
        p.add_argument("--screen", required=required, help="the app screenshot (PNG)")
        p.add_argument("--design", required=required, help="the design export (PNG), any whole scale")
        p.add_argument("--ignore", action="append", default=[], help="x,y,w,h region to ignore (repeatable)")
        p.add_argument("--threshold", default="0.1", help="channel difference (0 to 1) that counts as changed")
        p.add_argument("--max-diff", default="2", help="percent of pixels allowed to differ (default 2)")

    def element_args(p, required):
        p.add_argument("--current", required=required, help="this build's element tree (uiautomator XML or JSON)")
        p.add_argument("--previous", help="the last build's element tree")
        p.add_argument("--spec", help="the design's expected elements (JSON)")
        p.add_argument("--tolerance", type=float, default=4, help="pixels an element may move (default 4)")
        p.add_argument("--tolerance-pct", type=float, default=2, help="percent its size may change (default 2)")

    c = sub.add_parser("compare", help="compare one screenshot with its design export")
    screen_args(c, True)
    c.add_argument("--name", help="screen name; also writes a diff image to evidence")
    c.set_defaults(func=cmd_compare)
    e = sub.add_parser("elements", help="compare element trees: new, missing, moved")
    element_args(e, True)
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_elements)
    r = sub.add_parser("record", help="write both layers for one screen to the ledger")
    r.add_argument("--name", required=True, help="screen name, e.g. login")
    screen_args(r, False)
    element_args(r, False)
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded scores and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
