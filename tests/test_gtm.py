import contextlib
import copy
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

gtm = load_tool("proof-go-to-market", "gtm.py")
ledger = load_tool("proof-core", "ledger.py")

SKILL = "proof-go-to-market"

with open(os.path.join(SKILLS_DIR, SKILL, "gtm.example.json"), encoding="utf-8") as _fh:
    BASE = json.load(_fh)


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = gtm.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


def spec(**changes):
    s = copy.deepcopy(BASE)
    s.update(changes)
    return s


def titles(a):
    return [f["title"] for f in a["findings"]]


class Analyse(unittest.TestCase):
    def test_example_is_clean_and_gate_is_not_checked_without_a_ledger(self):
        a = gtm.analyse(spec())
        self.assertEqual(a["findings"], [])
        for cid in ("positioning-stated", "channels-chosen", "brand-basics", "first-test-defined", "countdown-dated"):
            self.assertEqual(a["coverage"][cid][0], "covered", cid)
        self.assertEqual(a["coverage"]["promotion-gate"][0], "not_run")
        self.assertFalse(a["gate"]["checked"])
        self.assertAlmostEqual(a["total"], 2300.0)
        self.assertAlmostEqual(a["headroom"], 700.0)
        self.assertIn("For office workers", a["statement"])

    def test_currency_and_today_are_required(self):
        for key in ("currency", "today"):
            s = spec()
            del s[key]
            with self.assertRaises(gtm.GtmError):
                gtm.analyse(s)
        with self.assertRaises(gtm.GtmError):
            gtm.analyse(spec(currency="  "))
        with self.assertRaises(gtm.GtmError):
            gtm.analyse(spec(today="20 November"))

    def test_missing_cost_budget_or_date_is_an_error(self):
        s = spec()
        del s["channels"][0]["cost_per_month"]
        with self.assertRaises(gtm.GtmError):
            gtm.analyse(s)
        s = spec()
        del s["budget_per_month"]
        with self.assertRaises(gtm.GtmError):
            gtm.analyse(s)
        s = spec()
        del s["first_test"]["starts"]
        with self.assertRaises(gtm.GtmError):
            gtm.analyse(s)
        s = spec()
        del s["countdown"][0]["critical"]
        with self.assertRaises(gtm.GtmError):
            gtm.analyse(s)

    def test_positioning_needs_all_four_parts(self):
        s = spec()
        s["positioning"]["unlike"] = ""
        a = gtm.analyse(s)
        self.assertEqual(a["coverage"]["positioning-stated"][0], "not_run")
        self.assertIsNone(a["statement"])
        self.assertIn("unlike", gtm.render(a))

    def test_first_test_without_a_number_is_a_high_finding(self):
        s = spec()
        s["first_test"]["pass"] = "People like it"
        a = gtm.analyse(s)
        f = [x for x in a["findings"] if x["title"] == "The first test has no pass mark"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "high")
        self.assertEqual(a["coverage"]["first-test-defined"][0], "not_run")
        s["first_test"]["pass"] = "12 of 60 pay"
        del s["first_test"]["fail"]
        self.assertIn("The first test has no pass mark", titles(gtm.analyse(s)))

    def test_first_test_dates(self):
        s = spec()
        s["first_test"]["starts"] = "2026-11-10"  # before today
        self.assertEqual(gtm.analyse(s)["coverage"]["first-test-defined"][0], "not_run")
        s = spec()
        s["first_test"]["ends"] = "2026-11-29"  # before the start
        a = gtm.analyse(s)
        self.assertFalse(a["first_test"]["in_order"])
        self.assertEqual(a["coverage"]["first-test-defined"][0], "not_run")

    def test_over_budget_and_no_budget(self):
        a = gtm.analyse(spec(budget_per_month="2000"))
        self.assertIn("Channels cost more than the budget", titles(a))
        self.assertAlmostEqual(a["headroom"], -300.0)
        a = gtm.analyse(spec(budget_per_month=0))
        f = {x["title"]: x["severity"] for x in a["findings"]}
        self.assertEqual(f["The plan depends on paid reach with no budget"], "low")
        s = spec(budget_per_month=0)
        for c in s["channels"]:
            c["cost_per_month"] = 0
        self.assertEqual(gtm.analyse(s)["findings"], [])

    def test_all_estimates_no_measure_and_name_checks(self):
        s = spec()
        for c in s["channels"]:
            c["evidence"] = "estimate"
        s["channels"][2]["measure"] = ""
        s["name_checks"][0]["status"] = "to_do"
        s["name_checks"][1]["status"] = "to_do"
        f = {x["title"]: x["severity"] for x in gtm.analyse(s)["findings"]}
        self.assertEqual(f["Every channel rests on an estimate"], "medium")
        self.assertEqual(f["Channel 'Printed menu card in the lift lobby' has no measure"], "low")
        self.assertEqual(f["The trademark check on the name is still to do"], "medium")
        self.assertEqual(f["The domain check on the name is still to do"], "medium")
        self.assertEqual(len(f), 4)  # the handle check being to do is not a finding

    def test_channels_and_brand_coverage(self):
        s = spec()
        s["channels"] = s["channels"][:1]
        s["voice"] = s["voice"][:2]
        s["name_checks"] = s["name_checks"][:3]
        a = gtm.analyse(s)
        self.assertEqual(a["coverage"]["channels-chosen"][0], "not_run")
        self.assertEqual(a["coverage"]["brand-basics"][0], "not_run")
        self.assertIn("meaning", a["coverage"]["brand-basics"][1])

    def test_countdown_overdue_and_critical_after_launch(self):
        s = spec()
        s["countdown"][0]["date"] = "2026-11-01"
        a = gtm.analyse(s)
        self.assertEqual(len(a["overdue"]), 1)
        self.assertEqual(a["coverage"]["countdown-dated"][0], "not_run")
        s = spec()
        s["countdown"][4]["date"] = "2026-12-20"  # critical, after the launch on 12-14
        a = gtm.analyse(s)
        self.assertEqual(len(a["late"]), 1)
        self.assertEqual(a["coverage"]["countdown-dated"][0], "not_run")
        s = spec()
        s["countdown"].reverse()
        a = gtm.analyse(s)
        self.assertFalse(a["in_order"])
        text = gtm.render(a)
        self.assertLess(text.index("2026-11-24"), text.index("2026-12-12 |"))


class Recorded(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.path = os.path.join(self.tp.base, "gtm.json")

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def save(self, s):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(s, fh)

    def blocker(self, skill="proof-privacy", fid="no-consent", severity="high", verify=True):
        ledger.write_target(self.d, {"name": "Tiffin Loop", "kind": "web", "authorised": True,
                                     "required_skills": [skill]})
        ev = self.tp.write("evidence/%s/%s.txt" % (skill, fid), "captured")
        ledger.write_finding(self.d, ledger.new_finding(skill, "Tracking starts before consent", severity,
                                                        id=fid, evidence=[ev]))
        if verify:
            ledger.verify(self.d, skill, fid, by="evidence-file")
        return "%s/%s" % (skill, fid)

    def findings(self):
        return {f["title"]: f for f in ledger.load_findings(self.d, SKILL)}

    def scores(self):
        got = [s for s in ledger.load_all_scores(self.d) if s["skill"] == SKILL]
        return {m["id"]: m for m in got[0]["metrics"]} if got else {}

    def coverage(self):
        return {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}

    def test_template_then_record_writes_the_section(self):
        rc, out = run("template", "--out", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.d, "business", "go-to-market.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Go to market\n"))
        for part in ("## Positioning", "## Promise and voice", "## Name checks", "## Channels",
                     "## Before any promotion", "## The first test", "## Countdown", "Rs 2,300", "CRITICAL"):
            self.assertIn(part, text)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", SKILL, "gtm.json")))
        self.assertEqual(self.findings(), {})
        m = self.scores()
        self.assertEqual(m["channel_cost_within_budget"]["value"], 700)
        self.assertTrue(ledger.metric_passes(m["channel_cost_within_budget"]))

    def test_gate_is_not_checked_without_a_ledger(self):
        self.save(spec())
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.coverage()["promotion-gate"], "not_run")
        self.assertNotIn("blockers_without_a_fix_date", self.scores())
        with open(os.path.join(self.d, "business", "go-to-market.md"), encoding="utf-8") as fh:
            self.assertIn("was not checked", fh.read())

    def test_open_blocker_without_a_fix_is_a_high_finding(self):
        ref = self.blocker()
        self.save(spec())
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        f = self.findings()["Promotion is planned while a blocker is open: %s" % ref]
        self.assertEqual((f["severity"], f["status"]), ("high", "verified"))
        m = self.scores()["blockers_without_a_fix_date"]
        self.assertEqual(m["value"], 1)
        self.assertFalse(ledger.metric_passes(m))
        self.assertEqual(self.coverage()["promotion-gate"], "covered")

    def test_fix_due_after_the_first_test_does_not_clear_the_gate(self):
        ref = self.blocker(severity="critical")
        s = spec()
        s["before_promotion"].append({"fix": "Ask before tracking", "ref": ref, "due": "2026-12-01"})
        self.save(s)
        run("record", self.path, "--dir", self.d)
        self.assertEqual(self.scores()["blockers_without_a_fix_date"]["value"], 1)

    def test_dated_fix_clears_the_gate_and_rerecord_removes_the_stale_finding(self):
        ref = self.blocker()
        self.save(spec())
        run("record", self.path, "--dir", self.d)
        self.assertEqual(len(self.findings()), 1)
        s = spec()
        s["before_promotion"].append({"fix": "Ask before tracking", "ref": ref, "due": "2026-11-30"})
        self.save(s)
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.findings(), {})
        m = self.scores()["blockers_without_a_fix_date"]
        self.assertEqual(m["value"], 0)
        self.assertTrue(ledger.metric_passes(m))
        with open(os.path.join(self.d, "business", "go-to-market.md"), encoding="utf-8") as fh:
            self.assertIn(ref, fh.read())

    def test_only_verified_high_or_critical_findings_gate(self):
        self.blocker(fid="unverified-one", verify=False)
        self.blocker(fid="medium-one", severity="medium")
        self.save(spec())
        run("record", self.path, "--dir", self.d)
        self.assertEqual(self.scores()["blockers_without_a_fix_date"]["value"], 0)
        self.assertEqual(self.findings(), {})

    def test_own_findings_do_not_gate_themselves_and_stale_ones_go(self):
        self.blocker(fid="medium-one", severity="medium")  # a ledger with nothing blocking
        s = spec()
        s["first_test"]["pass"] = "People like it"
        self.save(s)
        run("record", self.path, "--dir", self.d)
        run("record", self.path, "--dir", self.d)
        self.assertEqual(list(self.findings()), ["The first test has no pass mark"])
        self.assertEqual(self.scores()["blockers_without_a_fix_date"]["value"], 0)
        self.save(spec())
        run("record", self.path, "--dir", self.d)
        self.assertEqual(self.findings(), {})
        self.assertEqual(self.coverage()["first-test-defined"], "covered")

    def test_errors_exit_2_and_record_nothing(self):
        s = spec()
        del s["currency"]
        self.save(s)
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 2)
        self.assertIn("currency", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "business")))
        rc, _ = run("check", os.path.join(self.tp.base, "missing.json"), "--dir", self.d)
        self.assertEqual(rc, 2)
        self.save(spec(budget_per_month="1,250"))  # ambiguous number
        rc, _ = run("check", self.path, "--dir", self.d)
        self.assertEqual(rc, 2)

    def test_check_prints_and_records_nothing(self):
        self.save(spec(budget_per_month="2000"))
        rc, out = run("check", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        self.assertIn("# Go to market", out)
        self.assertIn("Channels cost more than the budget", out)
        self.assertEqual(os.listdir(self.d), [])


if __name__ == "__main__":
    unittest.main()
