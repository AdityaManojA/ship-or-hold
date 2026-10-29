import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

explore = load_tool("proof-explore", "explore.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = explore.main(list(argv))
    return rc, out.getvalue()


class Explore(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_scaffold_writes_valid_profile(self):
        out = os.path.join(self.tp.base, "explore")
        rc, _ = run("--dir", self.d, "scaffold", "--target", "a web checkout", "--out", out)
        self.assertEqual(rc, 0)
        with open(os.path.join(out, "profile.json"), encoding="utf-8") as fh:
            prof = json.load(fh)
        self.assertEqual(prof["answer"]["outcome"], "found_problem")
        # shares in each dimension sum to 1
        for dim, opts in prof["dimensions"].items():
            self.assertAlmostEqual(sum(o["share"] for o in opts), 1.0, places=6)
        self.assertTrue(os.path.exists(os.path.join(out, "brief.md")))

    def test_scaffold_profile_works_with_swarm(self):
        swarm = load_tool("proof-swarm", "swarm.py")
        out = os.path.join(self.tp.base, "explore")
        run("--dir", self.d, "scaffold", "--target", "a web checkout", "--name", "explore-checkout", "--out", out)
        # swarm should accept the scaffolded profile + brief
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            rc = swarm.main(["--dir", self.d, "init", "--profile", os.path.join(out, "profile.json"),
                             "--brief", os.path.join(out, "brief.md"), "--quick", "--seed", "1"])
        self.assertEqual(rc, 0)

    def test_harvest_groups_and_writes_candidates(self):
        run_dir = os.path.join(self.d, "swarm", "explore-checkout")
        ans = os.path.join(run_dir, "answers")
        os.makedirs(ans)
        def write_answer(cid, found, what, sev="medium", repro="steps"):
            with open(os.path.join(ans, cid + ".json"), "w", encoding="utf-8") as fh:
                json.dump({"found_problem": found, "severity": sev, "what_happened": what, "repro_steps": repro}, fh)
        write_answer("a001", True, "Pay button stays disabled", "high", "fill form, press pay")
        write_answer("a002", True, "Pay button stays disabled", "high", "fill form, press pay twice")
        write_answer("a003", True, "Address field rejects apostrophes", "medium", "type O'Hara")
        write_answer("a004", False, "", "none", "")
        results = os.path.join(run_dir, "results.json")
        with open(results, "w", encoding="utf-8") as fh:
            json.dump({"answered": 4, "outcome_yes": 3}, fh)
        rc, out = run("--dir", self.d, "harvest", "--results", results)
        self.assertEqual(rc, 0, out)
        findings = ledger.load_findings(self.d, "proof-explore")
        # two distinct problems (the two "Pay button" ones collapse into one)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f["status"] == "unverified" for f in findings))
        titles = " ".join(f["title"] for f in findings)
        self.assertIn("Pay button", titles)


if __name__ == "__main__":
    unittest.main()
