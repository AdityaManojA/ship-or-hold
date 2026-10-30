#!/usr/bin/env python3
"""Compare screenshots against a saved baseline and flag pages whose layout changed.

Standard library only, no network. Reads and writes PNG itself (8-bit RGB or
RGBA, not interlaced), so nothing beyond Python 3.8 is needed. Every compared
name becomes a coverage check and a score (the percent of pixels that changed,
against a budget). A comparison over budget becomes a finding backed by a diff
image: red where pixels changed, a faded copy of the page everywhere else.

    python3 visual.py baseline home shots/home.png
    python3 visual.py compare home shots/home.png --threshold 0.1 --max-diff 0.5
    python3 visual.py approve home shots/home.png
    python3 visual.py status
"""

import argparse
import json
import os
import re
import shutil
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-visual"
CATEGORY = "visual"
FINDING_PREFIX = "visual-"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SAVE_HINT = "Save the screenshot as an 8-bit RGB or RGBA PNG (not palette, not 16-bit, not interlaced) and try again."
COLOR_TYPES = {2: 3, 6: 4}  # PNG colour type -> channels we read
COLOR_NAMES = {0: "greyscale", 2: "RGB", 3: "palette (indexed colour)", 4: "greyscale with alpha", 6: "RGBA"}


class PngError(ValueError):
    """The file is not a PNG this tool can read."""


# -------------------------------------------------------------------- PNG

def _u32(b, i):
    return int.from_bytes(b[i:i + 4], "big")


def _p32(n):
    return int(n).to_bytes(4, "big")


def decode_png(blob):
    """Return (width, height, rgba) where rgba is a bytearray of width*height*4.

    Raises PngError for anything outside 8-bit RGB / RGBA, non-interlaced.
    """
    if blob[:8] != PNG_SIGNATURE:
        raise PngError("not a PNG file (the first 8 bytes are not the PNG signature)")
    pos, header, idat = 8, None, []
    while pos + 8 <= len(blob):
        length = _u32(blob, pos)
        ctype = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        crc = blob[pos + 8 + length:pos + 12 + length]
        if len(data) != length or len(crc) != 4:
            raise PngError("the PNG is truncated inside the %r chunk" % ctype.decode("latin-1"))
        if _u32(crc, 0) != (zlib.crc32(ctype + data) & 0xFFFFFFFF):
            raise PngError("the PNG is corrupt (bad checksum on the %r chunk)" % ctype.decode("latin-1"))
        pos += 12 + length
        if ctype == b"IHDR":
            if length != 13:
                raise PngError("the PNG header chunk has the wrong size")
            header = (_u32(data, 0), _u32(data, 4), data[8], data[9], data[10], data[11], data[12])
        elif ctype == b"IDAT":
            idat.append(data)
        elif ctype == b"IEND":
            break
    if header is None:
        raise PngError("the PNG has no header chunk")
    width, height, depth, color, compression, filtering, interlace = header
    if width == 0 or height == 0:
        raise PngError("the PNG has no pixels (%dx%d)" % (width, height))
    if color not in COLOR_TYPES:
        raise PngError("%s PNGs are not supported" % COLOR_NAMES.get(color, "colour type %d" % color))
    if depth != 8:
        raise PngError("%d-bit PNGs are not supported, only 8 bits per channel" % depth)
    if interlace != 0:
        raise PngError("interlaced PNGs are not supported")
    if compression != 0 or filtering != 0:
        raise PngError("the PNG uses an unknown compression or filter method")
    if not idat:
        raise PngError("the PNG has no image data")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise PngError("the PNG image data does not decompress: %s" % e)
    bpp = COLOR_TYPES[color]
    stride = width * bpp
    if len(raw) < (stride + 1) * height:
        raise PngError("the PNG image data is shorter than %dx%d pixels" % (width, height))
    pixels = unfilter(raw, width, height, bpp)
    return width, height, to_rgba(pixels, width * height, bpp)


def unfilter(raw, width, height, bpp):
    """Undo the per-row PNG filters (None, Sub, Up, Average, Paeth)."""
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        row = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 255
        elif ftype == 2:
            row = bytearray((a + b) & 255 for a, b in zip(row, prev))
        elif ftype == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif ftype == 4:
            for i in range(bpp):
                row[i] = (row[i] + prev[i]) & 255  # a = c = 0, so Paeth picks the byte above
            for i in range(bpp, stride):
                a, b, c = row[i - bpp], prev[i], prev[i - bpp]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - c - c)
                if pa <= pb and pa <= pc:
                    row[i] = (row[i] + a) & 255
                elif pb <= pc:
                    row[i] = (row[i] + b) & 255
                else:
                    row[i] = (row[i] + c) & 255
        else:
            raise PngError("row %d uses unknown filter type %d" % (y, ftype))
        out[y * stride:(y + 1) * stride] = row
        prev = row
    return out


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def to_rgba(pixels, count, bpp):
    if bpp == 4:
        return bytearray(pixels)
    out = bytearray(b"\xff" * (count * 4))
    for ch in range(3):
        out[ch::4] = pixels[ch::3]
    return out


def _filter_row(ftype, row, prev, bpp):
    if ftype == 0:
        return bytearray(row)
    out = bytearray(len(row))
    for i in range(len(row)):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        pred = {1: a, 2: b, 3: (a + b) >> 1, 4: _paeth(a, b, c) if ftype == 4 else 0}[ftype]
        out[i] = (row[i] - pred) & 255
    return out


def encode_png(width, height, pixels, channels=3, filter_type=0):
    """Return PNG bytes for 8-bit RGB (channels=3) or RGBA (channels=4) pixels.

    ``filter_type`` (0-4) applies that PNG filter to every row; 0 is the default.
    """
    if channels not in (3, 4):
        raise ValueError("channels must be 3 (RGB) or 4 (RGBA)")
    if filter_type not in (0, 1, 2, 3, 4):
        raise ValueError("filter_type must be 0 to 4")
    stride = width * channels
    if len(pixels) != stride * height:
        raise ValueError("expected %d bytes of pixels, got %d" % (stride * height, len(pixels)))
    raw = bytearray()
    prev = bytearray(stride)
    for y in range(height):
        row = pixels[y * stride:(y + 1) * stride]
        raw.append(filter_type)
        raw += _filter_row(filter_type, row, prev, channels)
        prev = row
    ihdr = _p32(width) + _p32(height) + bytes([8, 2 if channels == 3 else 6, 0, 0, 0])

    def chunk(ctype, data):
        return _p32(len(data)) + ctype + data + _p32(zlib.crc32(ctype + data) & 0xFFFFFFFF)

    return PNG_SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")


def read_png(path):
    if not os.path.isfile(path):
        raise SystemExit("no such image: %s" % path)
    with open(path, "rb") as fh:
        blob = fh.read()
    try:
        return decode_png(blob)
    except PngError as e:
        raise PngError("%s: %s. %s" % (path, e, SAVE_HINT))


def write_png(path, width, height, pixels, channels=3):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(encode_png(width, height, pixels, channels))
    return path


# ---------------------------------------------------------------- compare

_FADE = bytes(255 - (255 - v) // 4 for v in range(256))  # a pixel washed three quarters toward white


def compare_images(base, cur, threshold):
    """Compare two decoded images, each (width, height, rgba).

    A pixel differs when its largest channel difference, scaled to 0..1, is over
    ``threshold``. Returns differing, total, percent, bbox (x0, y0, x1, y1
    inclusive, or None), size_mismatch, and the diff image as (width, height, rgb).
    """
    bw, bh, bpx = base
    cw, ch, cpx = cur
    if (bw, bh) != (cw, ch):
        total = cw * ch
        red = bytes([255, 0, 0]) * total
        return {"differing": total, "total": total, "percent": 100.0, "bbox": (0, 0, cw - 1, ch - 1),
                "size_mismatch": True, "diff": (cw, ch, bytearray(red))}
    w, h = cw, ch
    limit = threshold * 255.0
    rgb = bytearray(w * h * 3)
    for c in range(3):
        rgb[c::3] = cpx[c::4]
    rgb = bytearray(rgb.translate(_FADE))
    differing = 0
    x0 = y0 = None
    x1 = y1 = -1
    row_len = w * 4
    for y in range(h):
        start = y * row_len
        a = bpx[start:start + row_len]
        b = cpx[start:start + row_len]
        if a == b:
            continue
        for x in range(w):
            o = x * 4
            d = max(abs(a[o] - b[o]), abs(a[o + 1] - b[o + 1]),
                    abs(a[o + 2] - b[o + 2]), abs(a[o + 3] - b[o + 3]))
            if d > limit:
                differing += 1
                p = (y * w + x) * 3
                rgb[p:p + 3] = b"\xff\x00\x00"
                if x0 is None or x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y0 is None:
                    y0 = y
                y1 = y
    total = w * h
    bbox = (x0, y0, x1, y1) if differing else None
    return {"differing": differing, "total": total, "percent": 100.0 * differing / total, "bbox": bbox,
            "size_mismatch": False, "diff": (w, h, rgb)}


def describe_bbox(bbox):
    if bbox is None:
        return "no changed region"
    x0, y0, x1, y1 = bbox
    return "changed region x=%d..%d, y=%d..%d (%dx%d px)" % (x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1)


# ------------------------------------------------------------------ paths

def _check_name(name):
    if not _NAME.match(name or ""):
        raise SystemExit("name %r must be lowercase letters, digits and hyphens (for example home or checkout-step-2)" % name)
    return name


def baseline_dir(d):
    return os.path.join(ledger.root(d), "visual", "baseline")


def baseline_path(d, name):
    return os.path.join(baseline_dir(d), name + ".png")


def _baseline_names(d):
    folder = baseline_dir(d)
    if not os.path.isdir(folder):
        return []
    return sorted(n[:-4] for n in os.listdir(folder) if n.endswith(".png") and _NAME.match(n[:-4]))


def _rel(d, path):
    return os.path.relpath(path, ledger.root(d)).replace(os.sep, "/")


def _load_metrics(d):
    path = ledger.scores_path(d, SKILL)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("metrics", [])


def _save_metric(d, metric):
    metrics = [m for m in _load_metrics(d) if m["id"] != metric["id"]] + [metric]
    metrics.sort(key=lambda m: m["id"])
    ledger.write_scores(d, SKILL, metrics)


def _drop_metric(d, name):
    metrics = _load_metrics(d)
    kept = [m for m in metrics if m["id"] != "visual_" + name]
    if len(kept) != len(metrics):
        if kept:
            ledger.write_scores(d, SKILL, kept)
        else:
            os.remove(ledger.scores_path(d, SKILL))
        return True
    return False


def _drop_finding(d, name):
    path = ledger.finding_path(d, SKILL, FINDING_PREFIX + name)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def _sync_coverage(d):
    ledger.init_coverage(d, SKILL, [(n, "Visual match against the %s baseline" % n) for n in _baseline_names(d)])


def _bad_png(e):
    print("%s %s" % (ledger.BAD, e), file=sys.stderr)
    return 2


# --------------------------------------------------------------- commands

def cmd_baseline(a):
    name = _check_name(a.name)
    try:
        img = read_png(a.image)
    except PngError as e:
        return _bad_png(e)
    dest = baseline_path(a.dir, name)
    if os.path.exists(dest):
        with open(dest, "rb") as fh, open(a.image, "rb") as fi:
            same = fh.read() == fi.read()
        if same:
            print("%s baseline %r already holds this image; nothing changed." % (ledger.OK, name))
            return 0
        raise SystemExit("a baseline for %r already exists. If the change is intended, run: "
                         "visual.py approve %s <image.png>" % (name, name))
    os.makedirs(baseline_dir(a.dir), exist_ok=True)
    shutil.copyfile(a.image, dest)
    _sync_coverage(a.dir)
    print("%s baseline %r saved (%dx%d) to %s. Next: change the app, take the same screenshot, then compare."
          % (ledger.OK, name, img[0], img[1], _rel(a.dir, dest)))
    return 0


def cmd_compare(a):
    name = _check_name(a.name)
    threshold = num.parse_number(a.threshold, field="--threshold")
    max_diff = num.parse_number(a.max_diff, field="--max-diff")
    if not 0 <= threshold <= 1:
        raise SystemExit("--threshold is a channel difference from 0 to 1, got %s" % a.threshold)
    if not 0 <= max_diff <= 100:
        raise SystemExit("--max-diff is a percent from 0 to 100, got %s" % a.max_diff)
    base_file = baseline_path(a.dir, name)
    if not os.path.exists(base_file):
        raise SystemExit("no baseline for %r yet. Run: visual.py baseline %s <image.png>" % (name, name))
    try:
        base = read_png(base_file)
        cur = read_png(a.image)
    except PngError as e:
        return _bad_png(e)

    result = compare_images(base, cur, threshold)
    ev = ledger.evidence_dir(a.dir, SKILL)
    diff_path = os.path.join(ev, name + "-diff.png")
    cur_copy = os.path.join(ev, name + ".png")
    dw, dh, drgb = result["diff"]
    write_png(diff_path, dw, dh, drgb, 3)
    if os.path.abspath(a.image) != os.path.abspath(cur_copy):
        shutil.copyfile(a.image, cur_copy)
    diff_rel, cur_rel = _rel(a.dir, diff_path), _rel(a.dir, cur_copy)

    pct = round(result["percent"], 4)
    over = pct > max_diff
    _save_metric(a.dir, {"id": "visual_" + name, "value": pct, "unit": "%", "budget": max_diff, "op": "<=",
                         "source": "visual.py compare, threshold %s" % threshold,
                         "evidence": [diff_rel, cur_rel]})
    _sync_coverage(a.dir)
    note = "%.4f%% of pixels changed; %s" % (pct, describe_bbox(result["bbox"]))
    if result["size_mismatch"]:
        note = "size changed from %dx%d to %dx%d, counted as 100%% changed" % (base[0], base[1], cur[0], cur[1])
    ledger.mark_coverage(a.dir, SKILL, name, "covered", note=note, evidence=[diff_rel])

    # A finding from an earlier run is rebuilt from this run, never kept stale.
    _drop_finding(a.dir, name)
    if result["size_mismatch"]:
        print("%s %s: the image is %dx%d but the baseline is %dx%d. Different sizes count as 100%% changed; "
              "take the screenshot at the same viewport and device." % (ledger.BAD, name, cur[0], cur[1], base[0], base[1]))
    print("%s %s: %d of %d pixels differ (%.4f%%, budget %s%%), %s."
          % (ledger.BAD if over else ledger.OK, name, result["differing"], result["total"], pct,
             _fmt(max_diff), describe_bbox(result["bbox"])))
    print("   diff image: %s" % diff_rel)
    if not over:
        return 0
    desc = ("The screenshot %s differs from the saved baseline by %.4f%% of pixels, over the %s%% budget "
            "(a pixel counts when a channel moves more than %s of its range). %s. The diff image shows "
            "changed pixels in red over a faded copy of the new screenshot."
            % (cur_rel, pct, _fmt(max_diff), threshold, note[0].upper() + note[1:]))
    f = ledger.new_finding(SKILL, "Visual change on %s: %.2f%% of pixels differ from the baseline" % (name, pct),
                           "medium", id=FINDING_PREFIX + name, category=CATEGORY, description=desc,
                           location="baseline %s (%s)" % (name, describe_bbox(result["bbox"])),
                           evidence=[diff_rel, cur_rel],
                           repro="python3 visual.py compare %s <image.png> --threshold %s --max-diff %s"
                                 % (name, threshold, _fmt(max_diff)),
                           fix="If the change is a bug, fix it and compare again. If it is intended, "
                               "run: visual.py approve %s <image.png>" % name)
    ledger.write_finding(a.dir, f)
    ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
    print("%s finding %r written and verified by the diff image. Intended change? approve it." % (ledger.BAD, f["id"]))
    return 1


def _fmt(n):
    return ("%g" % n)


def cmd_approve(a):
    name = _check_name(a.name)
    try:
        img = read_png(a.image)
    except PngError as e:
        return _bad_png(e)
    dest = baseline_path(a.dir, name)
    existed = os.path.exists(dest)
    os.makedirs(baseline_dir(a.dir), exist_ok=True)
    shutil.copyfile(a.image, dest)
    _sync_coverage(a.dir)
    dropped = _drop_finding(a.dir, name)
    _drop_metric(a.dir, name)
    ledger.mark_coverage(a.dir, SKILL, name, "not_run")
    print("%s baseline %r %s with %s (%dx%d): the change is recorded as intended."
          % (ledger.OK, name, "replaced" if existed else "created", a.image, img[0], img[1]))
    if dropped:
        print("   removed the earlier finding %s%s; it described the old baseline." % (FINDING_PREFIX, name))
    print("   Run compare again to record a fresh score against the new baseline.")
    return 0


def cmd_status(a):
    names = _baseline_names(a.dir)
    if not names:
        print("no baselines yet. Run: visual.py baseline <name> <image.png>")
        return 0
    metrics = {m["id"]: m for m in _load_metrics(a.dir)}
    cov = ledger.load_coverage(a.dir, SKILL)
    status = {c["id"]: c["status"] for c in (cov["checks"] if cov else [])}
    print("%d baseline(s):" % len(names))
    for n in names:
        m = metrics.get("visual_" + n)
        if m is None:
            print("  ... %s: not compared yet" % n)
        else:
            ok = ledger.metric_passes(m)
            print("  %s %s: %s%% changed (budget %s%%)%s" % (ledger.OK if ok else ledger.BAD, n, _fmt(m["value"]),
                                                          _fmt(m["budget"]), "" if status.get(n) == "covered" else " (not covered)"))
    findings = ledger.load_findings(a.dir, SKILL)
    if findings:
        print("findings:")
        for f in findings:
            print("  [%s] %s (%s) %s" % (f["severity"], f["title"], f["status"], f["id"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof", help="the proof/ folder (default: proof)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="save a screenshot as the baseline for a name")
    b.add_argument("name", help="a short id for the page or state, e.g. home or checkout-step-2")
    b.add_argument("image", help="an 8-bit RGB or RGBA PNG")
    b.set_defaults(func=cmd_baseline)

    c = sub.add_parser("compare", help="compare a screenshot against its baseline")
    c.add_argument("name")
    c.add_argument("image")
    c.add_argument("--threshold", default="0.1",
                   help="how far (0 to 1) a channel must move before a pixel counts as changed (default 0.1)")
    c.add_argument("--max-diff", default="0.5",
                   help="the percent of changed pixels allowed before it is a finding (default 0.5)")
    c.set_defaults(func=cmd_compare)

    p = sub.add_parser("approve", help="replace the baseline with an intended new screenshot")
    p.add_argument("name")
    p.add_argument("image")
    p.set_defaults(func=cmd_approve)

    sub.add_parser("status", help="baselines, scores and findings so far").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (num.MissingInput, num.BadNumber) as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    sys.exit(main())
