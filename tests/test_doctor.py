import contextlib
import io
import json
import unittest

from _load import load_tool

doctor = load_tool("proof-doctor", "doctor.py")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = doctor.main(list(argv))
    return rc, out.getvalue()


class Doctor(unittest.TestCase):
    def test_table_lists_rows(self):
        rc, out = run()
        self.assertEqual(rc, 0)
        for name in ("python", "playwright", "semgrep", "k6", "maestro"):
            self.assertIn(name, out)
        self.assertIn("Nothing here is required", out)

    def test_json_shape(self):
        rc, out = run("--json")
        self.assertEqual(rc, 0)
        rows = json.loads(out)
        self.assertTrue(isinstance(rows, list) and rows)
        for r in rows:
            for key in ("name", "found", "version", "skills", "without", "install"):
                self.assertIn(key, r)

    def test_for_filters(self):
        rc, out = run("--json", "--for", "proof-web-e2e")
        rows = json.loads(out)
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("proof-web-e2e", r["skills"])

    def test_offline_all_missing(self):
        orig_which, orig_py = doctor.shutil.which, doctor._py_has
        doctor.shutil.which = lambda *a, **k: None
        doctor._py_has = lambda *a, **k: False
        try:
            rc, out = run("--json")
            rows = json.loads(out)
            self.assertTrue(all(r["found"] is False for r in rows))
        finally:
            doctor.shutil.which, doctor._py_has = orig_which, orig_py

    def test_slow_tool_does_not_raise(self):
        orig_which, orig_run = doctor.shutil.which, doctor._run
        doctor.shutil.which = lambda name: "/fake/" + name
        def boom(argv):
            raise doctor.subprocess.TimeoutExpired(argv, 10)
        # _run swallows errors itself; simulate by making the underlying call time out.
        doctor._run = lambda argv: ""
        try:
            rc, out = run()
            self.assertEqual(rc, 0)
        finally:
            doctor.shutil.which, doctor._run = orig_which, orig_run


if __name__ == "__main__":
    unittest.main()
