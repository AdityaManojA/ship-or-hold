import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

dev = load_tool("proof-llm-device", "llmdevice.py")
ledger = load_tool("proof-core", "ledger.py")

BENCH = [
    {"model_type": "gemma 2B Q4_K_M", "model_size": 1610612736, "n_prompt": 512, "n_gen": 0, "avg_ts": 61.4},
    {"model_type": "gemma 2B Q4_K_M", "model_size": 1610612736, "n_prompt": 0, "n_gen": 128, "avg_ts": 6.2},
]

MEMINFO = """Applications Memory Usage (in Kilobytes):
** MEMINFO in pid 4242 [com.example.chat] **
                   Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap
                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
                ------   ------   ------   ------   ------   ------   ------   ------
  Native Heap   812345   812000        0        0   815000   900000   850000    50000
        TOTAL  1843200  1700000    40000        0  1900000   900000   850000    50000
"""

MEMINFO_SUMMARY = """** MEMINFO in pid 4242 [com.example.chat] **
 App Summary
                       Pss(KB)                        Rss(KB)
           TOTAL PSS:  2,150,400            TOTAL RSS:  2,300,000      TOTAL SWAP PSS:      0
"""

BATTERY_BEFORE = "Current Battery Service state:\n  AC powered: false\n  level: 87\n  scale: 100\n"
BATTERY_AFTER = "Current Battery Service state:\n  AC powered: false\n  level: 81\n  scale: 100\n"



def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = dev.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class LlmDevice(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, text):
        p = os.path.join(self.tp.base, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text if isinstance(text, str) else json.dumps(text))
        return p

    def test_llama_bench(self):
        kind, parsed = dev.classify("b.json", json.dumps(BENCH))
        self.assertEqual(kind, "llama-bench")
        self.assertEqual(parsed["prefill_tps"], [61.4])
        self.assertEqual(parsed["decode_tps"], [6.2])
        self.assertAlmostEqual(parsed["model_mb"][0], 1536.0)

    def test_meminfo_table_and_summary(self):
        self.assertAlmostEqual(dev.parse_meminfo(MEMINFO), 1800.0)
        self.assertAlmostEqual(dev.parse_meminfo(MEMINFO_SUMMARY), 2100.0)
        self.assertEqual(dev.classify("m.txt", MEMINFO)[0], "meminfo")

    def test_csv_runs_median_and_worst(self):
        csv_text = "run,ttft_ms,decode_tps\n1,800,12.5\n2,900,11.5\n3,2000,10.5\n4,850,12\n"
        kind, parsed = dev.classify("r.csv", csv_text)
        self.assertEqual(kind, "measurements")
        s = dev.summarise("ttft_ms", parsed["ttft_ms"])
        self.assertEqual(s["median"], 875.0)
        self.assertGreater(s["worst"], 1500)  # the slow tail for a lower-is-better metric
        d = dev.summarise("decode_tps", parsed["decode_tps"])
        self.assertLess(d["worst"], d["median"])  # the slow tail for a higher-is-better metric

    def test_locale_safe_numbers(self):
        rows = [{"decode_tps": "12,5"}, {"decode_tps": "1.234,5"}]
        parsed = dev.parse_generic(rows)
        self.assertEqual(parsed["decode_tps"], [12.5, 1234.5])

    def test_throttle_derived_from_a_sustained_run(self):
        rows = [{"decode_tps": 10.0}] * 10 + [{"decode_tps": 7.0}] * 10
        parsed = dev.parse_generic({"runs": rows})
        self.assertAlmostEqual(parsed["throttle_pct"][0], 30.0)

    def test_battery_needs_two_captures_and_prompts(self):
        b1, b2 = self.file("before.txt", BATTERY_BEFORE), self.file("after.txt", BATTERY_AFTER)
        values, _, _ = dev.gather([b1, b2], prompts=200)
        self.assertAlmostEqual(values["battery_pct_per_100"][0], 3.0)
        with self.assertRaises(dev.InputError):
            dev.gather([b1, b2])
        with self.assertRaises(dev.InputError):
            dev.gather([b1], prompts=100)

    def test_peak_memory_is_the_highest_capture(self):
        values, _, _ = dev.gather([self.file("m1.txt", MEMINFO), self.file("m2.txt", MEMINFO_SUMMARY)])
        self.assertAlmostEqual(values["peak_mem_mb"][0], 2100.0)

    def test_unknown_file_is_an_error(self):
        rc, out = run("--dir", self.d, "measure", self.file("x.txt", "hello world\n"))
        self.assertNotEqual(rc, 0)
        self.assertIn("not a llama-bench", out)

    def test_budget_classes_and_overwrite_guard(self):
        out = os.path.join(self.tp.base, "budget.json")
        rc, msg = run("--dir", self.d, "budget", "--out", out, "--class", "low")
        self.assertEqual(rc, 0, msg)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual({m["id"]: m["budget"] for m in data["metrics"]}["decode_tps"], 4)
        self.assertEqual(run("--dir", self.d, "budget", "--out", out)[0], 0)  # its own file is replaced
        mine = self.file("mine.json", '{"mine": true}')
        rc, msg = run("--dir", self.d, "budget", "--out", mine)
        self.assertNotEqual(rc, 0)
        self.assertIn("--force", msg)
        self.assertEqual(run("--dir", self.d, "budget", "--out", mine, "--force")[0], 0)

    def test_record_scores_findings_and_coverage(self):
        files = [self.file("bench.json", BENCH), self.file("meminfo.txt", MEMINFO),
                 self.file("before.txt", BATTERY_BEFORE), self.file("after.txt", BATTERY_AFTER)]
        rc, out = run("--dir", self.d, "record", *files, "--prompts", "100", "--device", "Pixel 8",
                      "--model", "gemma-2b-q4")
        self.assertEqual(rc, 0, out)
        metrics = {m["id"]: m for m in ledger.load_all_scores(self.d)[0]["metrics"]}
        self.assertEqual(set(metrics), {"prefill_tps", "decode_tps", "peak_mem_mb", "battery_pct_per_100", "model_mb"})
        self.assertFalse(ledger.metric_passes(metrics["decode_tps"]))  # 6.2 against 8 (mid class)
        self.assertTrue(ledger.metric_passes(metrics["peak_mem_mb"]))
        fs = {f["id"]: f for f in ledger.load_findings(self.d, "proof-llm-device")}
        self.assertIn("llm-device-decode-tps", fs)
        self.assertIn("llm-device-model-mb", fs)  # 1536 MB against 1500
        self.assertTrue(all(f["status"] == "verified" and f["severity"] == "medium" for f in fs.values()))
        self.assertIn("Pixel 8", fs["llm-device-decode-tps"]["title"])
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-llm-device")["checks"]}
        self.assertEqual(cov["ttft_ms"], "not_applicable")
        self.assertEqual(cov["throttle_pct"], "not_applicable")
        self.assertEqual(cov["decode_tps"], "covered")
        self.assertTrue(os.listdir(ledger.evidence_dir(self.d, "proof-llm-device")))

    def test_a_fixed_metric_drops_its_finding(self):
        run("--dir", self.d, "record", self.file("bench.json", BENCH))
        self.assertIn("llm-device-decode-tps", {f["id"] for f in ledger.load_findings(self.d, "proof-llm-device")})
        fast = [dict(BENCH[0]), dict(BENCH[1], avg_ts=14.0)]
        fast[0]["model_size"] = fast[1]["model_size"] = 900 * 1024 * 1024
        rc, out = run("--dir", self.d, "record", self.file("bench2.json", fast))
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, "proof-llm-device"), [])

    def test_custom_budget(self):
        budget = self.file("b.json", {"metrics": [{"id": "decode_tps", "budget": 5, "op": ">="}]})
        rc, out = run("--dir", self.d, "record", self.file("bench.json", BENCH), "--budget", budget)
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-llm-device")["checks"]
        self.assertEqual([c["id"] for c in cov], ["decode_tps"])
        self.assertEqual(ledger.load_findings(self.d, "proof-llm-device"), [])

    def test_status(self):
        self.assertIn("no on-device", run("--dir", self.d, "status")[1])
        run("--dir", self.d, "record", self.file("bench.json", BENCH))
        self.assertIn("decode_tps", run("--dir", self.d, "status")[1])


if __name__ == "__main__":
    unittest.main()
