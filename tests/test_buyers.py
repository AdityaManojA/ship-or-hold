import contextlib
import io
import json
import os
import sys
import unittest

from _load import TempProof, load_tool

buyers = load_tool("proof-buyers", "buyers.py")
ledger = load_tool("proof-core", "ledger.py")

PITCH = "Lunch at your desk by 1pm.\nIf it is late, it is free.\nPay by the week.\n"
PITCH2 = "Lunch at your desk by 1pm.\nIf it is late, it is free.\nTry one day first, no plan needed.\n"


def customer():
    return {
        "business": "Lunch delivery", "target": "office workers", "where": "Pune", "currency": "Rs ",
        "price_for": "one lunch delivered", "age": [22, 45],
        "segments": [{"name": "Junior", "share": 0.5, "income": [25000, 45000]},
                     {"name": "Senior", "share": 0.5, "income": [80000, 150000]}],
        "behaviours": [{"name": "Brings lunch", "share": 0.4, "how": "Packs a box."},
                       {"name": "Orders on an app", "share": 0.6, "how": "Orders at noon."}],
        "objections": ["Quality drops after a month.", "I never heard of them."],
        "habits": ["Decides late.", "Tracks spending."],
    }


def answer(buys, code="habit", free=False, **extra):
    a = {"buys": buys, "times_first_month": 4 if buys else 0, "reason_code": code,
         "reason": "Because of %s, said my way." % code, "objection": "Not sure it lasts.",
         "would_change_mind": "" if buys else "A one day trial."}
    if not free:
        a.update({"price_too_cheap": 50, "price_bargain": 80, "price_getting_expensive": 120,
                  "price_too_expensive": 160})
    a.update(extra)
    return a


def run(*argv, stdin=None):
    out = io.StringIO()
    code = 0
    old = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = buyers.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    finally:
        sys.stdin = old
    return code, out.getvalue()


class Buyers(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.panel = os.path.join(self.d, "business", "panel")

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def path(self, name, content):
        p = os.path.join(self.tp.base, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        return p

    def init(self, cust=None, n=20, extra=(), pitch=PITCH):
        return run("--dir", self.d, "init", "--customer", self.path("customer.json", cust or customer()),
                   "--pitch", self.path("pitch.md", pitch), "--n", str(n), "--seed", "7", *extra)

    def cards(self):
        with open(os.path.join(self.panel, "cards.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def fill(self, plan, free=False):
        """plan: list of (buys, code); cycled over the dealt buyers in order."""
        for i, c in enumerate(self.cards()):
            buys, code = plan[i % len(plan)]
            code_, out = run("--dir", self.d, "save", c["id"], stdin=json.dumps(answer(buys, code, free)))
            self.assertEqual(code_, 0, out)

    def ids(self):
        return sorted(f["id"] for f in ledger.load_findings(self.d, "proof-buyers"))

    def cov(self):
        return {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-buyers")["checks"]}

    def section(self):
        with open(os.path.join(self.d, "business", "buyers.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_same_seed_deals_same_buyers(self):
        self.assertEqual(self.init()[0], 0)
        first = self.cards()
        self.assertEqual(len(first), 20)
        self.assertEqual(self.init()[0], 0)  # no answers yet, so no --force needed
        self.assertEqual(self.cards(), first)
        c = customer()
        for card in first:
            self.assertTrue(22 <= card["age"] <= 45)
            lo, hi = next(s["income"] for s in c["segments"] if s["name"] == card["segment"])
            self.assertTrue(lo <= card["income"] <= hi)
            self.assertIn(card["doubt"], c["objections"])
            self.assertIn(card["habit"], c["habits"])

    def test_currency_and_other_inputs_required(self):
        for key in ("currency", "business", "target", "where", "age", "segments", "behaviours",
                    "objections", "habits", "price_for"):
            c = customer()
            del c[key]
            code, out = self.init(c)
            self.assertEqual(code, 2, key)
            self.assertIn(key, out)
        self.assertFalse(os.path.exists(self.panel))
        code, _ = run("--dir", self.d, "init", "--customer", os.path.join(self.tp.base, "nope.json"),
                      "--pitch", self.path("pitch.md", PITCH))
        self.assertEqual(code, 2)
        self.assertEqual(run("--dir", self.d, "status")[0], 2)  # no panel yet

    def test_shares_must_sum_to_one(self):
        c = customer()
        c["segments"][0]["share"] = 0.3
        code, out = self.init(c)
        self.assertEqual(code, 2)
        self.assertIn("sum to 0.80", out)
        c = customer()
        c["behaviours"][1]["share"] = 0.9
        self.assertEqual(self.init(c)[0], 2)

    def test_pitch_must_be_three_to_six_lines(self):
        self.assertEqual(self.init(pitch="One line only.\n")[0], 2)
        self.assertEqual(self.init(pitch="\n".join("line %d" % i for i in range(7)))[0], 2)

    def test_brief_holds_persona_pitch_price_and_answer_path(self):
        self.init()
        code, out = run("--dir", self.d, "prompts")
        self.assertEqual(code, 0, out)
        self.assertIn("wave 1:", out)
        self.assertIn("wave 2:", out)
        card = self.cards()[0]
        bpath = os.path.abspath(os.path.join(self.panel, "briefs", "b001.md"))
        self.assertIn("b001: Read %s and follow it exactly. It is your whole brief." % bpath, out)
        with open(bpath, encoding="utf-8") as fh:
            brief = fh.read()
        for line in PITCH.strip().splitlines():
            self.assertIn("> " + line, brief)
        self.assertIn("Name: %s" % card["name"], brief)
        self.assertIn("Income: Rs {:,.0f}".format(card["income"]), brief)
        self.assertIn(card["doubt"], brief)
        self.assertIn(card["habit"], brief)
        self.assertIn("Do not be agreeable", brief)
        self.assertIn("Do not invent facts", brief)
        self.assertIn("is in Rs and is for one lunch delivered", brief)
        self.assertIn("price_too_expensive", brief)
        self.assertIn(os.path.abspath(os.path.join(self.panel, "answers", "b001.json")), brief)
        for key in ("buys", "times_first_month", "reason_code", "reason", "objection", "would_change_mind"):
            self.assertIn('"%s"' % key, brief)

    def test_free_product_asks_no_price(self):
        c = customer()
        del c["price_for"]
        self.assertEqual(self.init(c, extra=("--free",))[0], 0)
        run("--dir", self.d, "prompts")
        with open(os.path.join(self.panel, "briefs", "b001.md"), encoding="utf-8") as fh:
            brief = fh.read()
        self.assertNotIn("price_", brief)
        self.assertIn("would really start using it", brief)
        self.fill([(True, "need"), (False, "habit")], free=True)
        code, out = run("--dir", self.d, "tally")
        self.assertEqual(code, 0, out)
        self.assertIn("**10 would use it, 10 pass (50%)**", self.section())
        self.assertNotIn("## Price points", self.section())
        # "free": true in the file does the same as the flag
        c["free"] = True
        self.assertEqual(self.init(c, extra=("--force",))[0], 0)

    def test_save_refuses_invalid_answers(self):
        self.init()
        bad = [
            "not json",
            json.dumps(answer(True, "luck")),
            json.dumps(answer(True, times_first_month=1.5)),
            json.dumps(dict(answer(True), buys="yes")),
            json.dumps(answer(True, reason="")),
            json.dumps(answer(True, price_bargain=10)),  # below the price before it
            json.dumps({k: v for k, v in answer(True).items() if k != "price_too_cheap"}),
            json.dumps(answer(True, id="b002")),
        ]
        for text in bad:
            code, out = run("--dir", self.d, "save", "b001", stdin=text)
            self.assertEqual(code, 2, text)
            self.assertIn("refusing", out)
        self.assertEqual(run("--dir", self.d, "save", "b999", stdin=json.dumps(answer(True)))[0], 2)
        self.assertFalse(os.path.exists(os.path.join(self.panel, "answers", "b001.json")))
        self.assertEqual(run("--dir", self.d, "save", "b001", stdin=json.dumps(answer(True)))[0], 0)
        code, out = run("--dir", self.d, "check")
        self.assertEqual(code, 1)
        self.assertIn("19 of 20 need attention", out)
        self.assertEqual(run("--dir", self.d, "tally")[0], 0)

    def test_tally_with_no_answers_is_an_error(self):
        self.init()
        self.assertEqual(run("--dir", self.d, "tally")[0], 2)
        self.assertFalse(os.path.exists(os.path.join(self.d, "business", "buyers.md")))

    def test_tally_writes_section_results_and_ledger(self):
        self.init()
        # 20 buyers: 8 buy, 12 pass (9 habit... built as a cycle of 20)
        plan = [(True, "need")] * 8 + [(False, "habit")] * 9 + [(False, "price")] * 3
        self.fill(plan)
        self.assertEqual(run("--dir", self.d, "check")[0], 0)
        code, out = run("--dir", self.d, "tally")
        self.assertEqual(code, 0, out)
        text = self.section()
        self.assertTrue(text.startswith("# Buyer panel\n"))
        self.assertIn("**8 would buy, 12 pass (40%)**", text)
        self.assertIn("seed 7", text)
        self.assertIn("simulated buyers", text)
        self.assertIn("upper bound", text)
        self.assertIn("never customer testimonials", text)
        self.assertIn("1. **habit**: 9 of 20", text)
        self.assertIn("\"Because of habit, said my way.\"", text)
        self.assertIn("A one day trial.", text)
        self.assertIn("## By segment", text)
        self.assertIn("## By behaviour", text)
        self.assertIn("a good deal at Rs 80", text)
        with open(os.path.join(self.panel, "results.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        self.assertEqual((res["buys"], res["passes"], res["buy_rate"], res["seed"]), (8, 12, 40.0, 7))
        self.assertEqual(sum(r["buyers"] for r in res["by_segment"]), 20)
        metric = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual((metric["id"], metric["value"], metric["unit"], metric["budget"], metric["op"]),
                         ("buy_rate", 40.0, "percent", 25.0, ">="))
        self.assertEqual(self.ids(), ["buyers-pass-habit"])
        f = ledger.load_finding(self.d, "proof-buyers", "buyers-pass-habit")
        self.assertEqual((f["title"], f["severity"], f["status"], f["evidence"]),
                         ("9 of 20 pass because of habit", "medium", "verified", ["business/panel/results.json"]))
        self.assertEqual(self.cov(), {"panel-run": "covered", "objections-ranked": "covered",
                                      "flip-conditions": "covered", "retested": "not_applicable"})
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", "proof-buyers", "customer.json")))

    def test_min_buy_rate_sets_the_budget(self):
        self.init()
        self.fill([(True, "need"), (False, "trust")])
        self.assertEqual(run("--dir", self.d, "tally", "--min-buy-rate", "60")[0], 0)
        metric = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual(metric["budget"], 60.0)
        self.assertFalse(ledger.metric_passes(metric))

    def test_small_panel_and_thin_groups(self):
        self.init(n=6)
        self.fill([(True, "need")])
        self.assertEqual(run("--dir", self.d, "tally")[0], 0)
        self.assertEqual(self.ids(), ["buyers-panel-small"])
        f = ledger.load_finding(self.d, "proof-buyers", "buyers-panel-small")
        self.assertEqual((f["severity"], f["status"]), ("low", "verified"))
        self.assertIn("thin: under 5 buyers", self.section())
        cov = self.cov()
        self.assertEqual((cov["objections-ranked"], cov["flip-conditions"]), ("not_applicable", "not_applicable"))

    def test_retest_same_buyers_before_and_after(self):
        self.init()
        self.assertEqual(run("--dir", self.d, "retest", "--pitch", self.path("p2.md", PITCH2))[0], 2)  # not tallied
        self.fill([(True, "need")] * 8 + [(False, "habit")] * 9 + [(False, "price")] * 3)
        run("--dir", self.d, "tally")
        before = self.cards()
        self.assertEqual(run("--dir", self.d, "retest", "--pitch", self.path("same.md", PITCH))[0], 2)  # unchanged
        code, out = run("--dir", self.d, "retest", "--pitch", self.path("p2.md", PITCH2))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.cards(), before)
        self.assertEqual(os.listdir(os.path.join(self.panel, "answers")), [])
        self.assertTrue(os.path.isfile(os.path.join(self.panel, "runs", "1", "results.json")))
        self.assertEqual(len(os.listdir(os.path.join(self.panel, "runs", "1", "answers"))), 20)
        run("--dir", self.d, "prompts")
        with open(os.path.join(self.panel, "briefs", "b001.md"), encoding="utf-8") as fh:
            self.assertIn("> Try one day first, no plan needed.", fh.read())
        # the revised pitch moves habit from 9 to 3
        self.fill([(True, "need")] * 14 + [(False, "habit")] * 3 + [(False, "price")] * 3)
        self.assertEqual(run("--dir", self.d, "tally")[0], 0)
        text = self.section()
        self.assertIn("| | Before (run 1) | After (run 2) |", text)
        self.assertIn("| Would buy | 8 | 14 |", text)
        self.assertIn("| habit | 9 | 3 | -6 |", text)
        self.assertIn("| price | 3 | 3 | +0 |", text)
        self.assertEqual(self.cov()["retested"], "covered")
        self.assertEqual(self.ids(), [])  # the stale buyers-pass-habit finding is gone

    def test_retest_that_does_not_move_the_main_objection(self):
        self.init()
        plan = [(True, "need")] * 8 + [(False, "habit")] * 9 + [(False, "price")] * 3
        self.fill(plan)
        run("--dir", self.d, "tally")
        run("--dir", self.d, "retest", "--pitch", self.path("p2.md", PITCH2))
        self.fill(plan)
        self.assertEqual(run("--dir", self.d, "tally")[0], 0)
        self.assertEqual(self.ids(), ["buyers-pass-habit", "buyers-retest-no-move"])
        f = ledger.load_finding(self.d, "proof-buyers", "buyers-retest-no-move")
        self.assertEqual((f["title"], f["severity"], f["status"]),
                         ("A revised pitch did not move the main objection", "medium", "verified"))

    def test_init_over_a_run_panel_needs_force(self):
        self.init()
        self.fill([(True, "need")])
        code, out = self.init()
        self.assertEqual(code, 2)
        self.assertIn("--force", out)
        self.assertEqual(self.init(extra=("--force",))[0], 0)
        self.assertEqual(os.listdir(os.path.join(self.panel, "answers")), [])

    def test_template_is_runnable(self):
        out_dir = os.path.join(self.tp.base, "inputs")
        self.assertEqual(run("template", "--out", out_dir)[0], 0)
        code, out = run("--dir", self.d, "init", "--customer", os.path.join(out_dir, "customer.json"),
                        "--pitch", os.path.join(out_dir, "pitch.md"), "--n", "20", "--seed", "3")
        self.assertEqual(code, 0, out)
        self.assertIn("20 buyers, seed 3", out)
        code, out = run("--dir", self.d, "status")
        self.assertIn("0 answered, 20 to go", out)


if __name__ == "__main__":
    unittest.main()
