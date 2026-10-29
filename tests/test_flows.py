import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

flows = load_tool("proof-web-e2e", "flows.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = flows.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


SPEC = {"flows": [
    {"id": "login", "title": "Sign in", "critical": True, "start": "http://localhost:3000/login",
     "steps": ["enter", "submit"], "expect": "home"},
    {"id": "checkout", "title": "Checkout", "critical": False, "start": "http://localhost:3000/cart",
     "steps": ["pay"], "expect": "confirmation"},
]}


class Flows(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.spec = os.path.join(self.tp.base, "flows.json")
        with open(self.spec, "w", encoding="utf-8") as fh:
            json.dump(SPEC, fh)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def plan(self):
        return run("--dir", self.d, "plan", self.spec)

    def test_plan_rejects_missing_field(self):
        bad = {"flows": [{"id": "x", "title": "X", "start": "u", "steps": ["a"]}]}  # no expect
        with open(self.spec, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        rc, out = self.plan()
        self.assertNotEqual(rc, 0)
        self.assertIn("expect", out)

    def test_plan_rejects_duplicate_ids(self):
        dup = {"flows": [SPEC["flows"][0], SPEC["flows"][0]]}
        with open(self.spec, "w", encoding="utf-8") as fh:
            json.dump(dup, fh)
        rc, out = self.plan()
        self.assertNotEqual(rc, 0)
        self.assertIn("duplicate", out)

    def test_plan_creates_coverage_and_checklist(self):
        rc, out = self.plan()
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-web-e2e")
        self.assertEqual({c["id"] for c in cov["checks"]}, {"login", "checkout"})
        self.assertTrue(all(c["status"] == "not_run" for c in cov["checks"]))
        self.assertTrue(os.path.exists(os.path.join(self.d, "web", "checklist.md")))

    def test_pass_needs_evidence(self):
        self.plan()
        rc, out = run("--dir", self.d, "result", "login", "pass", "--evidence", "evidence/proof-web-e2e/missing.png")
        self.assertNotEqual(rc, 0)
        self.tp.write("evidence/proof-web-e2e/login.png", b"PNG")
        rc, out = run("--dir", self.d, "result", "login", "pass", "--evidence", "evidence/proof-web-e2e/login.png")
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-web-e2e")
        self.assertEqual(next(c for c in cov["checks"] if c["id"] == "login")["status"], "covered")

    def test_fail_writes_finding(self):
        self.plan()
        self.tp.write("evidence/proof-web-e2e/checkout.png", b"PNG")
        rc, out = run("--dir", self.d, "result", "checkout", "fail",
                      "--evidence", "evidence/proof-web-e2e/checkout.png",
                      "--title", "Double submit", "--severity", "high",
                      "--repro", "click twice")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-web-e2e")
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["category"], "flow:checkout")
        self.assertEqual(fs[0]["status"], "unverified")

    def test_fail_verify(self):
        self.plan()
        self.tp.write("evidence/proof-web-e2e/checkout.png", b"PNG")
        run("--dir", self.d, "result", "checkout", "fail",
            "--evidence", "evidence/proof-web-e2e/checkout.png",
            "--title", "Double submit", "--repro", "x", "--verify")
        fs = ledger.load_findings(self.d, "proof-web-e2e")
        self.assertEqual(fs[0]["status"], "verified")
        self.assertEqual(fs[0]["severity"], "medium")  # non-critical default

    def test_fail_needs_title(self):
        self.plan()
        self.tp.write("evidence/proof-web-e2e/checkout.png", b"PNG")
        rc, out = run("--dir", self.d, "result", "checkout", "fail", "--evidence", "evidence/proof-web-e2e/checkout.png")
        self.assertNotEqual(rc, 0)
        self.assertIn("title", out)

    def test_skip_needs_note(self):
        self.plan()
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                flows.main(["--dir", self.d, "skip", "login"])
        rc, out = run("--dir", self.d, "skip", "login", "--note", "removed in this build")
        self.assertEqual(rc, 0, out)

    def test_script_python_and_node(self):
        self.plan()
        out_py = os.path.join(self.tp.base, "s.py")
        rc, _ = run("--dir", self.d, "script", "login", "--out", out_py, "--python")
        self.assertEqual(rc, 0)
        with open(out_py, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("http://localhost:3000/login", body)
        self.assertIn("evidence/proof-web-e2e/login.png", body)
        out_js = os.path.join(self.tp.base, "s.js")
        run("--dir", self.d, "script", "login", "--out", out_js, "--node")
        with open(out_js, encoding="utf-8") as fh:
            self.assertIn("login.png", fh.read())

    def test_edge_cases(self):
        self.plan()
        rc, out = run("--dir", self.d, "edge-cases", "login")
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(out.count("  - "), 10)


if __name__ == "__main__":
    unittest.main()
