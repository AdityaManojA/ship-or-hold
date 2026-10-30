import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

res = load_tool("proof-resilience", "resilience.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = res.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


def entry(t, method, url, status=200, ms=100, body=None):
    req = {"method": method, "url": url}
    if body is not None:
        req["postData"] = {"mimeType": "application/json", "text": body}
    return {"startedDateTime": "2026-10-30T10:00:%06.3fZ" % t, "time": ms,
            "request": req, "response": {"status": status}}


def har(entries):
    return {"log": {"version": "1.2", "entries": entries}}


class Resilience(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_har(self, name, entries):
        p = os.path.join(self.tp.base, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(har(entries), fh)
        return p

    def test_plan_lists_scenarios(self):
        rc, out = run("--dir", self.d, "plan")
        self.assertEqual(rc, 0, out)
        ids = [c["id"] for c in ledger.load_coverage(self.d, "proof-resilience")["checks"]]
        self.assertEqual(len(ids), len(res.SCENARIOS))
        self.assertIn("double-submit", ids)
        self.assertNotIn("network-switch", ids)
        self.assertIn("route.fulfill", out)
        rc, out = run("--dir", self.d, "plan", "--mobile")
        ids = [c["id"] for c in ledger.load_coverage(self.d, "proof-resilience")["checks"]]
        self.assertIn("network-switch", ids)

    def test_har_finds_duplicates_storms_slow_and_cold_start(self):
        entries = [entry(0, "GET", "https://app.test/api/me", ms=9000)]
        entries += [entry(1 + i * 0.1, "GET", "https://app.test/api/feed", ms=50) for i in range(3)]
        entries += [entry(2.0, "POST", "https://app.test/api/order", body='{"id":1}'),
                    entry(2.4, "POST", "https://app.test/api/order", body='{"id":1}'),
                    entry(6.0, "POST", "https://app.test/api/order", body='{"id":2}')]
        entries.append(entry(10.0, "GET", "https://app.test/api/chat", status=503))
        entries += [entry(10.5 + i * 0.5, "GET", "https://app.test/api/chat", status=503) for i in range(7)]
        entries.append(entry(20.0, "GET", "https://app.test/api/slow", ms=15000))
        r = res.analyse_har(res.load_har(self.write_har("a.har", entries)))
        self.assertEqual(len(r["duplicates"]), 1)
        self.assertIn("/api/order", r["duplicates"][0]["endpoint"])
        self.assertEqual(len(r["storms"]), 1)
        self.assertIn("/api/chat", r["storms"][0]["endpoint"])
        self.assertEqual([s["ms"] for s in r["slow"]], [15000])
        self.assertTrue(r["cold_start"])

    def test_har_clean(self):
        entries = [entry(i, "GET", "https://app.test/api/x%d" % i) for i in range(5)]
        entries.append(entry(6, "POST", "https://app.test/api/send", body="a"))
        entries.append(entry(9, "POST", "https://app.test/api/send", body="a"))  # 3 s apart: not a duplicate
        r = res.analyse_har(res.load_har(self.write_har("ok.har", entries)))
        self.assertEqual((r["duplicates"], r["storms"], r["slow"], r["cold_start"]), ([], [], [], False))
        rc, out = run("har", self.write_har("ok2.har", entries), "--json")
        self.assertEqual(json.loads(out)["requests"], 7)

    def test_record_har_writes_verified_findings(self):
        p = self.write_har("dup.har", [entry(0, "POST", "https://app.test/api/pay", body="x"),
                                       entry(0.3, "POST", "https://app.test/api/pay", body="x")])
        run("--dir", self.d, "plan")
        rc, out = run("--dir", self.d, "record-har", p, "--scenario", "double-submit")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-resilience")
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["severity"], "high")
        self.assertEqual(fs[0]["status"], "verified")
        cov = dict((c["id"], c["status"]) for c in ledger.load_coverage(self.d, "proof-resilience")["checks"])
        self.assertEqual(cov["double-submit"], "covered")

    def test_result_pass_fail_and_na(self):
        run("--dir", self.d, "plan")
        rc, out = run("--dir", self.d, "result", "offline", "pass", "--evidence", "evidence/proof-resilience/none.png")
        self.assertNotEqual(rc, 0)
        self.tp.write("evidence/proof-resilience/offline.png", b"png")
        rc, out = run("--dir", self.d, "result", "offline", "pass", "--evidence", "evidence/proof-resilience/offline.png")
        self.assertEqual(rc, 0, out)
        self.tp.write("evidence/proof-resilience/500.png", b"png")
        rc, out = run("--dir", self.d, "result", "server-error", "fail", "--evidence", "evidence/proof-resilience/500.png")
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "result", "server-error", "fail", "--evidence", "evidence/proof-resilience/500.png",
                      "--title", "Spinner never stops on a 500", "--severity", "high", "--verify")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, "proof-resilience")[0]["status"], "verified")
        rc, out = run("--dir", self.d, "na", "token-expiry-midway", "--note", "no sign-in")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "status")
        self.assertIn("2 covered, 1 not applicable", out)

    def test_unknown_scenario_and_bad_har(self):
        rc, out = run("--dir", self.d, "result", "earthquake", "pass", "--evidence", "x")
        self.assertNotEqual(rc, 0)
        p = os.path.join(self.tp.base, "bad.har")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"nope": 1}')
        rc, out = run("har", p)
        self.assertNotEqual(rc, 0)
        self.assertIn("log.entries", out)


if __name__ == "__main__":
    unittest.main()
