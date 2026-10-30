import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

lic = load_tool("proof-licenses", "licenses.py")
ledger = load_tool("proof-core", "ledger.py")

LOCK = {"name": "app", "lockfileVersion": 3, "packages": {
    "": {"name": "app"},
    "node_modules/react": {"version": "18.3.1", "license": "MIT"},
    "node_modules/@scope/tool": {"version": "1.0.0", "license": "(MIT OR GPL-3.0-only)"},
    "node_modules/gpl-lib": {"version": "2.0.0", "license": "GPL-3.0-or-later"},
    "node_modules/agpl-lib": {"version": "1.1.0", "license": "AGPL-3.0-only"},
    "node_modules/lgpl-lib": {"version": "0.9.0", "license": "LGPL-2.1"},
    "node_modules/mystery": {"version": "0.1.0"},
    "node_modules/gpl-dev": {"version": "3.0.0", "license": "GPL-2.0", "dev": True},
}}

METADATA = {
    "requests": "Metadata-Version: 2.1\nName: requests\nVersion: 2.32.3\nLicense: Apache 2.0\n"
                "Classifier: License :: OSI Approved :: Apache Software License\n\nbody\n",
    "psycopg": "Metadata-Version: 2.4\nName: psycopg\nVersion: 3.2.0\nLicense-Expression: LGPL-3.0-only\n\nbody\n",
    "weird": "Metadata-Version: 2.1\nName: weird\nVersion: 1.0\nClassifier: License :: OSI Approved :: "
             "GNU General Public License v3 (GPLv3)\n\n",
}


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = lic.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Licenses(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.proj = os.path.join(self.tp.base, "proj")
        os.makedirs(self.proj)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write(self, rel, text):
        p = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text if isinstance(text, str) else json.dumps(text))
        return p

    def site(self):
        for name, text in METADATA.items():
            self.write("venv/site-packages/%s-1.0.dist-info/METADATA" % name, text)
        return os.path.join(self.proj, "venv", "site-packages")

    def test_classify_expressions_and_free_text(self):
        c = lic.classify
        self.assertEqual(c("MIT"), lic.PERMISSIVE)
        self.assertEqual(c("(MIT OR GPL-3.0-only)"), lic.PERMISSIVE)
        self.assertEqual(c("MIT AND GPL-2.0-only"), lic.STRONG)
        self.assertEqual(c("GPL-2.0-only WITH Classpath-exception-2.0"), lic.STRONG)
        self.assertEqual(c("AGPL-3.0-or-later"), lic.NETWORK)
        self.assertEqual(c("SSPL-1.0"), lic.NETWORK)
        self.assertEqual(c("MPL-2.0"), lic.WEAK)
        self.assertEqual(c("Apache Software License"), lic.PERMISSIVE)
        self.assertEqual(c("BSD"), lic.PERMISSIVE)
        self.assertEqual(c("UNLICENSED"), lic.UNKNOWN)
        self.assertEqual(c("SEE LICENSE IN LICENSE.md"), lic.UNKNOWN)
        self.assertEqual(c(None), lic.UNKNOWN)
        self.assertEqual(c("Custom Corp Terms"), lic.UNKNOWN)
        self.assertEqual(c("MIT OR UNKNOWN-THING"), lic.PERMISSIVE)

    def test_policy_by_project_licence(self):
        s = lic.severity_for
        self.assertEqual(s(lic.NETWORK, "proprietary"), "high")
        self.assertEqual(s(lic.STRONG, "proprietary"), "medium")
        self.assertEqual(s(lic.WEAK, "proprietary"), "low")
        self.assertIsNone(s(lic.PERMISSIVE, "proprietary"))
        self.assertEqual(s(lic.STRONG, "MIT"), "medium")
        self.assertIsNone(s(lic.WEAK, "MIT"))
        self.assertIsNone(s(lic.STRONG, "GPL-3.0-only"))
        self.assertEqual(s(lic.NETWORK, "proprietary", dev=True), "low")

    def test_collect_npm_lock_and_python(self):
        self.write("package-lock.json", LOCK)
        comps = {(c["ecosystem"], c["name"]): c for c in lic.collect([self.proj], self.site())}
        self.assertEqual(comps[("npm", "@scope/tool")]["category"], lic.PERMISSIVE)
        self.assertEqual(comps[("npm", "mystery")]["category"], lic.UNKNOWN)
        self.assertTrue(comps[("npm", "gpl-dev")]["dev"])
        self.assertEqual(comps[("pypi", "requests")]["license"], "Apache-2.0")
        self.assertEqual(comps[("pypi", "psycopg")]["category"], lic.WEAK)
        self.assertEqual(comps[("pypi", "weird")]["category"], lic.STRONG)

    def test_yarn_pnpm_and_node_modules(self):
        self.write("yarn.lock", '# yarn lockfile v1\n\n"left-pad@^1.0.0", left-pad@^1.3.0:\n  version "1.3.0"\n'
                                '  resolved "x"\n\n"@babel/core@^7.0.0":\n  version "7.24.0"\n')
        self.write("node_modules/left-pad/package.json", {"name": "left-pad", "version": "1.3.0",
                                                          "licenses": [{"type": "WTFPL"}]})
        self.write("node_modules/@babel/core/package.json", {"name": "@babel/core", "version": "7.24.0",
                                                             "license": {"type": "MIT"}})
        other = os.path.join(self.tp.base, "other")
        os.makedirs(other)
        with open(os.path.join(other, "pnpm-lock.yaml"), "w", encoding="utf-8") as fh:
            fh.write("lockfileVersion: '9.0'\n\npackages:\n\n  zod@3.23.8:\n    resolution: {}\n\n"
                     "  '@types/node@20.11.0':\n    resolution: {}\n\nsnapshots:\n\n  zod@3.23.8: {}\n")
        comps = {c["name"]: c for c in lic.collect([self.proj, other])}
        self.assertEqual(comps["left-pad"]["license"], "WTFPL")
        self.assertEqual(comps["@babel/core"]["category"], lic.PERMISSIVE)
        self.assertEqual(comps["zod"]["version"], "3.23.8")
        self.assertEqual(comps["@types/node"]["category"], lic.UNKNOWN)

    def test_requirements_and_pyproject_without_env(self):
        self.write("requirements.txt", "fastapi==0.115.0\nuvicorn[standard]\n-r other.txt\n")
        self.write("pyproject.toml", '[project]\nname = "x"\ndependencies = [\n  "httpx==0.27.0",\n]\n')
        comps = {c["name"]: c for c in lic.collect([self.proj])}
        self.assertEqual(comps["fastapi"]["version"], "0.115.0")
        self.assertIn("uvicorn", comps)
        self.assertEqual(comps["httpx"]["category"], lic.UNKNOWN)

    def test_record_findings_coverage_and_sbom(self):
        self.write("package-lock.json", LOCK)
        rc, out = run("--dir", self.d, "record", self.proj)
        self.assertEqual(rc, 0, out)
        fs = {f["category"]: f for f in ledger.load_findings(self.d, "proof-licenses")}
        self.assertEqual(fs["licence:network-copyleft"]["severity"], "high")
        self.assertEqual(fs["licence:strong-copyleft"]["severity"], "medium")  # gpl-dev is capped at low inside it
        self.assertIn("gpl-lib", fs["licence:strong-copyleft"]["description"])
        self.assertEqual(fs["licence:weak-copyleft"]["severity"], "low")
        self.assertEqual(fs["licence:unknown"]["severity"], "low")
        self.assertTrue(all(f["status"] == "verified" for f in fs.values()))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-licenses")["checks"]}
        self.assertEqual(cov["policy"], "not_applicable")
        self.assertEqual(cov["sbom"], "covered")
        with open(os.path.join(self.d, "evidence", "proof-licenses", "sbom.cdx.json"), encoding="utf-8") as fh:
            bom = json.load(fh)
        self.assertEqual(bom["bomFormat"], "CycloneDX")
        self.assertEqual(bom["specVersion"], "1.5")
        purls = {c["purl"]: c for c in bom["components"]}
        self.assertIn("pkg:npm/%40scope/tool@1.0.0", purls)
        self.assertEqual(purls["pkg:npm/%40scope/tool@1.0.0"]["licenses"][0]["expression"], "(MIT OR GPL-3.0-only)")
        self.assertEqual(purls["pkg:npm/gpl-dev@3.0.0"]["scope"], "excluded")
        # Allowing GPL and the unknown ones' fix clears findings on the next run.
        rc, out = run("--dir", self.d, "record", self.proj, "--allow", "GPL-3.0-or-later", "--allow", "GPL-2.0",
                      "--deny", "LGPL-2.1")
        cats = {f["category"]: f for f in ledger.load_findings(self.d, "proof-licenses")}
        self.assertNotIn("licence:strong-copyleft", cats)
        self.assertNotIn("licence:weak-copyleft", cats)
        self.assertEqual(cats["licence:policy"]["severity"], "high")
        self.assertIn("lgpl-lib", cats["licence:policy"]["description"])

    def test_open_source_project_only_flags_conflicts(self):
        self.write("package-lock.json", LOCK)
        rc, out = run("--dir", self.d, "scan", self.proj, "--project-license", "MIT", "--json")
        data = json.loads(out)
        flagged = {i["name"] for i in data["issues"]}
        self.assertIn("gpl-lib", flagged)
        self.assertNotIn("lgpl-lib", flagged)
        self.assertNotIn("react", flagged)

    def test_sbom_out_respects_guard(self):
        self.write("package-lock.json", LOCK)
        mine = os.path.join(self.tp.base, "sbom.json")
        with open(mine, "w", encoding="utf-8") as fh:
            fh.write('{"mine": true}')
        rc, out = run("--dir", self.d, "sbom", self.proj, "--out", mine)
        self.assertNotEqual(rc, 0)
        self.assertIn("--force", out)
        rc, out = run("--dir", self.d, "sbom", self.proj, "--out", mine, "--force")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "sbom", self.proj, "--out", mine)  # ours now, so replaced freely
        self.assertEqual(rc, 0, out)

    def test_nothing_found_is_not_applicable(self):
        rc, out = run("--dir", self.d, "record", self.proj)
        self.assertEqual(rc, 0)
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-licenses")["checks"]}
        self.assertTrue(all(s == "not_applicable" for s in cov.values()))


if __name__ == "__main__":
    unittest.main()
