import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

from _load import TempProof, load_tool

ledger = load_tool("proof-core", "ledger.py")
watch = load_tool("proof-watch", "watch.py")

PY = '"%s"' % sys.executable


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = watch.main(argv)
    return rc, out.getvalue() + err.getvalue()


def rows(d):
    return watch.load_history(d)[0]


class Watch(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def base(self):
        ledger.write_target(self.d, {"name": "Demo", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        ledger.init_coverage(self.d, "proof-web-e2e", [("login", "Login")])
        self.tp.write("evidence/proof-web-e2e/login.png", b"png")
        ledger.mark_coverage(self.d, "proof-web-e2e", "login", "covered",
                             evidence=["evidence/proof-web-e2e/login.png"])

    def add_high(self):
        f = ledger.new_finding("proof-web-e2e", "Login accepts empty password", "high",
                               evidence=["evidence/proof-web-e2e/login.png"])
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-web-e2e", f["id"], by="evidence-file")
        return f["id"]

    def test_snapshot_appends_row_with_counts(self):
        self.base()
        fid = self.add_high()
        low = ledger.new_finding("proof-web-e2e", "Unverified low", "low")
        ledger.write_finding(self.d, low)  # unverified: must not count
        rc, out = run(["snapshot", "--dir", self.d, "--label", "first"])
        self.assertEqual(rc, 0, out)
        self.assertIn("Not yet", out)
        hist = rows(self.d)
        self.assertEqual(len(hist), 1)
        row = hist[0]
        self.assertEqual(row["label"], "first")
        self.assertEqual(row["verdict"], "Not yet")
        self.assertEqual(row["findings"], {"critical": 0, "high": 1, "medium": 0, "low": 0})
        self.assertEqual(row["gaps"], 0)
        self.assertEqual(row["failing_scores"], 0)
        self.assertEqual(row["fingerprint"], ["proof-web-e2e/" + fid])
        run(["snapshot", "--dir", self.d])
        self.assertEqual(len(rows(self.d)), 2)

    def test_snapshot_counts_gaps_and_failing_scores(self):
        self.base()
        ledger.write_scores(self.d, "proof-web-e2e", [
            {"id": "lcp", "value": 4000, "unit": "ms", "budget": 2500, "op": "<="}])
        run(["snapshot", "--dir", self.d])
        row = rows(self.d)[0]
        self.assertEqual(row["failing_scores"], 1)
        self.assertEqual(row["verdict"], "Not yet")
        with TempProof() as empty:
            run(["snapshot", "--dir", empty.dir])
            r = rows(empty.dir)[0]
            self.assertEqual(r["verdict"], "Incomplete")
            self.assertGreater(r["gaps"], 0)

    def test_history_marks_verdict_change(self):
        self.base()
        run(["snapshot", "--dir", self.d, "--label", "clean"])
        self.add_high()
        run(["snapshot", "--dir", self.d, "--label", "broken"])
        rc, out = run(["history", "--dir", self.d])
        self.assertEqual(rc, 0, out)
        self.assertIn("verdict Ship -> Not yet", out)
        self.assertIn("findings +1", out)
        self.assertIn("0/1/0/0", out)
        md = os.path.join(self.d, "watch", "history.md")
        with open(md, encoding="utf-8") as fh:
            self.assertIn("verdict Ship -> Not yet", fh.read())
        rc, out = run(["history", "--dir", self.d, "--last", "1", "--json"])
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["label"], "broken")
        self.assertIn("verdict Ship -> Not yet", data[0]["changes"])

    def test_history_empty(self):
        rc, out = run(["history", "--dir", self.d])
        self.assertEqual(rc, 0)
        self.assertIn("No snapshots yet", out)

    def test_run_executes_logs_and_returns_verdict_code(self):
        self.base()
        rc, out = run(["run", "--dir", self.d, "--cmd", PY + ' -c "print(4242)"', "--timeout", "30"])
        self.assertEqual(rc, 0, out)  # Ship
        log = os.path.join(self.d, "evidence", "proof-watch", "run-1.log")
        with open(log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("4242", text)
        self.assertIn("[exit 0", text)
        row = rows(self.d)[-1]
        self.assertEqual(row["log"], "evidence/proof-watch/run-1.log")
        self.assertEqual(row["commands"][0]["exit"], 0)
        self.add_high()
        rc, out = run(["run", "--dir", self.d, "--cmd", PY + ' -c "print(1)"'])
        self.assertEqual(rc, 1, out)  # Not yet
        self.assertTrue(os.path.exists(os.path.join(self.d, "evidence", "proof-watch", "run-2.log")))

    def test_run_incomplete_and_failed_command(self):
        rc, out = run(["run", "--dir", self.d, "--cmd", PY + ' -c "import sys; sys.exit(3)"'])
        self.assertEqual(rc, 2, out)  # no target: Incomplete
        self.assertEqual(rows(self.d)[-1]["commands"][0]["exit"], 3)

    def test_fail_on_cmd_turns_ship_into_1(self):
        self.base()
        cmd = PY + ' -c "import sys; sys.exit(5)"'
        self.assertEqual(run(["run", "--dir", self.d, "--cmd", cmd])[0], 0)
        self.assertEqual(run(["run", "--dir", self.d, "--cmd", cmd, "--fail-on-cmd"])[0], 1)

    def test_run_timeout_kills_command(self):
        self.base()
        start = time.time()
        rc, out = run(["run", "--dir", self.d, "--cmd", PY + ' -c "import time; time.sleep(30)"',
                       "--timeout", "1"])
        self.assertLess(time.time() - start, 15)
        cmd = rows(self.d)[-1]["commands"][0]
        self.assertTrue(cmd["timed_out"])
        self.assertNotEqual(cmd["exit"], 0)

    def test_watch_detects_change(self):
        self.base()
        src = tempfile.mkdtemp(prefix="proof-watch-src-")
        try:
            target = os.path.join(src, "app.py")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            os.makedirs(os.path.join(src, "node_modules"))

            def touch():
                time.sleep(0.8)
                with open(os.path.join(src, "node_modules", "skip.js"), "w", encoding="utf-8") as fh:
                    fh.write("ignored")
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write("x = 2  # changed\n")

            t = threading.Thread(target=touch)
            t.start()
            start = time.time()
            rc, out = run(["watch", "--dir", self.d, "--path", src, "--cmd", PY + ' -c "print(7)"',
                           "--interval", "0.2", "--debounce", "0.2", "--max-runs", "1"])
            t.join()
            self.assertLess(time.time() - start, 12)
            self.assertEqual(rc, 0, out)
            row = rows(self.d)[-1]
            self.assertIn("app.py", row["label"])
            self.assertNotIn("skip.js", row["label"])
            self.assertEqual(row["run"], 1)
        finally:
            shutil.rmtree(src, ignore_errors=True)

    def test_fingerprint_skips_ignored(self):
        src = tempfile.mkdtemp(prefix="proof-watch-fp-")
        try:
            for rel in ("a.txt", os.path.join(".git", "HEAD"), os.path.join("proof", "x.json"),
                        os.path.join("build", "out.bin")):
                full = os.path.join(src, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write("1")
            fp = watch.tree_fingerprint(src, {os.path.join(src, "proof")}, {"build"})
            self.assertEqual(list(fp), ["a.txt"])
        finally:
            shutil.rmtree(src, ignore_errors=True)

    def test_watch_rejects_bad_args(self):
        self.assertEqual(run(["watch", "--dir", self.d, "--path", os.path.join(self.d, "nope"),
                              "--cmd", "x", "--max-runs", "1"])[0], 2)
        self.assertEqual(run(["watch", "--dir", self.d, "--cmd", "x", "--max-runs", "0"])[0], 2)


if __name__ == "__main__":
    unittest.main()
