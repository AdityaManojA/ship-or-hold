import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

ma11y = load_tool("proof-mobile-a11y", "ma11y.py")
ledger = load_tool("proof-core", "ledger.py")

ELL = chr(0x2026)

# Density 2.0 so px / 2 = dp: a 96px button is 48dp.
ANDROID = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
 <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false" text="" content-desc="" resource-id="">
  <node class="android.widget.ImageButton" resource-id="app:id/menu" bounds="[0,0][60,60]" clickable="true" text="" content-desc="" />
  <node class="android.widget.Button" resource-id="app:id/send" bounds="[100,100][300,180]" clickable="true" text="Send" content-desc="" />
  <node class="android.widget.ImageView" resource-id="app:id/logo" bounds="[100,300][200,400]" clickable="true" text="" content-desc="ic_logo" />
  <node class="android.view.ViewGroup" resource-id="app:id/row" bounds="[0,500][1080,700]" clickable="true" text="" content-desc="">
   <node class="android.widget.TextView" resource-id="app:id/title" bounds="[20,520][600,580]" clickable="false" text="Order history" content-desc="" />
  </node>
  <node class="android.widget.Button" resource-id="app:id/edit1" bounds="[0,800][200,900]" clickable="true" text="Edit" content-desc="" />
  <node class="android.widget.Button" resource-id="app:id/edit2" bounds="[0,1000][200,1100]" clickable="true" text="Edit" content-desc="" />
 </node>
</hierarchy>"""

ANDROID_LARGE = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
 <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false" text="" content-desc="" resource-id="">
  <node class="android.widget.TextView" resource-id="app:id/price" bounds="[20,100][300,160]" text="Total: $12%s" content-desc="" />
  <node class="android.widget.TextView" resource-id="app:id/wide" bounds="[500,200][1300,260]" text="A very long heading" content-desc="" />
  <node class="android.widget.TextView" resource-id="app:id/a" bounds="[20,400][600,480]" text="First line" content-desc="" />
  <node class="android.widget.TextView" resource-id="app:id/b" bounds="[20,450][600,530]" text="Second line" content-desc="" />
 </node>
</hierarchy>""" % ELL

CLEAN = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
 <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false" text="" content-desc="" resource-id="">
  <node class="android.widget.Button" resource-id="app:id/send" bounds="[100,100][300,200]" clickable="true" text="Send message" content-desc="" />
  <node class="android.widget.ImageButton" resource-id="app:id/close" bounds="[400,100][520,220]" clickable="true" text="" content-desc="Close" />
 </node>
</hierarchy>"""

IDB = [
    {"AXLabel": "", "AXFrame": "{{10, 40}, {30, 30}}", "type": "Button", "role": "AXButton", "AXUniqueId": "back"},
    {"AXLabel": "Buy now", "frame": {"x": 20, "y": 700, "width": 350, "height": 50}, "type": "Button",
     "role": "AXButton"},
    {"AXLabel": "Welcome", "frame": {"x": 20, "y": 100, "width": 200, "height": 30}, "type": "StaticText",
     "role": "AXStaticText"},
]


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ma11y.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class MobileA11y(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, text):
        p = os.path.join(self.tp.base, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text if isinstance(text, str) else json.dumps(text))
        return p

    def android(self, large=None):
        plat, els, _ = ma11y.load_tree(self.file("home.xml", ANDROID), density=2.0)
        lg = ma11y.load_tree(self.file("large.xml", large), plat, 2.0) if large else None
        return ma11y.analyse(plat, els, lg)

    def test_unlabeled_controls(self):
        res = self.android()
        text = " ".join(res["unlabeled"])
        self.assertIn("app:id/menu", text)
        # A clickable row named by its text child is fine, as TalkBack reads children.
        self.assertNotIn("app:id/row", text)
        self.assertNotIn("app:id/send", text)

    def test_touch_targets_in_dp(self):
        res = self.android()
        text = " ".join(res["targets"])
        self.assertIn("app:id/menu is 30x30 dp", text)
        self.assertIn("app:id/send", text)  # 100 x 40 dp: too short
        self.assertNotIn("app:id/row", text)

    def test_dpi_flag_converts(self):
        p = self.file("home.xml", ANDROID)
        rc, out = run("check", p, "--dpi", "320", "--json")
        self.assertEqual(rc, 0)
        res = json.loads(out)["problems"]
        self.assertTrue(any("app:id/menu is 30x30 dp" in t for t in res["targets"]))

    def test_meaningless_and_duplicate_labels(self):
        res = self.android()
        text = " ".join(res["labels"])
        self.assertIn('"ic_logo"', text)
        self.assertIn('2 controls are all announced as "Edit"', text)

    def test_large_text(self):
        res = self.android(ANDROID_LARGE)
        text = " ".join(res["large-text"])
        self.assertIn("truncated", text)
        self.assertIn("runs off the screen", text)
        self.assertIn("overlaps", text)

    def test_clean_screen(self):
        plat, els, _ = ma11y.load_tree(self.file("c.xml", CLEAN), density=2.0)
        res = ma11y.analyse(plat, els)
        self.assertEqual([p for v in res.values() for p in v], [])

    def test_idb_json_in_points(self):
        plat, els, _ = ma11y.load_tree(self.file("home.json", IDB))
        self.assertEqual(plat, "ios")
        res = ma11y.analyse(plat, els)
        self.assertTrue(any("back" in t for t in res["unlabeled"]))
        self.assertTrue(any("back is 30x30 pt, under 44" in t for t in res["targets"]))
        self.assertFalse(any("Buy" in t for t in res["targets"]))

    def test_generic_json_with_screen(self):
        tree = {"platform": "ios", "screen": {"w": 390, "h": 844},
                "elements": [{"id": "t", "text": "Hello" + ELL, "type": "StaticText", "x": 0, "y": 0, "w": 100, "h": 20},
                             {"id": "go", "label": "Go", "clickable": True, "x": 0, "y": 50, "w": 44, "h": 44}]}
        plat, els, screen = ma11y.load_tree(self.file("g.json", tree))
        self.assertEqual(screen["w"], 390)
        res = ma11y.analyse(plat, els, (plat, els, screen))
        self.assertEqual(res["targets"], [])
        self.assertTrue(res["large-text"])

    def test_record_and_rerecord(self):
        tree = self.file("home.xml", ANDROID)
        large = self.file("large.xml", ANDROID_LARGE)
        rc, out = run("--dir", self.d, "record", tree, "--large", large, "--screen", "home", "--density", "2")
        self.assertEqual(rc, 0, out)
        fs = {f["category"]: f for f in ledger.load_findings(self.d, "proof-mobile-a11y")}
        self.assertEqual(fs["mobile-a11y:unlabeled"]["severity"], "high")
        self.assertIn("mobile-a11y:large-text", fs)
        self.assertTrue(all(f["status"] == "verified" for f in fs.values()))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-mobile-a11y")["checks"]}
        self.assertEqual(cov["home-unlabeled"], "covered")
        # A second screen keeps the first screen's checks.
        rc, _ = run("--dir", self.d, "record", self.file("s.xml", CLEAN), "--screen", "settings", "--density", "2")
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-mobile-a11y")["checks"]}
        self.assertEqual(cov["home-targets"], "covered")
        self.assertEqual(cov["settings-large-text"], "not_run")
        # Fixing the home screen drops its stale findings.
        rc, _ = run("--dir", self.d, "record", self.file("fixed.xml", CLEAN), "--screen", "home", "--density", "2")
        cats = {f["category"] for f in ledger.load_findings(self.d, "proof-mobile-a11y")}
        self.assertNotIn("mobile-a11y:unlabeled", cats)

    def test_check_without_large_says_not_checked(self):
        rc, out = run("check", self.file("home.xml", ANDROID), "--density", "2")
        self.assertEqual(rc, 0)
        self.assertIn("not checked", out)


if __name__ == "__main__":
    unittest.main()
