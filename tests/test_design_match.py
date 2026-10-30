import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

design = load_tool("proof-design-match", "design.py")
visual = load_tool("proof-visual", "visual.py")
ledger = load_tool("proof-core", "ledger.py")

SKILL = "proof-design-match"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = design.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


def screen(w, h, rgb=(255, 255, 255), boxes=()):
    """An RGB image with filled rectangles (x, y, w, h, rgb)."""
    px = bytearray(bytes(rgb) * (w * h))
    for x, y, bw, bh, c in boxes:
        for yy in range(y, y + bh):
            for xx in range(x, x + bw):
                p = (yy * w + xx) * 3
                px[p:p + 3] = bytes(c)
    return w, h, px


def decoded(img):
    w, h, px = img
    return visual.decode_png(visual.encode_png(w, h, px, 3))


TREE = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" resource-id="" text="" content-desc="" bounds="[0,0][1080,2400]">
    <node class="android.widget.TextView" resource-id="app:id/title" text="Sign in" bounds="[40,100][600,180]"/>
    <node class="android.widget.EditText" resource-id="app:id/email" text="" content-desc="Email" bounds="[40,300][1040,420]"/>
    <node class="android.widget.Button" resource-id="app:id/submit" text="Continue" bounds="[40,500][1040,620]"/>
    %s
  </node>
</hierarchy>"""


class DesignMatch(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, data):
        p = os.path.join(self.tp.base, "in", name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
        with open(p, mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as fh:
            fh.write(data)
        return p

    def png(self, name, img):
        w, h, px = img
        return self.file(name, visual.encode_png(w, h, px, 3))

    # ---- screen layer

    def test_matching_design_at_another_scale(self):
        app = screen(30, 60, boxes=[(3, 6, 12, 6, (20, 40, 200))])
        art = screen(10, 20, boxes=[(1, 2, 4, 2, (20, 40, 200))])  # the same frame at 1/3 scale
        r = design.compare_screen(decoded(app), decoded(art))
        self.assertEqual(r["differing"], 0)
        self.assertEqual(r["scale"], 3.0)

    def test_difference_and_ignored_status_bar(self):
        app = screen(20, 40, boxes=[(0, 0, 20, 4, (0, 0, 0)), (5, 20, 10, 5, (200, 0, 0))])
        art = screen(20, 40, boxes=[(5, 20, 10, 5, (0, 0, 200))])
        r = design.compare_screen(decoded(app), decoded(art))
        self.assertEqual(r["differing"], 20 * 4 + 50)
        r = design.compare_screen(decoded(app), decoded(art), [design.parse_region("0,0,20,4")])
        self.assertEqual(r["differing"], 50)
        self.assertEqual(r["bbox"], (5, 20, 14, 24))

    def test_different_shapes_are_refused(self):
        with self.assertRaises(SystemExit):
            design.compare_screen(decoded(screen(20, 40)), decoded(screen(40, 40)))

    def test_bad_region(self):
        for bad in ("1,2,3", "a,b,c,d", "0,0,0,5"):
            with self.assertRaises(SystemExit):
                design.parse_region(bad)

    def test_compare_command_exit_codes(self):
        a = self.png("app.png", screen(20, 20))
        b = self.png("art.png", screen(20, 20, boxes=[(0, 0, 20, 10, (0, 0, 0))]))
        rc, out = run("--dir", self.d, "compare", "--screen", a, "--design", a)
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "compare", "--screen", a, "--design", b, "--name", "home")
        self.assertEqual(rc, 1, out)
        self.assertTrue(os.path.isfile(os.path.join(ledger.evidence_dir(self.d, SKILL), "home-diff.png")))

    # ---- element layer

    def test_parse_uiautomator_skips_containers(self):
        els = design.parse_uiautomator(TREE % "")
        self.assertEqual([e["id"] for e in els], ["app:id/title", "app:id/email", "app:id/submit"])
        self.assertEqual(els[1]["label"], "Email")
        self.assertEqual(els[2]["type"], "Button")
        self.assertEqual((els[2]["x"], els[2]["y"], els[2]["w"], els[2]["h"]), (40, 500, 1000, 120))

    def test_parse_json_with_ios_keys(self):
        els = design.parse_json_elements({"elements": [
            {"AXLabel": "Continue", "AXRole": "AXButton", "frame": {"x": 20, "y": 500, "width": 335, "height": 44}},
            {"id": "email", "label": "Email", "type": "TextField", "x": 20, "y": 300, "w": 335, "h": 44},
            {"frame": {"x": 0, "y": 0, "width": 10, "height": 10}},
        ]})
        self.assertEqual(len(els), 2)
        self.assertEqual(els[0]["label"], "Continue")
        self.assertEqual(els[0]["w"], 335)

    def test_new_missing_and_moved(self):
        old = design.parse_uiautomator(TREE % "")
        cur = design.parse_uiautomator((TREE % '<node class="android.widget.Button" resource-id="app:id/google" '
                                                'text="Sign in with Google" bounds="[40,700][1040,820]"/>')
                                       .replace('resource-id="app:id/title" text="Sign in" bounds="[40,100][600,180]"', '')
                                       .replace("[40,500][1040,620]", "[40,520][1040,640]"))
        rep = design.diff_elements(cur, previous=old)
        p = rep["previous"]
        self.assertEqual([e["id"] for e in p["new"]], ["app:id/google"])
        self.assertEqual([e["id"] for e in p["missing"]], ["app:id/title"])  # removed from this build
        moved = [b["id"] for _, b, _ in p["drifted"]]
        self.assertEqual(moved, ["app:id/submit"])

    def test_missing_element_and_tolerance(self):
        old = [{"id": "a", "label": "", "type": "Button", "x": 0, "y": 0, "w": 100, "h": 40},
               {"id": "b", "label": "", "type": "Text", "x": 0, "y": 50, "w": 100, "h": 40}]
        cur = [{"id": "a", "label": "", "type": "Button", "x": 3, "y": 0, "w": 101, "h": 40}]
        rep = design.diff_elements(cur, previous=old)["previous"]
        self.assertEqual([e["id"] for e in rep["missing"]], ["b"])
        self.assertEqual(rep["drifted"], [])  # 3 px and 1% are inside the default tolerance
        rep = design.diff_elements(cur, previous=old, tol_px=2)["previous"]
        self.assertEqual(len(rep["drifted"]), 1)

    def test_match_falls_back_to_label_when_id_changes(self):
        old = [{"id": "btn_ok", "label": "Continue", "type": "Button", "x": 0, "y": 0, "w": 10, "h": 10}]
        cur = [{"id": "button_continue", "label": "Continue", "type": "Button", "x": 0, "y": 0, "w": 10, "h": 10}]
        pairs, missing, new = design.match(old, cur)
        self.assertEqual((len(pairs), missing, new), (1, [], []))

    def test_repeated_labels_match_in_order(self):
        row = lambda y: {"id": "", "label": "Item", "type": "Cell", "x": 0, "y": y, "w": 10, "h": 10}
        pairs, missing, new = design.match([row(0), row(20)], [row(0), row(20), row(40)])
        self.assertEqual((len(pairs), len(missing), len(new)), (2, 0, 1))

    def test_elements_command_needs_a_comparison(self):
        cur = self.file("cur.xml", TREE % "")
        rc, out = run("--dir", self.d, "elements", "--current", cur)
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "elements", "--current", cur, "--previous", cur)
        self.assertEqual(rc, 0, out)
        self.assertIn("0 missing, 0 new", out)

    # ---- record

    def test_record_writes_findings_scores_and_coverage(self):
        app = self.png("app.png", screen(20, 20, boxes=[(0, 10, 20, 10, (0, 0, 0))]))
        art = self.png("art.png", screen(10, 10))
        cur = self.file("cur.xml", TREE % '<node class="android.widget.Button" resource-id="app:id/google" '
                                         'text="Google" bounds="[40,700][1040,820]"/>')
        prev = self.file("prev.xml", TREE % "")
        spec = self.file("spec.json", json.dumps([
            {"id": "app:id/title", "label": "Sign in", "type": "TextView", "x": 40, "y": 100, "w": 560, "h": 80},
            {"id": "app:id/forgot", "label": "Forgot password?", "type": "TextView", "x": 40, "y": 640, "w": 400, "h": 60},
        ]))
        rc, out = run("--dir", self.d, "record", "--name", "login", "--screen", app, "--design", art,
                      "--current", cur, "--previous", prev, "--spec", spec)
        self.assertEqual(rc, 0, out)
        fs = {f["id"]: f for f in ledger.load_findings(self.d, SKILL)}
        self.assertIn("design-login-screen", fs)
        self.assertEqual(fs["design-login-screen"]["severity"], "medium")
        self.assertTrue(any("previous-new" in i and fs[i]["severity"] == "low" for i in fs))
        self.assertTrue(any("spec-missing" in i and "forgot" in i and fs[i]["severity"] == "medium" for i in fs))
        self.assertTrue(all(f["status"] == "verified" for f in fs.values()))
        for f in fs.values():
            for ev in f["evidence"]:
                self.assertTrue(os.path.isfile(os.path.join(ledger.root(self.d), ev)), ev)
        m = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual(m["id"], "design-diff-pct-login")
        self.assertFalse(ledger.metric_passes(m))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(cov, {"login-screen": "covered", "login-elements": "covered"})

    def test_rerun_clears_old_findings_and_keeps_other_screens(self):
        same = self.png("same.png", screen(10, 10))
        other = self.png("other.png", screen(10, 10, boxes=[(0, 0, 10, 10, (0, 0, 0))]))
        run("--dir", self.d, "record", "--name", "home", "--screen", same, "--design", other)
        run("--dir", self.d, "record", "--name", "cart", "--screen", same, "--design", same)
        self.assertEqual([f["id"] for f in ledger.load_findings(self.d, SKILL)], ["design-home-screen"])
        run("--dir", self.d, "record", "--name", "home", "--screen", same, "--design", same)
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])
        ids = {c["id"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(ids, {"home-screen", "cart-screen"})
        self.assertEqual(len(ledger.load_all_scores(self.d)[0]["metrics"]), 2)

    def test_record_argument_rules(self):
        a = self.png("a.png", screen(4, 4))
        for argv in (["--name", "x", "--screen", a], ["--name", "x"], ["--name", "Bad Name", "--screen", a, "--design", a],
                     ["--name", "x", "--current", a]):
            rc, _ = run("--dir", self.d, "record", *argv)
            self.assertNotEqual(rc, 0, argv)


if __name__ == "__main__":
    unittest.main()
