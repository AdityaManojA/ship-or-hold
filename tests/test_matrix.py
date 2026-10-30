import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

matrix = load_tool("proof-compat", "matrix.py")
ledger = load_tool("proof-core", "ledger.py")

SKILL = "proof-compat"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = matrix.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


SPEC = {
    "targets": [
        {"id": "checkout", "title": "Checkout", "critical": True},
        {"id": "home", "title": "Home page"},
    ],
    "environments": [
        {"id": "chrome-desktop", "browser": "Chrome", "version": "latest", "viewport": "1440x900", "os": "Windows"},
        {"id": "safari-ios", "browser": "Safari", "version": "latest", "viewport": "390x844", "os": "iOS"},
    ],
}


class Matrix(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.spec = os.path.join(self.tp.base, "matrix.json")
        self.write_spec(SPEC)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_spec(self, data):
        with open(self.spec, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def plan(self):
        return run("--dir", self.d, "plan", self.spec)

    def status_of(self, cid):
        cov = ledger.load_coverage(self.d, SKILL)
        return next(c for c in cov["checks"] if c["id"] == cid)["status"]

    def test_template_presets_are_valid(self):
        sizes = {}
        for preset in ("web", "mobile", "full"):
            out = os.path.join(self.tp.base, "t-%s.json" % preset)
            rc, msg = run("--dir", self.d, "template", "--out", out, "--preset", preset)
            self.assertEqual(rc, 0, msg)
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(matrix.validate(data), [], preset)
            sizes[preset] = len(data["environments"])
        with open(os.path.join(self.tp.base, "t-web.json"), encoding="utf-8") as fh:
            web = json.load(fh)
        ids = {e["id"]: e for e in web["environments"]}
        self.assertEqual(ids["safari-ios"]["viewport"], "390x844")
        self.assertEqual(ids["chrome-android"]["viewport"], "412x915")
        self.assertEqual(sum(1 for e in web["environments"] if e["viewport"] == "1440x900"), 4)
        self.assertGreater(sizes["full"], sizes["web"])
        self.assertGreater(sizes["full"], sizes["mobile"])

    def test_template_refuses_overwrite(self):
        out = os.path.join(self.tp.base, "t.json")
        self.assertEqual(run("--dir", self.d, "template", "--out", out)[0], 0)
        rc, msg = run("--dir", self.d, "template", "--out", out)
        self.assertNotEqual(rc, 0)
        self.assertIn("--force", msg)

    def test_example_is_valid(self):
        path = os.path.join(os.path.dirname(matrix.__file__), "matrix.example.json")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(matrix.validate(json.load(fh)), [])

    def test_plan_creates_every_cell(self):
        rc, out = self.plan()
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, SKILL)
        ids = {c["id"] for c in cov["checks"]}
        self.assertEqual(ids, {"checkout@chrome-desktop", "checkout@safari-ios",
                               "home@chrome-desktop", "home@safari-ios"})
        self.assertTrue(all(c["status"] == "not_run" for c in cov["checks"]))
        titles = {c["id"]: c["title"] for c in cov["checks"]}
        self.assertEqual(titles["checkout@safari-ios"], "checkout on Safari iOS 390x844")
        self.assertTrue(os.path.exists(os.path.join(self.d, "compat", "matrix.json")))

    def test_plan_rejects_duplicates_and_missing_fields(self):
        dup = {"targets": SPEC["targets"] + [SPEC["targets"][0]], "environments": SPEC["environments"]}
        self.write_spec(dup)
        rc, out = self.plan()
        self.assertNotEqual(rc, 0)
        self.assertIn("duplicate", out)
        dup_env = {"targets": SPEC["targets"], "environments": SPEC["environments"] * 2}
        self.write_spec(dup_env)
        rc, out = self.plan()
        self.assertNotEqual(rc, 0)
        self.assertIn("duplicate environment", out)
        env = dict(SPEC["environments"][0])
        del env["viewport"]
        self.write_spec({"targets": SPEC["targets"], "environments": [env]})
        rc, out = self.plan()
        self.assertNotEqual(rc, 0)
        self.assertIn("viewport", out)
        env["viewport"] = "wide"
        self.write_spec({"targets": SPEC["targets"], "environments": [env]})
        self.assertNotEqual(self.plan()[0], 0)
        self.assertIsNone(ledger.load_coverage(self.d, SKILL))

    def test_pass_needs_evidence(self):
        self.plan()
        rc, out = run("--dir", self.d, "result", "checkout", "safari-ios", "pass",
                      "--evidence", "evidence/proof-compat/missing.png")
        self.assertNotEqual(rc, 0)
        self.tp.write("evidence/proof-compat/empty.png", b"")
        rc, out = run("--dir", self.d, "result", "checkout", "safari-ios", "pass",
                      "--evidence", "evidence/proof-compat/empty.png")
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.status_of("checkout@safari-ios"), "not_run")
        ev = self.tp.write("evidence/proof-compat/checkout@safari-ios.png", b"PNG")
        rc, out = run("--dir", self.d, "result", "checkout", "safari-ios", "pass", "--evidence", ev)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.status_of("checkout@safari-ios"), "covered")

    def test_one_cell_one_evidence(self):
        self.plan()
        ev = self.tp.write("evidence/proof-compat/shot.png", b"PNG")
        self.assertEqual(run("--dir", self.d, "result", "home", "safari-ios", "pass", "--evidence", ev)[0], 0)
        rc, out = run("--dir", self.d, "result", "home", "chrome-desktop", "pass", "--evidence", ev)
        self.assertNotEqual(rc, 0)
        self.assertIn("one cell", out)
        self.assertEqual(self.status_of("home@chrome-desktop"), "not_run")

    def test_unknown_cell_rejected(self):
        self.plan()
        ev = self.tp.write("evidence/proof-compat/x.png", b"PNG")
        self.assertNotEqual(run("--dir", self.d, "result", "nope", "safari-ios", "pass", "--evidence", ev)[0], 0)
        self.assertNotEqual(run("--dir", self.d, "result", "home", "nope", "pass", "--evidence", ev)[0], 0)

    def test_fail_writes_finding(self):
        self.plan()
        ev = self.tp.write("evidence/proof-compat/checkout@safari-ios.png", b"PNG")
        rc, out = run("--dir", self.d, "result", "checkout", "safari-ios", "fail", "--evidence", ev)
        self.assertNotEqual(rc, 0)
        self.assertIn("title", out)
        rc, out = run("--dir", self.d, "result", "checkout", "safari-ios", "fail", "--evidence", ev,
                      "--title", "Pay button hidden", "--repro", "tap card field")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, SKILL)
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["category"], "compat:safari-ios")
        self.assertEqual(fs[0]["severity"], "high")  # critical target default
        self.assertEqual(fs[0]["status"], "unverified")
        self.assertEqual(self.status_of("checkout@safari-ios"), "covered")

    def test_fail_verify(self):
        self.plan()
        ev = self.tp.write("evidence/proof-compat/home@chrome-desktop.png", b"PNG")
        run("--dir", self.d, "result", "home", "chrome-desktop", "fail", "--evidence", ev,
            "--title", "Menu overlaps logo", "--verify")
        fs = ledger.load_findings(self.d, SKILL)
        self.assertEqual(fs[0]["status"], "verified")
        self.assertEqual(fs[0]["severity"], "medium")

    def test_skip_needs_note(self):
        self.plan()
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                matrix.main(["--dir", self.d, "skip", "home", "safari-ios"])
        rc, out = run("--dir", self.d, "skip", "home", "safari-ios", "--note", "desktop only page")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.status_of("home@safari-ios"), "not_applicable")

    def test_grid_marks_and_markdown(self):
        self.plan()
        ok = self.tp.write("evidence/proof-compat/checkout@chrome-desktop.png", b"PNG")
        bad = self.tp.write("evidence/proof-compat/checkout@safari-ios.png", b"PNG")
        run("--dir", self.d, "result", "checkout", "chrome-desktop", "pass", "--evidence", ok)
        run("--dir", self.d, "result", "checkout", "safari-ios", "fail", "--evidence", bad, "--title", "Broken")
        run("--dir", self.d, "skip", "home", "safari-ios", "--note", "desktop only")
        _, marks = matrix.cell_marks(self.d)
        self.assertEqual(marks, {"checkout@chrome-desktop": "ok", "checkout@safari-ios": "!!",
                                 "home@safari-ios": "--", "home@chrome-desktop": ".."})
        rc, out = run("--dir", self.d, "grid")
        self.assertEqual(rc, 0, out)
        out.encode("ascii")
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("target"))
        self.assertIn("chrome-desktop", lines[0])
        row = next(l for l in lines if l.startswith("checkout")).split()
        self.assertEqual(row, ["checkout", "ok", "!!"])
        row = next(l for l in lines if l.startswith("home")).split()
        self.assertEqual(row, ["home", "..", "--"])
        path = os.path.join(self.d, "compat", "grid.md")
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("| target | chrome-desktop | safari-ios |", md)
        self.assertIn("| checkout | ok | !! |", md)
        self.assertIn("| home | .. | -- |", md)

    def test_status(self):
        rc, out = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("no matrix plan", out)
        self.plan()
        rc, out = run("--dir", self.d, "status")
        self.assertIn("0 covered", out)
        self.assertIn("4 not run", out)


if __name__ == "__main__":
    unittest.main()
