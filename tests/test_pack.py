import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

pack = load_tool("proof-audit-trail", "pack.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = pack.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Pack(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        ledger.write_target(self.d, {"name": "Demo", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        self.tp.write("evidence/proof-web-e2e/login.png", b"PNGDATA")
        with open(os.path.join(self.d, "verdict.json"), "w", encoding="utf-8") as fh:
            json.dump({"verdict": "Ship"}, fh)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def _pack_dir(self):
        audit = os.path.join(self.d, "audit")
        runs = [os.path.join(audit, r) for r in os.listdir(audit)]
        return runs[0]

    def test_build_and_verify(self):
        rc, out = run("build", "--dir", self.d)
        self.assertEqual(rc, 0, out)
        self.assertIn("Ship", out)
        p = self._pack_dir()
        self.assertTrue(os.path.exists(os.path.join(p, "manifest.json")))
        self.assertTrue(os.path.exists(os.path.join(p, "target.json")))
        with open(os.path.join(p, "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        self.assertEqual(man["verdict"], "Ship")
        self.assertTrue(any(f["path"].endswith("login.png") for f in man["files"]))
        # verify is clean
        rc, out = run("verify", p)
        self.assertEqual(rc, 0, out)
        self.assertIn("intact", out)

    def test_verify_detects_tampering(self):
        run("build", "--dir", self.d)
        p = self._pack_dir()
        # change a packed file
        with open(os.path.join(p, "target.json"), "a", encoding="utf-8") as fh:
            fh.write("\n ")
        rc, out = run("verify", p)
        self.assertEqual(rc, 1)
        self.assertIn("changed", out)

    def test_pack_excludes_itself(self):
        run("build", "--dir", self.d)
        run("build", "--dir", self.d)  # second pack
        p = self._pack_dir()
        with open(os.path.join(p, "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        self.assertFalse(any(f["path"].startswith("audit/") for f in man["files"]))


if __name__ == "__main__":
    unittest.main()
