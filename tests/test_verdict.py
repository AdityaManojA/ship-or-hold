import os
import unittest

from _load import TempProof, load_tool

ledger = load_tool("proof-core", "ledger.py")
verdict = load_tool("proof-report", "verdict.py")


class Verdict(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def v(self, **kw):
        return verdict.compute(self.d, **kw)["verdict"]

    def base(self):
        ledger.write_target(self.d, {"name": "Demo", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        ledger.init_coverage(self.d, "proof-web-e2e", [("login", "Login")])
        self.tp.write("evidence/proof-web-e2e/login.png", b"png")
        ledger.mark_coverage(self.d, "proof-web-e2e", "login", "covered",
                             evidence=["evidence/proof-web-e2e/login.png"])

    def test_no_target_is_incomplete(self):
        self.assertEqual(self.v(), "Incomplete")

    def test_unauthorised_is_incomplete(self):
        ledger.write_target(self.d, {"name": "D", "kind": "web", "authorised": False,
                                     "required_skills": ["proof-web-e2e"]})
        self.assertEqual(self.v(), "Incomplete")

    def test_required_not_reported_is_incomplete(self):
        ledger.write_target(self.d, {"name": "D", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        self.assertEqual(self.v(), "Incomplete")

    def test_not_run_check_is_incomplete(self):
        ledger.write_target(self.d, {"name": "D", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        ledger.init_coverage(self.d, "proof-web-e2e", [("login", "Login")])
        self.assertEqual(self.v(), "Incomplete")

    def test_all_covered_no_findings_is_ship(self):
        self.base()
        self.assertEqual(self.v(), "Ship")

    def test_the_thesis_unverified_then_verified(self):
        """Flipping a finding's status is what changes the verdict, nothing else."""
        self.base()
        f = ledger.new_finding("proof-web-e2e", "Login accepts empty password", "high",
                               evidence=["evidence/proof-web-e2e/login.png"])
        ledger.write_finding(self.d, f)
        self.assertEqual(self.v(), "Ship")  # unverified does not count
        ledger.verify(self.d, "proof-web-e2e", f["id"], by="evidence-file")
        self.assertEqual(self.v(), "Not yet")  # verified blocks
        ledger.reject(self.d, "proof-web-e2e", f["id"], "field was browser-autofilled")
        self.assertEqual(self.v(), "Ship")  # ruled out does not count

    def test_fail_on_threshold(self):
        self.base()
        f = ledger.new_finding("proof-web-e2e", "Minor text overflow", "medium",
                               evidence=["evidence/proof-web-e2e/login.png"])
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-web-e2e", f["id"], by="evidence-file")
        # default fail_on is high; a verified medium finding is below it, so Ship
        self.assertEqual(self.v(), "Ship")
        # lower the bar to medium and the same finding now blocks
        self.assertEqual(self.v(fail_on="medium"), "Not yet")

    def test_optional_skill_never_blocks(self):
        self.base()
        # an optional skill reports a failing score; it is not required, but a run
        # score still counts as evidence once present. Here optional means not in
        # required_skills; its verified findings/scores DO count (they were run).
        ledger.write_scores(self.d, "proof-web-perf", [{"id": "lcp", "value": 9, "unit": "s", "budget": 2.5, "op": "<="}])
        self.assertEqual(self.v(), "Not yet")
        # remove it: back to Ship (its absence never counts against us)
        os.remove(ledger.scores_path(self.d, "proof-web-perf"))
        self.assertEqual(self.v(), "Ship")

    def test_broken_ledger_is_incomplete(self):
        self.base()
        p = ledger.finding_path(self.d, "proof-web-e2e", "x")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"id": "x", "skill": "proof-web-e2e", "severity": "nope", "title": "t", "status": "verified", "created": "2026-01-01T00:00:00+00:00"}')
        self.assertEqual(self.v(), "Incomplete")


if __name__ == "__main__":
    unittest.main()
