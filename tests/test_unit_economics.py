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

    def test_currency_prefix_is_printed_not_a_dollar_sign(self):
        text = ue.render(ue.analyse(dict(BASE, currency="Rs ")))
        self.assertIn("| Price | Rs 6.50 per cup |", text)
        self.assertIn("Rs 3,200", text)
        self.assertNotIn("$", text)
        self.assertIn("$6.50", ue.render(ue.analyse(BASE)))  # the default is unchanged
        with self.assertRaises(ue.NumbersError):
            ue.analyse(dict(BASE, currency=""))

    def test_month_by_month_ramp(self):
        ramp = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200]
        a = ue.analyse(dict(BASE, ramp_per_day=ramp))
        self.assertAlmostEqual(a["months"][0]["units"], 300)        # 10 a day for 30 days
        self.assertAlmostEqual(a["months"][0]["profit"], 300 * 5.0 - 3200)
        self.assertAlmostEqual(a["months"][11]["units"], 6000)
        with self.assertRaises(ue.NumbersError):
            ue.analyse(dict(BASE, ramp_per_day=[1, 2, 3]))

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

    # ---- intake, COD and RTO, three scenarios, what-if

    def write_intake(self, **over):
        data = {"currency": "Rs ", "period": "last 30 days",
                "landed_unit_cost": {"value": 300, "source": "owner"},
                "monthly_ad_spend": {"value": 50000, "source": "owner"},
                "daily_sessions": {"value": 20, "source": "shopify-export"},
                "daily_orders": {"value": 0.4, "source": "shopify-export"}}
        data.update(over)
        os.makedirs(os.path.join(self.d, "business"), exist_ok=True)
        with open(os.path.join(self.d, "business", "intake.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def numbers(self, spec):
        path = os.path.join(self.tp.base, "n.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        return path

    D2C = {"business": "Kurtas", "unit": "order", "price": "850", "from_intake": True,
           "fixed_monthly": [{"label": "apps", "cost": "2000"}]}

    def test_from_intake_reads_cost_ads_and_orders(self):
        self.write_intake()
        spec = ue.apply_intake(self.D2C, ue.load_intake(os.path.join(self.d, "business", "intake.json")))
        a = ue.analyse(spec)
        self.assertAlmostEqual(a["variable"], 300)
        self.assertAlmostEqual(a["fixed_monthly"], 52000)
        self.assertAlmostEqual(a["plan_per_day"], 0.4)
        self.assertEqual(a["currency"], "Rs ")
        self.assertIn(("Landed unit cost (intake)", 300.0), a["variable_items"])
        self.assertIn(("Ads (intake)", 50000.0), a["fixed_items"])
        rc, out = run(self.numbers(self.D2C), "--dir", self.d, "--record")
        self.assertEqual(rc, 0, out)

    def test_from_intake_missing_or_incomplete_is_exit_2(self):
        n = self.numbers(self.D2C)
        rc, out = run(n, "--dir", self.d)
        self.assertEqual(rc, 2)
        self.assertIn("intake", out)
        self.write_intake(daily_orders=None)
        rc, out = run(n, "--dir", self.d)
        self.assertEqual(rc, 2)
        self.assertIn("daily_orders", out)
        # --intake points at a file anywhere, without from_intake in the numbers
        self.write_intake()
        plain = dict(self.D2C)
        del plain["from_intake"]
        rc, out = run(self.numbers(plain), "--intake", os.path.join(self.d, "business", "intake.json"))
        self.assertEqual(rc, 0, out)

    def test_from_intake_refuses_double_counting(self):
        self.write_intake()
        twice_ads = dict(self.D2C, fixed_monthly=[{"label": "Meta ads", "cost": "10000"}])
        self.assertEqual(run(self.numbers(twice_ads), "--dir", self.d)[0], 2)
        twice_orders = dict(self.D2C, plan_per_day="5")
        self.assertEqual(run(self.numbers(twice_orders), "--dir", self.d)[0], 2)
        # "Adobe" and "loading" are not ads
        ok = dict(self.D2C, fixed_monthly=[{"label": "Adobe licence", "cost": "1000"}])
        self.assertEqual(run(self.numbers(ok), "--dir", self.d)[0], 0)

    def test_cod_and_rto_cost_per_delivered_order(self):
        cod = {"share": 0.6, "rto_rate": 0.25, "rto_cost": 140, "cod_fee": 30}
        spec = dict(BASE, cod=cod)
        a = ue.analyse(spec)
        # 1.50 + 0.6 * 30 + 0.6 * 140 * 0.25 / 0.75 = 1.50 + 18 + 28
        self.assertAlmostEqual(a["variable"], 47.5)
        self.assertAlmostEqual(a["contribution"], 6.5 - 47.5)
        self.assertAlmostEqual(ue.analyse(dict(BASE, cod=dict(cod, share=60, rto_rate=25)))["variable"], 47.5)
        text = ue.render(a)
        self.assertIn("## Cash on delivery and return to origin", text)
        self.assertIn("0.333 returned cups", text)
        for bad in (dict(cod, rto_rate=1), dict(cod, rto_rate=100), dict(cod, share=150)):
            with self.assertRaises(ue.NumbersError):
                ue.analyse(dict(BASE, cod=bad))
        no_cost = dict(cod)
        del no_cost["rto_cost"]
        with self.assertRaises(ue.NumbersError):
            ue.analyse(dict(BASE, cod=no_cost))

    def test_three_scenarios(self):
        spec = dict(BASE, fixed_monthly=[{"label": "rent", "cost": "3200"}, {"label": "ads", "cost": "1800"}],
                    target_per_day="300")
        a = ue.analyse(spec)
        rows = dict(ue.scenarios(a))
        self.assertAlmostEqual(rows["Status quo"]["per_day"], 200)
        self.assertAlmostEqual(rows["Break-even"]["per_day"], 5000 / 5.0 / 30)
        self.assertAlmostEqual(rows["Break-even"]["profit"], 0, places=6)
        self.assertAlmostEqual(rows["Break-even"]["ad_per_order"], 1800 / (5000 / 5.0))
        self.assertAlmostEqual(rows["Target scale"]["profit"], 300 * 30 * 5.0 - 5000)
        text = ue.render(a)
        self.assertIn("## Three scenarios", text)
        self.assertNotIn("## Year 1, month by month", text)  # no ramp: one line instead
        self.assertIn("every month of year one is the same", text)
        no_target = ue.render(ue.analyse(BASE))
        self.assertIn("| Target scale | set target_per_day |", no_target)
        self.assertIn("## Year 1, month by month", ue.render(ue.analyse(dict(BASE, ramp_months=3))))

    def test_breakeven_score(self):
        rc, out = run(self.numbers(BASE), "--dir", self.d, "--record")
        self.assertEqual(rc, 0, out)
        m = {x["id"]: x for x in [s for s in ledger.load_all_scores(self.d)
                                  if s["skill"] == "proof-finance"][0]["metrics"]}
        self.assertAlmostEqual(m["breakeven_orders_per_day"]["value"], round(3200 / 5 / 30, 2))
        self.assertEqual((m["breakeven_orders_per_day"]["op"], m["breakeven_orders_per_day"]["budget"]), (">=", 0))
        self.assertIn("year1_operating_profit", m)
        with open(os.path.join(self.d, "finance.md"), encoding="utf-8") as fh:
            self.assertIn("## Three scenarios", fh.read())

    def test_what_if_prints_the_vote_and_writes_nothing(self):
        self.write_intake()
        n = self.numbers(self.D2C)
        rc, out = run(n, "--dir", self.d, "--what-if", "unit_cost=320")
        self.assertEqual(rc, 0, out)
        self.assertIn("## Three scenarios", out)
        self.assertIn("Investor: Hold, year one loses Rs ", out)
        rc, out = run(n, "--dir", self.d, "--what-if", "conversion=10,ad_spend=4000")  # 2 orders a day
        self.assertEqual(rc, 0, out)
        self.assertIn("| Status quo | 2 |", out)
        self.assertIn("Investor: Go, ", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "finance.md")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "scores")))
        self.assertEqual(run(n, "--dir", self.d, "--what-if", "colour=3")[0], 2)
        # conversion needs the intake's sessions
        self.assertEqual(run(self.numbers(BASE), "--dir", self.d, "--what-if", "conversion=3")[0], 2)

    def test_investor_verdict_is_a_pure_function(self):
        self.assertTrue(ue.investor_verdict(ue.analyse(BASE)).startswith("Investor: Go"))
        self.assertIn("loses", ue.investor_verdict(ue.analyse(dict(BASE, price="1.00"))))

    def test_template(self):
        out = os.path.join(self.tp.base, "t.json")
        run("--template", out, "--dir", self.d)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("price", json.load(fh))


if __name__ == "__main__":
    unittest.main()
