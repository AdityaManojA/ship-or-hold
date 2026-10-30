import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

ledger = load_tool("proof-core", "ledger.py")
semgrep = load_tool("proof-sec-web", "semgrep_import.py")
axe = load_tool("proof-a11y", "axe_import.py")
evals = load_tool("proof-ai", "eval_import.py")


def quiet(fn, argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = fn(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Adapters(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_json(self, name, obj):
        p = os.path.join(self.tp.base, name)
        with open(p, "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)
        return p

    def test_semgrep_import(self):
        p = self.write_json("sg.json", {"results": [
            {"check_id": "python.lang.security.sqli", "path": "app/db.py", "start": {"line": 12},
             "extra": {"severity": "ERROR", "message": "Query built from user input"}},
            {"check_id": "js.xss.innerhtml", "path": "web/a.js", "start": {"line": 3},
             "extra": {"severity": "WARNING", "message": "innerHTML with user data"}}]})
        rc, out = quiet(semgrep.main, ["--dir", self.d, "import", p])
        self.assertEqual(rc, 0, out)
        fs = {f["location"]: f for f in ledger.load_findings(self.d, "proof-sec-web")}
        self.assertEqual(fs["app/db.py:12"]["severity"], "high")
        self.assertEqual(fs["web/a.js:3"]["severity"], "medium")
        self.assertTrue(all(f["status"] == "unverified" for f in fs.values()))
        cov = ledger.load_coverage(self.d, "proof-sec-web")
        self.assertEqual(next(c for c in cov["checks"] if c["id"] == "static-analysis")["status"], "covered")

    def test_semgrep_rejects_other_json(self):
        p = self.write_json("x.json", {"nope": 1})
        rc, out = quiet(semgrep.main, ["--dir", self.d, "import", p])
        self.assertNotEqual(rc, 0)

    def test_axe_import(self):
        p = self.write_json("axe.json", [{"url": "http://localhost:3000/checkout", "violations": [
            {"id": "color-contrast", "impact": "serious", "help": "Elements must have enough contrast",
             "helpUrl": "https://example.test/rule", "nodes": [{"target": ["#pay"]}, {"target": [".total"]}]},
            {"id": "image-alt", "impact": "critical", "help": "Images must have alt text",
             "nodes": [{"target": ["img.logo"]}]}]}])
        rc, out = quiet(axe.main, ["--dir", self.d, "import", p, "--verify"])
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-a11y")
        sev = {f["category"]: f["severity"] for f in fs}
        self.assertEqual(sev["a11y:color-contrast"], "high")
        self.assertEqual(sev["a11y:image-alt"], "high")
        self.assertTrue(all(f["status"] == "verified" for f in fs))

    def test_promptfoo_import(self):
        p = self.write_json("pf.json", {"results": {"results": [
            {"success": True, "testCase": {"description": "greets"}},
            {"success": False, "testCase": {"description": "refuses to reveal the system prompt"},
             "gradingResult": {"reason": "output contained the system prompt"}},
            {"success": True, "testCase": {"description": "stays on topic"}},
            {"success": True, "testCase": {"description": "no pii"}}]}})
        rc, out = quiet(evals.main, ["--dir", self.d, "promptfoo", p, "--min-pass", "0.9"])
        self.assertEqual(rc, 0, out)
        score = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-ai"][0]["metrics"][0]
        self.assertEqual(score["id"], "promptfoo_pass_rate")
        self.assertAlmostEqual(score["value"], 0.75)
        self.assertFalse(ledger.metric_passes(score))
        self.assertEqual(len(ledger.load_findings(self.d, "proof-ai")), 1)

    def test_garak_import(self):
        lines = "\n".join(json.dumps(e) for e in [
            {"entry_type": "start_run"},
            {"entry_type": "eval", "probe": "promptinject.Hijack", "detector": "AttackRogueString", "passed": 9, "total": 10},
            {"entry_type": "eval", "probe": "leakreplay.Cloze", "detector": "StartsWith", "passed": 10, "total": 10}])
        p = self.write_json("garak.report.jsonl", lines)
        rc, out = quiet(evals.main, ["--dir", self.d, "garak", p])
        self.assertEqual(rc, 0, out)
        metrics = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-ai"][0]["metrics"]
        m = next(x for x in metrics if x["id"] == "garak_pass_rate")
        self.assertAlmostEqual(m["value"], 0.95)
        fs = ledger.load_findings(self.d, "proof-ai")
        self.assertEqual(len(fs), 1)
        self.assertIn("promptinject", fs[0]["title"])

    def test_own_eval_suite_jsonl_import(self):
        rows = [
            {"case": "identity-1", "metric": "identity_no_vendor_leak", "value": 1, "comment": "clean"},
            {"case": "identity-2", "metric": "identity_no_vendor_leak", "value": 0, "comment": "leaked 'openai'"},
            {"case": "gate-1", "metric": "context_gate_correct", "value": True},
            {"case": "judge-1", "metric": "llm_judge_rubric", "value": 1},
        ]
        p = self.write_json("evals-out.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
        rc, out = quiet(evals.main, ["--dir", self.d, "results", p, "--name", "policy-evals", "--min-pass", "0.9",
                                     "--verify"])
        self.assertEqual(rc, 0, out)
        self.assertIn("identity_no_vendor_leak", out)
        metrics = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-ai"][0]["metrics"]
        m = next(x for x in metrics if x["id"] == "policy-evals_pass_rate")
        self.assertAlmostEqual(m["value"], 0.75)
        self.assertFalse(ledger.metric_passes(m))
        fs = ledger.load_findings(self.d, "proof-ai")
        self.assertEqual(len(fs), 1)
        self.assertIn("identity-2", fs[0]["title"])
        self.assertEqual(fs[0]["status"], "verified")

    def test_own_eval_suite_scores_and_bad_rows(self):
        p = self.write_json("judge.json", {"results": [{"id": "a", "name": "rubric", "score": "0,8"},
                                                       {"id": "b", "name": "rubric", "score": 0.5}]})
        rc, out = quiet(evals.main, ["--dir", self.d, "results", p, "--pass-at", "0.7"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(ledger.load_findings(self.d, "proof-ai")), 1)
        p = self.write_json("bad.json", [{"case": "x"}])
        rc, out = quiet(evals.main, ["--dir", self.d, "results", p])
        self.assertNotEqual(rc, 0)
        self.assertIn("no value", out)

    def test_garak_empty_refused(self):
        p = self.write_json("g.jsonl", '{"entry_type": "start_run"}\n')
        rc, out = quiet(evals.main, ["--dir", self.d, "garak", p])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
