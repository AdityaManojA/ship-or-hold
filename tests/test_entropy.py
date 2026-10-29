import contextlib
import io
import os
import unittest

from _load import TempProof, load_tool

entropy = load_tool("proof-sec-secrets", "entropy.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = entropy.main(list(argv))
    return rc, out.getvalue()


class Entropy(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_mask_never_whole_secret(self):
        self.assertEqual(entropy.mask("abcdefghijklmnop"), "abcd" + "*" * 12)
        self.assertEqual(entropy.mask("short"), "*****")
        self.assertNotIn("efghijkl", entropy.mask("abcdefghijklmnop"))

    def test_pattern_detection(self):
        # A key-shaped string with no placeholder word in it.
        text = 'const k = "AKIAQ7R9ZB3MK2WX5TUV"\napi_key = "a9Xk28Lm01Qp77Rz04Tv"\n'
        hits = entropy.scan_text(text, "x.js")
        kinds = {h["pattern"] for h in hits}
        self.assertIn("aws-access-key", kinds)
        self.assertTrue(any(h["pattern"] in ("generic-assignment", "high-entropy") for h in hits))
        for h in hits:
            self.assertNotIn("Q7R9ZB3MK2WX5TUV", h["masked"])

    def test_placeholder_ignored(self):
        text = 'api_key = "your_api_key_here_please"\ntoken = "changeme_changeme_1234"\n'
        hits = entropy.scan_text(text, "x.env")
        self.assertEqual(hits, [])

    def test_private_key_header(self):
        hits = entropy.scan_text("-----BEGIN RSA PRIVATE KEY-----", "id_rsa")
        self.assertTrue(any(h["pattern"] == "private-key" for h in hits))

    def test_scan_tree_and_skip(self):
        self.tp.write("app.py", "secret = 'Zk38Xp91Lm44Qr07Tv22Ab'\n")
        self.tp.write(os.path.join("node_modules", "junk.js"), "key = 'AKIAIOSFODNN7EXAMPLE1'\n")
        findings = entropy._scan_path(self.d)
        files = {f["file"].replace(os.sep, "/") for f in findings}
        self.assertTrue(any("app.py" in f for f in files))
        self.assertFalse(any("node_modules" in f for f in files))

    def test_record_masks_and_writes_ledger(self):
        self.tp.write("conf.js", 'const token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"\n')
        rc, out = run("--dir", self.d, "record", self.d)
        self.assertEqual(rc, 0, out)
        findings = ledger.load_findings(self.d, "proof-sec-secrets")
        self.assertTrue(findings)
        # the evidence report exists and does not contain the full token
        ev = os.path.join(self.d, "evidence", "proof-sec-secrets", "secret-scan.txt")
        body = open(ev, encoding="utf-8").read()
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", body)
        self.assertEqual(findings[0]["status"], "unverified")

    def test_clean_tree(self):
        self.tp.write("readme.md", "# hello\nnothing secret here, just words and words.\n")
        rc, out = run("--dir", self.d, "scan", self.d)
        self.assertEqual(rc, 0)
        self.assertIn("no secret", out.lower())


if __name__ == "__main__":
    unittest.main()
