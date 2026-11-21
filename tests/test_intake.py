import contextlib
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

it = load_tool("proof-intake", "intake.py")
ledger = load_tool("proof-core", "ledger.py")
SKILL = "proof-intake"
ORDERS = os.path.join(SKILLS_DIR, "proof-intake", "orders.example.csv")
SESSIONS = os.path.join(SKILLS_DIR, "proof-intake", "sessions.example.csv")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = it.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Intake(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def intake(self):
        with open(os.path.join(self.d, "business", "intake.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def full(self, **over):
        args = {"--currency": "Rs ", "--unit-cost": "300", "--ad-spend": "50000", "--sessions": "20",
                "--orders": "0.4"}
        args.update(over)
        argv = ["--dir", self.d, "set"]
        for k, v in args.items():
            argv += [k, v]
        return run(*argv)

    def ids(self):
        return sorted(f["id"] for f in ledger.load_findings(self.d, SKILL))

    def test_set_writes_the_contract_shape(self):
        rc, out = self.full(**{"--period": "last 30 days"})
        self.assertEqual(rc, 0, out)
        data = self.intake()
        self.assertEqual(data["currency"], "Rs ")
        self.assertEqual(data["period"], "last 30 days")
        self.assertEqual(data["landed_unit_cost"], {"value": 300.0, "source": "owner"})
        self.assertEqual(data["daily_orders"], {"value": 0.4, "source": "owner"})
        self.assertEqual(data["computed"]["conversion_pct"], 2.0)
        self.assertEqual(data["computed"]["ad_spend_per_order"], 4166.67)

    def test_set_changes_one_field_and_keeps_the_rest(self):
        self.full()
        rc, out = run("set", "--sessions", "25", "--source", "analytics-export", "--dir", self.d)
        self.assertEqual(rc, 0, out)
        data = self.intake()
        self.assertEqual(data["daily_sessions"], {"value": 25.0, "source": "analytics-export"})
        self.assertEqual(data["landed_unit_cost"]["source"], "owner")
        self.assertEqual(run("--dir", self.d, "set", "--orders", "1", "--source", "guess")[0], 2)

    def test_check_names_each_missing_field(self):
        run("--dir", self.d, "set", "--currency", "Rs ", "--unit-cost", "300")
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 2)
        for name in ("ad-spend", "sessions", "orders"):
            self.assertIn("missing: %s" % name, out)
        self.assertNotIn("missing: unit-cost", out)
        self.assertEqual(run("--dir", self.d, "check")[0], 2)
        self.full()
        self.assertEqual(run("--dir", self.d, "check")[0], 0)

    def test_import_shopify_example_files(self):
        rc, out = run("--dir", self.d, "import-shopify", "--orders-csv", ORDERS, "--sessions-csv", SESSIONS)
        self.assertEqual(rc, 0, out)
        data = self.intake()
        # 8 distinct orders, #1003 voided and #1005 refunded are left out; 1 Oct to 10 Oct is 10 days
        self.assertEqual(data["daily_orders"], {"value": 0.6, "source": "shopify-export"})
        self.assertEqual(data["daily_sessions"], {"value": 30.0, "source": "shopify-export"})
        self.assertAlmostEqual(data["average_order_value"]["value"], 833.33, places=2)
        self.assertIn("10 days", data["period"])
        self.assertNotIn("landed_unit_cost", data)
        self.assertNotIn("monthly_ad_spend", data)
        self.assertEqual(run("--dir", self.d, "check")[0], 2)  # cost and ads still come from the owner

    def test_import_shopify_days_override(self):
        rc, out = run("--dir", self.d, "import-shopify", "--orders-csv", ORDERS, "--days", "30")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.intake()["daily_orders"]["value"], 0.2)
        self.assertNotIn("daily_sessions", self.intake())

    def test_import_shopify_rejects_a_file_without_the_columns(self):
        bad = os.path.join(self.tp.base, "bad.csv")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("Order,Amount\n1,2\n")
        rc, out = run("--dir", self.d, "import-shopify", "--orders-csv", bad, "--days", "30")
        self.assertEqual(rc, 2)
        self.assertIn("Financial Status", out)

    def test_orders_over_sessions_is_high(self):
        self.full(**{"--sessions": "0.4", "--orders": "20"})
        rc, out = run("--dir", self.d, "record")
        self.assertEqual(rc, 0, out)
        f = ledger.load_finding(self.d, SKILL, "intake-orders-exceed-sessions")
        self.assertEqual((f["severity"], f["status"]), ("high", "verified"))
        self.assertEqual(self.ids(), ["intake-orders-exceed-sessions"])  # not also a conversion flag

    def test_conversion_flags(self):
        self.full(**{"--sessions": "100", "--orders": "30"})
        run("--dir", self.d, "record")
        f = ledger.load_finding(self.d, SKILL, "intake-conversion-high")
        self.assertEqual(f["severity"], "medium")
        self.assertIn("swapped", f["fix"])
        self.full(**{"--sessions": "10000", "--orders": "1"})
        run("--dir", self.d, "record")
        self.assertIn("intake-conversion-low", self.ids())
        self.assertNotIn("intake-conversion-high", self.ids())  # re-recording replaced the earlier one

    def test_ad_spend_over_aov(self):
        self.full(**{"--aov": "850"})  # 50000 / (0.4 * 30) = 4166.67 per order
        run("--dir", self.d, "record")
        self.assertIn("intake-ad-spend-over-aov", self.ids())
        self.full(**{"--aov": "5000"})
        run("--dir", self.d, "record")
        self.assertEqual(self.ids(), [])

    def test_record_coverage_scores_and_section(self):
        run("--dir", self.d, "set", "--currency", "Rs ", "--unit-cost", "300", "--sessions", "20")
        rc, out = run("record", "--dir", self.d)
        self.assertEqual(rc, 0, out)
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(cov, {"unit-cost": "covered", "sessions": "covered",
                               "ad-spend": "not_run", "orders": "not_run"})
        self.full()
        run("record", "--dir", self.d)
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(set(cov.values()), {"covered"})
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == SKILL][0]
        m = {x["id"]: x for x in scores["metrics"]}
        self.assertEqual(m["conversion_pct"]["value"], 2.0)
        self.assertEqual(m["ad_spend_per_order"]["value"], 4166.67)
        self.assertTrue(ledger.metric_passes(m["conversion_pct"]))
        with open(os.path.join(self.d, "business", "intake.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# The numbers we started from\n"))
        self.assertIn("| Landed unit cost | Rs 300.00 | owner |", text)

    def test_status(self):
        self.full()
        rc, out = run("status", "--dir", self.d)
        self.assertEqual(rc, 0)
        self.assertIn("complete", out)


if __name__ == "__main__":
    unittest.main()
