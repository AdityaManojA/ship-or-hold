import contextlib
import io
import os
import unittest

from _load import TempProof, load_tool

diffscope = load_tool("proof-sec-diff", "diffscope.py")
ledger = load_tool("proof-core", "ledger.py")

DIFF = """diff --git a/app/auth.py b/app/auth.py
index 111..222 100644
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,2 +1,3 @@
+def check(user):
+    if user.is_admin or user.role == 'owner':
+        cursor.execute("select * from t where id = %s" % user_id)
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,1 @@
+just docs here
"""


def run(*argv, stdin=None):
    out = io.StringIO()
    old = diffscope.sys.stdin
    if stdin is not None:
        diffscope.sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = diffscope.main(list(argv))
    finally:
        diffscope.sys.stdin = old
    return rc, out.getvalue()


class DiffScope(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_parse_diff(self):
        files = diffscope.parse_diff(DIFF)
        names = {f["file"] for f in files}
        self.assertEqual(names, {"app/auth.py", "README.md"})
        auth = next(f for f in files if f["file"] == "app/auth.py")
        self.assertEqual(auth["count"], 3)

    def test_classify_signals(self):
        files = diffscope.classify(diffscope.parse_diff(DIFF))
        auth = next(f for f in files if f["file"] == "app/auth.py")
        self.assertIn("authz", auth["signals"])
        self.assertIn("injection", auth["signals"])
        docs = next(f for f in files if f["file"] == "README.md")
        self.assertEqual(docs["signals"], [])

    def test_changed_stdin(self):
        rc, out = run("--dir", self.d, "changed", "--stdin", stdin=DIFF)
        self.assertEqual(rc, 0)
        self.assertIn("app/auth.py", out)
        self.assertIn("authz", out)

    def test_record_creates_review_checks(self):
        patch = os.path.join(self.tp.base, "c.patch")
        with open(patch, "w", encoding="utf-8") as fh:
            fh.write(DIFF)
        rc, out = run("--dir", self.d, "record", "--diff", patch)
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-sec-diff")
        ids = {c["id"] for c in cov["checks"]}
        self.assertIn("changed-files", ids)
        self.assertIn("review-authz", ids)
        self.assertIn("review-injection", ids)
        self.assertEqual(next(c for c in cov["checks"] if c["id"] == "changed-files")["status"], "covered")


if __name__ == "__main__":
    unittest.main()
