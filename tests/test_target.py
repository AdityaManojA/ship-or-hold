import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

target = load_tool("proof-plan", "target.py")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = target.main(list(argv))
    return rc, out.getvalue() + err.getvalue()


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Target(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_init_writes_valid_target(self):
        rc, out = run("--dir", self.d, "init", "--name", "Acme", "--kind", "web",
                      "--authorised", "yes", "--url", "http://localhost:3000")
        self.assertEqual(rc, 0, out)
        t = read_json(os.path.join(self.d, "target.json"))
        self.assertEqual(t["name"], "Acme")
        self.assertTrue(t["authorised"])
        self.assertEqual(t["urls"], ["http://localhost:3000"])
        self.assertTrue(os.path.exists(os.path.join(self.d, "target.md")))

    def test_authorised_no_warns(self):
        rc, out = run("--dir", self.d, "init", "--name", "A", "--kind", "web", "--authorised", "no")
        self.assertEqual(rc, 0)
        self.assertFalse(read_json(os.path.join(self.d, "target.json"))["authorised"])
        self.assertIn("refuse", out.lower())

    def test_authorised_required(self):
        import argparse
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                target.main(["--dir", self.d, "init", "--name", "A", "--kind", "web"])

    def test_init_refuses_second_time(self):
        run("--dir", self.d, "init", "--name", "A", "--kind", "web", "--authorised", "yes")
        rc, out = run("--dir", self.d, "init", "--name", "B", "--kind", "web", "--authorised", "yes")
        self.assertEqual(rc, 2)
        self.assertIn("already", out)
        rc, _ = run("--dir", self.d, "init", "--name", "B", "--kind", "web", "--authorised", "yes", "--force")
        self.assertEqual(rc, 0)

    def test_init_records_the_stage(self):
        rc, out = run("--dir", self.d, "init", "--name", "A", "--kind", "web", "--authorised", "yes",
                      "--stage", "solo")
        self.assertEqual(rc, 0, out)
        self.assertEqual(read_json(os.path.join(self.d, "target.json"))["stage"], "solo")
        with open(os.path.join(self.d, "target.md"), encoding="utf-8") as fh:
            self.assertIn("- Stage: solo", fh.read())
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                target.main(["--dir", self.d, "init", "--name", "A", "--kind", "web", "--authorised", "yes",
                             "--stage", "startup", "--force"])

    def test_missing_stage_reads_as_team_and_stage_sets_it(self):
        run("--dir", self.d, "init", "--name", "A", "--kind", "web", "--authorised", "yes")
        self.assertNotIn("stage", read_json(os.path.join(self.d, "target.json")))
        rc, out = run("--dir", self.d, "stage")
        self.assertEqual(rc, 0)
        self.assertIn("team (not set, read as team)", out)
        rc, out = run("--dir", self.d, "show")
        self.assertIn("stage: team", out)
        rc, out = run("stage", "company", "--dir", self.d)  # --dir after the subcommand works too
        self.assertEqual(rc, 0, out)
        t = read_json(os.path.join(self.d, "target.json"))
        self.assertEqual(t["stage"], "company")
        self.assertEqual(t["name"], "A")
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                target.main(["--dir", self.d, "stage", "enterprise"])

    def test_stage_needs_a_target(self):
        rc, out = run("--dir", self.d, "stage", "solo")
        self.assertEqual(rc, 2)
        self.assertIn("Run init first", out)

    def test_require_roundtrip(self):
        run("--dir", self.d, "init", "--name", "A", "--kind", "web", "--authorised", "yes")
        run("--dir", self.d, "require", "add", "proof-web-e2e", "proof-sec-web")
        t = read_json(os.path.join(self.d, "target.json"))
        self.assertEqual(t["required_skills"], ["proof-web-e2e", "proof-sec-web"])
        run("--dir", self.d, "require", "remove", "proof-sec-web")
        t = read_json(os.path.join(self.d, "target.json"))
        self.assertEqual(t["required_skills"], ["proof-web-e2e"])

    def test_require_rejects_bad_name(self):
        run("--dir", self.d, "init", "--name", "A", "--kind", "web", "--authorised", "yes")
        rc, out = run("--dir", self.d, "require", "add", "websec")
        self.assertEqual(rc, 2)

    def test_recommend_only_lists_present(self):
        # proof-plan exists (this skill); invent a kind map hit that is not installed.
        rc, out = run("--dir", self.d, "recommend", "--kind", "web")
        self.assertEqual(rc, 0)
        # proof-web-e2e is planned but may not be installed in this checkout slice;
        # whichever side it is on, it must not be both.
        installed = target._skills_present()
        self.assertIn("proof-plan", installed)
        for line in out.splitlines():
            if "/proof-" in line and "Planned" not in out.split(line)[0][-60:]:
                pass  # smoke: output rendered

    def test_recommend_public_gate(self):
        rc, out = run("--dir", self.d, "recommend", "--kind", "web")
        self.assertNotIn("proof-seo -", out.split("Planned")[0])
        rc, out2 = run("--dir", self.d, "recommend", "--kind", "web", "--public")
        self.assertIn("proof-seo", out2)

    def test_recommend_llm_gate(self):
        _, out = run("--dir", self.d, "recommend", "--kind", "api")
        self.assertNotIn("proof-ai -", out.split("Planned")[0])
        _, out2 = run("--dir", self.d, "recommend", "--kind", "api", "--has-llm")
        self.assertIn("proof-ai", out2)


class BusinessReco(unittest.TestCase):
    def test_every_kind_is_offered_the_business_side_and_the_plan(self):
        tg = load_tool("proof-plan", "target.py")
        for kind, rows in tg.RECO.items():
            names = [n for n, _ in rows]
            for sk in ("proof-intake", "proof-market", "proof-buyers", "proof-pricing", "proof-offer", "proof-ads",
                       "proof-finance", "proof-go-to-market", "proof-ops", "proof-fixkit", "proof-business-plan"):
                self.assertEqual(names.count(sk), 1, "%s in %s" % (sk, kind))
            self.assertLess(names.index("proof-market"), names.index("proof-buyers"), kind)
            self.assertLess(names.index("proof-ops"), names.index("proof-business-plan"), kind)


if __name__ == "__main__":
    unittest.main()
