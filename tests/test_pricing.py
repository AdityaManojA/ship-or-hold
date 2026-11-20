import contextlib
import copy
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

pr = load_tool("proof-pricing", "pricing.py")
ledger = load_tool("proof-core", "ledger.py")
SKILL = "proof-pricing"


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = pr.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


def answers(n=12):
    """n tidy answers whose curves overlap around 100 to 140."""
    return [{"too_cheap": 40 + 10 * i, "cheap": 70 + 10 * i, "expensive": 100 + 10 * i,
             "too_expensive": 140 + 10 * i} for i in range(n)]


PAID = {
    "business": "Cafe", "currency": "Rs ", "model": "paid", "price": "130", "price_for": "one cup",
    "variable_cost": "50", "answers": answers(),
    "competitor_prices": [{"name": "A", "price": "100", "includes": "cup", "url": "https://example.com/a"},
                          {"name": "B", "price": "150", "includes": "cup", "url": "https://example.com/b"},
                          {"name": "C", "price": "120", "includes": "cup", "url": "https://example.com/c"}],
    "candidates": ["110", "40"],
    "ladder": [{"name": "Cup", "price": "130", "why_step_up": ""}],
    "opening_offer": {"what": "Half price second cup", "ends": "after 14 days"},
    "tests": [{"price": "130", "how": "Market stall for two Saturdays"}],
}

FREE = {
    "business": "Guide", "currency": "$", "model": "free_to_user",
    "revenue_lines": [
        {"name": "Adverts", "kind": "per_1000_pageviews", "amount": "4", "pages_per_visit": "2.5",
         "status": "live", "source": "owner"},
        {"name": "Referrals", "kind": "per_action", "amount": "10", "rate": "0.02",
         "status": "unproven", "source": "estimate"},
        {"name": "Sponsor", "kind": "fixed_monthly", "amount": "500", "status": "paused", "source": "owner"},
    ],
    "fixed_monthly_cost": "900", "variable_cost_per_1000_visits": "1",
    "tests": [{"how": "Run the referral link for four weeks"}],
}


class Pricing(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def save(self, spec, name="pricing.json"):
        path = os.path.join(self.tp.base, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        return path

    def record(self, spec):
        return run("record", self.save(spec), "--dir", self.d)

    def ids(self):
        return sorted(f["id"] for f in ledger.load_findings(self.d, SKILL))

    def coverage(self):
        return {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}

    def scores(self):
        with open(ledger.scores_path(self.d, SKILL), encoding="utf-8") as fh:
            return {m["id"]: m for m in json.load(fh)["metrics"]}

    # ---- the survey

    def test_four_points_are_ordered_and_exact(self):
        s = pr.sensitivity(answers())
        self.assertTrue(s["ok"])
        p = s["points"]
        self.assertLessEqual(p["marginal_cheapness"], p["optimal"])
        self.assertLessEqual(p["marginal_cheapness"], p["indifference"])
        self.assertLessEqual(p["optimal"], p["marginal_expensiveness"])
        self.assertLessEqual(p["indifference"], p["marginal_expensiveness"])
        # One answer repeated: every curve steps at its own value, so the points are those values' meeting places.
        same = [{"too_cheap": 10, "cheap": 20, "expensive": 30, "too_expensive": 40}] * 10
        p = pr.sensitivity(same)["points"]
        self.assertEqual(p["marginal_cheapness"], 20)   # first price where nobody calls it too cheap any more
        self.assertEqual(p["marginal_expensiveness"], 30)

    def test_shares_at_a_price(self):
        rows = pr.sensitivity(answers())["rows"]
        sh = pr.shares_at(rows, 100)
        self.assertAlmostEqual(sh["too_cheap"], 6 / 12.0)      # too_cheap values 100..150
        self.assertAlmostEqual(sh["expensive"], 1 / 12.0)      # only the first expensive value is <= 100
        self.assertAlmostEqual(sh["too_expensive"], 0.0)

    def test_dropped_answers_are_counted(self):
        rows = answers(14) + [{"too_cheap": 50, "cheap": 40, "expensive": 90, "too_expensive": 100},  # out of order
                              {"too_cheap": 50, "cheap": 60, "expensive": 90}]                        # one missing
        s = pr.sensitivity(rows)
        self.assertEqual((s["total"], s["usable"], s["dropped"]), (16, 14, 2))
        self.assertTrue(s["ok"])

    def test_more_than_a_quarter_dropped_gives_no_range(self):
        bad = [{"too_cheap": 900, "cheap": 80, "expensive": 100, "too_expensive": 120}] * 6
        s = pr.sensitivity(answers(12) + bad)
        self.assertFalse(s["ok"])
        self.assertIsNone(s["points"])
        self.assertIn("quarter", s["why"])

    def test_fewer_than_ten_usable_gives_no_range(self):
        s = pr.sensitivity(answers(9))
        self.assertFalse(s["ok"])
        self.assertIn("at least 10", s["why"])

    def test_answers_from_csv_and_folder(self):
        with open(os.path.join(self.tp.base, "a.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("Too_Cheap,cheap,expensive,too_expensive\n")
            for a in answers():
                fh.write("%d,%d,%d,%d\n" % (a["too_cheap"], a["cheap"], a["expensive"], a["too_expensive"]))
        spec = dict(PAID, answers_file="a.csv")
        del spec["answers"]
        self.assertEqual(pr.analyse(spec, self.tp.base)["sens"]["usable"], 12)

        folder = os.path.join(self.tp.base, "panel")
        os.makedirs(folder)
        for i, a in enumerate(answers()):
            with open(os.path.join(folder, "buyer-%02d.json" % i), "w", encoding="utf-8") as fh:
                json.dump({"buyer": i, "price": a} if i % 2 else a, fh)  # flat, or one level down
        with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump({"count": 12}, fh)
        spec = dict(PAID, answers_dir="panel")
        del spec["answers"]
        res = pr.analyse(spec, self.tp.base)
        self.assertEqual(res["sens"]["usable"], 12)
        self.assertEqual(res["answers_skipped"], 1)
        # recording keeps a copy of the answers it read
        code, _ = run("record", self.save(spec), "--dir", self.d)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", SKILL, "answers.json")))

    def test_reads_the_buyer_panels_answers_folder_as_it_is(self):
        folder = os.path.join(self.tp.base, "answers")
        os.makedirs(folder)
        for i, a in enumerate(answers()):
            with open(os.path.join(folder, "b%03d.json" % i), "w", encoding="utf-8") as fh:
                json.dump({"buys": i % 3 == 0, "reason_code": "habit", "price_too_cheap": a["too_cheap"],
                           "price_bargain": a["cheap"], "price_getting_expensive": a["expensive"],
                           "price_too_expensive": a["too_expensive"]}, fh)
        spec = dict(PAID, answers_dir="answers")
        del spec["answers"]
        panel = pr.analyse(spec, self.tp.base)
        self.assertEqual(panel["sens"]["usable"], 12)
        self.assertEqual(panel["sens"]["points"], pr.analyse(PAID, self.tp.base)["sens"]["points"])

    def test_sensitivity_command_prints_points_only(self):
        code, out = run("sensitivity", self.save(PAID), "--dir", self.d)
        self.assertEqual(code, 0)
        self.assertIn("Point of marginal cheapness", out)
        self.assertIn("Rs ", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "business")))
        code, _ = run("sensitivity", self.save(FREE), "--dir", self.d)
        self.assertEqual(code, 2)

    # ---- paid

    def test_paid_happy_path(self):
        res = pr.analyse(PAID)
        self.assertEqual(res["position"], "inside")
        self.assertEqual((res["comp"]["lowest"], res["comp"]["median"], res["comp"]["highest"]), (100, 120, 150))
        self.assertAlmostEqual(res["margins"][0]["margin"], 80)
        self.assertAlmostEqual(res["margins"][0]["margin_pct"], 100 * 80 / 130.0)
        self.assertTrue(res["margins"][2]["below_cost"])  # the 40 candidate
        self.assertEqual(res["findings"], [])

        code, out = self.record(PAID)
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.d, "business", "pricing.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Pricing\n"))
        for part in ("## The decision", "Indifference point", "## What competitors charge", "does not cover the cost",
                     "## The price ladder", "## Opening offer", "## What to test with real people", "Rs 130"):
            self.assertIn(part, text)
        self.assertEqual(set(self.coverage().values()), {"covered"})
        sc = self.scores()
        self.assertEqual(sc["margin_per_unit"]["value"], 80)
        self.assertEqual(sc["usable_answers_pct"]["value"], 100)
        self.assertTrue(all(ledger.metric_passes(m) for m in sc.values()))
        self.assertEqual(self.ids(), [])
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", SKILL, "pricing.json")))

    def test_check_prints_and_records_nothing(self):
        code, out = run("check", self.save(PAID), "--dir", self.d)
        self.assertEqual(code, 0)
        self.assertIn("# Pricing", out)
        self.assertEqual(os.listdir(self.d), [])

    def test_price_below_cost_is_high(self):
        self.record(dict(PAID, price="50"))
        f = ledger.load_finding(self.d, SKILL, "pricing-below-cost")
        self.assertEqual((f["severity"], f["status"]), ("high", "verified"))
        self.assertEqual(f["title"], "The price does not cover the cost of one unit")
        self.assertFalse(ledger.metric_passes(self.scores()["margin_per_unit"]))

    def test_unusable_answers_is_medium_and_no_range(self):
        bad = [{"too_cheap": 900, "cheap": 80, "expensive": 100, "too_expensive": 120}] * 6
        self.record(dict(PAID, answers=answers(12) + bad))
        f = ledger.load_finding(self.d, SKILL, "pricing-answers-unusable")
        self.assertEqual((f["severity"], f["status"]), ("medium", "verified"))
        self.assertEqual(self.coverage()["buyers-asked"], "not_run")
        self.assertFalse(ledger.metric_passes(self.scores()["usable_answers_pct"]))
        with open(os.path.join(self.d, "business", "pricing.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("No range is reported", text)
        self.assertNotIn("Optimal point", text)

    def test_price_outside_range_is_medium(self):
        self.record(dict(PAID, price="400"))
        f = ledger.load_finding(self.d, SKILL, "pricing-outside-range")
        self.assertEqual((f["severity"], f["status"]), ("medium", "verified"))
        self.assertEqual(f["title"], "The price sits outside what buyers called acceptable")

    def test_no_test_is_low_and_missing_parts_are_not_run(self):
        spec = {k: v for k, v in PAID.items() if k not in ("tests", "answers", "competitor_prices", "ladder")}
        self.record(spec)
        self.assertEqual(self.ids(), ["pricing-no-real-test"])
        self.assertEqual(ledger.load_finding(self.d, SKILL, "pricing-no-real-test")["severity"], "low")
        self.assertEqual(self.coverage(), {"buyers-asked": "not_run", "competitors-compared": "not_run",
                                           "margin-checked": "covered", "ladder-or-lines": "not_run",
                                           "real-test-planned": "not_run"})
        self.assertNotIn("usable_answers_pct", self.scores())

    def test_rerecord_replaces_stale_findings(self):
        self.record(dict(PAID, price="50"))
        self.assertIn("pricing-below-cost", self.ids())
        # a finding someone else wrote for this skill is left alone
        other = ledger.new_finding(SKILL, "Checked by hand", "low", id="by-hand")
        ledger.write_finding(self.d, other)
        self.record(PAID)
        self.assertEqual(self.ids(), ["by-hand"])
        self.assertEqual(self.coverage()["buyers-asked"], "covered")

    # ---- free to user

    def test_free_counts_live_unproven_and_paused(self):
        res = pr.analyse(FREE)
        self.assertAlmostEqual(res["base"]["per_1000"], 10.0)        # 4 x 2.5
        self.assertAlmostEqual(res["upside"]["per_1000"], 210.0)     # + 10 x 0.02 x 1000
        self.assertEqual(res["upside"]["fixed_revenue"], 0)          # the paused sponsor counts as zero
        self.assertAlmostEqual(res["base"]["contribution"], 9.0)
        self.assertAlmostEqual(res["base"]["breakeven_month"], 100000.0)   # 900 / 9 x 1000
        self.assertAlmostEqual(res["base"]["breakeven_day"], 100000.0 / 30)
        self.assertAlmostEqual(res["upside"]["breakeven_month"], 900 / 209.0 * 1000)

        code, out = self.record(FREE)
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.d, "business", "pricing.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Pricing\n"))
        for part in ("Break-even visits a month", "100,000", "paused: counts as zero", "estimate (not a quoted figure)",
                     "$10", "$210"):
            self.assertIn(part, text)
        cov = self.coverage()
        self.assertEqual(cov["buyers-asked"], "not_applicable")
        self.assertEqual(cov["competitors-compared"], "not_applicable")
        self.assertEqual(cov["ladder-or-lines"], "covered")
        self.assertEqual(self.scores()["live_revenue_per_1000_visits"]["value"], 10)
        self.assertEqual(self.ids(), [])

    def test_free_fixed_line_offsets_fixed_cost(self):
        spec = copy.deepcopy(FREE)
        spec["revenue_lines"][2]["status"] = "live"
        res = pr.analyse(spec)
        self.assertAlmostEqual(res["base"]["breakeven_month"], 400 / 9.0 * 1000)
        spec["revenue_lines"][2]["amount"] = "900"
        self.assertEqual(pr.analyse(spec)["base"]["breakeven_month"], 0)

    def test_free_no_live_line_is_high(self):
        spec = copy.deepcopy(FREE)
        spec["revenue_lines"][0]["status"] = "unproven"
        self.record(spec)
        f = ledger.load_finding(self.d, SKILL, "pricing-no-live-revenue")
        self.assertEqual((f["severity"], f["status"], f["title"]), ("high", "verified", "No revenue line is live"))
        self.assertFalse(ledger.metric_passes(self.scores()["live_revenue_per_1000_visits"]))
        with open(os.path.join(self.d, "business", "pricing.md"), encoding="utf-8") as fh:
            self.assertIn("never, at these numbers", fh.read())

    def test_free_mostly_estimate_is_medium(self):
        spec = copy.deepcopy(FREE)
        spec["revenue_lines"][1]["status"] = "live"   # the estimated referral line now carries 200 of 210
        del spec["tests"]
        self.record(spec)
        self.assertEqual(self.ids(), ["pricing-no-real-test", "pricing-rests-on-estimate"])
        f = ledger.load_finding(self.d, SKILL, "pricing-rests-on-estimate")
        self.assertEqual((f["severity"], f["title"]), ("medium", "Most revenue rests on an estimate"))

    # ---- refusals

    def test_currency_is_required(self):
        for spec in (PAID, FREE):
            bad = {k: v for k, v in spec.items() if k != "currency"}
            code, out = self.record(bad)
            self.assertEqual(code, 2)
            self.assertIn("currency", out)
            self.assertEqual(os.listdir(self.d), [])
        code, _ = self.record(dict(PAID, currency="  "))
        self.assertEqual(code, 2)

    def test_missing_inputs_exit_2(self):
        for key in ("model", "price", "price_for", "variable_cost"):
            code, out = self.record({k: v for k, v in PAID.items() if k != key})
            self.assertEqual(code, 2, key)
            self.assertIn(key, out)
        for key in ("revenue_lines", "fixed_monthly_cost", "variable_cost_per_1000_visits"):
            code, out = self.record({k: v for k, v in FREE.items() if k != key})
            self.assertEqual(code, 2, key)
        for field in ("rate", "amount", "status", "source"):
            spec = copy.deepcopy(FREE)
            del spec["revenue_lines"][1][field]
            self.assertEqual(self.record(spec)[0], 2, field)
        spec = copy.deepcopy(FREE)
        del spec["revenue_lines"][0]["pages_per_visit"]
        self.assertEqual(self.record(spec)[0], 2)
        spec = copy.deepcopy(FREE)
        spec["revenue_lines"][1]["rate"] = "2"   # a percent typed where a share belongs
        self.assertEqual(self.record(spec)[0], 2)
        self.assertEqual(self.record(dict(PAID, opening_offer={"what": "Half price"}))[0], 2)  # no end date
        self.assertEqual(run("record", os.path.join(self.tp.base, "nope.json"), "--dir", self.d)[0], 2)
        self.assertEqual(os.listdir(self.d), [])

    # ---- templates

    def test_templates_run_and_match_the_examples(self):
        for flag, example in ((None, "pricing.example.json"), ("--free", "pricing-free.example.json")):
            out_path = os.path.join(self.tp.base, example)
            argv = ["template", "--out", out_path, "--dir", self.d] + ([flag] if flag else [])
            self.assertEqual(run(*argv)[0], 0)
            with open(out_path, encoding="utf-8") as fh:
                made = json.load(fh)
            with open(os.path.join(SKILLS_DIR, SKILL, example), encoding="utf-8") as fh:
                self.assertEqual(made, json.load(fh))
            code, out = run("record", out_path, "--dir", self.d)
            self.assertEqual(code, 0, out)
            self.assertEqual(self.ids(), [])
        # an existing file that Proof did not write is not overwritten
        mine = os.path.join(self.tp.base, "mine.json")
        with open(mine, "w", encoding="utf-8") as fh:
            fh.write("{}")
        self.assertNotEqual(run("template", "--out", mine, "--dir", self.d)[0], 0)


if __name__ == "__main__":
    unittest.main()
