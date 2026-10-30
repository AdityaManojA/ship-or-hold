import contextlib
import io
import json
import os
import struct
import unittest

from _load import TempProof, load_tool

pwa = load_tool("proof-pwa", "pwa.py")
ledger = load_tool("proof-core", "ledger.py")

GOOD_HTML = """<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#123456">
<meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<script>if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/sw.js'); }</script>
</head><body>App</body></html>"""

BAD_HTML = "<html><head><title>App</title></head><body>App</body></html>"

GOOD_MANIFEST = {
    "name": "Clear App", "short_name": "Clear", "id": "/", "start_url": "/", "scope": "/",
    "display": "standalone", "theme_color": "#123456", "background_color": "#ffffff",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}

BAD_MANIFEST = {"display": "browser", "icons": [{"src": "/icon.png", "sizes": "64x64"}]}

GOOD_SW = """const CACHE = 'v1';
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/', '/offline.html']))));
self.addEventListener('fetch', e => {
  e.respondWith(fetch(e.request).catch(() => caches.match('/offline.html')));
});
"""

CACHE_ONLY_SW = """self.addEventListener('install', e => e.waitUntil(caches.open('v1').then(c => c.addAll(['/']))));
self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));
"""

NO_FETCH_SW = "self.addEventListener('install', () => self.skipWaiting());\n"


def png(path, w, h):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = pwa.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Pwa(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def file(self, name, text):
        p = os.path.join(self.tp.base, "saved", name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text if isinstance(text, str) else json.dumps(text))
        return p

    def good_files(self):
        return ["--html", self.file("home.html", GOOD_HTML), "--manifest", self.file("manifest.json", GOOD_MANIFEST),
                "--sw", self.file("sw.js", GOOD_SW), "--url", "https://app.example/"]

    def test_png_size_reads_ihdr(self):
        p = os.path.join(self.tp.base, "i.png")
        png(p, 192, 192)
        self.assertEqual(pwa.png_size(p), (192, 192))
        self.assertIsNone(pwa.png_size(self.file("not.png", "text")))

    def test_good_html(self):
        res, _ = pwa.check_html(GOOD_HTML)
        self.assertEqual(res, {"manifest-linked": [], "ios": [], "meta": []})

    def test_bad_html(self):
        res, _ = pwa.check_html(BAD_HTML)
        self.assertTrue(res["manifest-linked"])
        self.assertEqual(len(res["ios"]), 2)
        self.assertEqual(len(res["meta"]), 2)

    def test_good_manifest(self):
        self.assertEqual(pwa.check_manifest(GOOD_MANIFEST), {"manifest-fields": [], "icons": []})

    def test_bad_manifest(self):
        res = pwa.check_manifest(BAD_MANIFEST)
        text = " ".join(res["manifest-fields"])
        for word in ("name", "start_url", "id", "scope", "theme_color", "background_color", "display"):
            self.assertIn(word, text)
        icons = " ".join(res["icons"])
        self.assertIn("192x192", icons)
        self.assertIn("512x512", icons)
        self.assertIn("maskable", icons)

    def test_icons_checked_against_the_files(self):
        public = os.path.join(self.tp.base, "public")
        png(os.path.join(public, "icon-192.png"), 192, 192)
        png(os.path.join(public, "icon-512.png"), 256, 256)  # lies about its size
        res = pwa.check_manifest(GOOD_MANIFEST, public)
        self.assertEqual(len(res["icons"]), 1)
        self.assertIn("256x256", res["icons"][0])
        os.remove(os.path.join(public, "icon-192.png"))
        res = pwa.check_manifest(GOOD_MANIFEST, public)
        self.assertTrue(any("not found" in p for p in res["icons"]))

    def test_service_worker(self):
        self.assertEqual(pwa.check_sw(GOOD_SW, [GOOD_HTML]), {"service-worker": [], "offline": []})
        res = pwa.check_sw(CACHE_ONLY_SW, [GOOD_HTML])
        self.assertIn("never answers from the cache", res["offline"][0])
        res = pwa.check_sw(NO_FETCH_SW, ["no register here"])
        self.assertEqual(len(res["service-worker"]), 2)
        self.assertIn("no offline fallback", res["offline"][0])

    def test_sw_headers(self):
        res = pwa.check_sw(GOOD_SW, None, "HTTP/1.1 200 OK\nContent-Type: text/html\nCache-Control: max-age=604800\n")
        self.assertEqual(len(res["service-worker"]), 2)
        res = pwa.check_sw(GOOD_SW, None, "Content-Type: application/javascript\nCache-Control: no-cache\n")
        self.assertEqual(res["service-worker"], [])

    def test_https(self):
        self.assertEqual(pwa.check_url("https://app.example"), [])
        self.assertEqual(pwa.check_url("http://localhost:3000"), [])
        self.assertEqual(pwa.check_url("http://app.test.localhost/"), [])
        self.assertTrue(pwa.check_url("http://app.example"))

    def test_check_good_scores_100(self):
        rc, out = run("--dir", self.d, "check", *self.good_files(), "--json")
        self.assertEqual(rc, 0, out)
        data = json.loads(out)
        self.assertEqual(data["score"], 100)
        self.assertTrue(all(v == [] for v in data["checks"].values()))

    def test_missing_inputs_are_not_checked_never_passed(self):
        rc, out = run("--dir", self.d, "check", "--manifest", self.file("m.json", GOOD_MANIFEST), "--json")
        data = json.loads(out)
        self.assertIsInstance(data["checks"]["manifest-linked"], str)
        self.assertIsInstance(data["checks"]["service-worker"], str)
        self.assertIsInstance(data["checks"]["https"], str)
        self.assertEqual(data["checks"]["manifest-fields"], [])

    def test_https_from_target(self):
        ledger.write_target(self.d, {"name": "App", "kind": "web", "authorised": True, "required_skills": [],
                                     "urls": ["http://app.example/"]})
        rc, out = run("--dir", self.d, "check", "--html", self.file("h.html", GOOD_HTML), "--json")
        self.assertTrue(json.loads(out)["checks"]["https"])

    def test_record_bad(self):
        rc, out = run("--dir", self.d, "record", "--html", self.file("home.html", BAD_HTML),
                      "--manifest", self.file("manifest.json", BAD_MANIFEST), "--sw", self.file("sw.js", NO_FETCH_SW),
                      "--url", "http://app.example/")
        self.assertEqual(rc, 0, out)
        fs = {f["category"]: f for f in ledger.load_findings(self.d, "proof-pwa")}
        for cid in ("manifest-linked", "manifest-fields", "icons", "service-worker", "offline", "https", "ios", "meta"):
            self.assertIn("pwa:%s" % cid, fs)
            self.assertEqual(fs["pwa:%s" % cid]["status"], "verified")
        self.assertEqual(fs["pwa:https"]["severity"], "high")
        m = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual(m["id"], "pwa-score")
        self.assertEqual(m["value"], 0)
        self.assertFalse(ledger.metric_passes(m))

    def test_record_good_then_rerun_drops_stale(self):
        bad = ["--html", self.file("bad.html", BAD_HTML), "--url", "https://app.example/"]
        run("--dir", self.d, "record", *bad)
        self.assertTrue(ledger.load_findings(self.d, "proof-pwa"))
        rc, out = run("--dir", self.d, "record", *self.good_files())
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, "proof-pwa"), [])
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-pwa")["checks"]}
        self.assertTrue(all(s == "covered" for s in cov.values()))
        self.assertEqual(ledger.load_all_scores(self.d)[0]["metrics"][0]["value"], 100)

    def test_record_marks_missing_not_applicable(self):
        rc, out = run("--dir", self.d, "record", "--html", self.file("home.html", GOOD_HTML))
        self.assertEqual(rc, 0, out)
        cov = {c["id"]: c for c in ledger.load_coverage(self.d, "proof-pwa")["checks"]}
        self.assertEqual(cov["manifest-fields"]["status"], "not_applicable")
        self.assertIn("--manifest", cov["manifest-fields"]["note"])
        self.assertEqual(cov["offline"]["status"], "not_applicable")
        self.assertEqual(cov["service-worker"]["status"], "covered")

    def test_registration_in_a_bundled_script(self):
        html = GOOD_HTML.replace("navigator.serviceWorker.register('/sw.js');", "")
        res, page = pwa.check_html(html)
        self.assertTrue(pwa.check_sw(GOOD_SW, page.scripts)["service-worker"])
        rc, out = run("--dir", self.d, "check", "--html", self.file("h.html", html), "--sw", self.file("sw.js", GOOD_SW),
                      "--script", self.file("main.js", "navigator.serviceWorker.register('/sw.js')"), "--json")
        self.assertEqual(json.loads(out)["checks"]["service-worker"], [])

    def test_missing_file_is_an_error(self):
        rc, out = run("--dir", self.d, "check", "--html", os.path.join(self.tp.base, "nope.html"))
        self.assertNotEqual(rc, 0)
        self.assertIn("no file at", out)


if __name__ == "__main__":
    unittest.main()
