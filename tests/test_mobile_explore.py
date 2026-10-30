import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

mx = load_tool("proof-mobile-explore", "mexplore.py")
ledger = load_tool("proof-core", "ledger.py")

LOGCAT = """10-30 12:00:01.100  1234  1234 I ActivityManager: Start proc com.example.shop
10-30 12:00:05.200  1234  1234 E AndroidRuntime: FATAL EXCEPTION: main
10-30 12:00:05.200  1234  1234 E AndroidRuntime: Process: com.example.shop, PID: 1234
10-30 12:00:05.200  1234  1234 E AndroidRuntime: java.lang.NullPointerException: cart was null
10-30 12:00:05.200  1234  1234 E AndroidRuntime:     at com.example.shop.Cart.total(Cart.kt:42)
10-30 12:01:00.000   900   910 E ActivityManager: ANR in com.example.shop (com.example.shop/.CheckoutActivity)
10-30 12:02:00.000  1300  1300 F libc    : Fatal signal 11 (SIGSEGV), code 1, fault addr 0x0 in tid 1300
"""

CLEAN_LOGCAT = "10-30 12:00:01.100  1234  1234 I ActivityManager: Start proc com.example.shop\n"

IPS = ('{"app_name":"Shop","bug_type":"309","timestamp":"2026-10-30"}\n'
       '{"procName":"Shop","exception":{"type":"EXC_CRASH","signal":"SIGABRT"}}')


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = mx.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class MobileExplore(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.run_dir = os.path.join(self.tp.base, "run")
        os.makedirs(os.path.join(self.run_dir, "shots"))

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, rel, text):
        p = os.path.join(self.run_dir, rel)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return p

    def session(self, goals):
        for g in goals:
            for st in g.get("steps", []):
                if st.get("screenshot"):
                    self.file(st["screenshot"], "png")
        return self.file("session.json", json.dumps({"app": "com.example.shop", "platform": "android",
                                                     "driver": "mobile-mcp", "goals": goals}))

    def test_parse_logcat(self):
        found = mx.parse_logcat(LOGCAT)
        kinds = [c["kind"] for c in found]
        self.assertEqual(kinds, ["crash", "anr", "crash"])
        self.assertEqual(found[0]["process"], "com.example.shop")
        self.assertIn("NullPointerException", found[0]["detail"])
        self.assertTrue(found[1]["process"].startswith("com.example.shop"))
        self.assertIn("SIGSEGV", found[2]["detail"])
        self.assertEqual(mx.parse_logcat(CLEAN_LOGCAT), [])

    def test_parse_ips(self):
        c = mx.parse_ips(IPS)
        self.assertEqual(c["process"], "Shop")
        self.assertIn("SIGABRT", c["detail"])
        self.assertIsNone(mx.parse_ips("not json"))

    def test_logs_command(self):
        rc, out = run("--dir", self.d, "logs", self.file("logcat.txt", LOGCAT))
        self.assertEqual(rc, 0)
        self.assertIn("[anr]", out)
        rc, out = run("--dir", self.d, "logs", self.file("Shop.ips", IPS))
        self.assertIn("SIGABRT", out)

    def test_plan_writes_brief_and_coverage(self):
        rc, out = run("--dir", self.d, "plan", "--app", "com.example.shop", "--goal", "sign up",
                      "--goal", "check out", "--platform", "ios")
        self.assertEqual(rc, 0, out)
        brief = os.path.join(self.d, "evidence", "proof-mobile-explore", "brief.md")
        with open(brief, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("1. sign up", text)
        self.assertIn("(ios)", text)
        self.assertIn("own app", text)
        cov = ledger.load_coverage(self.d, "proof-mobile-explore")
        self.assertEqual([c["id"] for c in cov["checks"]], ["goal-1", "goal-2"])
        self.assertNotEqual(run("--dir", self.d, "plan", "--app", "x")[0], 0)

    def test_record(self):
        s = self.session([
            {"goal": "sign up", "outcome": "reached",
             "steps": [{"action": "tap Sign up", "screenshot": "shots/01.png"}]},
            {"goal": "check out", "outcome": "dead_end", "note": "Pay button does nothing",
             "steps": [{"action": "tap Pay", "screenshot": "shots/07.png", "note": "no response"}]},
            {"goal": "ask the assistant", "outcome": "crash",
             "steps": [{"action": "send a question", "screenshot": "shots/09.png"}]},
            {"goal": "log in with Google", "outcome": "blocked", "note": "no test account", "steps": []},
        ])
        rc, out = run("--dir", self.d, "record", "--session", s, "--logcat", self.file("logcat.txt", LOGCAT),
                      "--crash-report", self.file("Shop.ips", IPS))
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-mobile-explore")
        sev = sorted(f["severity"] for f in fs)
        self.assertEqual(sev.count("high"), 5)  # goal crash + logcat crash, ANR, SIGSEGV + ips crash
        self.assertIn("medium", sev)
        self.assertIn("low", sev)
        self.assertTrue(all(f["status"] == "verified" for f in fs))
        dead = [f for f in fs if "dead end" in f["title"]][0]
        self.assertIn("Pay button does nothing", dead["title"])
        self.assertTrue(dead["evidence"][0].endswith("shots/07.png"))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-mobile-explore")["checks"]}
        self.assertEqual(cov, {"goal-1": "covered", "goal-2": "covered", "goal-3": "covered",
                               "goal-4": "covered", "device-logs": "covered"})
        ev = os.path.join(self.d, "evidence", "proof-mobile-explore")
        self.assertTrue(os.path.isfile(os.path.join(ev, "shots", "07.png")))
        self.assertTrue(os.path.isfile(os.path.join(ev, "logs", "logcat.txt")))

    def test_record_all_reached_is_clean(self):
        s = self.session([{"goal": "sign up", "outcome": "reached",
                           "steps": [{"action": "tap", "screenshot": "shots/01.png"}]}])
        rc, out = run("--dir", self.d, "record", "--session", s, "--logcat", self.file("logcat.txt", CLEAN_LOGCAT))
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, "proof-mobile-explore"), [])

    def test_record_rejects_a_bad_session(self):
        s = self.file("session.json", json.dumps({"goals": [{"goal": "x", "outcome": "maybe"}]}))
        rc, out = run("--dir", self.d, "record", "--session", s)
        self.assertNotEqual(rc, 0)
        self.assertIn("outcome must be one of", out)


if __name__ == "__main__":
    unittest.main()
