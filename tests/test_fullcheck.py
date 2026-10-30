import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

fc = load_tool("proof-full-check", "fullcheck.py")
ledger = load_tool("proof-core", "ledger.py")

ALL = {s for s, _ in fc.ORDER}


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = fc.main(list(argv))
    return rc, out.getvalue()


class FullCheck(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def target(self, kind="web"):
        ledger.write_target(self.d, {"name": "App", "kind": kind, "authorised": True, "required_skills": []})

    def test_select_web_includes_seo_excludes_mobile_and_ai(self):
        got = {s for s, _ in fc.select("web", installed=ALL)}
        self.assertIn("proof-seo", got)
        self.assertIn("proof-geo", got)
        self.assertNotIn("proof-mobile-e2e", got)
        self.assertNotIn("proof-ai", got)

    def test_select_no_seo(self):
        got = {s for s, _ in fc.select("web", no_seo=True, installed=ALL)}
        self.assertNotIn("proof-seo", got)
        self.assertNotIn("proof-geo", got)
        self.assertIn("proof-sec-web", got)
        self.assertIn("proof-web-e2e", got)

    def test_select_flags(self):
        got = {s for s, _ in fc.select("web", has_llm=True, mobile=True, skip={"proof-finance"}, installed=ALL)}
        self.assertIn("proof-ai", got)
        self.assertIn("proof-mobile-audit", got)
        self.assertNotIn("proof-finance", got)

    def test_select_repo_kind(self):
        got = {s for s, _ in fc.select("repo", installed=ALL)}
        self.assertEqual(got, {"proof-sec-secrets", "proof-sec-deps", "proof-mutation", "proof-restraint"})

    def test_select_skips_uninstalled(self):
        got = [s for s, _ in fc.select("web", installed={"proof-web-e2e"})]
        self.assertEqual(got, ["proof-web-e2e"])

    def test_plan_needs_target(self):
        rc, out = run("--dir", self.d, "plan")
        self.assertEqual(rc, 2)
        self.assertIn("proof-plan", out)

    def test_plan_writes_required_skills(self):
        self.target()
        rc, out = run("--dir", self.d, "plan", "--no-seo")
        self.assertEqual(rc, 0, out)
        req = ledger.load_target(self.d)["required_skills"]
        self.assertTrue(req)
        self.assertNotIn("proof-seo", req)
        with open(os.path.join(self.d, "fullcheck.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["mode"], "full check without SEO")

    def test_status_and_next(self):
        self.target()
        run("--dir", self.d, "plan", "--skip", "proof-finance")
        first = ledger.load_target(self.d)["required_skills"][0]
        rc, out = run("--dir", self.d, "next")
        self.assertIn(first, out)
        # finish the first skill: coverage all covered
        self.tp.write("evidence/%s/x.txt" % first, b"x")
        ledger.init_coverage(self.d, first, [("c1", "Check 1")])
        ledger.mark_coverage(self.d, first, "c1", "covered", evidence=["evidence/%s/x.txt" % first])
        rc, out = run("--dir", self.d, "status")
        self.assertIn("1 of", out)
        rc, out = run("--dir", self.d, "next")
        self.assertNotIn(first, out)


if __name__ == "__main__":
    unittest.main()
