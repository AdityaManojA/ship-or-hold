import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

bloat = load_tool("proof-restraint", "bloat.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = bloat.main(list(argv))
    return rc, out.getvalue()


class Bloat(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.proj = os.path.join(self.tp.base, "proj")
        os.makedirs(self.proj)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write(self, rel, content):
        full = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_deadcode_finds_unreferenced(self):
        self.write("a.py", "def used():\n    return 1\n\ndef lonely():\n    return 2\n")
        self.write("b.py", "from a import used\nprint(used())\n")
        dead = {d["name"] for d in bloat.deadcode(self.proj)}
        self.assertIn("lonely", dead)
        self.assertNotIn("used", dead)

    def test_deadcode_ignores_private_and_main(self):
        self.write("a.py", "def _helper():\n    return 1\n\ndef main():\n    return 2\n")
        self.assertEqual(bloat.deadcode(self.proj), [])

    def test_big_files(self):
        self.write("big.py", "\n".join("x = %d" % i for i in range(700)) + "\n")
        self.write("small.py", "x = 1\n")
        big = {b["file"] for b in bloat.big_files(self.proj)}
        self.assertTrue(any("big.py" in f for f in big))
        self.assertFalse(any("small.py" in f for f in big))

    def test_unused_deps_pip(self):
        self.write("requirements.txt", "flask==3.0.0\nleftpad==1.0.0\n")
        self.write("app.py", "import flask\nprint(flask)\n")
        deps = {d["package"] for d in bloat.unused_deps(self.proj)}
        self.assertIn("leftpad", deps)
        self.assertNotIn("flask", deps)

    def test_debug_markers(self):
        self.write("a.js", "function f(){ console.log('x'); // TODO fix\n}\n")
        markers = bloat.debug_markers(self.proj)
        self.assertGreaterEqual(len(markers), 1)

    def test_record_low_severity(self):
        self.write("a.py", "def lonely():\n    return 2\n")
        self.write("requirements.txt", "leftpad==1.0.0\n")
        rc, out = run("--dir", self.d, "record", self.proj)
        self.assertEqual(rc, 0, out)
        findings = ledger.load_findings(self.d, "proof-restraint")
        self.assertTrue(findings)
        self.assertTrue(all(f["severity"] == "low" for f in findings))
        self.assertTrue(all(f["status"] == "verified" for f in findings))

    def test_scan_json(self):
        self.write("a.py", "def lonely():\n    return 2\n")
        rc, out = run("--dir", self.d, "scan", self.proj, "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("deadcode", data)
        self.assertIn("unused_deps", data)


if __name__ == "__main__":
    unittest.main()
