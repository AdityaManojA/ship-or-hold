import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

ue = load_tool("proof-finance", "unit_economics.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ue.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


BASE = {
    "business": "Cafe", "unit": "cup", "price": "6.50", "days_per_month": 30,
    "variable": [{"label": "milk", "cost": "1.10"}, {"label": "cup", "cost": "0.40"}],
    "fixed_monthly": [{"label": "rent", "cost": "3200"}],
    "startup": [{"label": "machine", "cost": "12000"}],
    "plan_per_day": "200", "capacity_per_day": "400",
}


class UnitEconomics(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_contribution_and_breakeven(self):
        a = ue.analyse(BASE)
        self.assertAlmostEqual(a["contribution"], 5.0)  # 6.50 - 1.50
        self.assertAlmostEqual(a["contribution_pct"], 5.0 / 6.5, places=4)
        # break-even per day = fixed / contribution / days = 3200 / 5 / 30
        self.assertAlmostEqual(a["breakeven_per_day"], 3200 / 5 / 30, places=4)

    def test_missing_field_is_error(self):
        bad = dict(BASE)
        del bad["plan_per_day"]
        with self.assertRaises(ue.NumbersError):
            ue.analyse(bad)
        bad2 = dict(BASE)
        del bad2["price"]
        with self.assertRaises(ue.NumbersError):
            ue.analyse(bad2)

    def test_missing_cost_entry_is_error(self):
        bad = dict(BASE)
        bad["variable"] = [{"label": "milk"}]  # no cost
        with self.assertRaises(ue.NumbersError):
            ue.analyse(bad)

    def test_locale_numbers(self):
        spec = dict(BASE, price="6,50")
        a = ue.analyse(spec)
        self.assertAlmostEqual(a["price"], 6.5)

    def test_year1_and_cash(self):
        a = ue.analyse(dict(BASE, ramp_months=3))
        self.assertEqual(len(a["months"]), 12)
        # ramp means month 1 sells a third of plan
        self.assertAlmostEqual(a["months"][0]["units"], 200 * 30 / 3.0, places=2)
        self.assertLessEqual(a["cash_needed"], a["startup"] + 1e6)
        self.assertGreaterEqual(a["cash_needed"], a["startup"])  # startup at least

    def test_record_writes_scores(self):
        numbers = os.path.join(self.tp.base, "n.json")
        with open(numbers, "w", encoding="utf-8") as fh:
            json.dump(BASE, fh)
        rc, out = run(numbers, "--dir", self.d, "--record")
        self.assertEqual(rc, 0, out)
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-finance"][0]
        ids = {m["id"] for m in scores["metrics"]}
        self.assertIn("contribution_per_unit", ids)
        self.assertTrue(os.path.exists(os.path.join(self.d, "finance.md")))

    def test_negative_contribution_finding(self):
        loss = dict(BASE, price="1.00")  # below the 1.50 variable cost
        numbers = os.path.join(self.tp.base, "n.json")
        with open(numbers, "w", encoding="utf-8") as fh:
            json.dump(loss, fh)
        run(numbers, "--dir", self.d, "--record")
        fs = ledger.load_findings(self.d, "proof-finance")
        self.assertTrue(any(f["severity"] == "high" and f["status"] == "verified" for f in fs))

    def test_template(self):
        out = os.path.join(self.tp.base, "t.json")
        run("--template", out, "--dir", self.d)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("price", json.load(fh))


if __name__ == "__main__":
    unittest.main()
