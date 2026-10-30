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
        self.assertEqual(got, {"proof-sec-secrets", "proof-sec-deps", "proof-infra", "proof-restraint", "proof-slop"})

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

    def project(self, files):
        root = os.path.join(self.tp.base, "app")
        for rel, text in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text if isinstance(text, str) else json.dumps(text))
        return root

    WEB_AI = {
        "frontend/package.json": {"dependencies": {"next": "15", "react-dom": "19"}},
        "frontend/app/manifest.ts": "export default function manifest() { return {} }",
        "frontend/app/layout.tsx": "navigator.serviceWorker.register('/sw.js')",
        "backend/requirements.txt": "fastapi==0.1\nopenai==1.0\npgvector==0.3\nalembic==1.0\npyjwt==2\npytest==8\n",
    }

    def test_review_reads_a_web_ai_project(self):
        r = fc.appreview.review([self.project(self.WEB_AI)])
        self.assertEqual(r["kind"], "web")
        for sig in ("web", "pwa", "api", "llm", "rag", "database", "auth", "tests"):
            self.assertIn(sig, r["signals"], sig)
        self.assertNotIn("react_native", r["signals"])

    def test_review_reads_an_expo_app_as_mobile(self):
        r = fc.appreview.review([self.project({
            "package.json": {"dependencies": {"expo": "52", "react-native": "0.76", "react-dom": "19",
                                              "react-native-webrtc": "1", "expo-apple-authentication": "1"},
                             "devDependencies": {"jest": "29"}},
            "app.json": {"expo": {"name": "x"}}})])
        self.assertEqual(r["kind"], "mobile")  # react-dom from react-native-web is not a second app
        self.assertEqual(r["mobile"], ["android", "ios"])
        for sig in ("react_native", "voice", "auth", "tests"):
            self.assertIn(sig, r["signals"], sig)

    def test_plan_reviews_first_and_ultra_adds_what_fits(self):
        self.target("web")
        root = self.project(self.WEB_AI)
        rc, out = run("--dir", self.d, "plan", "--project", root)
        self.assertEqual(rc, 0, out)
        self.assertIn("Review of", out)
        full = set(ledger.load_target(self.d)["required_skills"])
        self.assertIn("proof-ai", full)  # the review found an LLM, no --has-llm needed
        self.assertNotIn("proof-rag", full)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "review.json")))
        rc, out = run("--dir", self.d, "plan", "--ultra", "--project", root)
        self.assertEqual(rc, 0, out)
        self.assertIn("ultra check", out)
        ultra = set(ledger.load_target(self.d)["required_skills"])
        installed = fc._installed()
        for s in ("proof-rag", "proof-database", "proof-auth", "proof-llm-cost", "proof-pwa", "proof-mutation",
                  "proof-release", "proof-licenses", "proof-resilience", "proof-compat"):
            if s in installed:
                self.assertIn(s, ultra, s)
        for s in ("proof-react-native", "proof-mobile-explore", "proof-store-ready", "proof-voice", "proof-llm-device"):
            self.assertNotIn(s, ultra, s)
        self.assertTrue(full < ultra)


if __name__ == "__main__":
    unittest.main()
