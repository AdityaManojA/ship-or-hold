import os
import unittest

from _load import TempProof, load_tool

ledger = load_tool("proof-core", "ledger.py")


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_target_roundtrip_and_schema(self):
        ledger.write_target(self.d, {"name": "A", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        t = ledger.load_target(self.d)
        self.assertEqual(t["name"], "A")
        with self.assertRaises(ledger.LedgerError):
            ledger.write_target(self.d, {"name": "A", "kind": "weird", "authorised": True, "required_skills": []})
        with self.assertRaises(ledger.LedgerError):
            ledger.write_target(self.d, {"name": "A", "kind": "web", "authorised": True,
                                         "required_skills": ["websec"]})

    def test_new_finding_is_unverified(self):
        f = ledger.new_finding("proof-sec-web", "Thing", "high")
        self.assertEqual(f["status"], "unverified")
        self.assertEqual(f["id"], "thing")
        with self.assertRaises(ledger.LedgerError):
            ledger.new_finding("proof-sec-web", "Thing", "enormous")
        with self.assertRaises(ledger.LedgerError):
            ledger.new_finding("websec", "Thing", "high")

    def test_verify_requires_real_evidence(self):
        f = ledger.new_finding("proof-sec-web", "IDOR", "critical")
        ledger.write_finding(self.d, f)
        # no evidence at all: refused
        with self.assertRaises(ledger.LedgerError):
            ledger.verify(self.d, "proof-sec-web", f["id"], by="evidence-file")
        # evidence pointing at a missing file: refused
        with self.assertRaises(ledger.LedgerError):
            ledger.verify(self.d, "proof-sec-web", f["id"], by="evidence-file",
                          evidence=["evidence/proof-sec-web/nope.txt"])
        self.assertEqual(ledger.load_finding(self.d, "proof-sec-web", f["id"])["status"], "unverified")
        # a real, non-empty evidence file: verified. Start from a clean finding so
        # the earlier missing path is not still listed.
        ledger.reopen(self.d, "proof-sec-web", f["id"])
        clean = ledger.load_finding(self.d, "proof-sec-web", f["id"])
        clean["evidence"] = ["evidence/proof-sec-web/idor.txt"]
        ledger.write_finding(self.d, clean)
        self.tp.write("evidence/proof-sec-web/idor.txt", b"captured")
        ledger.verify(self.d, "proof-sec-web", f["id"], by="evidence-file")
        self.assertEqual(ledger.load_finding(self.d, "proof-sec-web", f["id"])["status"], "verified")

    def test_verify_manual_needs_note(self):
        f = ledger.new_finding("proof-ai", "Leak", "medium")
        ledger.write_finding(self.d, f)
        with self.assertRaises(ledger.LedgerError):
            ledger.verify(self.d, "proof-ai", f["id"], by="manual")
        ledger.verify(self.d, "proof-ai", f["id"], by="manual", note="checked by Sam")
        self.assertEqual(ledger.load_finding(self.d, "proof-ai", f["id"])["status"], "verified")

    def test_reject_and_reopen(self):
        f = ledger.new_finding("proof-ai", "Leak", "medium")
        ledger.write_finding(self.d, f)
        with self.assertRaises(ledger.LedgerError):
            ledger.reject(self.d, "proof-ai", f["id"], "")
        ledger.reject(self.d, "proof-ai", f["id"], "false alarm")
        self.assertEqual(ledger.load_finding(self.d, "proof-ai", f["id"])["status"], "false_positive")
        ledger.reopen(self.d, "proof-ai", f["id"])
        self.assertEqual(ledger.load_finding(self.d, "proof-ai", f["id"])["status"], "unverified")

    def test_coverage(self):
        ledger.init_coverage(self.d, "proof-web-e2e", [("a", "A"), ("b", "B")])
        with self.assertRaises(ledger.LedgerError):
            ledger.mark_coverage(self.d, "proof-web-e2e", "b", "not_applicable")  # needs a note
        ledger.mark_coverage(self.d, "proof-web-e2e", "b", "not_applicable", note="n/a here")
        cov = ledger.load_coverage(self.d, "proof-web-e2e")
        self.assertEqual({c["id"]: c["status"] for c in cov["checks"]}, {"a": "not_run", "b": "not_applicable"})
        # re-init keeps the existing status
        ledger.init_coverage(self.d, "proof-web-e2e", [("a", "A"), ("b", "B"), ("c", "C")])
        cov = ledger.load_coverage(self.d, "proof-web-e2e")
        self.assertEqual(next(c for c in cov["checks"] if c["id"] == "b")["status"], "not_applicable")

    def test_scores_and_metric_pass(self):
        ledger.write_scores(self.d, "proof-web-perf", [{"id": "lcp", "value": 2.0, "unit": "s", "budget": 2.5, "op": "<="}])
        s = ledger.load_all_scores(self.d)[0]
        self.assertTrue(ledger.metric_passes(s["metrics"][0]))
        self.assertFalse(ledger.metric_passes({"value": 3, "budget": 2.5, "op": "<="}))

    def test_evidence_must_live_under_proof(self):
        f = ledger.new_finding("proof-sec-web", "X", "low")
        f["evidence"] = ["../../etc/hosts"]
        ledger.write_finding(self.d, f)
        with self.assertRaises(ledger.LedgerError):
            ledger.verify(self.d, "proof-sec-web", f["id"], by="evidence-file")


if __name__ == "__main__":
    unittest.main()
