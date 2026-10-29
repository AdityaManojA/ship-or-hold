import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

adv = load_tool("proof-sec-deps", "advisories.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = adv.main(list(argv))
    return rc, out.getvalue()


class Advisories(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.proj = os.path.join(self.tp.base, "proj")
        os.makedirs(self.proj)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write(self, name, content):
        with open(os.path.join(self.proj, name), "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_parse_requirements(self):
        self.assertEqual(adv._parse_requirements("flask==3.0.0\n# c\nrequests==2.31.0\n-e .\n"),
                         {"flask": "3.0.0", "requests": "2.31.0"})

    def test_parse_package_lock_v3(self):
        data = json.dumps({"packages": {"": {}, "node_modules/left-pad": {"version": "1.3.0"},
                                        "node_modules/x": {"version": "2.0.0"}}})
        self.assertEqual(adv._parse_package_lock(data), {"left-pad": "1.3.0", "x": "2.0.0"})

    def test_version_range(self):
        self.assertTrue(adv._in_range("1.4.1", "<1.4.2"))
        self.assertFalse(adv._in_range("1.4.2", "<1.4.2"))
        self.assertTrue(adv._in_range("2.1.0", ">=2.0,<2.3.1"))
        self.assertFalse(adv._in_range("2.3.1", ">=2.0,<2.3.1"))
        self.assertTrue(adv._in_range("9.9.9", "*"))

    def test_collect(self):
        self.write("requirements.txt", "flask==3.0.0\n")
        self.write("package-lock.json", json.dumps({"packages": {"node_modules/left-pad": {"version": "1.0.0"}}}))
        found = adv.collect(self.proj)
        self.assertEqual(found["pip"], {"flask": "3.0.0"})
        self.assertEqual(found["npm"], {"left-pad": "1.0.0"})

    def test_audit_writes_verified_findings(self):
        self.write("requirements.txt", "example-pkg==2.1.0\nsafe-pkg==9.9.9\n")
        advfile = os.path.join(self.tp.base, "adv.json")
        with open(advfile, "w", encoding="utf-8") as fh:
            json.dump({"advisories": [
                {"id": "ADV-1", "ecosystem": "pip", "package": "example-pkg",
                 "vulnerable": ">=2.0,<2.3.1", "severity": "critical", "fixed": "2.3.1",
                 "title": "RCE", "reference": "u"},
                {"id": "ADV-2", "ecosystem": "pip", "package": "not-installed",
                 "vulnerable": "*", "severity": "high", "fixed": "1.0"},
            ]}, fh)
        rc, out = run("--dir", self.d, "audit", self.proj, "--advisories", advfile)
        self.assertEqual(rc, 0, out)
        findings = ledger.load_findings(self.d, "proof-sec-deps")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["status"], "verified")
        self.assertIn("2.3.1", findings[0]["fix"])
        cov = ledger.load_coverage(self.d, "proof-sec-deps")
        self.assertEqual(cov["checks"][0]["status"], "covered")

    def test_template(self):
        out = os.path.join(self.tp.base, "t.json")
        run("--dir", self.d, "template", "--out", out)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("advisories", json.load(fh))


if __name__ == "__main__":
    unittest.main()
