import contextlib
import copy
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

of = load_tool("proof-offer", "offer.py")
ledger = load_tool("proof-core", "ledger.py")
SKILL = "proof-offer"


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = of.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


def base():
    """Ten problems, each answered by a built, high-value, low-cost solution. No findings."""
    stages = ["before"] * 4 + ["during"] * 3 + ["after"] * 3
    sources = ["panel", "panel", "competitors", "owner", "panel", "competitors", "owner", "panel", "owner", "panel"]
    roles = ["core", "bonus", "bonus", "guarantee", "core", "bonus", "bonus", "bonus", "bonus", "bonus"]
    return {
        "business": "Cafe", "currency": "$", "name": "First cup", "core": "A good cup in four minutes.",
        "problems": [{"id": "p%d" % (i + 1), "stage": stages[i], "problem": "Problem %d" % (i + 1),
                      "source": sources[i]} for i in range(10)],
        "solutions": [{"problem": "p%d" % (i + 1), "solution": "Solution %d" % (i + 1), "value": 4, "cost": 2,
                       "exists": "yes", "role": roles[i]} for i in range(10)],
        "guarantee": {"promise": "Next one free", "cost_if_claimed": "8", "expected_claim_rate": "0.05"},
        "urgency": {"what": "Opening week refill", "true": True, "ends": "Sunday"},
        "scores": {"before": {"outcome": 6, "belief": 4, "speed": 5, "ease": 5},
                   "after": {"outcome": 7, "belief": 8, "speed": 5, "ease": 4}},
        "retest": "Show the new pitch to the same panel.",
    }


class Offer(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def record(self, spec):
        path = os.path.join(self.tp.base, "offer.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        return run("record", path, "--dir", self.d)

    def ids(self):
        return sorted(f["id"] for f in ledger.load_findings(self.d, SKILL))

    def coverage(self):
        return {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}

    def scores(self):
        with open(ledger.scores_path(self.d, SKILL), encoding="utf-8") as fh:
            return {m["id"]: m for m in json.load(fh)["metrics"]}

    def section(self):
        with open(os.path.join(self.d, "business", "offer.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_happy_path(self):
        res = of.analyse(base())
        self.assertEqual([len(res["by_stage"][s]) for s in of.STAGES], [4, 3, 3])
        self.assertEqual(res["by_source"], {"panel": 5, "competitors": 2, "owner": 3, "estimate": 0})
        self.assertEqual(len(res["stack"]), 10)
        self.assertAlmostEqual(res["guarantee"]["expected_cost"], 0.4)
        self.assertEqual(res["scores"]["change"], {"outcome": 1, "belief": 4, "speed": 0, "ease": -1})
        self.assertEqual(res["findings"], [])

        code, out = self.record(base())
        self.assertEqual(code, 0, out)
        text = self.section()
        self.assertTrue(text.startswith("# The offer\n"))
        for part in ("### Before buying", "## The solutions, scored", "## The stack", "**Bonuses**",
                     "## The guarantee", "Expected cost: $0.40", "## Before and after", "| +4 |",
                     "## What the offer cannot fix", "## Re-testing the pitch"):
            self.assertIn(part, text)
        self.assertEqual(set(self.coverage().values()), {"covered"})
        sc = self.scores()
        self.assertEqual(sc["problems_answered_pct"]["value"], 100)
        self.assertEqual((sc["problems_answered_pct"]["op"], sc["problems_answered_pct"]["budget"]), (">=", 0))
        self.assertEqual(sc["stack_promises_missing"]["value"], 0)
        self.assertTrue(all(ledger.metric_passes(m) for m in sc.values()))
        self.assertEqual(self.ids(), [])
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", SKILL, "offer.json")))

    def test_check_prints_and_records_nothing(self):
        path = os.path.join(self.tp.base, "offer.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(base(), fh)
        code, out = run("check", path, "--dir", self.d)
        self.assertEqual(code, 0)
        self.assertIn("# The offer", out)
        self.assertEqual(os.listdir(self.d), [])

    def test_stack_takes_only_high_value_low_cost(self):
        spec = base()
        spec["solutions"][1].update(value=3)            # a bonus that is not worth enough
        spec["solutions"][2].update(cost=3)             # a bonus that costs too much
        res = of.analyse(spec)
        self.assertEqual(len(res["stack"]), 8)
        self.assertEqual([s["solution"] for s in res["left_out"]], ["Solution 2", "Solution 3"])
        self.assertAlmostEqual(res["answered_pct"], 80.0)
        self.record(spec)
        self.assertIn("Left out because", self.section())

    def test_unbuilt_stack_item_is_high(self):
        spec = base()
        spec["solutions"][0]["exists"] = "no"
        spec["solutions"][4]["exists"] = "partly"
        self.record(spec)
        f = ledger.load_finding(self.d, SKILL, "offer-not-built-1")
        self.assertEqual((f["severity"], f["status"]), ("high", "verified"))
        self.assertEqual(f["title"], "The offer promises something that is not built: Solution 1")
        self.assertEqual(ledger.load_finding(self.d, SKILL, "offer-partly-built-5")["severity"], "medium")
        sc = self.scores()
        self.assertEqual(sc["stack_promises_missing"]["value"], 1)   # 'partly' is not counted as missing
        self.assertFalse(ledger.metric_passes(sc["stack_promises_missing"]))
        self.assertEqual(sc["problems_answered_pct"]["value"], 90)   # 'partly' still answers its problem
        # Looked at, so covered; the unbuilt item stays a high finding and is named in the note.
        self.assertEqual(self.coverage()["promises-exist"], "covered")
        entry = [c for c in ledger.load_coverage(self.d, SKILL)["checks"] if c["id"] == "promises-exist"][0]
        self.assertIn("Solution 1", entry.get("note", ""))
        self.assertIn("**not built today**", self.section())

    def test_unbuilt_item_outside_the_stack_is_not_a_promise(self):
        spec = base()
        spec["solutions"][0].update(exists="no", role="later")
        self.record(spec)
        self.assertNotIn("offer-not-built-1", self.ids())
        self.assertEqual(self.scores()["stack_promises_missing"]["value"], 0)

    def test_biggest_objections_unanswered_is_medium(self):
        spec = base()
        spec["solutions"][0].update(value=5, role="later", exists="no")
        spec["solutions"][1].update(value=5, cost=5, role="not_ours", exists="no")
        spec["solutions"][2].update(value=4, role="later")
        res = of.analyse(spec)
        self.assertEqual(len(res["big_unanswered"]), 3)
        self.record(spec)
        f = ledger.load_finding(self.d, SKILL, "offer-big-objections-unanswered")
        self.assertEqual((f["severity"], f["status"]), ("medium", "verified"))
        self.assertEqual(f["title"], "The biggest objections are not answered by the offer")
        self.assertIn("Solution 1", f["description"])
        self.assertIn("Solution 2", f["description"])
        self.assertNotIn("Solution 3", f["description"])   # value 4 is listed in the section, not in this finding
        text = self.section()
        self.assertIn("Not ours to fix: Solution 2", text)
        self.assertIn("Later: Solution 3", text)

    def test_value_four_later_alone_raises_no_finding(self):
        spec = base()
        spec["solutions"][2].update(role="later")
        self.record(spec)
        self.assertEqual(self.ids(), [])

    def test_untrue_urgency_is_medium(self):
        spec = base()
        spec["urgency"]["true"] = False
        self.record(spec)
        f = ledger.load_finding(self.d, SKILL, "offer-urgency-not-true")
        self.assertEqual((f["severity"], f["title"]), ("medium", "Urgency is not true"))
        self.assertIn("must not be used", self.section())

    def test_problem_without_solution_is_low(self):
        spec = base()
        spec["problems"].append({"id": "Late_Fee", "stage": "after", "problem": "I was charged twice", "source": "estimate"})
        spec["unsolved"] = [{"problem": "Late_Fee", "why": "The payment provider decides this."}]
        self.record(spec)
        self.assertEqual(self.ids(), ["offer-no-solution-late-fee"])
        f = ledger.load_finding(self.d, SKILL, "offer-no-solution-late-fee")
        self.assertEqual(f["severity"], "low")
        self.assertIn("The payment provider decides this.", f["description"])
        text = self.section()
        self.assertIn("**No solution yet.**", text)
        self.assertIn("Why: The payment provider decides this.", text)
        self.assertAlmostEqual(self.scores()["problems_answered_pct"]["value"], round(100 * 10 / 11.0, 1))

    def test_fewer_than_ten_problems_is_low(self):
        spec = base()
        spec["problems"] = spec["problems"][:9]
        spec["solutions"] = spec["solutions"][:9]
        self.record(spec)
        self.assertEqual(self.ids(), ["offer-fewer-than-ten-problems"])
        f = ledger.load_finding(self.d, SKILL, "offer-fewer-than-ten-problems")
        self.assertEqual((f["severity"], f["title"]), ("low", "Fewer than ten problems listed"))
        self.assertEqual(self.coverage()["problems-listed"], "not_run")

    def test_guarantee_and_retest_coverage(self):
        spec = base()
        del spec["guarantee"]
        del spec["retest"]
        spec["solutions"][3]["role"] = "bonus"           # nothing is a guarantee any more
        self.record(spec)
        cov = self.coverage()
        self.assertEqual(cov["guarantee-costed"], "not_applicable")
        self.assertEqual(cov["retest-planned"], "not_run")
        self.assertIn("No guarantee is offered.", self.section())
        spec["solutions"][3]["role"] = "guarantee"       # promised, but never costed
        self.record(spec)
        self.assertEqual(self.coverage()["guarantee-costed"], "not_run")

    def test_rerecord_replaces_stale_findings(self):
        spec = base()
        spec["solutions"][0]["exists"] = "no"
        spec["urgency"]["true"] = False
        self.record(spec)
        self.assertEqual(self.ids(), ["offer-not-built-1", "offer-urgency-not-true"])
        other = ledger.new_finding(SKILL, "Checked by hand", "low", id="by-hand")
        ledger.write_finding(self.d, other)
        self.record(base())
        self.assertEqual(self.ids(), ["by-hand"])       # ours are gone, someone else's finding is left alone
        self.assertEqual(self.coverage()["promises-exist"], "covered")

    def test_currency_is_required(self):
        spec = base()
        del spec["currency"]
        code, out = self.record(spec)
        self.assertEqual(code, 2)
        self.assertIn("currency", out)
        self.assertEqual(os.listdir(self.d), [])

    def test_missing_and_bad_inputs_exit_2(self):
        for key in ("core", "name", "problems", "solutions"):
            spec = base()
            del spec[key]
            code, out = self.record(spec)
            self.assertEqual(code, 2, key)
            self.assertIn(key, out)
        edits = [
            lambda s: s["problems"][0].pop("stage"),
            lambda s: s["problems"][0].update(source="a hunch"),
            lambda s: s["problems"][1].update(id="p1"),                 # the same id twice
            lambda s: s["solutions"][0].update(problem="p99"),          # no such problem
            lambda s: s["solutions"][0].pop("value"),
            lambda s: s["solutions"][0].update(cost=6),
            lambda s: s["solutions"][0].update(exists="soon"),
            lambda s: s["solutions"][0].update(role="hero"),
            lambda s: s["guarantee"].pop("expected_claim_rate"),
            lambda s: s["guarantee"].update(expected_claim_rate="5"),   # a percent typed where a share belongs
            lambda s: s["urgency"].update(true="yes"),
            lambda s: s["scores"]["after"].pop("ease"),
            lambda s: s["scores"]["before"].update(belief=11),
            lambda s: s.update(unsolved=[{"problem": "p1"}]),           # no why
        ]
        for i, edit in enumerate(edits):
            spec = copy.deepcopy(base())
            edit(spec)
            self.assertEqual(self.record(spec)[0], 2, "edit %d" % i)
        self.assertEqual(run("record", os.path.join(self.tp.base, "nope.json"), "--dir", self.d)[0], 2)
        self.assertEqual(os.listdir(self.d), [])

    def test_template_runs_and_matches_the_example(self):
        out_path = os.path.join(self.tp.base, "offer.example.json")
        self.assertEqual(run("template", "--out", out_path, "--dir", self.d)[0], 0)
        with open(out_path, encoding="utf-8") as fh:
            made = json.load(fh)
        with open(os.path.join(SKILLS_DIR, SKILL, "offer.example.json"), encoding="utf-8") as fh:
            self.assertEqual(made, json.load(fh))
        code, out = run("record", out_path, "--dir", self.d)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.coverage()["problems-listed"], "covered")
        self.assertEqual(self.scores()["stack_promises_missing"]["value"], 0)
        mine = os.path.join(self.tp.base, "mine.json")
        with open(mine, "w", encoding="utf-8") as fh:
            fh.write("{}")
        self.assertNotEqual(run("template", "--out", mine, "--dir", self.d)[0], 0)


if __name__ == "__main__":
    unittest.main()
