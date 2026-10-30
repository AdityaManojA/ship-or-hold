import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

cost = load_tool("proof-llm-cost", "llmcost.py")
ledger = load_tool("proof-core", "ledger.py")

PRICES = {"_note": "test prices", "big-model": {"input_per_1m": 2.0, "output_per_1m": 8.0, "cached_input_per_1m": 0.5},
          "small-model": {"input_per_1m": 0.2, "output_per_1m": 0.8}}


def rows():
    out = []
    # Two users over two days, three conversations; c3 runs away.
    for i in range(4):
        out.append({"model": "big-model-2026-01-01", "input_tokens": 1000, "output_tokens": 500, "cached_tokens": 400,
                    "latency_ms": 1200 + i * 100, "user": "u1", "conversation": "c1",
                    "timestamp": "2026-10-01T10:00:%02dZ" % (i * 10), "prompt_hash": "h%d" % i})
    for i in range(3):
        out.append({"model": "small-model", "prompt_tokens": 2000, "completion_tokens": 100, "latency_ms": 900,
                    "user_id": "u2", "session_id": "c2", "timestamp": "2026-10-02T09:00:%02dZ" % (i * 20),
                    "prompt_hash": "x%d" % i, "status": "ok"})
    for i in range(30):
        out.append({"model": "small-model", "input_tokens": 500, "output_tokens": 50, "latency_ms": 20000 if i == 0 else 800,
                    "user": "u2", "conversation": "c3", "timestamp": "2026-10-02T12:%02d:00Z" % i,
                    "prompt_hash": "loop", "error": "rate limited" if i < 2 else None})
    # A duplicate: same prompt twice within two seconds.
    out.append({"model": "small-model", "input_tokens": 500, "output_tokens": 50, "latency_ms": 800, "user": "u2",
                "conversation": "c2", "timestamp": "2026-10-02T09:00:41Z", "prompt_hash": "x2"})
    return out


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cost.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class LlmCost(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, text):
        p = os.path.join(self.tp.base, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text if isinstance(text, str) else json.dumps(text))
        return p

    def prices(self):
        return self.file("prices.json", PRICES)

    def test_cost_math_with_cache_and_dated_model(self):
        prices = cost.load_prices(self.prices())
        recs = [cost.normalise({"model": "big-model-2026-01-01", "input_tokens": 1000, "output_tokens": 500,
                                "cached_tokens": 400})]
        r = cost.analyse(recs, prices)
        # 600 * 2 + 400 * 0.5 + 500 * 8 = 5400 per million
        self.assertAlmostEqual(r["values"]["cost_per_request_p50"], 0.0054)
        self.assertAlmostEqual(r["values"]["cache_hit_share"], 0.4)

    def test_cached_price_defaults_to_input_price(self):
        prices = cost.load_prices(self.prices())
        self.assertEqual(prices["small-model"]["cached"], 0.2)

    def test_unknown_model_is_refused_not_guessed(self):
        p = self.file("u.jsonl", json.dumps({"model": "mystery-model", "input_tokens": 10, "output_tokens": 5}) + "\n")
        rc, out = run("--dir", self.d, "analyze", p, "--prices", self.prices())
        self.assertNotEqual(rc, 0)
        self.assertIn("no price for: mystery-model", out)

    def test_analyze_jsonl_and_metrics(self):
        p = self.file("usage.jsonl", "\n".join(json.dumps(r) for r in rows()) + "\n")
        rc, out = run("--dir", self.d, "analyze", p, "--prices", self.prices(), "--json")
        self.assertEqual(rc, 0, out)
        data = json.loads(out)
        v = data["values"]
        for mid in ("cost_per_request_p95", "cost_per_conversation_p95", "cost_per_user_day", "heavy_user_month",
                    "tokens_per_request_p95", "latency_p99_ms", "error_rate", "cache_hit_share"):
            self.assertIn(mid, v)
        self.assertAlmostEqual(v["error_rate"], 2 / 38.0)
        self.assertEqual([x["conversation"] for x in data["runaway"]], ["c3"])
        self.assertTrue(any(d["prompt_hash"] == "x2" for d in data["duplicates"]))
        self.assertTrue(any(d["prompt_hash"] == "loop" for d in data["duplicates"]) is False)  # 60 s apart

    def test_csv_with_comma_decimals_and_seconds(self):
        csv_text = "model,input_tokens,output_tokens,latency_s,user,conversation,timestamp\n" \
                   "small-model,1000,200,\"1,5\",u1,c1,1759312800\n" \
                   "small-model,1000,200,\"2,5\",u1,c1,1759312900000\n"
        p = self.file("usage.csv", csv_text)
        rc, out = run("--dir", self.d, "analyze", p, "--prices", self.prices(), "--json")
        self.assertEqual(rc, 0, out)
        v = json.loads(out)["values"]
        self.assertAlmostEqual(v["latency_p50_ms"], 2000)
        self.assertIn("cost_per_user_day", v)

    def test_record_writes_scores_findings_and_summary(self):
        p = self.file("usage.jsonl", "\n".join(json.dumps(r) for r in rows()) + "\n")
        rc, out = run("--dir", self.d, "record", p, "--prices", self.prices(), "--budget-latency-p99-ms", "5000")
        self.assertEqual(rc, 0, out)
        metrics = {m["id"]: m for m in ledger.load_all_scores(self.d)[0]["metrics"]}
        self.assertFalse(ledger.metric_passes(metrics["latency_p99_ms"]))
        self.assertFalse(ledger.metric_passes(metrics["error_rate"]))
        ids = {f["id"]: f for f in ledger.load_findings(self.d, "proof-llm-cost")}
        self.assertIn("llm-cost-latency-p99-ms", ids)
        self.assertIn("llm-cost-runaway", ids)
        self.assertEqual(ids["llm-cost-runaway"]["severity"], "medium")
        self.assertIn("c3", ids["llm-cost-runaway"]["description"])
        self.assertEqual(ids["llm-cost-duplicates"]["severity"], "low")
        self.assertTrue(all(f["status"] == "verified" for f in ids.values()))
        with open(os.path.join(self.d, "evidence", "proof-llm-cost", "cost-summary.json"), encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["active_users"], 2)
        self.assertGreater(summary["llm_cost_per_heavy_user_per_month_usd"], 0)
        # Raising the budget and recording again drops the stale finding.
        rc, out = run("--dir", self.d, "record", p, "--prices", self.prices(), "--budget-latency-p99-ms", "60000")
        ids = {f["id"] for f in ledger.load_findings(self.d, "proof-llm-cost")}
        self.assertNotIn("llm-cost-latency-p99-ms", ids)

    def test_missing_fields_are_not_applicable(self):
        p = self.file("bare.json", {"records": [{"model": "small-model", "input_tokens": 100, "output_tokens": 10}]})
        rc, out = run("--dir", self.d, "record", p, "--prices", self.prices())
        self.assertEqual(rc, 0, out)
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-llm-cost")["checks"]}
        for mid in ("cost_per_conversation_p50", "cost_per_user_day", "latency_p95_ms", "cache_hit_share",
                    "runaway", "duplicates"):
            self.assertEqual(cov[mid], "not_applicable", mid)
        self.assertEqual(cov["cost_per_request_p50"], "covered")

    def test_prices_template_and_guard(self):
        out = os.path.join(self.tp.base, "proj", "prices.json")
        rc, msg = run("--dir", self.d, "prices", "--out", out)
        self.assertEqual(rc, 0, msg)
        self.assertIn("placeholder", msg)
        rc, msg = run("--dir", self.d, "prices", "--out", out)  # its own file is replaced
        self.assertEqual(rc, 0, msg)
        mine = self.file("proj/mine.json", '{"x": 1}')
        rc, msg = run("--dir", self.d, "prices", "--out", mine)
        self.assertNotEqual(rc, 0)
        self.assertIn("--force", msg)

    def test_scan_code_finds_uncapped_calls(self):
        src = os.path.join(self.tp.base, "src")
        self.file("src/chat.py", "client = OpenAI()\n"
                                 "r = client.responses.create(model='m', input=msgs)\n"
                                 "s = client.chat.completions.create(\n    model='m',\n    max_tokens=500,\n)\n")
        self.file("src/agent.ts", "const r = await anthropic.messages.create({ model: 'm', messages });\n")
        self.file("src/node_modules/x.js", "client.chat.completions.create({})\n")
        rc, out = run("--dir", self.d, "scan-code", src, "--json")
        hits = json.loads(out)["uncapped"]
        self.assertEqual(sorted((os.path.basename(h["file"]), h["line"]) for h in hits),
                         [("agent.ts", 1), ("chat.py", 2)])
        rc, out = run("--dir", self.d, "scan-code", src, "--record")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-llm-cost")
        self.assertEqual(len(fs), 2)
        self.assertTrue(all(f["severity"] == "low" and ":" in f["location"] for f in fs))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-llm-cost")["checks"]}
        self.assertEqual(cov["token-caps"], "covered")
        # A later record keeps the code check.
        p = self.file("bare.json", [{"model": "small-model", "input_tokens": 100, "output_tokens": 10}])
        run("--dir", self.d, "record", p, "--prices", self.prices())
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-llm-cost")["checks"]}
        self.assertEqual(cov["token-caps"], "covered")

    def test_status(self):
        rc, out = run("--dir", self.d, "status")
        self.assertIn("no LLM cost", out)


if __name__ == "__main__":
    unittest.main()
