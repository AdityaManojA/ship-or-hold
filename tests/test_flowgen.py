import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

flowgen = load_tool("proof-mobile-e2e", "flowgen.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = flowgen.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


SPEC = {"flows": [
    {"id": "sign-in", "title": "Sign in", "critical": True, "steps": ["Tap email", "Type", "Tap Sign in"], "expect": "home"},
    {"id": "checkout", "title": "Checkout", "steps": ["Tap Pay"], "expect": "confirmation"},
]}


class FlowGen(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.spec = os.path.join(self.tp.base, "flows.json")
        with open(self.spec, "w", encoding="utf-8") as fh:
            json.dump(SPEC, fh)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_plan(self):
        rc, out = run("--dir", self.d, "plan", self.spec)
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-mobile-e2e")
        self.assertEqual({c["id"] for c in cov["checks"]}, {"sign-in", "checkout"})

    def test_maestro_generation(self):
        run("--dir", self.d, "plan", self.spec)
        out = os.path.join(self.tp.base, "s.yaml")
        rc, _ = run("--dir", self.d, "maestro", "sign-in", "--out", out, "--app", "com.example.app")
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            y = fh.read()
        self.assertIn("appId: com.example.app", y)
        self.assertIn("launchApp", y)
        self.assertIn("takeScreenshot", y)

    def test_result_pass_and_fail(self):
        run("--dir", self.d, "plan", self.spec)
        self.tp.write("evidence/proof-mobile-e2e/sign-in.png", b"PNG")
        rc, _ = run("--dir", self.d, "result", "sign-in", "pass", "--evidence", "evidence/proof-mobile-e2e/sign-in.png")
        self.assertEqual(rc, 0)
        self.tp.write("evidence/proof-mobile-e2e/checkout.png", b"PNG")
        run("--dir", self.d, "result", "checkout", "fail", "--evidence", "evidence/proof-mobile-e2e/checkout.png",
            "--title", "Pay does nothing", "--repro", "tap pay", "--verify")
        fs = ledger.load_findings(self.d, "proof-mobile-e2e")
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["status"], "verified")

    def test_pass_needs_evidence(self):
        run("--dir", self.d, "plan", self.spec)
        rc, out = run("--dir", self.d, "result", "sign-in", "pass", "--evidence", "evidence/proof-mobile-e2e/nope.png")
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
