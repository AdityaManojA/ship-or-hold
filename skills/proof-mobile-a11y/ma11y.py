#!/usr/bin/env python3
"""Accessibility checks for a native mobile app from captured UI trees. Standard library only, offline.

Reads the UI tree of one screen: an Android `uiautomator dump` XML file, or a JSON list
of elements (the generic shape below, or iOS `idb ui describe-all` output as is). It
flags controls a screen reader cannot name, touch targets that are too small, labels
that say nothing, and, given a second tree captured at a large font size, text that is
cut off, truncated or overlapping.

    python3 ma11y.py check saved/home.xml
    python3 ma11y.py check saved/home.xml --large saved/home-large.xml --density 2.75
    python3 ma11y.py check saved/home.json --platform ios --json
    python3 ma11y.py record saved/home.xml --large saved/home-large.xml --screen home
    python3 ma11y.py status

Generic JSON tree (points on iOS, dp on Android):
    {"platform": "ios", "screen": {"w": 390, "h": 844},
     "elements": [{"id": "send", "label": "Send", "type": "Button", "traits": ["button"],
                   "clickable": true, "x": 300, "y": 780, "w": 60, "h": 44, "text": ""}]}
A bare list of elements is accepted too.
"""

import argparse
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-mobile-a11y"
MIN_TARGET = {"android": 48.0, "ios": 44.0}  # dp, pt
DEFAULT_DENSITY = 2.625  # 420 dpi, a common phone
ELLIPSIS = ("\u2026", "...")

CHECKS = [
    ("unlabeled", "Every control has a name a screen reader can say"),
    ("targets", "Touch targets meet the minimum size"),
    ("labels", "Control names are meaningful and distinct"),
    ("large-text", "Text survives the largest font size"),
]
TITLE = {"unlabeled": "Unlabelled controls", "targets": "Small touch targets",
         "labels": "Unclear control names", "large-text": "Text breaks at large font sizes"}
SEVERITY = {"unlabeled": "high", "targets": "medium", "labels": "low", "large-text": "medium"}
FIX = {
    "unlabeled": "Give each control a label: contentDescription (Android), accessibilityLabel (iOS), "
                 "or Semantics/aria-label in the cross-platform toolkit.",
    "targets": "Make the touchable area at least 48 x 48 dp (Android) or 44 x 44 pt (iOS), with padding "
               "or a larger hit area if the visible icon stays small.",
    "labels": "Name what the control does (\"Send message\", \"Close\"), not what it is, and make labels "
              "on the same screen distinct.",
    "large-text": "Let text wrap and containers grow at large font sizes; avoid fixed heights and single-line "
                  "limits on text that matters.",
}

INTERACTIVE_ANDROID = re.compile(r"(Button|ImageButton|Switch|CheckBox|RadioButton|ToggleButton|Spinner|SeekBar|"
                                 r"EditText|Chip|FloatingActionButton)$")
INTERACTIVE_TYPES = {"button", "link", "switch", "toggle", "checkbox", "radiobutton", "slider", "textfield",
                     "securetextfield", "searchfield", "tab", "menuitem", "segmentedcontrol", "stepper",
                     "picker", "cell", "imagebutton", "image button"}
TEXT_TYPES = {"statictext", "text", "label", "textview", "heading", "header"}
MEANINGLESS = {"button", "image", "icon", "img", "picture", "photo", "graphic", "unlabeled", "untitled",
               "label", "view", "imageview", "btn", "tap", "click", "click here", "tap here", "null", "none"}
FILE_LIKE = re.compile(r"\.(png|jpe?g|svg|webp|gif|pdf)$|^(ic|img|icon|image|btn|button|iv)[_\-][\w\-]*$|"
                       r"^[a-z]+_\d+$", re.I)


# ---------------------------------------------------------------- parsing

def _bounds(text):
    m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", text or "")
    if not m:
        return 0, 0, 0, 0
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    return x1, y1, x2 - x1, y2 - y1


def parse_android(text, density):
    """Elements from a uiautomator dump, converted from px to dp."""
    root = ET.fromstring(text)
    out = []

    def label_below(node):
        for child in node.iter("node"):
            if child is not node and ((child.get("text") or "").strip() or (child.get("content-desc") or "").strip()):
                return True
        return False

    for n, node in enumerate(root.iter("node")):
        own = (node.get("content-desc") or "").strip() or (node.get("text") or "").strip()
        cls = node.get("class") or ""
        clickable = node.get("clickable") == "true" or node.get("checkable") == "true"
        interactive = clickable or bool(INTERACTIVE_ANDROID.search(cls))
        x, y, w, h = _bounds(node.get("bounds"))
        out.append({
            "id": node.get("resource-id") or "%s#%d" % (cls.rsplit(".", 1)[-1] or "node", n),
            "label": own,
            # TalkBack reads a clickable group's children aloud, so labelled children name the group.
            "named": bool(own) or (interactive and label_below(node)),
            "type": cls.rsplit(".", 1)[-1],
            "interactive": interactive,
            "is_text": bool((node.get("text") or "").strip()),
            "text": (node.get("text") or "").strip(),
            "x": x / density, "y": y / density, "w": w / density, "h": h / density,
            "visible": w > 0 and h > 0 and node.get("visible-to-user", "true") != "false",
        })
    return out


def _frame(e):
    if all(k in e for k in ("x", "y", "w", "h")):
        return float(e["x"]), float(e["y"]), float(e["w"]), float(e["h"])
    f = e.get("frame")
    if isinstance(f, dict):
        return (float(f.get("x", 0)), float(f.get("y", 0)), float(f.get("width", f.get("w", 0))),
                float(f.get("height", f.get("h", 0))))
    m = re.findall(r"-?\d+(?:\.\d+)?", str(e.get("AXFrame") or ""))
    if len(m) == 4:
        return tuple(float(v) for v in m)
    return 0.0, 0.0, 0.0, 0.0


def parse_generic(obj):
    """Elements from the generic JSON shape or idb `ui describe-all` output."""
    items = obj.get("elements", []) if isinstance(obj, dict) else obj
    out = []
    for n, e in enumerate(items or []):
        if not isinstance(e, dict):
            continue
        typ = str(e.get("type") or e.get("role") or e.get("class") or "")
        traits = e.get("traits") or []
        if isinstance(traits, str):
            traits = [traits]
        role = str(e.get("role") or "").replace("AX", "")
        kind = re.sub(r"[^a-z ]", "", typ.lower())
        interactive = bool(e.get("clickable")) or kind in INTERACTIVE_TYPES or role.lower() in INTERACTIVE_TYPES \
            or any(str(t).lower() in ("button", "link") for t in traits)
        label = str(e.get("label") or e.get("AXLabel") or e.get("content-desc") or "").strip()
        text = str(e.get("text") or e.get("value") or e.get("AXValue") or "").strip()
        is_text = kind in TEXT_TYPES or role.lower() in TEXT_TYPES or bool(text)
        x, y, w, h = _frame(e)
        out.append({
            "id": str(e.get("id") or e.get("AXUniqueId") or e.get("identifier") or "%s#%d" % (typ or "element", n)),
            "label": label or (text if is_text else ""),
            "named": bool(label or text),
            "type": typ, "interactive": interactive, "is_text": is_text, "text": text or label,
            "x": x, "y": y, "w": w, "h": h, "visible": w > 0 and h > 0,
        })
    return out


def load_tree(path, platform=None, density=DEFAULT_DENSITY):
    """Return (platform, elements, screen {w,h}) for a saved tree file."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    if text.lstrip().startswith("<"):
        els = parse_android(text, density)
        plat = platform or "android"
        # The first node is the window, so its bounds are the screen.
        screen = {"w": els[0]["x"] + els[0]["w"], "h": els[0]["y"] + els[0]["h"]} if els else None
    else:
        obj = json.loads(text)
        els = parse_generic(obj)
        plat = platform or (obj.get("platform") if isinstance(obj, dict) else None) or "ios"
        screen = obj.get("screen") if isinstance(obj, dict) else None
    if screen and screen.get("w"):
        sw, sh = float(screen["w"]), float(screen.get("h") or 0)
    else:
        vis = [e for e in els if e["visible"]]
        sw = max([e["x"] + e["w"] for e in vis] or [0])
        sh = max([e["y"] + e["h"] for e in vis] or [0])
    return plat, els, {"w": sw, "h": sh}


# ---------------------------------------------------------------- checks

def _where(e):
    return "%s at %d,%d size %dx%d" % (e["id"], round(e["x"]), round(e["y"]), round(e["w"]), round(e["h"]))


def _overlap(a, b):
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return w > 1 and h > 1


def _inside(a, b):
    return a["x"] >= b["x"] and a["y"] >= b["y"] and a["x"] + a["w"] <= b["x"] + b["w"] and a["y"] + a["h"] <= b["y"] + b["h"]


def analyse(platform, els, large=None):
    """Return {check_id: [problem text]} for one screen."""
    out = dict((c, []) for c, _ in CHECKS)
    controls = [e for e in els if e["interactive"] and e["visible"]]
    minimum = MIN_TARGET.get(platform, 48.0)
    unit = "dp" if platform == "android" else "pt"
    for e in controls:
        if not e["named"]:
            out["unlabeled"].append("%s (%s) has no label" % (_where(e), e["type"] or "control"))
        if e["w"] < minimum or e["h"] < minimum:
            out["targets"].append("%s is %dx%d %s, under %d x %d" % (
                e["id"], round(e["w"]), round(e["h"]), unit, minimum, minimum))
        lab = e["label"].strip()
        if lab and (lab.lower() in MEANINGLESS or FILE_LIKE.search(lab)):
            out["labels"].append("%s is announced as \"%s\"" % (e["id"], lab))
    seen = {}
    for e in controls:
        lab = e["label"].strip().lower()
        if lab:
            seen.setdefault(lab, []).append(e)
    for lab, group in sorted(seen.items()):
        if len(group) > 1:
            out["labels"].append("%d controls are all announced as \"%s\": %s" % (
                len(group), group[0]["label"], ", ".join(g["id"] for g in group[:5])))
    if large is not None:
        _, lels, screen = large
        texts = [e for e in lels if e["is_text"] and e["visible"]]
        for e in texts:
            if e["text"].endswith(ELLIPSIS):
                out["large-text"].append("\"%s\" is truncated at the large font size (%s)" % (e["text"][:40], e["id"]))
            elif screen["w"] and (e["x"] < -1 or e["x"] + e["w"] > screen["w"] + 1):
                out["large-text"].append("\"%s\" runs off the screen at the large font size (%s)" % (e["text"][:40], e["id"]))
        for i, a in enumerate(texts):
            for b in texts[i + 1:]:
                if _overlap(a, b) and not _inside(a, b) and not _inside(b, a):
                    out["large-text"].append("\"%s\" overlaps \"%s\" at the large font size" % (a["text"][:30], b["text"][:30]))
    return out


# ---------------------------------------------------------------- commands

def _density(a):
    if getattr(a, "dpi", None):
        return a.dpi / 160.0
    return a.density or DEFAULT_DENSITY


def _run(a):
    d = _density(a)
    plat, els, screen = load_tree(a.tree, a.platform, d)
    large = load_tree(a.large, plat, d) if a.large else None
    return plat, els, analyse(plat, els, large)


def cmd_check(a):
    plat, els, res = _run(a)
    if a.json:
        print(json.dumps({"platform": plat, "elements": len(els), "problems": res}, indent=2))
        return 0
    controls = sum(1 for e in els if e["interactive"] and e["visible"])
    print("Mobile accessibility check of %s (%s, %d controls)" % (a.tree, plat, controls))
    for cid, title in CHECKS:
        if cid == "large-text" and not a.large:
            print("  -- %s: not checked (pass --large with a tree captured at the largest font size)" % title)
            continue
        probs = res[cid]
        print("  %s %s%s" % (ledger.BAD if probs else ledger.OK, title, (": %d" % len(probs)) if probs else ""))
        for p in probs[:a.show]:
            print("      %s" % p)
    return 0


def _evidence(d, path, screen, suffix):
    ext = os.path.splitext(path)[1] or ".txt"
    dest = os.path.join(ledger.evidence_dir(d, SKILL), "%s%s%s" % (screen, suffix, ext))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


def cmd_record(a):
    plat, els, res = _run(a)
    screen = ledger.slug(a.screen or os.path.splitext(os.path.basename(a.tree))[0])
    checks = [("%s-%s" % (screen, cid), "%s: %s" % (screen, title)) for cid, title in CHECKS]
    old = ledger.load_coverage(a.dir, SKILL)
    keep = [(c["id"], c["title"]) for c in (old or {}).get("checks", []) if c["id"] not in dict(checks)]
    ledger.init_coverage(a.dir, SKILL, keep + checks)
    ev = [_evidence(a.dir, a.tree, screen, "")]
    if a.large:
        ev.append(_evidence(a.dir, a.large, screen, "-large"))
    found = 0
    for cid, _ in CHECKS:
        check_id = "%s-%s" % (screen, cid)
        fid = ledger.slug("ma11y-%s-%s" % (screen, cid))[:60]
        path = ledger.finding_path(a.dir, SKILL, fid)
        existing = ledger.load_finding(a.dir, SKILL, fid) if os.path.exists(path) else None
        if cid == "large-text" and not a.large:
            ledger.mark_coverage(a.dir, SKILL, check_id, "not_run",
                                 note="needs a tree captured at the largest font size (--large)")
            continue
        probs = res[cid]
        ledger.mark_coverage(a.dir, SKILL, check_id, "covered", evidence=ev, note="%d problem(s)" % len(probs))
        if existing and existing["status"] == "false_positive":
            continue  # the user ruled this out; leave it alone
        if not probs:
            if existing:
                os.remove(path)
            continue
        f = ledger.new_finding(SKILL, "%s on %s: %d problem(s)" % (TITLE[cid], screen, len(probs)),
                               SEVERITY[cid], id=fid, category="mobile-a11y:%s" % cid, evidence=ev,
                               location="%s (%s)" % (screen, plat), fix=FIX[cid],
                               description="; ".join(probs[:12])[:1500])
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
        found += 1
    print("%s recorded %s (%s): %d finding(s)." % (ledger.OK if not found else ledger.BAD, screen, plat, found))
    for cid, _ in CHECKS:
        for p in res[cid][:3]:
            print("  [%s] %s" % (SEVERITY[cid], p))
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
    for name, func, hlp in (("check", cmd_check, "print the problems on one screen"),
                            ("record", cmd_record, "write one screen's results to the ledger")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("tree", help="uiautomator XML, idb describe-all JSON, or the generic JSON tree")
        p.add_argument("--large", help="the same screen captured at the largest font size")
        p.add_argument("--platform", choices=["android", "ios"], help="default: android for XML, ios for JSON")
        p.add_argument("--density", type=float, help="Android px per dp (default %.3f, i.e. 420 dpi)" % DEFAULT_DENSITY)
        p.add_argument("--dpi", type=float, help="Android screen dpi, instead of --density")
        if name == "check":
            p.add_argument("--json", action="store_true")
            p.add_argument("--show", type=int, default=5, help="problems to print per check")
        else:
            p.add_argument("--screen", help="name for this screen (default: the file name)")
        p.set_defaults(func=func)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
