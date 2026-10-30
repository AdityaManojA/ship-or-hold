import base64
import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

auth = load_tool("proof-auth", "auth.py")
ledger = load_tool("proof-core", "ledger.py")


def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def jwt(header, claims, sig="c2lnbmF0dXJl"):
    return "%s.%s.%s" % (b64(header), b64(claims), sig)


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = auth.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


GOOD = jwt({"alg": "RS256", "typ": "JWT"}, {"sub": "u1", "iat": 1000, "exp": 1000 + 900})
BAD = jwt({"alg": "none"}, {"sub": "u1", "password": "hunter2"}, sig="")
LONG = jwt({"alg": "HS256"}, {"sub": "u1", "iat": 0, "exp": 30 * 86400})

HEADERS_BAD = ("HTTP/1.1 200 OK\nSet-Cookie: session=abc123; Path=/\n"
               "Set-Cookie: theme=dark; Path=/\nSet-Cookie: refresh_token=x; HttpOnly; Secure; SameSite=Lax; "
               "Max-Age=31536000\n\n")
HEADERS_GOOD = "HTTP/1.1 200 OK\nSet-Cookie: sid=abc; HttpOnly; Secure; SameSite=Lax; Max-Age=3600\n\n"


class Auth(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, text):
        p = os.path.join(self.tp.base, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return p

    def authorise(self, ok=True):
        ledger.write_target(self.d, {"name": "App", "kind": "web", "authorised": ok, "required_skills": []})

    def test_jwt_good_bad_and_long(self):
        self.assertEqual(auth.analyse_jwt("Bearer " + GOOD)["problems"], [])
        bad = auth.analyse_jwt(BAD)
        sevs = [s for s, _ in bad["problems"]]
        self.assertIn("high", sevs)
        text = " ".join(m for _, m in bad["problems"])
        self.assertIn("unsigned", text)
        self.assertIn("no exp", text)
        self.assertIn("password", text)
        long_ = auth.analyse_jwt(LONG)
        self.assertTrue(any("hours" in m for _, m in long_["problems"]))
        self.assertTrue(any("shared secret" in m for _, m in long_["problems"]))

    def test_jwt_never_prints_full_token(self):
        rc, out = run("--dir", self.d, "jwt", self.file("t.txt", GOOD))
        self.assertEqual(rc, 0, out)
        self.assertNotIn(GOOD, out)
        self.assertIn(GOOD[:4] + "...", out)

    def test_cookies(self):
        cs = {c["name"]: c["problems"] for c in auth.analyse_cookies(HEADERS_BAD)}
        self.assertNotIn("theme", cs)
        self.assertEqual({s for s, _ in cs["session"]} >= {"high", "medium"}, True)
        self.assertTrue(any("Max-Age" in m for _, m in cs["refresh_token"]))
        self.assertEqual(auth.analyse_cookies(HEADERS_GOOD)[0]["problems"], [])

    def test_enum(self):
        a = "HTTP/1.1 404 Not Found\n\n{\"error\": \"No account with that email\"}"
        b = "HTTP/1.1 401 Unauthorized\n\n{\"error\": \"Wrong password\"}"
        diffs = auth.analyse_enum(a, b)
        self.assertTrue(any("status" in d for d in diffs))
        same = "HTTP/1.1 401 Unauthorized\n\n{\"error\": \"Email or password is wrong\", \"id\": \"req-1234abcd99\"}"
        same2 = "HTTP/1.1 401 Unauthorized\n\n{\"error\": \"Email or password is wrong\", \"id\": \"req-9876fedc11\"}"
        self.assertEqual(auth.analyse_enum(same, same2), [])

    def test_ratelimit_text_and_json(self):
        lines = "\n".join("attempt %d -> 401" % i for i in range(1, 8)) + "\nattempt 8 -> 429\n"
        r = auth.analyse_ratelimit(lines, attempts=20)
        self.assertTrue(r["limited"])
        self.assertEqual(r["first_limited"], 8)
        never = json.dumps([{"status": 401}] * 25)
        r = auth.analyse_ratelimit(never, attempts=20)
        self.assertFalse(r["limited"])
        locked = json.dumps([{"status": 401}] * 5 + [{"status": 200, "body": "Too many attempts, try again later"}])
        self.assertTrue(auth.analyse_ratelimit(locked)["limited"])

    def test_record_requires_authorisation(self):
        self.authorise(False)
        rc, out = run("--dir", self.d, "jwt", self.file("t.txt", BAD), "--record")
        self.assertNotEqual(rc, 0)
        self.assertIn("authorised", out)

    def test_analysers_record_findings(self):
        self.authorise()
        rc, out = run("--dir", self.d, "jwt", self.file("bad.txt", BAD), "--record")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "cookies", self.file("h.txt", HEADERS_BAD), "--record")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "ratelimit", self.file("r.json", json.dumps([401] * 25)), "--record")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "enum", self.file("a.txt", "HTTP/1.1 404 x\n\nno user"),
                      self.file("b.txt", "HTTP/1.1 401 x\n\nwrong password"), "--record")
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-auth")
        cats = {f["category"] for f in fs}
        self.assertEqual(cats, {"auth:jwt-hygiene", "auth:cookie-flags", "auth:rate-limiting", "auth:enumeration"})
        self.assertTrue(all(f["status"] == "verified" for f in fs))
        for f in fs:
            self.assertNotIn(BAD, json.dumps(f))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-auth")["checks"]}
        self.assertEqual(cov["jwt-hygiene"], "covered")
        self.assertEqual(cov["mfa"], "not_run")

    def test_clean_capture_records_a_pass(self):
        self.authorise()
        rc, out = run("--dir", self.d, "cookies", self.file("g.txt", HEADERS_GOOD), "--record")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, "proof-auth"), [])
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-auth")["checks"]}
        self.assertEqual(cov["cookie-flags"], "covered")

    def test_short_capture_is_not_judged(self):
        self.authorise()
        rc, out = run("--dir", self.d, "ratelimit", self.file("few.txt", "401\n401\n401\n"), "--record")
        self.assertEqual(rc, 0, out)
        self.assertIn("capture more", out)
        self.assertEqual(ledger.load_findings(self.d, "proof-auth"), [])

    def test_plan_result_na_status(self):
        self.authorise()
        rc, out = run("--dir", self.d, "plan")
        self.assertEqual(rc, 0, out)
        self.assertIn("logout-invalidates", out)
        rc, out = run("--dir", self.d, "result", "logout-invalidates", "fail",
                      "--evidence", self.file("replay.txt", "GET /me -> 200 after logout"))
        self.assertNotEqual(rc, 0)
        self.assertIn("--title", out)
        rc, out = run("--dir", self.d, "result", "logout-invalidates", "fail", "--evidence",
                      self.file("replay.txt", "GET /me -> 200 after logout"),
                      "--title", "Old token works after logout", "--verify")
        self.assertEqual(rc, 0, out)
        f = ledger.load_findings(self.d, "proof-auth")[0]
        self.assertEqual((f["severity"], f["status"]), ("high", "verified"))
        rc, out = run("--dir", self.d, "result", "session-expiry", "pass",
                      "--evidence", self.file("exp.txt", "401 after idle"))
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "na", "mfa", "--note", "no second factor offered")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "na", "nope", "--note", "x")
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "status")
        self.assertIn("2 covered, 1 not applicable", out)

    def test_bad_inputs(self):
        rc, out = run("--dir", self.d, "jwt", self.file("x.txt", "no token here"))
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "ratelimit", self.file("e.txt", "nothing"))
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
