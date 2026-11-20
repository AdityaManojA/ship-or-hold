"""End to end: run the business side on the example inputs and check the plan is compiled from all of it."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

from _load import ROOT

_spec = importlib.util.spec_from_file_location("run_business_demo",
                                               os.path.join(ROOT, "examples", "run_business_demo.py"))
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

SECTIONS = ["Market and competition", "Buyer panel", "Pricing", "The offer", "The numbers", "Go to market",
            "Operations"]


class BusinessDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The plan always prints a PDF; keep the suite from launching a real browser.
        cls._browser = os.environ.get("PROOF_BROWSER")
        os.environ["PROOF_BROWSER"] = "no-such-browser-for-proof-tests"
        cls.base = tempfile.mkdtemp(prefix="proof-business-demo-")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            cls.d, cls.code = demo.run(os.path.join(cls.base, "proof"), quiet=True)

    @classmethod
    def tearDownClass(cls):
        if cls._browser is None:
            os.environ.pop("PROOF_BROWSER", None)
        else:
            os.environ["PROOF_BROWSER"] = cls._browser
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_the_plan_page_for_the_pdf_is_written(self):
        page = self.read("business-plan.html")
        self.assertIn("<div class='cover'>", page)
        self.assertIn("Ship or hold: business plan and board report", page)

    def read(self, name):
        with open(os.path.join(self.d, name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_three_result_files_are_written(self):
        for name in ("summary.md", "business-plan.md", "one-pager.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.d, name)), name)

    def test_every_business_skill_reported_and_the_verdict_rests_on_evidence(self):
        v = json.loads(self.read("verdict.json"))
        self.assertEqual(v["gaps"], [], "a first-pass run must not be held at Incomplete")
        self.assertIn(v["verdict"], ("Ship", "Not yet"))
        self.assertEqual(sorted(v["required"]), sorted(demo.BUSINESS))

    def test_the_plan_holds_every_section_and_fills_none_in(self):
        plan = self.read("business-plan.md")
        for title in SECTIONS:
            self.assertIn("## %s\n" % title, plan)
        self.assertNotIn("Not done. Run /proof-", plan)
        self.assertEqual(plan.count("\n# "), 0, "a section kept a top-level heading")
        # the one part the demo does not do is named, not invented
        self.assertIn("The board has not met", plan)
        self.assertIn("The board's decision: run /proof-board", plan)

    def test_key_numbers_come_from_every_business_skill(self):
        plan = self.read("business-plan.md")
        for prefix in ("market:", "buyers:", "pricing:", "offer:", "finance:", "go-to-market:", "ops:"):
            self.assertIn("| %s" % prefix, plan, prefix)

    def test_one_pager_carries_the_same_numbers_word_for_word(self):
        plan, one = self.read("business-plan.md"), self.read("one-pager.md")
        rows = [line for line in plan.splitlines() if line.startswith(("| market:", "| buyers:", "| finance:"))]
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(row, one)

    def test_money_is_printed_in_the_inputs_currency(self):
        self.assertIn("Rs ", self.read("finance.md"))
        self.assertNotIn("$", self.read("finance.md"))

    def test_the_summary_states_the_computed_verdict(self):
        v = json.loads(self.read("verdict.json"))["verdict"]
        self.assertIn("Verdict: %s." % v, self.read("summary.md"))
        self.assertIn("**Verdict: %s**" % v, self.read("business-plan.md"))


if __name__ == "__main__":
    unittest.main()
