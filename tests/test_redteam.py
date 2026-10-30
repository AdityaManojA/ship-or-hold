import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

redteam = load_tool("proof-ai", "redteam.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = redteam.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class RedTeam(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_deck_has_owasp_ids(self):
        rc, out = run("deck")
        self.assertEqual(rc, 0)
        self.assertIn("LLM01", out)
        for c in redteam.CATEGORIES:
            self.assertTrue(c["owasp"].startswith("LLM"))

    def test_plan_creates_coverage(self):
        rc, out = run("--dir", self.d, "plan")
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-ai")
        self.assertEqual(len(cov["checks"]), len(redteam.CATEGORIES))

    def test_mobile_plan_adds_the_phone_checks(self):
        rc, out = run("--dir", self.d, "plan", "--mobile")
        self.assertEqual(rc, 0, out)
        ids = {c["id"] for c in ledger.load_coverage(self.d, "proof-ai")["checks"]}
        for cid in ("offline-fallback", "clipboard-injection", "deeplink-injection", "image-text-injection",
                    "on-device-leak", "model-integrity", "prompt-injection"):
            self.assertIn(cid, ids)
        self.tp.write("evidence/proof-ai/logcat.txt", "prompt text in the device log")
        rc, out = run("--dir", self.d, "result", "on-device-leak", "fail", "--evidence", "evidence/proof-ai/logcat.txt",
                      "--title", "Prompts are written to logcat", "--verify")
        self.assertEqual(rc, 0, out)
        self.assertTrue(any("LLM06" in r for r in ledger.load_findings(self.d, "proof-ai")[0]["references"]))
        rc, out = run("deck", "--mobile")
        self.assertIn("model-integrity", out)
        rc, out = run("deck")
        self.assertNotIn("model-integrity", out)

    def test_result_pass_needs_evidence(self):
        run("--dir", self.d, "plan")
        rc, out = run("--dir", self.d, "result", "pii-leak", "pass", "--evidence", "evidence/proof-ai/missing.txt")
        self.assertNotEqual(rc, 0)
        self.tp.write("evidence/proof-ai/pii-1.txt", "transcript")
        rc, out = run("--dir", self.d, "result", "pii-leak", "pass", "--evidence", "evidence/proof-ai/pii-1.txt")
        self.assertEqual(rc, 0, out)

    def test_result_fail_writes_finding(self):
        run("--dir", self.d, "plan")
        self.tp.write("evidence/proof-ai/pi-1.txt", "transcript showing injection")
        rc, out = run("--dir", self.d, "result", "prompt-injection", "fail",
                      "--evidence", "evidence/proof-ai/pi-1.txt",
                      "--title", "Injected text overrode the system prompt", "--severity", "high", "--verify")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-ai")
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["status"], "verified")
        self.assertTrue(any("LLM01" in r for r in fs[0].get("references", [])))

    def test_unknown_category(self):
        run("--dir", self.d, "plan")
        rc, out = run("--dir", self.d, "na", "not-a-cat", "--note", "x")
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
