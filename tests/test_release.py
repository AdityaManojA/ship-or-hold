import contextlib
import datetime
import io
import json
import unittest

from _load import TempProof, load_tool

release = load_tool("proof-release", "release.py")
ledger = load_tool("proof-core", "ledger.py")

SKILL = "proof-release"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = release.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


class Release(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def items(self):
        rc, out = run("list", "--json")
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def critical_and_not(self):
        its = self.items()
        crit = next(i for i in its if i["critical"])
        plain = next(i for i in its if not i["critical"])
        return crit, plain

    def test_list_parses_checklist(self):
        its = self.items()
        self.assertGreaterEqual(len(its), 18)
        ids = [i["id"] for i in its]
        self.assertEqual(len(ids), len(set(ids)))
        for key in ("id", "title", "group", "critical"):
            self.assertTrue(all(key in i for i in its), key)
        self.assertTrue(all(isinstance(i["critical"], bool) for i in its))
        self.assertTrue(any(i["critical"] for i in its))
        self.assertTrue(any(not i["critical"] for i in its))
        self.assertGreaterEqual(len({i["group"] for i in its}), 5)

    def test_start_creates_coverage(self):
        rc, out = run("--dir", self.d, "start")
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, SKILL)
        self.assertEqual(len(cov["checks"]), len(self.items()))
        self.assertTrue(all(c["status"] == "not_run" for c in cov["checks"]))

    def test_done_needs_existing_evidence(self):
        run("--dir", self.d, "start")
        crit, _ = self.critical_and_not()
        rc, out = run("--dir", self.d, "done", crit["id"], "--evidence", "evidence/proof-release/missing.png")
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "done", crit["id"])
        self.assertNotEqual(rc, 0)
        status = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(status[crit["id"]], "not_run")
        ev = self.tp.write("evidence/proof-release/restore.log", b"restored 1200 rows")
        rc, out = run("--dir", self.d, "done", crit["id"], "--evidence", ev, "--note", "restored to scratch")
        self.assertEqual(rc, 0, out)
        status = {c["id"]: c for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(status[crit["id"]]["status"], "covered")
        self.assertIn(ev, status[crit["id"]]["evidence"])

    def test_done_refuses_empty_evidence_and_unknown_item(self):
        run("--dir", self.d, "start")
        ev = self.tp.write("evidence/proof-release/empty.txt", b"")
        crit, _ = self.critical_and_not()
        rc, _ = run("--dir", self.d, "done", crit["id"], "--evidence", ev)
        self.assertNotEqual(rc, 0)
        rc, _ = run("--dir", self.d, "na", "not-an-item", "--note", "x")
        self.assertNotEqual(rc, 0)

    def test_na_needs_note(self):
        run("--dir", self.d, "start")
        _, plain = self.critical_and_not()
        rc, _ = run("--dir", self.d, "na", plain["id"])
        self.assertNotEqual(rc, 0)
        rc, _ = run("--dir", self.d, "na", plain["id"], "--note", "  ")
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "na", plain["id"], "--note", "internal tool")
        self.assertEqual(rc, 0, out)
        status = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(status[plain["id"]], "not_applicable")

    def test_gap_default_severity_and_category(self):
        run("--dir", self.d, "start")
        crit, plain = self.critical_and_not()
        rc, out = run("--dir", self.d, "gap", crit["id"], "--title", "Not ready", "--id", "gap-crit")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "gap", plain["id"], "--title", "Not ready either", "--id", "gap-plain")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "gap", plain["id"], "--title", "Override", "--id", "gap-low",
                      "--severity", "low")
        self.assertEqual(rc, 0, out)
        f = ledger.load_finding(self.d, SKILL, "gap-crit")
        self.assertEqual(f["severity"], "high")
        self.assertEqual(f["status"], "unverified")
        self.assertEqual(f["category"], "release:%s" % crit["group"])
        self.assertEqual(ledger.load_finding(self.d, SKILL, "gap-plain")["severity"], "medium")
        self.assertEqual(ledger.load_finding(self.d, SKILL, "gap-low")["severity"], "low")
        # the same id twice is refused rather than overwritten
        rc, _ = run("--dir", self.d, "gap", plain["id"], "--title", "Again", "--id", "gap-plain")
        self.assertNotEqual(rc, 0)

    def test_gap_verify_gate(self):
        run("--dir", self.d, "start")
        crit, _ = self.critical_and_not()
        run("--dir", self.d, "gap", crit["id"], "--title", "Never restored", "--id", "no-restore")
        rc, _ = run("--dir", self.d, "verify", "no-restore", "--by", "manual")
        self.assertNotEqual(rc, 0)
        rc, _ = run("--dir", self.d, "verify", "no-restore", "--by", "evidence-file")
        self.assertNotEqual(rc, 0)
        self.assertEqual(ledger.load_finding(self.d, SKILL, "no-restore")["status"], "unverified")
        rc, out = run("--dir", self.d, "verify", "no-restore", "--by", "manual", "--note", "confirmed by Sam")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_finding(self.d, SKILL, "no-restore")["status"], "verified")

    def test_status_orders_critical_not_done_first(self):
        run("--dir", self.d, "start")
        its = self.items()
        crit = [i for i in its if i["critical"]]
        plain = [i for i in its if not i["critical"]]
        ev = self.tp.write("evidence/proof-release/proof.txt", b"ok")
        # one critical item done; it must drop out of the first band
        run("--dir", self.d, "done", crit[0]["id"], "--evidence", ev)
        rc, out = run("--dir", self.d, "status", "--json")
        self.assertEqual(rc, 0, out)
        res = json.loads(out)
        rows = res["items"]
        n_open_crit = len(crit) - 1
        first = rows[:n_open_crit]
        self.assertTrue(all(r["critical"] and r["status"] == "not_run" for r in first))
        self.assertEqual(res["critical_not_done"], [r["id"] for r in first])
        self.assertNotIn(crit[0]["id"], res["critical_not_done"])
        nxt = rows[n_open_crit:n_open_crit + len(plain)]
        self.assertTrue(all(not r["critical"] and r["status"] == "not_run" for r in nxt))
        self.assertEqual(rows[-1]["id"], crit[0]["id"])
        rc, text = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("CRITICAL", text)

    def test_days_remaining_from_date(self):
        rc, out = run("--dir", self.d, "start", "--date", "2026-11-12")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_coverage(self.d, SKILL)["launch_date"], "2026-11-12")
        rc, out = run("--dir", self.d, "status", "--json", "--today", "2026-11-02")
        self.assertEqual(json.loads(out)["days_remaining"], 10)
        rc, out = run("--dir", self.d, "status", "--json", "--today", "2026-11-14")
        self.assertEqual(json.loads(out)["days_remaining"], -2)
        # without --today it counts from the real date
        expected = (datetime.date(2026, 11, 12) - datetime.date.today()).days
        rc, out = run("--dir", self.d, "status", "--json")
        self.assertEqual(json.loads(out)["days_remaining"], expected)
        # the date survives marking items and re-running start
        ev = self.tp.write("evidence/proof-release/x.txt", b"x")
        run("--dir", self.d, "done", self.items()[0]["id"], "--evidence", ev)
        run("--dir", self.d, "start")
        self.assertEqual(ledger.load_coverage(self.d, SKILL)["launch_date"], "2026-11-12")
        rc, text = run("--dir", self.d, "status", "--today", "2026-11-11")
        self.assertIn("1 day to launch", text)

    def test_bad_date_refused(self):
        rc, _ = run("--dir", self.d, "start", "--date", "12/11/2026")
        self.assertNotEqual(rc, 0)

    def test_commands_before_start(self):
        crit, _ = self.critical_and_not()
        rc, out = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("start", out)
        rc, _ = run("--dir", self.d, "na", crit["id"], "--note", "x")
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
