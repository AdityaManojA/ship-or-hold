import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

ledger = load_tool("proof-core", "ledger.py")
verdict = load_tool("proof-report", "verdict.py")
htmlreport = load_tool("proof-report", "htmlreport.py")
rundiff = load_tool("proof-report", "rundiff.py")

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
       b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def quiet(fn, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = fn(argv)
    return rc, out.getvalue()


class Extras(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        ledger.write_target(self.d, {"name": "Shop <demo>", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        ledger.init_coverage(self.d, "proof-web-e2e", [("checkout", "Checkout")])
        self.tp.write("evidence/proof-web-e2e/checkout.png", PNG)
        ledger.mark_coverage(self.d, "proof-web-e2e", "checkout", "covered",
                             evidence=["evidence/proof-web-e2e/checkout.png"])

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def add(self, title, sev, verify=True, location=None):
        f = ledger.new_finding("proof-web-e2e", title, sev, evidence=["evidence/proof-web-e2e/checkout.png"],
                               location=location, fix="do the fix")
        ledger.write_finding(self.d, f)
        if verify:
            ledger.verify(self.d, "proof-web-e2e", f["id"], by="evidence-file")
        return f["id"]

    # ------------------------------------------------------------ SARIF
    def test_sarif_only_verified_by_default(self):
        self.add("Double submit", "high", location="app/checkout.js:42")
        self.add("Maybe slow", "low", verify=False)
        sarif = verdict.to_sarif(verdict.compute(self.d))
        self.assertEqual(sarif["version"], "2.1.0")
        results = sarif["runs"][0]["results"]
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["level"], "error")
        loc = r["locations"][0]["physicalLocation"]
        self.assertEqual(loc["artifactLocation"]["uri"], "app/checkout.js")
        self.assertEqual(loc["region"]["startLine"], 42)
        self.assertEqual(sarif["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["security-severity"], "7.5")
        self.assertEqual(len(verdict.to_sarif(verdict.compute(self.d), include_unverified=True)["runs"][0]["results"]), 2)

    def test_sarif_flag_writes_file(self):
        self.add("Double submit", "high")
        out = os.path.join(self.tp.base, "proof.sarif")
        rc, _ = quiet(verdict.main, ["--dir", self.d, "--sarif", out, "--check"])
        self.assertEqual(rc, 1)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["runs"][0]["results"]), 1)

    # ------------------------------------------------------------- HTML
    def test_html_report(self):
        self.add("Double submit <b>", "high")
        rc, _ = quiet(htmlreport.main, ["--dir", self.d])
        self.assertEqual(rc, 1)
        with open(os.path.join(self.d, "report.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("Verdict: Not yet", page)
        self.assertIn("data:image/png;base64,", page)
        self.assertIn("Double submit &lt;b&gt;", page)  # escaped
        self.assertIn("Shop &lt;demo&gt;", page)
        self.assertNotIn("<b>", page.replace("<body>", ""))

    # ------------------------------------------------------------- diff
    def test_rundiff(self):
        a = self.add("Double submit", "high")
        ledger.write_scores(self.d, "proof-web-perf", [{"id": "lcp", "value": 3.0, "unit": "s", "budget": 2.5, "op": "<="}])
        quiet(rundiff.main, ["--dir", self.d, "save", "--label", "before"])
        ledger.reject(self.d, "proof-web-e2e", a, "fixed in build 2")
        self.add("Coupon field accepts negative values", "medium")
        ledger.write_scores(self.d, "proof-web-perf", [{"id": "lcp", "value": 2.0, "unit": "s", "budget": 2.5, "op": "<="}])
        quiet(rundiff.main, ["--dir", self.d, "save", "--label", "after"])
        rc, out = quiet(rundiff.main, ["--dir", self.d, "compare", "--json"])
        self.assertEqual(rc, 0)
        dd = json.loads(out)
        self.assertEqual(dd["from"]["verdict"], "Not yet")
        self.assertEqual(dd["to"]["verdict"], "Ship")
        self.assertEqual(len(dd["fixed"]), 1)
        self.assertEqual(len(dd["new"]), 1)
        lcp = dd["scores"][0]
        self.assertAlmostEqual(lcp["percent"], -33.3, places=1)
        self.assertTrue(lcp["passes_now"])
        rc, text = quiet(rundiff.main, ["--dir", self.d, "compare"])
        self.assertIn("1 fixed, 1 new", text)

    def test_rundiff_needs_two(self):
        rc, out = quiet(rundiff.main, ["--dir", self.d, "compare"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
