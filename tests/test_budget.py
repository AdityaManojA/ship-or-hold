"""proof-web-perf/budget.py: budgets, hand-measured records, Lighthouse results, skips."""

import contextlib
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

budget = load_tool("proof-web-perf", "budget.py")
ledger = load_tool("proof-core", "ledger.py")

SKILL = "proof-web-perf"
EXPECTED_IDS = {"lcp", "cls", "tbt", "fcp", "ttfb", "total_js", "total_transfer", "requests"}

LCP = {"id": "lcp", "label": "Largest Contentful Paint", "unit": "s", "budget": 2.5, "op": "<="}
CLS = {"id": "cls", "label": "Cumulative Layout Shift", "unit": "score", "budget": 0.1, "op": "<="}
TBT = {"id": "tbt", "label": "Total Blocking Time", "unit": "ms", "budget": 200, "op": "<="}
REQUESTS = {"id": "requests", "label": "Network requests", "unit": "count", "budget": 80, "op": "<="}
TOTAL_JS = {"id": "total_js", "label": "JavaScript transferred", "unit": "kb", "budget": 300, "op": "<="}
TRANSFER = {"id": "total_transfer", "label": "Total bytes transferred", "unit": "kb", "budget": 1600, "op": "<="}

# A hand-made Lighthouse result with a few audits; tbt is deliberately absent.
LHR = {
    "lighthouseVersion": "12.6.0",
    "requestedUrl": "http://localhost:3000/",
    "finalDisplayedUrl": "http://localhost:3000/",
    "fetchTime": "2026-10-30T09:00:00.000Z",
    "configSettings": {"formFactor": "mobile"},
    "audits": {
        "largest-contentful-paint": {"numericValue": 2450.0, "numericUnit": "millisecond", "score": 0.8},
        "cumulative-layout-shift": {"numericValue": 0.05, "numericUnit": "unitless"},
        "total-byte-weight": {"numericValue": 1572864, "numericUnit": "byte"},
        "network-requests": {"details": {"type": "table", "items": [
            {"url": "http://localhost:3000/", "resourceType": "Document", "transferSize": 2048},
            {"url": "http://localhost:3000/app.js", "resourceType": "Script", "transferSize": 20480},
            {"url": "http://localhost:3000/vendor.js", "resourceType": "Script", "transferSize": 10240},
        ]}},
    },
}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = budget.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


class Budget(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, obj=None, text=None):
        path = os.path.join(self.tp.base, name)
        with open(path, "w", encoding="utf-8") as fh:
            if text is None:
                json.dump(obj, fh)
            else:
                fh.write(text)
        return path

    def scores(self):
        for s in ledger.load_all_scores(self.d):
            if s["skill"] == SKILL:
                return {m["id"]: m for m in s["metrics"]}
        return {}

    def coverage(self):
        cov = ledger.load_coverage(self.d, SKILL)
        return {c["id"]: c for c in cov["checks"]} if cov else {}

    def record(self, metrics, measured, *extra):
        b = self.file("budget.json", {"metrics": metrics})
        m = self.file("measured.json", measured)
        return run("--dir", self.d, "record", "--budget", b, "--measured", m, *extra)

    # template ---------------------------------------------------------------

    def test_template_writes_expected_ids(self):
        out = os.path.join(self.tp.base, "budget.json")
        rc, text = run("--dir", self.d, "template", "--out", out)
        self.assertEqual(rc, 0, text)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual({m["id"] for m in data["metrics"]}, EXPECTED_IDS)
        for m in data["metrics"]:
            for key in ("label", "unit", "budget", "op"):
                self.assertIn(key, m, m["id"])
        self.assertEqual(len(budget.load_budget(out)), 8)  # the template is itself a valid budget

    def test_template_does_not_clobber_an_edited_budget(self):
        out = self.file("budget.json", {"metrics": [LCP]})
        rc, text = run("--dir", self.d, "template", "--out", out)
        self.assertNotEqual(rc, 0)
        self.assertIn("--force", text)
        self.assertEqual(len(budget.load_budget(out)), 1)
        rc, _ = run("--dir", self.d, "template", "--out", out, "--force")
        self.assertEqual(rc, 0)
        self.assertEqual(len(budget.load_budget(out)), 8)

    def test_example_budget_matches_the_template(self):
        example = os.path.join(SKILLS_DIR, SKILL, "budget.example.json")
        self.assertEqual({m["id"] for m in budget.load_budget(example)}, EXPECTED_IDS)

    # record -----------------------------------------------------------------

    def test_record_scores_pass_and_fail(self):
        rc, text = self.record([LCP, CLS, REQUESTS], {"lcp": 2.1, "cls": 0.15, "requests": 80},
                               "--source", "devtools, run 2")
        self.assertEqual(rc, 0, text)
        s = self.scores()
        self.assertEqual(set(s), {"lcp", "cls", "requests"})
        self.assertTrue(ledger.metric_passes(s["lcp"]))
        self.assertFalse(ledger.metric_passes(s["cls"]))
        self.assertTrue(ledger.metric_passes(s["requests"]))  # <= is inclusive: on the line passes
        self.assertEqual((s["lcp"]["unit"], s["lcp"]["budget"], s["lcp"]["op"]), ("s", 2.5, "<="))
        self.assertEqual(s["cls"]["source"], "devtools, run 2")
        self.assertEqual({k: c["status"] for k, c in self.coverage().items()},
                         {"lcp": "covered", "cls": "covered", "requests": "covered"})
        self.assertIn(ledger.OK, text)
        self.assertIn(ledger.BAD, text)
        self.assertIn("2 of 3 within budget, 1 over", text)

    def test_record_missing_value_is_an_error(self):
        rc, text = self.record([LCP, CLS, REQUESTS], {"lcp": 2.1, "requests": 12})
        self.assertNotEqual(rc, 0)
        self.assertIn("cls", text)
        self.assertFalse(os.path.exists(ledger.scores_path(self.d, SKILL)))
        self.assertIsNone(ledger.load_coverage(self.d, SKILL))

    def test_record_unknown_measured_id_is_an_error(self):
        rc, text = self.record([LCP], {"lcp": 2.1, "speed_index": 3.4})
        self.assertNotEqual(rc, 0)
        self.assertIn("speed_index", text)
        self.assertFalse(os.path.exists(ledger.scores_path(self.d, SKILL)))

    def test_record_parses_locale_strings(self):
        rc, text = self.record([LCP, CLS, REQUESTS], {"lcp": "2,5", "cls": "0,05", "requests": "12"})
        self.assertEqual(rc, 0, text)
        s = self.scores()
        self.assertEqual(s["lcp"]["value"], 2.5)
        self.assertEqual(s["cls"]["value"], 0.05)
        self.assertEqual(s["requests"]["value"], 12)
        rc, text = self.record([REQUESTS], {"requests": "1,250"})  # thousands or decimal? refuse
        self.assertNotEqual(rc, 0)
        self.assertIn("ambiguous", text)
        rc, text = self.record([LCP], {"lcp": ""})  # empty is not zero
        self.assertNotEqual(rc, 0)

    def test_record_rejects_a_bad_budget(self):
        rc, text = self.record([dict(LCP, op="~")], {"lcp": 1})
        self.assertNotEqual(rc, 0)
        self.assertIn("op", text)
        rc, text = self.record([LCP, LCP], {"lcp": 1})
        self.assertNotEqual(rc, 0)
        self.assertIn("duplicate", text)
        rc, text = self.record([{"id": "lcp", "unit": "s", "op": "<="}], {"lcp": 1})  # no budget number
        self.assertNotEqual(rc, 0)
        self.assertIn("budget", text)
        rc, text = self.record([dict(LCP, budget="2,5")], {"lcp": 2.6})  # budgets parse locale-safe too
        self.assertEqual(rc, 0, text)
        self.assertFalse(ledger.metric_passes(self.scores()["lcp"]))

    def test_record_evidence_must_exist_under_proof(self):
        rc, text = self.record([LCP], {"lcp": 1}, "--evidence", "evidence/proof-web-perf/missing.txt")
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(ledger.scores_path(self.d, SKILL)))
        ev = self.tp.write("evidence/proof-web-perf/devtools.txt", "ttfb 120ms")
        rc, text = self.record([LCP], {"lcp": 1}, "--evidence", ev)
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.scores()["lcp"]["evidence"], [ev])
        self.assertEqual(self.coverage()["lcp"]["evidence"], [ev])

    # skip -------------------------------------------------------------------

    def test_skip_lets_record_leave_a_metric_out_with_a_reason(self):
        b = self.file("budget.json", {"metrics": [LCP, TBT]})
        rc, text = run("--dir", self.d, "skip", "tbt", "--budget", b)  # no note
        self.assertNotEqual(rc, 0)
        rc, text = run("--dir", self.d, "skip", "nope", "--note", "x", "--budget", b)  # not in the budget
        self.assertNotEqual(rc, 0)
        self.assertIn("nope", text)
        rc, text = run("--dir", self.d, "skip", "tbt", "--note", "no long-task timing in this browser", "--budget", b)
        self.assertEqual(rc, 0, text)
        rc, text = self.record([LCP, TBT], {"lcp": 2.0})
        self.assertEqual(rc, 0, text)
        self.assertEqual(set(self.scores()), {"lcp"})
        cov = self.coverage()
        self.assertEqual(cov["tbt"]["status"], "not_applicable")
        self.assertIn("long-task", cov["tbt"]["note"])
        self.assertIn("not applicable", text)

    def test_skip_removes_a_recorded_score(self):
        self.record([LCP, CLS], {"lcp": 2.0, "cls": 0.2})
        rc, text = run("--dir", self.d, "skip", "cls", "--note", "layout is static on this page")
        self.assertEqual(rc, 0, text)
        self.assertEqual(set(self.scores()), {"lcp"})
        self.assertEqual(self.coverage()["cls"]["status"], "not_applicable")

    # from-lighthouse --------------------------------------------------------

    def test_from_lighthouse_reads_a_small_result(self):
        b = self.file("budget.json", {"metrics": [LCP, CLS, TBT, TOTAL_JS, TRANSFER, REQUESTS]})
        j = self.file("lhr.json", LHR)
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", j, "--budget", b)
        self.assertEqual(rc, 0, text)
        s = self.scores()
        self.assertAlmostEqual(s["lcp"]["value"], 2.45)  # ms -> s
        self.assertAlmostEqual(s["cls"]["value"], 0.05)
        self.assertEqual(s["total_transfer"]["value"], 1536)  # bytes -> kb
        self.assertEqual(s["total_js"]["value"], 30)  # the two Script rows
        self.assertEqual(s["requests"]["value"], 3)
        self.assertNotIn("tbt", s)  # no audit, no value
        self.assertIn("total-blocking-time", text)
        cov = self.coverage()
        self.assertEqual(cov["tbt"]["status"], "not_run")
        self.assertEqual(cov["lcp"]["status"], "covered")
        ev = s["lcp"]["evidence"][0]
        self.assertTrue(ev.startswith("evidence/proof-web-perf/"), ev)
        self.assertTrue(os.path.isfile(os.path.join(self.d, ev)))
        self.assertIn("12.6.0", s["lcp"]["source"])
        self.assertIn("http://localhost:3000/", s["lcp"]["source"])

    def test_from_lighthouse_reads_a_utf16_result(self):
        b = self.file("budget.json", {"metrics": [LCP]})
        j = os.path.join(self.tp.base, "lhr16.json")
        with open(j, "w", encoding="utf-16") as fh:  # what a PowerShell redirect produces
            json.dump(LHR, fh)
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", j, "--budget", b)
        self.assertEqual(rc, 0, text)
        self.assertAlmostEqual(self.scores()["lcp"]["value"], 2.45)

    def test_from_lighthouse_merges_hand_measured_values(self):
        b = self.file("budget.json", {"metrics": [LCP, TBT]})
        j = self.file("lhr.json", LHR)
        extra = self.file("extra.json", {"tbt": 150})
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", j, "--budget", b, "--measured", extra)
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.scores()["tbt"]["value"], 150)
        self.assertEqual(self.coverage()["tbt"]["status"], "covered")
        clash = self.file("clash.json", {"lcp": 1})
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", j, "--budget", b, "--measured", clash)
        self.assertNotEqual(rc, 0)
        self.assertIn("lcp", text)

    def test_from_lighthouse_never_invents_a_value(self):
        lhr = {"lhr": {"lighthouseVersion": "11.0.0", "audits": {
            "largest-contentful-paint": {"scoreDisplayMode": "error", "errorMessage": "trace failed"},
            "cumulative-layout-shift": {"numericValue": 0.2, "numericUnit": "unitless"},
            "first-contentful-paint": {"numericValue": 1.2, "numericUnit": "second"},
        }}}
        fcp = {"id": "fcp", "unit": "s", "budget": 1.8, "op": "<="}
        b = self.file("budget.json", {"metrics": [LCP, CLS, fcp]})
        j = self.file("lhr.json", lhr)
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", j, "--budget", b)
        self.assertEqual(rc, 0, text)
        self.assertEqual(set(self.scores()), {"cls"})
        cov = self.coverage()
        self.assertEqual(cov["lcp"]["status"], "not_run")
        self.assertIn("trace failed", cov["lcp"]["note"])
        self.assertEqual(cov["fcp"]["status"], "not_run")
        self.assertIn("second", cov["fcp"]["note"])  # an unexpected unit is a gap, not a conversion
        # a result carrying none of the budget's metrics records nothing at all
        only = self.file("only.json", {"metrics": [LCP]})
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", j, "--budget", only)
        self.assertNotEqual(rc, 0)
        self.assertIn("nothing recorded", text)

    def test_from_lighthouse_rejects_a_file_that_is_not_a_result(self):
        b = self.file("budget.json", {"metrics": [LCP]})
        other = self.file("other.json", {"hello": 1})
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", other, "--budget", b)
        self.assertEqual(rc, 2)
        self.assertIn("audits", text)
        notes = self.file("notes.txt", text="not json at all")
        rc, _ = run("--dir", self.d, "from-lighthouse", "--json", notes, "--budget", b)
        self.assertEqual(rc, 2)
        rc, _ = run("--dir", self.d, "from-lighthouse", "--json", os.path.join(self.tp.base, "missing.json"), "--budget", b)
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(ledger.scores_path(self.d, SKILL)))

    # status and re-runs -----------------------------------------------------

    def test_status_and_rerun_replace_old_state(self):
        rc, text = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("no scores recorded", text)
        self.record([LCP, CLS, REQUESTS], {"lcp": 2.1, "cls": 0.15, "requests": 10})
        rc, text = run("--dir", self.d, "status")
        self.assertEqual(rc, 0, text)
        self.assertIn("2 of 3 within budget, 1 over", text)
        for mid in ("lcp", "cls", "requests"):
            self.assertIn(mid, text)
        # a new run against a smaller budget leaves nothing of the old one behind
        b = self.file("budget.json", {"metrics": [LCP]})
        j = self.file("lhr.json", LHR)
        rc, text = run("--dir", self.d, "from-lighthouse", "--json", j, "--budget", b)
        self.assertEqual(rc, 0, text)
        self.assertEqual(set(self.scores()), {"lcp"})
        self.assertEqual(set(self.coverage()), {"lcp"})


if __name__ == "__main__":
    unittest.main()
