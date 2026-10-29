import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

load = load_tool("proof-load", "loadplan.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = load.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Load(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_plan_writes_budget_and_script(self):
        out = os.path.join(self.tp.base, "load")
        rc, _ = run("--dir", self.d, "plan", "--target", "http://localhost:3000/", "--vus", "10",
                    "--duration", "30", "--out", out)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(out, "budget.json")))
        with open(os.path.join(out, "load.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("http://localhost:3000/", js)
        self.assertIn("vus: 10", js)

    def test_record_scores(self):
        out = os.path.join(self.tp.base, "load")
        run("--dir", self.d, "plan", "--target", "http://x/", "--out", out)
        measured = os.path.join(self.tp.base, "m.json")
        with open(measured, "w", encoding="utf-8") as fh:
            json.dump({"p95_ms": 420, "p99_ms": 900, "error_rate": 0.002, "rps": 75}, fh)
        rc, o = run("--dir", self.d, "record", "--measured", measured, "--budget", os.path.join(out, "budget.json"))
        self.assertEqual(rc, 0, o)
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-load"][0]
        by = {m["id"]: m for m in scores["metrics"]}
        self.assertTrue(ledger.metric_passes(by["p95_ms"]))
        self.assertTrue(ledger.metric_passes(by["rps"]))

    def test_record_over_budget(self):
        out = os.path.join(self.tp.base, "load")
        run("--dir", self.d, "plan", "--target", "http://x/", "--out", out)
        measured = os.path.join(self.tp.base, "m.json")
        with open(measured, "w", encoding="utf-8") as fh:
            json.dump({"p95_ms": 1200, "p99_ms": 3000, "error_rate": 0.2, "rps": 5}, fh)
        run("--dir", self.d, "record", "--measured", measured, "--budget", os.path.join(out, "budget.json"))
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-load"][0]
        by = {m["id"]: m for m in scores["metrics"]}
        self.assertFalse(ledger.metric_passes(by["p95_ms"]))
        self.assertFalse(ledger.metric_passes(by["error_rate"]))

    def test_missing_metric_is_error(self):
        out = os.path.join(self.tp.base, "load")
        run("--dir", self.d, "plan", "--target", "http://x/", "--out", out)
        measured = os.path.join(self.tp.base, "m.json")
        with open(measured, "w", encoding="utf-8") as fh:
            json.dump({"p95_ms": 420}, fh)  # missing the rest
        rc, o = run("--dir", self.d, "record", "--measured", measured, "--budget", os.path.join(out, "budget.json"))
        self.assertNotEqual(rc, 0)
        self.assertIn("missing", o.lower())

    def test_from_k6(self):
        out = os.path.join(self.tp.base, "load")
        run("--dir", self.d, "plan", "--target", "http://x/", "--out", out)
        summary = os.path.join(self.tp.base, "k6.json")
        with open(summary, "w", encoding="utf-8") as fh:
            json.dump({"metrics": {
                "http_req_duration": {"values": {"p(95)": 300, "p(99)": 800}},
                "http_req_failed": {"values": {"rate": 0.001}},
                "http_reqs": {"values": {"rate": 120}},
            }}, fh)
        rc, o = run("--dir", self.d, "from-k6", "--summary", summary, "--budget", os.path.join(out, "budget.json"))
        self.assertEqual(rc, 0, o)
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-load"][0]
        by = {m["id"]: m["value"] for m in scores["metrics"]}
        self.assertEqual(by["p95_ms"], 300)
        self.assertEqual(by["rps"], 120)


if __name__ == "__main__":
    unittest.main()
