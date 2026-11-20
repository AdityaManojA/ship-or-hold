import contextlib
import copy
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

ops = load_tool("proof-ops", "ops.py")
ledger = load_tool("proof-core", "ledger.py")

SKILL = "proof-ops"

with open(os.path.join(SKILLS_DIR, SKILL, "ops.example.json"), encoding="utf-8") as _fh:
    BASE = json.load(_fh)


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ops.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


def spec(**changes):
    s = copy.deepcopy(BASE)
    s.update(changes)
    return s


def dep(**changes):
    d = {"name": "Map data feed", "kind": "data", "used_for": "Delivery routes", "permission": "written",
         "if_lost": "Routes are planned by hand", "fallback": "Printed floor map", "likelihood": 2, "impact": 3}
    d.update(changes)
    return d


def found(a):
    return {f["title"]: f["severity"] for f in a["findings"]}


class Analyse(unittest.TestCase):
    def test_example_is_clean_and_adds_up(self):
        a = ops.analyse(spec())
        self.assertEqual(a["findings"], [])
        self.assertTrue(all(status == "covered" for status, _ in a["coverage"].values()))
        self.assertAlmostEqual(a["cost_total"], 46200.0)
        self.assertAlmostEqual(a["estimate_share"], 5000.0 / 46200.0)
        self.assertAlmostEqual(a["team_hours"], 101.0)
        # daily 180 + 75 + 40 minutes seven times a week, weekly 90, monthly 120 / 4.3; on_event left out
        expected = (295 * 7 + 90 + 120 / 4.3) / 60.0
        self.assertAlmostEqual(a["routine_hours"], expected, places=6)
        self.assertAlmostEqual(a["hours_fit"], 101.0 - expected, places=6)

    def test_currency_is_required(self):
        s = spec()
        del s["currency"]
        with self.assertRaises(ops.OpsError):
            ops.analyse(s)
        with self.assertRaises(ops.OpsError):
            ops.analyse(spec(currency=""))

    def test_missing_inputs_are_errors(self):
        s = spec()
        del s["monthly_costs"][0]["cost"]
        with self.assertRaises(ops.OpsError):
            ops.analyse(s)
        s = spec()
        del s["team"][0]["hours_per_week"]
        with self.assertRaises(ops.OpsError):
            ops.analyse(s)
        s = spec()
        del s["routines"][0]["time_minutes"]
        with self.assertRaises(ops.OpsError):
            ops.analyse(s)
        for bad in (dep(impact=6), dep(likelihood=0), dep(impact=2.5), dep(kind="weather"), dep(permission="probably")):
            with self.assertRaises(ops.OpsError):
                ops.analyse(spec(dependencies=[bad]))
        missing = dep()
        del missing["likelihood"]
        with self.assertRaises(ops.OpsError):
            ops.analyse(spec(dependencies=[missing]))

    def test_dependencies_are_ranked_by_risk(self):
        a = ops.analyse(spec(dependencies=[dep(name="A", likelihood=1, impact=2), dep(name="B", likelihood=5, impact=4),
                                           dep(name="C", likelihood=3, impact=3)]))
        self.assertEqual([(x["name"], x["risk"]) for x in a["dependencies"]], [("B", 20), ("C", 9), ("A", 2)])

    def test_platform_or_data_without_permission_is_high(self):
        for kind in ("platform", "data"):
            for permission in ("unknown", "none"):
                a = ops.analyse(spec(dependencies=[dep(kind=kind, permission=permission, impact=4)]))
                self.assertEqual(found(a), {"The business depends on Map data feed without permission": "high"})
        # low impact, another kind, or a held permission: listed, but no finding
        self.assertEqual(found(ops.analyse(spec(dependencies=[dep(permission="none", impact=3)]))), {})
        self.assertEqual(found(ops.analyse(spec(dependencies=[dep(kind="supplier", permission="none", impact=5)]))), {})
        self.assertEqual(found(ops.analyse(spec(dependencies=[dep(permission="public_terms", impact=5)]))), {})
        a = ops.analyse(spec(dependencies=[dep(permission="none", impact=3)]))
        self.assertEqual(len(a["no_permission"]), 1)

    def test_single_point_of_failure_severity_follows_the_risk_score(self):
        a = ops.analyse(spec(dependencies=[dep(name="Only cook", kind="person", fallback="", likelihood=4, impact=4)]))
        self.assertEqual(found(a), {"Only cook is a single point of failure": "high"})
        a = ops.analyse(spec(dependencies=[dep(name="Only cook", kind="person", fallback="  ", likelihood=3, impact=5)]))
        self.assertEqual(found(a), {"Only cook is a single point of failure": "medium"})
        self.assertEqual(len(a["spofs"]), 1)
        a = ops.analyse(spec(dependencies=[dep(fallback="", impact=3, likelihood=5)]))
        self.assertEqual(a["spofs"], [])

    def test_what_could_stop_this_is_built_from_the_high_findings(self):
        a = ops.analyse(spec(dependencies=[dep(permission="unknown", fallback="", likelihood=4, impact=5)]))
        self.assertEqual(sorted(found(a).values()), ["high", "high"])
        text = ops.render(a)
        para = text.split("## What could stop this")[1].split("## Support")[0]
        self.assertIn("Map data feed", para)
        self.assertIn("Permission is 'unknown'", para)
        self.assertIn("no fallback", para)
        self.assertIn("Nothing in the inputs rises to a high risk", ops.render(ops.analyse(spec())))

    def test_routines_need_more_hours_than_the_team_has(self):
        a = ops.analyse(spec(team=[{"who": "Owner", "hours_per_week": 20, "does": "Everything"}]))
        f = found(a)
        self.assertEqual(f["The routines need more hours than the team has"], "medium")
        self.assertEqual(f["One person owns every routine"], "low")
        self.assertLess(a["hours_fit"], 0)

    def test_most_costs_are_estimates(self):
        s = spec()
        for c in s["monthly_costs"]:
            c["source"] = "estimate"
        s["monthly_costs"][2]["source"] = "invoice"  # 7,000 of 46,200 is not an estimate
        self.assertEqual(found(ops.analyse(s)), {"Most monthly costs are estimates": "medium"})
        half = spec(monthly_costs=[{"label": "a", "cost": 100, "source": "estimate"},
                                   {"label": "b", "cost": 100, "source": "quote"}])
        self.assertEqual(found(ops.analyse(half)), {})  # exactly half is not "most"

    def test_owners_legal_and_one_person(self):
        s = spec()
        s["routines"][1]["owner"] = ""
        s["legal"][0]["status"] = "unknown"
        a = ops.analyse(s)
        self.assertEqual(found(a), {"Routine 'Delivery run' has no owner": "low",
                                    "Nobody knows where 'Business registration' stands": "low"})
        s = spec()
        for r in s["routines"]:
            r["owner"] = "Owner"
        self.assertEqual(found(ops.analyse(s)), {"One person owns every routine": "low"})

    def test_coverage_is_left_not_run_for_missing_parts(self):
        s = spec(kpis=BASE["kpis"][:2], support={"channel": "Phone"})
        s["routines"] = s["routines"][:2]
        for key in ("team", "monthly_costs", "dependencies", "legal"):
            del s[key]
        a = ops.analyse(s)
        self.assertTrue(all(status == "not_run" for status, _ in a["coverage"].values()), a["coverage"])
        self.assertIsNone(a["hours_fit"])


class Recorded(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.path = os.path.join(self.tp.base, "ops.json")

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def save(self, s):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(s, fh)

    def scores(self):
        got = [s for s in ledger.load_all_scores(self.d) if s["skill"] == SKILL]
        return {m["id"]: m for m in got[0]["metrics"]} if got else {}

    def test_template_then_record_writes_the_section_and_the_ledger(self):
        rc, out = run("template", "--out", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.d, "business", "ops.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Operations\n"))
        for part in ("## Team", "## Routines", "## Monthly costs", "## Dependencies", "## What could stop this",
                     "## Support", "## Legal list", "## KPIs", "Rs 46,200", "not legal advice", "/proof-release"):
            self.assertIn(part, text)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", SKILL, "ops.json")))
        cov = ledger.load_coverage(self.d, SKILL)
        self.assertEqual(len(cov["checks"]), 7)
        self.assertTrue(all(c["status"] == "covered" for c in cov["checks"]))
        m = self.scores()
        self.assertEqual(m["routine_hours_fit"]["unit"], "hours")
        self.assertTrue(ledger.metric_passes(m["routine_hours_fit"]))
        self.assertEqual(m["single_points_of_failure"]["value"], 0)
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])

    def test_findings_are_verified_and_a_rerecord_replaces_them(self):
        bad = spec(dependencies=[dep(permission="none", fallback="", likelihood=4, impact=5)])
        self.save(bad)
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, SKILL)
        self.assertEqual(len(fs), 2)
        self.assertTrue(all(f["status"] == "verified" and f["severity"] == "high" for f in fs))
        self.assertTrue(all(f["evidence"] == ["evidence/proof-ops/ops.json"] for f in fs))
        m = self.scores()["single_points_of_failure"]
        self.assertEqual(m["value"], 1)
        self.assertFalse(ledger.metric_passes(m))
        self.save(spec())
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])
        self.assertEqual(self.scores()["single_points_of_failure"]["value"], 0)

    def test_a_hand_written_finding_survives_a_rerecord(self):
        self.save(spec())
        run("record", self.path, "--dir", self.d)
        ledger.write_finding(self.d, ledger.new_finding(SKILL, "The owner has no cover for holidays", "low", id="holiday-cover"))
        run("record", self.path, "--dir", self.d)
        self.assertEqual([f["id"] for f in ledger.load_findings(self.d, SKILL)], ["holiday-cover"])

    def test_errors_exit_2_and_record_nothing(self):
        s = spec()
        del s["currency"]
        self.save(s)
        rc, out = run("record", self.path, "--dir", self.d)
        self.assertEqual(rc, 2)
        self.assertIn("currency", out)
        self.assertEqual(os.listdir(self.d), [])
        rc, _ = run("check", os.path.join(self.tp.base, "missing.json"))
        self.assertEqual(rc, 2)
        s = spec()
        s["monthly_costs"][0]["cost"] = "twelve"
        self.save(s)
        rc, _ = run("check", self.path)
        self.assertEqual(rc, 2)

    def test_check_prints_and_records_nothing(self):
        self.save(spec(dependencies=[dep(fallback="", impact=5, likelihood=1)]))
        rc, out = run("check", self.path, "--dir", self.d)
        self.assertEqual(rc, 0, out)
        self.assertIn("# Operations", out)
        self.assertIn("single point of failure", out)
        self.assertEqual(os.listdir(self.d), [])


if __name__ == "__main__":
    unittest.main()
