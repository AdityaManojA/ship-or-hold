import contextlib
import io
import os
import unittest

from _load import TempProof, load_tool

slop = load_tool("proof-slop", "slop.py")
ledger = load_tool("proof-core", "ledger.py")

DASH = chr(0x2014)
ROCKET = chr(0x1F680)

SLOPPY_PAGE = """<html><head><style>
.hero { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
h1 { background-clip: text; -webkit-text-fill-color: transparent; }
body { font-family: 'Inter', sans-serif; }
</style></head><body>
<h1>ROCKET Revolutionize your workflow with our next-gen platform</h1>
<p>It's not just a tool, it's a movement DASH built for teams.</p>
<p>Trusted by 10,000 teams. 10,000% ROI and 0.0001ms latency.</p>
<p>Lorem ipsum dolor sit amet. Contact john.doe@example.com</p>
<script>const x = "seamless";</script>
</body></html>""".replace("ROCKET", ROCKET).replace("DASH", DASH)

CLEAN_PAGE = """<html><head><style>body { font-family: "Fraunces", Georgia, serif; }</style></head>
<body><h1>Invoices that send themselves</h1>
<p>Connect your bank and Ledgerly drafts each invoice when a job is marked done.</p>
<p>Median send time last quarter: 4 minutes (from our status page).</p></body></html>"""

SLOPPY_CODE = """# ==========================================
# Imports
# ==========================================
import os

# Get the user
user = get_user()
# Load the config
config = load_config()
# Save the file
save_file(path)

def load():
    try:
        return os.listdir(".")
    except Exception:
        pass

print("ROCKET Server started")  # Certainly! Here is the updated code
""".replace("ROCKET", ROCKET)

CLEAN_CODE = """import os


def load(path):
    # Missing folders are normal on first run, so treat them as empty.
    try:
        return os.listdir(path)
    except FileNotFoundError:
        return []


def main():
    try:
        run()
    except KeyboardInterrupt: pass
"""


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = slop.main(list(argv))
    return rc, out.getvalue()


class Slop(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, text):
        p = os.path.join(self.tp.base, "site", name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return p

    def test_sloppy_page(self):
        hits = slop.scan_text("index.html", SLOPPY_PAGE)
        for cid in ("hype", "dashes", "emoji", "contrast-frame", "claims", "placeholders", "gradients",
                    "gradient-text", "stock-type"):
            self.assertIn(cid, hits, cid)
        claims = " ".join(s for _, s in hits["claims"])
        self.assertIn("above 100", claims)
        self.assertIn("precise time", claims)
        self.assertIn("user-count", claims)
        # Script contents are not copy.
        self.assertFalse(any("seamless" in s.lower() for _, s in hits["hype"]))

    def test_clean_page(self):
        self.assertEqual(slop.scan_text("index.html", CLEAN_PAGE), {})

    def test_sloppy_code(self):
        hits = slop.scan_text("app.py", SLOPPY_CODE)
        for cid in ("banners", "echo-comments", "swallowed-errors", "emoji", "assistant-chatter"):
            self.assertIn(cid, hits, cid)

    def test_clean_code(self):
        self.assertEqual(slop.scan_text("app.py", CLEAN_CODE), {})

    def test_js_swallow_and_ignore_pragma(self):
        hits = slop.scan_text("a.js", "try { go() } catch (e) {}\ntry { go() } catch (e) {} // slop: ignore\n")
        self.assertEqual([n for n, _ in hits["swallowed-errors"]], [1])

    def test_score_is_capped_per_check(self):
        many = dict(("hype", [("f%d.html" % k, 1, "x") for k in range(10)]) for _ in range(1))
        self.assertEqual(slop.score(many), 100 - 3 * 4)
        self.assertEqual(slop.score({}), 100)

    def test_record(self):
        self.file("index.html", SLOPPY_PAGE)
        self.file("app.py", SLOPPY_CODE)
        rc, out = run("--dir", self.d, "record", os.path.join(self.tp.base, "site"), "--budget", "70")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-slop")
        cats = {f["category"] for f in fs}
        self.assertIn("slop:claims", cats)
        self.assertIn("slop:swallowed-errors", cats)
        self.assertTrue(all(f["status"] == "verified" for f in fs))
        self.assertTrue(all(":" in f["location"] for f in fs))
        m = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual(m["id"], "slop-score")
        self.assertFalse(ledger.metric_passes(m))

    def test_record_clean_marks_missing_kinds_not_applicable(self):
        self.file("app.py", CLEAN_CODE)
        rc, out = run("--dir", self.d, "record", os.path.join(self.tp.base, "site"))
        self.assertEqual(rc, 0)
        self.assertEqual(ledger.load_findings(self.d, "proof-slop"), [])
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-slop")["checks"]}
        self.assertEqual(cov["hype"], "not_applicable")
        self.assertEqual(cov["banners"], "covered")
        self.assertEqual(cov["assistant-chatter"], "covered")

    def test_skips_dependency_folders(self):
        self.file("node_modules/pkg/index.js", SLOPPY_CODE)
        self.file("app.py", CLEAN_CODE)
        r = slop.scan([os.path.join(self.tp.base, "site")])
        self.assertEqual(r["files"], 1)


if __name__ == "__main__":
    unittest.main()
