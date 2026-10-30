import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

geo = load_tool("proof-geo", "geo_score.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = geo.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


RICH = """<!doctype html><html lang="en"><head>
<title>How time tracking works | Larkspur</title>
<meta name="author" content="Dana Lee">
<meta property="article:published_time" content="2026-02-01">
<script type="application/ld+json">{"@type":"Article","author":{"@type":"Person","name":"Dana Lee"},"datePublished":"2026-02-01","publisher":{"@type":"Organization","name":"Larkspur"}}</script>
</head><body>
<h1>How time tracking works</h1>
<h2>What counts as billable time?</h2>
<p>Larkspur records billable time from calendar events and git commits. In 2026 a typical design studio logged 1,240 hours across 18 clients, and Larkspur rounded each session to the nearest 6 minutes, which is the standard increment most studios use for invoicing their clients fairly.</p>
<h2>How are invoices produced?</h2>
<p>Each client has a rate. Larkspur multiplies the rounded hours by the rate, groups them by project, and produces an invoice. A studio billing 1,240 hours at 95 dollars generated about 117,800 dollars in a year, and the figure reconciles against the accounting export within 2 percent.</p>
</body></html>"""

THIN = """<!doctype html><html><head><title>Larkspur</title></head>
<body><div id="root"></div><noscript>You need to enable JavaScript to run this app.</noscript></body></html>"""


class Geo(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def _page(self, html, name="p"):
        path = os.path.join(self.tp.base, name + ".html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return path

    def test_rich_beats_thin(self):
        rich = geo.analyse_page(RICH, "rich")
        thin = geo.analyse_page(THIN, "thin")
        self.assertGreater(rich["score"], thin["score"])
        self.assertGreater(rich["score"], 55)
        self.assertLess(thin["score"], 35)

    def test_detects_ld_json_type(self):
        page = geo.analyse_page(RICH, "r")
        self.assertIn("Article", page["facts"]["ld_json_types"])
        self.assertTrue(page["facts"]["author"])
        self.assertTrue(page["facts"]["date"])

    def test_empty_shell_flagged(self):
        page = geo.analyse_page(THIN, "t")
        stable = next(s for s in page["signals"] if s["id"] == "stable")
        self.assertLess(stable["share"], 0.5)
        self.assertTrue(page["facts"]["noscript_warning"])

    def test_invalid_ld_json(self):
        html = '<html><head><title>X</title><script type="application/ld+json">{bad}</script></head><body><h1>X</h1></body></html>'
        page = geo.analyse_page(html, "x")
        structured = next(s for s in page["signals"] if s["id"] == "structured")
        self.assertLess(structured["share"], 0.5)

    def test_robots_blocks_named_crawler(self):
        robots = "User-agent: PerplexityBot\nDisallow: /\n\nUser-agent: *\nDisallow:\n"
        acc = geo.analyse_access(robots, None)
        perp = next(c for c in acc["crawlers"] if c["name"] == "PerplexityBot")
        self.assertFalse(perp["allowed"])
        other = next(c for c in acc["crawlers"] if c["name"] == "OAI-SearchBot")
        self.assertTrue(other["allowed"])

    def test_robots_blocks_all(self):
        acc = geo.analyse_access("User-agent: *\nDisallow: /\n", None)
        self.assertEqual(acc["answers_allowed"], 0)

    def test_llms_wellformed(self):
        good = "# Site\n\n> A one line summary.\n\n## Docs\n- [Start](https://x.example/start): how to begin.\n"
        acc = geo.analyse_access(None, good)
        self.assertTrue(acc["llms"]["wellformed"])
        bad = geo.analyse_access(None, "just some text, no title, no links")
        self.assertFalse(bad["llms"]["wellformed"])

    def test_record_writes_score_and_findings(self):
        path = self._page(RICH, "pricing")
        rc, out = run("--dir", self.d, "record", path, "--budget", "60")
        self.assertEqual(rc, 0, out)
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-geo"][0]
        self.assertEqual(scores["metrics"][0]["id"], "geo_citability")
        cov = ledger.load_coverage(self.d, "proof-geo")
        self.assertTrue(all(c["status"] in ("covered", "not_applicable") for c in cov["checks"]))
        # the page was copied under proof/
        self.assertTrue(os.path.exists(os.path.join(self.d, "evidence", "proof-geo", "pricing.html")))

    def test_record_findings_verified(self):
        path = self._page(THIN, "thin")
        run("--dir", self.d, "record", path, "--budget", "70")
        fs = ledger.load_findings(self.d, "proof-geo")
        self.assertTrue(fs)
        self.assertTrue(all(f["status"] == "verified" for f in fs))

    def test_example_file_is_present_and_scores(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ex = os.path.join(here, "skills", "proof-geo", "page.example.html")
        if os.path.exists(ex):
            with open(ex, encoding="utf-8") as fh:
                page = geo.analyse_page(fh.read(), "example")
            self.assertIsInstance(page["score"], int)


if __name__ == "__main__":
    unittest.main()
