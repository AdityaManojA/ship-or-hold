import contextlib
import io
import os
import struct
import unittest
import zlib

from _load import TempProof, load_tool

visual = load_tool("proof-visual", "visual.py")
ledger = load_tool("proof-core", "ledger.py")

SKILL = "proof-visual"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = visual.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


def chunk(ctype, data):
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)


def raw_png(width, height, raw_rows, color=2, depth=8, interlace=0):
    """Build a PNG by hand (independent of the tool's writer) from pre-filtered rows."""
    ihdr = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(raw_rows))) + chunk(b"IEND", b""))


def solid(w, h, rgb):
    return bytearray(bytes(rgb) * (w * h))


def paint(pixels, w, x0, y0, x1, y1, rgb):
    """Fill an inclusive rectangle of an RGB buffer."""
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            p = (y * w + x) * 3
            pixels[p:p + 3] = bytes(rgb)
    return pixels


class Decode(unittest.TestCase):
    def test_hand_built_sub_row(self):
        # 2x1 RGB: (10,20,30) then (15,25,35). Sub stores the second pixel as the delta.
        png = raw_png(2, 1, [bytes([1, 10, 20, 30, 5, 5, 5])])
        w, h, px = visual.decode_png(png)
        self.assertEqual((w, h), (2, 1))
        self.assertEqual(bytes(px), bytes([10, 20, 30, 255, 15, 25, 35, 255]))

    def test_hand_built_up_and_paeth_rows(self):
        # 1x2 RGB. Row 0 plain, row 1 Up (adds the pixel above).
        png = raw_png(1, 2, [bytes([0, 100, 100, 100]), bytes([2, 1, 2, 3])])
        _, _, px = visual.decode_png(png)
        self.assertEqual(bytes(px), bytes([100, 100, 100, 255, 101, 102, 103, 255]))
        # 2x2 RGB, row 1 Paeth. For the first pixel a=c=0 so the predictor is b (above).
        # Second pixel: a=row1[0]=(51,51,51), b=(60,60,60), c=(50,50,50); p=61, pa=10,
        # pb=1, pc=11 so the predictor is b=60.
        rows = [bytes([0, 50, 50, 50, 60, 60, 60]), bytes([4, 1, 1, 1, 2, 2, 2])]
        _, _, px = visual.decode_png(raw_png(2, 2, rows))
        self.assertEqual(bytes(px[8:]), bytes([51, 51, 51, 255, 62, 62, 62, 255]))

    def test_hand_built_average_row(self):
        # 2x2 RGB, row 1 Average: first pixel (0 + above)//2, second (left + above)//2.
        rows = [bytes([0, 10, 10, 10, 20, 20, 20]), bytes([3, 1, 1, 1, 1, 1, 1])]
        _, _, px = visual.decode_png(raw_png(2, 2, rows))
        # first: 1 + 10//2 = 6; second: 1 + (6 + 20)//2 = 14
        self.assertEqual(bytes(px[8:]), bytes([6, 6, 6, 255, 14, 14, 14, 255]))

    def test_round_trip_every_filter_rgb_and_rgba(self):
        w, h = 7, 5
        for channels in (3, 4):
            pixels = bytearray((x * 37 + y * 91 + c * 53) % 256
                               for y in range(h) for x in range(w) for c in range(channels))
            for ftype in range(5):
                png = visual.encode_png(w, h, pixels, channels, filter_type=ftype)
                dw, dh, rgba = visual.decode_png(png)
                self.assertEqual((dw, dh), (w, h))
                if channels == 4:
                    self.assertEqual(bytes(rgba), bytes(pixels), "filter %d rgba" % ftype)
                else:
                    want = bytearray()
                    for i in range(0, len(pixels), 3):
                        want += pixels[i:i + 3] + b"\xff"
                    self.assertEqual(bytes(rgba), bytes(want), "filter %d rgb" % ftype)

    def test_unsupported_pngs_raise(self):
        palette = raw_png(1, 1, [b"\x00\x00"], color=3)
        sixteen = raw_png(1, 1, [b"\x00" + b"\x00" * 6], depth=16)
        interlaced = raw_png(1, 1, [b"\x00\x00\x00\x00"], interlace=1)
        for blob, word in ((palette, "palette"), (sixteen, "16-bit"), (interlaced, "interlaced")):
            with self.assertRaises(visual.PngError) as ctx:
                visual.decode_png(blob)
            self.assertIn(word, str(ctx.exception))
        with self.assertRaises(visual.PngError):
            visual.decode_png(b"not a png at all")


class Compare(unittest.TestCase):
    def img(self, w, h, pixels):
        return visual.decode_png(visual.encode_png(w, h, pixels, 3))

    def test_identical_is_zero(self):
        a = self.img(10, 10, solid(10, 10, (200, 10, 10)))
        r = visual.compare_images(a, a, 0.1)
        self.assertEqual(r["differing"], 0)
        self.assertEqual(r["percent"], 0.0)
        self.assertIsNone(r["bbox"])

    def test_changed_block_percent_and_bbox(self):
        base = solid(20, 10, (255, 255, 255))
        cur = paint(solid(20, 10, (255, 255, 255)), 20, 3, 2, 7, 5, (0, 0, 0))  # 5x4 = 20 px
        r = visual.compare_images(self.img(20, 10, base), self.img(20, 10, cur), 0.1)
        self.assertEqual(r["differing"], 20)
        self.assertAlmostEqual(r["percent"], 10.0)
        self.assertEqual(r["bbox"], (3, 2, 7, 5))
        dw, dh, rgb = r["diff"]
        self.assertEqual(bytes(rgb[(2 * 20 + 3) * 3:(2 * 20 + 3) * 3 + 3]), b"\xff\x00\x00")
        self.assertNotEqual(bytes(rgb[0:3]), b"\xff\x00\x00")

    def test_threshold_ignores_small_shifts(self):
        base = solid(4, 4, (100, 100, 100))
        cur = solid(4, 4, (110, 100, 100))  # 10/255 = 0.039
        a, b = self.img(4, 4, base), self.img(4, 4, cur)
        self.assertEqual(visual.compare_images(a, b, 0.1)["differing"], 0)
        self.assertEqual(visual.compare_images(a, b, 0.01)["differing"], 16)

    def test_size_mismatch_is_full_change(self):
        r = visual.compare_images(self.img(4, 4, solid(4, 4, (1, 2, 3))),
                                  self.img(5, 4, solid(5, 4, (1, 2, 3))), 0.1)
        self.assertTrue(r["size_mismatch"])
        self.assertEqual(r["percent"], 100.0)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.white = self.save("white.png", 20, 10, solid(20, 10, (255, 255, 255)))
        self.block = self.save("block.png", 20, 10,
                               paint(solid(20, 10, (255, 255, 255)), 20, 3, 2, 7, 5, (0, 0, 0)))

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def save(self, name, w, h, pixels):
        path = os.path.join(self.tp.base, name)
        visual.write_png(path, w, h, pixels, 3)
        return path

    def metric(self, name):
        for s in ledger.load_all_scores(self.d):
            for m in s["metrics"]:
                if m["id"] == "visual_" + name:
                    return m
        return None

    def test_baseline_copies_image(self):
        rc, out = run("--dir", self.d, "baseline", "home", self.white)
        self.assertEqual(rc, 0, out)
        path = os.path.join(self.d, "visual", "baseline", "home.png")
        with open(path, "rb") as a, open(self.white, "rb") as b:
            self.assertEqual(a.read(), b.read())
        rc, out = run("--dir", self.d, "baseline", "home", self.block)
        self.assertNotEqual(rc, 0)
        self.assertIn("approve", out)

    def test_compare_within_budget_records_score_no_finding(self):
        run("--dir", self.d, "baseline", "home", self.white)
        rc, out = run("--dir", self.d, "compare", "home", self.white)
        self.assertEqual(rc, 0, out)
        m = self.metric("home")
        self.assertEqual((m["value"], m["unit"], m["budget"], m["op"]), (0.0, "%", 0.5, "<="))
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])
        cov = ledger.load_coverage(self.d, SKILL)
        self.assertEqual(cov["checks"][0]["status"], "covered")
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", SKILL, "home-diff.png")))
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", SKILL, "home.png")))

    def test_compare_over_budget_writes_verified_finding(self):
        run("--dir", self.d, "baseline", "home", self.white)
        rc, out = run("--dir", self.d, "compare", "home", self.block, "--max-diff", "5")
        self.assertEqual(rc, 1, out)
        self.assertIn("x=3..7, y=2..5", out)
        self.assertEqual(self.metric("home")["value"], 10.0)
        fs = ledger.load_findings(self.d, SKILL)
        self.assertEqual(len(fs), 1)
        f = fs[0]
        self.assertEqual((f["severity"], f["category"], f["status"]), ("medium", "visual", "verified"))
        self.assertIn("evidence/proof-visual/home-diff.png", f["evidence"])
        # the diff image is a PNG the tool can read back, with red in the block
        w, h, px = visual.read_png(os.path.join(self.d, "evidence", SKILL, "home-diff.png"))
        self.assertEqual((w, h), (20, 10))
        o = (3 * 20 + 5) * 4
        self.assertEqual(bytes(px[o:o + 3]), b"\xff\x00\x00")
        # a later pass clears the stale finding
        rc, _ = run("--dir", self.d, "compare", "home", self.white)
        self.assertEqual(rc, 0)
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])

    def test_budget_is_respected(self):
        run("--dir", self.d, "baseline", "home", self.white)
        rc, out = run("--dir", self.d, "compare", "home", self.block, "--max-diff", "10")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])

    def test_size_mismatch_cli(self):
        run("--dir", self.d, "baseline", "home", self.white)
        small = self.save("small.png", 10, 10, solid(10, 10, (255, 255, 255)))
        rc, out = run("--dir", self.d, "compare", "home", small)
        self.assertEqual(rc, 1)
        self.assertIn("100%", out)
        self.assertEqual(self.metric("home")["value"], 100.0)

    def test_approve_replaces_baseline(self):
        run("--dir", self.d, "baseline", "home", self.white)
        run("--dir", self.d, "compare", "home", self.block)
        self.assertEqual(len(ledger.load_findings(self.d, SKILL)), 1)
        rc, out = run("--dir", self.d, "approve", "home", self.block)
        self.assertEqual(rc, 0, out)
        self.assertIn("replaced", out)
        with open(os.path.join(self.d, "visual", "baseline", "home.png"), "rb") as a, open(self.block, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])
        self.assertIsNone(self.metric("home"))
        rc, out = run("--dir", self.d, "compare", "home", self.block)
        self.assertEqual(rc, 0, out)

    def test_unsupported_png_exits_2(self):
        bad = os.path.join(self.tp.base, "palette.png")
        with open(bad, "wb") as fh:
            fh.write(raw_png(1, 1, [b"\x00\x00"], color=3))
        rc, out = run("--dir", self.d, "baseline", "home", bad)
        self.assertEqual(rc, 2)
        self.assertIn("8-bit RGB", out)
        run("--dir", self.d, "baseline", "home", self.white)
        rc, out = run("--dir", self.d, "compare", "home", bad)
        self.assertEqual(rc, 2)

    def test_refusals(self):
        rc, out = run("--dir", self.d, "compare", "home", self.white)
        self.assertNotEqual(rc, 0)
        self.assertIn("no baseline", out)
        rc, out = run("--dir", self.d, "baseline", "Home Page", self.white)
        self.assertNotEqual(rc, 0)
        run("--dir", self.d, "baseline", "home", self.white)
        rc, out = run("--dir", self.d, "compare", "home", self.white, "--threshold", "2")
        self.assertNotEqual(rc, 0)

    def test_status(self):
        rc, out = run("--dir", self.d, "status")
        self.assertIn("no baselines", out)
        run("--dir", self.d, "baseline", "home", self.white)
        run("--dir", self.d, "baseline", "pricing", self.white)
        run("--dir", self.d, "compare", "home", self.block)
        rc, out = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("pricing: not compared yet", out)
        self.assertIn("home: 10% changed", out)


if __name__ == "__main__":
    unittest.main()
