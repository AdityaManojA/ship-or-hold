import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

ck = load_tool("proof-check", "check.py")
ledger = load_tool("proof-core", "ledger.py")

ALL = {s for s, _, _ in ck.SKILLS}
WEB_AI = {"web", "pwa", "api", "llm", "rag", "database", "auth", "tests", "email"}


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = ck.main(list(argv))
    return rc, out.getvalue()


def picked(profile, signals, **kw):
    return {s for s, _ in ck.select(profile, set(signals), installed=ALL, **kw)}


class Profiles(unittest.TestCase):
    def test_every_profile_names_known_skills(self):
        for name, p in ck.PROFILES.items():
            for s in p["skills"] or []:
                self.assertIn(s, ALL, "%s lists unknown %s" % (name, s))

    def test_quick_is_offline_repo_scans(self):
        self.assertEqual(picked("quick", WEB_AI), {"proof-sec-secrets", "proof-sec-deps", "proof-infra"})

    def test_webapp_fits_the_app_and_leaves_seo_off_unless_public(self):
        got = picked("webapp", WEB_AI)
        for s in ("proof-web-e2e", "proof-api", "proof-sec-web", "proof-auth", "proof-database", "proof-pwa",
                  "proof-web-perf", "proof-a11y", "proof-ai", "proof-email"):
            self.assertIn(s, got, s)
        for s in ("proof-seo", "proof-geo", "proof-mobile-e2e", "proof-slop", "proof-explore", "proof-load",
                  "proof-finance", "proof-i18n"):
            self.assertNotIn(s, got, s)
        self.assertTrue({"proof-seo", "proof-geo"} <= picked("webapp", WEB_AI | {"public"}))

    def test_webapp_without_signals_is_short(self):
        got = picked("webapp", {"web"})
        self.assertNotIn("proof-auth", got)
        self.assertNotIn("proof-ai", got)
        self.assertNotIn("proof-api", got)

    def test_startup_follows_web_or_mobile(self):
        web = picked("startup", {"web", "auth"})
        self.assertIn("proof-web-e2e", web)
        self.assertIn("proof-finance", web)
        self.assertNotIn("proof-store-ready", web)
        app = picked("startup", {"react_native", "auth"})
        self.assertIn("proof-mobile-e2e", app)
        self.assertIn("proof-store-ready", app)
        self.assertNotIn("proof-web-e2e", app)
        self.assertNotIn("proof-privacy", app)

    def test_mobile_profile(self):
        got = picked("mobile", {"react_native", "auth", "api"})
        for s in ("proof-mobile-e2e", "proof-mobile-audit", "proof-react-native", "proof-store-ready",
                  "proof-mobile-a11y", "proof-auth", "proof-api"):
            self.assertIn(s, got, s)
        self.assertNotIn("proof-web-perf", got)
        self.assertNotIn("proof-react-native", picked("mobile", {"native_mobile"}))

    def test_backend_and_security(self):
        be = picked("backend", {"api", "database", "llm"})
        for s in ("proof-api", "proof-sec-web", "proof-database", "proof-infra", "proof-ai", "proof-llm-cost"):
            self.assertIn(s, be, s)
        self.assertNotIn("proof-web-e2e", be)
        sec = picked("security", {"web"})
        self.assertEqual(sec, {"proof-sec-secrets", "proof-sec-deps", "proof-sec-web", "proof-infra", "proof-privacy"})

    def test_ultra_is_everything_that_fits(self):
        got = picked("ultra", WEB_AI)
        for s in ("proof-rag", "proof-llm-cost", "proof-release", "proof-licenses",
                  "proof-slop", "proof-explore", "proof-compat"):
            self.assertIn(s, got, s)
        for s in ("proof-react-native", "proof-store-ready", "proof-voice", "proof-llm-device", "proof-seo"):
            self.assertNotIn(s, got, s)
        for name in ck.PROFILES:
            self.assertTrue(picked(name, WEB_AI) <= got, name)

    def test_add_skip_and_installed(self):
        got = picked("webapp", {"web"}, add={"proof-slop"}, skip={"proof-a11y"})
        self.assertIn("proof-slop", got)
        self.assertNotIn("proof-a11y", got)
        only = [s for s, _ in ck.select("webapp", {"web"}, installed={"proof-web-e2e"})]
        self.assertEqual(only, ["proof-web-e2e"])

    def test_target_kind_tops_up_signals(self):
        self.assertIn("proof-api", picked("backend", ck.signals_for([], kind="api")))
        self.assertIn("proof-mobile-e2e", picked("mobile", ck.signals_for([], mobile=True)))

    def test_profile_for_another_kind_runs_nothing(self):
        self.assertEqual(picked("mobile", {"web", "auth", "api"}), set())
        self.assertEqual(picked("webapp", {"api"}), set())
        self.assertEqual(picked("mobile", {"web"}, add={"proof-slop"}), {"proof-slop"})
        self.assertTrue(picked("startup", {"api"}))  # startup, security, quick fit any product

    def test_suggest(self):
        self.assertEqual(ck.suggest({"react_native", "web"}), "mobile")
        self.assertEqual(ck.suggest({"web", "api"}), "webapp")
        self.assertEqual(ck.suggest({"api"}), "backend")
        self.assertEqual(ck.suggest(set()), "quick")


class Plan(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def target(self, kind="web"):
        ledger.write_target(self.d, {"name": "App", "kind": kind, "authorised": True, "required_skills": []})

    def project(self, files):
        root = os.path.join(self.tp.base, "app")
        for rel, text in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text if isinstance(text, str) else json.dumps(text))
        return root

    WEB_AI_FILES = {
        "frontend/package.json": {"dependencies": {"next": "15", "react-dom": "19"}},
        "frontend/app/manifest.ts": "export default function manifest() { return {} }",
        "frontend/app/layout.tsx": "navigator.serviceWorker.register('/sw.js')",
        "backend/requirements.txt": "fastapi==0.1\nopenai==1.0\npgvector==0.3\nalembic==1.0\npyjwt==2\npytest==8\n",
    }

    def test_review_reads_a_web_ai_project(self):
        r = ck.appreview.review([self.project(self.WEB_AI_FILES)])
        self.assertEqual(r["kind"], "web")
        for sig in ("web", "pwa", "api", "llm", "rag", "database", "auth", "tests"):
            self.assertIn(sig, r["signals"], sig)

    def test_review_reads_an_expo_app_as_mobile(self):
        r = ck.appreview.review([self.project({
            "package.json": {"dependencies": {"expo": "52", "react-native": "0.76", "react-dom": "19",
                                              "react-native-webrtc": "1", "expo-apple-authentication": "1"},
                             "devDependencies": {"jest": "29"}},
            "app.json": {"expo": {"name": "x"}}})])
        self.assertEqual(r["kind"], "mobile")
        self.assertEqual(r["mobile"], ["android", "ios"])

    def test_plan_needs_target_and_profile(self):
        rc, out = run("--dir", self.d, "plan", "--profile", "webapp")
        self.assertEqual(rc, 2)
        self.assertIn("proof-plan", out)
        with self.assertRaises(SystemExit):
            run("--dir", self.d, "plan")

    def test_plan_writes_required_skills_and_lists_extras(self):
        self.target()
        root = self.project(self.WEB_AI_FILES)
        rc, out = run("--dir", self.d, "plan", "--profile", "webapp", "--project", root)
        self.assertEqual(rc, 0, out)
        self.assertIn("Review of", out)
        req = ledger.load_target(self.d)["required_skills"]
        self.assertIn("proof-ai", req)
        self.assertNotIn("proof-seo", req)
        self.assertIn("Also fits this app", out)
        with open(os.path.join(self.d, "check.json"), encoding="utf-8") as fh:
            plan = json.load(fh)
        self.assertEqual(plan["profile"], "webapp")
        self.assertEqual(plan["mode"], "web app check")
        rc, out = run("--dir", self.d, "plan", "--profile", "webapp", "--public", "--project", root)
        self.assertIn("proof-seo", ledger.load_target(self.d)["required_skills"])

    def test_profiles_lists_counts_after_review(self):
        self.target()
        run("--dir", self.d, "review", "--project", self.project(self.WEB_AI_FILES))
        rc, out = run("--dir", self.d, "profiles")
        self.assertEqual(rc, 0)
        self.assertIn("/proof-webapp", out)
        self.assertIn("fits this app", out)
        self.assertIn("/proof-ultracheck", out)

    def test_status_and_next(self):
        self.target()
        run("--dir", self.d, "plan", "--profile", "quick", "--project", self.project({"README.md": "x"}))
        first = ledger.load_target(self.d)["required_skills"][0]
        rc, out = run("--dir", self.d, "next")
        self.assertIn(first, out)
        self.tp.write("evidence/%s/x.txt" % first, b"x")
        ledger.init_coverage(self.d, first, [("c1", "Check 1")])
        ledger.mark_coverage(self.d, first, "c1", "covered", evidence=["evidence/%s/x.txt" % first])
        rc, out = run("--dir", self.d, "status")
        self.assertIn("1 of 3", out)
        rc, out = run("--dir", self.d, "next")
        self.assertNotIn(first, out)


class Cache(unittest.TestCase):
    """A second run skips skills whose inputs have not changed."""

    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        ledger.write_target(self.d, {"name": "App", "kind": "web", "authorised": True, "required_skills": []})
        self.root = os.path.join(self.tp.base, "app")
        for rel, text in {"package.json": '{"dependencies": {"next": "15"}}', "Dockerfile": "FROM node:22\n",
                          "src/app.js": "x = 1\n", "README.md": "hi\n"}.items():
            self.put(rel, text)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def put(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def finish(self, skill):
        ev = self.tp.write("evidence/%s/x.txt" % skill, b"x")
        ledger.init_coverage(self.d, skill, [("c1", "Check 1")])
        ledger.mark_coverage(self.d, skill, "c1", "covered", evidence=[ev])

    def second_run(self):
        """Re-plan as a later run would, with the plan dated after the first run finished."""
        run("--dir", self.d, "plan", "--profile", "quick", "--project", self.root)
        plan = ck._load_plan(self.d)
        plan["created"] = "2999-01-01T00:00:00+00:00"
        ck._save_plan(self.d, plan)

    def states(self):
        return {r["skill"]: r["state"] for r in ck._progress(self.d)[1]}

    def first_run(self):
        run("--dir", self.d, "plan", "--profile", "quick", "--project", self.root)
        for s in ("proof-sec-secrets", "proof-sec-deps", "proof-infra"):
            self.finish(s)
        self.assertEqual(set(self.states().values()), {"done"})

    def test_unchanged_inputs_are_skipped(self):
        self.first_run()
        self.second_run()
        self.assertEqual(set(self.states().values()), {"unchanged"})
        rc, out = run("--dir", self.d, "next")
        self.assertIn("3 unchanged and skipped", out)
        rc, out = run("--dir", self.d, "plan", "--profile", "quick", "--project", self.root)
        self.assertIn("finished in an earlier run", out)

    def test_only_the_skills_whose_inputs_moved_go_stale(self):
        self.first_run()
        self.put("package.json", '{"dependencies": {"next": "16"}}')
        self.second_run()
        got = self.states()
        self.assertEqual(got["proof-sec-deps"], "stale")
        self.assertEqual(got["proof-infra"], "unchanged")
        rc, out = run("--dir", self.d, "next")
        self.assertIn("inputs changed", out)
        os.utime(ledger.coverage_path(self.d, "proof-sec-secrets"), None)  # re-run after the change
        os.utime(ledger.coverage_path(self.d, "proof-sec-deps"), None)
        self.finish("proof-sec-deps")
        got = self.states()
        self.assertEqual(got["proof-sec-deps"], "done")

    def test_docs_do_not_invalidate_code_skills_and_refresh_forces(self):
        self.first_run()
        self.put("README.md", "changed\n")
        self.second_run()
        self.assertEqual(self.states()["proof-sec-secrets"], "unchanged")
        run("--dir", self.d, "refresh", "proof-infra")
        self.assertEqual(self.states()["proof-infra"], "refresh")
        rc, out = run("--dir", self.d, "next")
        self.assertIn("marked to run again", out)

    def test_fingerprints_group_inputs(self):
        a = ck.fingerprints([self.root])
        self.put("Dockerfile", "FROM node:24\n")
        b = ck.fingerprints([self.root])
        self.assertNotEqual(a["infra"], b["infra"])
        self.assertEqual(a["deps"], b["deps"])


if __name__ == "__main__":
    unittest.main()
