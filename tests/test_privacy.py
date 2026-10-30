import contextlib
import io
import os
import unittest

from _load import TempProof, load_tool

privacy = load_tool("proof-privacy", "privacy.py")
ledger = load_tool("proof-core", "ledger.py")

BAD_PAGE = """<html><head>
<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
<script src="https://connect.facebook.net/en_US/fbevents.js"></script>
<script src="https://plausible.io/js/script.js"></script>
</head><body><h1>Shop</h1>
<form action="http://shop.example/signup" method="post"><input type="email" name="email"><input type="password" name="password"></form>
</body></html>"""

GOOD_PAGE = """<html><head>
<script type="text/plain" data-consent="analytics" src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
<script src="/app.js"></script>
</head><body><div id="banner">We use cookies. <button>Accept</button> <button>Reject</button></div>
<form action="/signup" method="post"><input type="email" name="email"></form>
<footer><a href="/privacy">Privacy policy</a></footer></body></html>"""

HEADERS = "HTTP/1.1 200 OK\nSet-Cookie: session=abc; HttpOnly\nSet-Cookie: _ga=GA1.2.3; Path=/\nSet-Cookie: _fbp=fb.1; Path=/\n\n"

URLS = """https://shop.example/search?q=shoes
https://shop.example/reset?email=dana@example.test&step=2
https://shop.example/track?phone=+44 7700 900123
https://shop.example/reset?email=other@example.test&step=2
"""


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = privacy.main(list(argv))
    return rc, out.getvalue()


class Privacy(unittest.TestCase):
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

    def test_bad_page(self):
        r = privacy.analyse_page(BAD_PAGE, "shop.example")
        doms = {t["domain"] for t in r["ungated_trackers"]}
        self.assertIn("googletagmanager.com", doms)
        self.assertIn("connect.facebook.net", doms)
        self.assertNotIn("plausible.io", doms)  # cookieless is not flagged
        self.assertFalse(r["consent_banner"])
        self.assertFalse(r["privacy_policy_link"])
        self.assertEqual(len(r["insecure_forms"]), 1)

    def test_good_page(self):
        r = privacy.analyse_page(GOOD_PAGE, "shop.example")
        self.assertEqual(r["ungated_trackers"], [])
        self.assertTrue(r["consent_banner"])
        self.assertTrue(r["privacy_policy_link"])
        self.assertEqual(r["insecure_forms"], [])

    def test_cookies(self):
        cookies = {c["name"]: c["nonessential"] for c in privacy.analyse_cookies(HEADERS)}
        self.assertFalse(cookies["session"])
        self.assertTrue(cookies["_ga"])
        self.assertTrue(cookies["_fbp"])

    def test_urls_dedup(self):
        leaks = privacy.analyse_urls(URLS.splitlines())
        paths = {(l["path"], l["param"]) for l in leaks}
        self.assertEqual(paths, {("/reset", "email"), ("/track", "phone")})

    def test_record(self):
        page = self.file("home.html", BAD_PAGE)
        rc, out = run("--dir", self.d, "record", page, "--host", "shop.example",
                      "--headers", self.file("h.txt", HEADERS), "--urls", self.file("u.txt", URLS))
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-privacy")
        cats = {f["category"] for f in fs}
        for c in ("privacy:trackers-before-consent", "privacy:consent-banner", "privacy:privacy-policy",
                  "privacy:insecure-forms", "privacy:first-load-cookies", "privacy:pii-in-urls"):
            self.assertIn(c, cats)
        self.assertTrue(all(f["status"] == "verified" for f in fs))
        cov = ledger.load_coverage(self.d, "proof-privacy")
        self.assertTrue(all(c["status"] == "covered" for c in cov["checks"]))

    def test_record_good_page_without_extras(self):
        rc, out = run("--dir", self.d, "record", self.file("g.html", GOOD_PAGE), "--host", "shop.example")
        self.assertEqual(rc, 0)
        self.assertEqual(ledger.load_findings(self.d, "proof-privacy"), [])
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-privacy")["checks"]}
        self.assertEqual(cov["pii-in-urls"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
