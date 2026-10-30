"""End to end: run Proof against the planted-bug demo shop and check it catches everything."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

from _load import ROOT

_spec = importlib.util.spec_from_file_location("run_demo", os.path.join(ROOT, "examples", "run_demo.py"))
run_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_demo)


class Demo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="proof-demo-")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            cls.d, cls.code = run_demo.run(os.path.join(cls.base, "proof"), quiet=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_every_planted_problem_is_caught(self):
        missed = [desc for desc, _, _, found in run_demo.caught(self.d) if not found]
        self.assertEqual(missed, [])

    def test_verdict_is_not_yet(self):
        with open(os.path.join(self.d, "verdict.json"), encoding="utf-8") as fh:
            v = json.load(fh)
        self.assertEqual(v["verdict"], "Not yet")
        self.assertEqual(v["gaps"], [])  # every required skill reported fully

    def test_outputs_written(self):
        for name in ("report.md", "report.html", "proof.sarif", "target.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.d, name)), name)
        with open(os.path.join(self.d, "proof.sarif"), encoding="utf-8") as fh:
            sarif = json.load(fh)
        self.assertGreaterEqual(len(sarif["runs"][0]["results"]), len(run_demo.EXPECTED))

    def test_ledger_validates(self):
        validate = run_demo.tool("proof-core", "validate.py")
        problems, _ = validate.validate_dir(self.d)
        self.assertEqual(problems, [])

    def test_fixing_everything_gives_ship(self):
        """Ruling out every finding (as if each were fixed) flips the verdict to Ship."""
        ledger = run_demo.tool("proof-core", "ledger.py")
        verdict = run_demo.tool("proof-report", "verdict.py")
        copy = os.path.join(self.base, "fixed")
        shutil.copytree(self.d, copy)
        for f in ledger.load_findings(copy):
            if f["status"] != "false_positive":
                ledger.reject(copy, f["skill"], f["id"], "fixed in the next build (test)")
        r = verdict.compute(copy)
        # Findings no longer block; what remains are measured scores still over budget,
        # which only change when the page is measured again.
        self.assertTrue(r["blockers"])
        self.assertTrue(all(b["kind"] == "score" for b in r["blockers"]))
        for s in ledger.load_all_scores(copy):
            fixed = [dict(m, value=m["budget"]) for m in s["metrics"]]
            ledger.write_scores(copy, s["skill"], fixed)
        self.assertEqual(verdict.compute(copy)["verdict"], "Ship")


if __name__ == "__main__":
    unittest.main()
