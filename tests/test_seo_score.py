import contextlib
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

seo = load_tool("proof-seo", "seo_score.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = seo.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


WORDS = " ".join("word%d" % i for i in range(320))
LDJSON = '{"@context": "https://schema.org", "@type": "WebPage", "name": "Test page"}'


def make_page(title="A clear page title for the tests",
              description="A meta description that is comfortably longer than the fifty character minimum.",
              h1s=("Main heading",), headings='<h2>Second</h2><h3>Third</h3>',
              imgs=('<img src="a.png" alt="A">', '<img src="b.png" alt="">'),
              canonical='<link rel="canonical" href="https://example.test/page">',
              lang=' lang="en"', viewport=True, og=True, ldjson=LDJSON, links=5, words=WORDS):
    head = ['<meta charset="utf-8">']
    if title is not None:
        head.append("<title>%s</title>" % title)
    if description is not None:
        head.append('<meta name="description" content="%s">' % description)
    if viewport:
        head.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    if og:
        head += ['<meta property="og:title" content="T">', '<meta property="og:description" content="D">',
                 '<meta property="og:image" content="https://example.test/i.png">']
    if canonical:
        head.append(canonical)
    if ldjson is not None:
        head.append('<script type="application/ld+json">%s</script>' % ldjson)
    body = ["".join("<h1>%s</h1>" % h for h in h1s), headings]
    body += ['<a href="/p%d">link</a>' % i for i in range(links)]
    body.append('<a href="https://elsewhere.test/x">out</a>')
    body += list(imgs)
    body.append("<p>%s</p>" % words)
    return "<!DOCTYPE html><html%s><head>%s</head><body>%s</body></html>" % (lang, "".join(head), "".join(body))


class SeoScore(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def page_file(self, html, name="index.html", under_proof=True):
        if under_proof:
            rel = self.tp.write("evidence/proof-seo/" + name, html)
            return os.path.join(self.d, rel)
        path = os.path.join(self.tp.base, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return path

    def score(self, html, *extra):
        rc, out = run("--dir", self.d, "page", self.page_file(html), "--json", *extra)
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def check(self, result, cid):
        return next(c for c in result["checks"] if c["id"] == cid)

    # ------------------------------------------------------------ page checks

    def test_clean_page_scores_100(self):
        r = self.score(make_page())
        self.assertEqual(r["score"], 100)
        self.assertEqual([c["id"] for c in r["checks"] if c["status"] != "pass"], [])
        self.assertEqual(sum(c["weight"] for c in r["checks"]), 100)

    def test_missing_title(self):
        r = self.score(make_page(title=None))
        c = self.check(r, "title")
        self.assertEqual((c["status"], c["severity"], c["points"]), ("fail", "medium", 0))
        self.assertIn("missing", c["problem"])
        self.assertEqual(r["score"], 88)

    def test_title_too_long(self):
        r = self.score(make_page(title="x" * 70))
        c = self.check(r, "title")
        self.assertEqual((c["status"], c["severity"]), ("fail", "low"))
        self.assertIn("70 chars", c["problem"])

    def test_missing_meta_description(self):
        r = self.score(make_page(description=None))
        c = self.check(r, "meta-description")
        self.assertEqual((c["status"], c["severity"]), ("fail", "medium"))
        self.assertEqual(r["score"], 90)

    def test_h1_count(self):
        two = self.check(self.score(make_page(h1s=("One", "Two"))), "h1")
        self.assertEqual((two["status"], two["severity"]), ("fail", "low"))
        self.assertIn("2 h1", two["problem"])
        none = self.check(self.score(make_page(h1s=())), "h1")
        self.assertEqual((none["status"], none["severity"]), ("fail", "medium"))

    def test_missing_alt(self):
        r = self.score(make_page(imgs=('<img src="a.png" alt="A">', '<img src="photo.jpg">')))
        c = self.check(r, "img-alt")
        self.assertEqual((c["status"], c["severity"]), ("fail", "low"))
        self.assertIn("1 of 2", c["problem"])
        self.assertIn("photo.jpg", c["found"])
        self.assertEqual(r["score"], 90)

    def test_no_images_is_not_applicable_and_rescaled(self):
        r = self.score(make_page(imgs=()))
        self.assertEqual(self.check(r, "img-alt")["status"], "na")
        self.assertEqual((r["applicable"], r["earned"], r["score"]), (90, 90, 100))

    def test_heading_skip(self):
        r = self.score(make_page(headings="<h2>A</h2><h4>B</h4>"))
        c = self.check(r, "heading-order")
        self.assertEqual(c["status"], "fail")
        self.assertIn("h2 to h4", c["problem"])

    def test_invalid_ldjson_flagged(self):
        r = self.score(make_page(ldjson='{"@type": "WebPage", '))
        c = self.check(r, "structured-data")
        self.assertEqual(c["status"], "fail")
        self.assertIn("not valid JSON", c["problem"])
        self.assertEqual(self.check(self.score(make_page(ldjson=None)), "structured-data")["status"], "fail")

    def test_internal_links_use_url_host(self):
        html = make_page(links=0, canonical="").replace("<body>", '<body><a href="https://example.test/a">a</a>'
                                                                 '<a href="https://www.example.test/b">b</a>'
                                                                 '<a href="https://example.test/c">c</a>')
        without = self.check(self.score(html), "internal-links")
        self.assertEqual(without["status"], "fail")
        with_url = self.check(self.score(html, "--url", "https://example.test/"), "internal-links")
        self.assertEqual(with_url["status"], "pass")
        self.assertIn("3 internal", with_url["found"])

    def test_score_is_deterministic(self):
        path = self.page_file(make_page(title="short", imgs=('<img src="x.png">',)))
        rc1, out1 = run("--dir", self.d, "page", path, "--json")
        rc2, out2 = run("--dir", self.d, "page", path, "--json")
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertEqual(out1, out2)
        self.assertEqual(json.loads(out1)["score"], 100 - 12 - 10)

    def test_example_page_fails_exactly_two(self):
        example = os.path.join(SKILLS_DIR, "proof-seo", "page.example.html")
        rc, out = run("--dir", self.d, "page", example, "--json")
        self.assertEqual(rc, 0, out)
        r = json.loads(out)
        failed = sorted(c["id"] for c in r["checks"] if c["status"] == "fail")
        self.assertEqual(failed, ["heading-order", "img-alt"])
        self.assertEqual(r["score"], 84)
        rc, text = run("--dir", self.d, "page", example)
        self.assertEqual(rc, 0)
        self.assertIn("Score: 84/100", text)
        self.assertTrue(all(ord(ch) < 128 for ch in text))

    def test_checks_command_lists_weights(self):
        rc, out = run("checks")
        self.assertEqual(rc, 0)
        for cid in ("title", "meta-description", "img-alt", "robots-open", "sitemap-valid"):
            self.assertIn(cid, out)
        self.assertEqual(sum(c[2] for c in seo.CHECKS), 100)

    # ---------------------------------------------------------------- record

    def test_record_writes_scores_findings_and_coverage(self):
        path = self.page_file(make_page(title=None, imgs=('<img src="a.png">', '<img src="b.png">')), "about.html")
        rc, out = run("--dir", self.d, "record", path, "--url", "https://example.test/about")
        self.assertEqual(rc, 0, out)
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-seo"][0]
        by = dict((m["id"], m) for m in scores["metrics"])
        self.assertEqual((by["seo_score"]["value"], by["seo_score"]["unit"], by["seo_score"]["budget"], by["seo_score"]["op"]),
                         (78, "points", 80, ">="))
        self.assertEqual(by["seo_score:about"]["value"], 78)
        self.assertFalse(ledger.metric_passes(by["seo_score"]))
        findings = dict((f["id"], f) for f in ledger.load_findings(self.d, "proof-seo"))
        self.assertEqual(sorted(findings), ["seo-about-img-alt", "seo-about-title"])
        for f in findings.values():
            self.assertEqual((f["status"], f["verified_by"], f["category"]), ("verified", "manual", "seo"))
            self.assertEqual(f["location"], "https://example.test/about")
            self.assertTrue(f["evidence"])
            self.assertIn("seo_score.py", f["verified_note"])
        self.assertEqual(findings["seo-about-title"]["severity"], "medium")
        self.assertEqual(findings["seo-about-img-alt"]["severity"], "medium")  # no image has alt
        cov = ledger.load_coverage(self.d, "proof-seo")
        statuses = dict((c["id"], c["status"]) for c in cov["checks"])
        self.assertEqual(set(statuses.values()) - {"covered", "not_applicable"}, set())
        for cid in ("title", "img-alt", "h1", "word-count"):
            self.assertEqual(statuses[cid], "covered")
        self.assertEqual(statuses["lighthouse-seo"], "not_applicable")
        self.assertEqual(statuses["robots-present"], "not_applicable")

    def test_record_removes_stale_finding_and_keeps_lowest_score(self):
        path = self.page_file(make_page(title=None), "index.html")
        run("--dir", self.d, "record", path)
        self.assertEqual(len(ledger.load_findings(self.d, "proof-seo")), 1)
        # a second, better page: seo_score stays the lowest
        other = self.page_file(make_page(), "pricing.html")
        run("--dir", self.d, "record", other)
        by = dict((m["id"], m) for m in [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-seo"][0]["metrics"])
        self.assertEqual((by["seo_score"]["value"], by["seo_score:index"]["value"], by["seo_score:pricing"]["value"]), (88, 88, 100))
        # fix the first page: its finding goes, its score rises
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(make_page())
        rc, out = run("--dir", self.d, "record", path)
        self.assertEqual(rc, 0, out)
        self.assertIn("removed stale finding", out)
        self.assertEqual(ledger.load_findings(self.d, "proof-seo"), [])
        by = dict((m["id"], m) for m in [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-seo"][0]["metrics"])
        self.assertEqual(by["seo_score"]["value"], 100)

    def test_record_keeps_rejected_finding(self):
        path = self.page_file(make_page(title="short"), "index.html")
        run("--dir", self.d, "record", path)
        ledger.reject(self.d, "proof-seo", "seo-index-title", "the brand name is the title on purpose")
        rc, out = run("--dir", self.d, "record", path)
        self.assertEqual(rc, 0, out)
        self.assertIn("kept seo-index-title", out)
        self.assertEqual(ledger.load_finding(self.d, "proof-seo", "seo-index-title")["status"], "false_positive")

    def test_record_copies_page_from_outside_proof(self):
        path = self.page_file(make_page(), "landing.html", under_proof=False)
        rc, out = run("--dir", self.d, "record", path)
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", "proof-seo", "landing.html")))
        cov = ledger.load_coverage(self.d, "proof-seo")
        self.assertEqual(next(c for c in cov["checks"] if c["id"] == "title")["evidence"], ["evidence/proof-seo/landing.html"])

    def test_record_with_robots_and_sitemap(self):
        path = self.page_file(make_page(canonical='<link rel="canonical" href="https://example.test/">'), "index.html")
        robots = os.path.join(self.d, self.tp.write("evidence/proof-seo/robots.txt", "User-agent: *\nDisallow: /\n"))
        sitemap = os.path.join(self.d, self.tp.write("evidence/proof-seo/sitemap.xml", "<urlset><url><loc>https://example.test/</loc></url></urlset>"))
        rc, out = run("--dir", self.d, "record", path, "--robots", robots, "--sitemap", sitemap)
        self.assertEqual(rc, 0, out)
        f = ledger.load_finding(self.d, "proof-seo", "seo-robots-blocks-all")
        self.assertEqual((f["status"], f["severity"]), ("verified", "medium"))
        statuses = dict((c["id"], c["status"]) for c in ledger.load_coverage(self.d, "proof-seo")["checks"])
        self.assertEqual(statuses["robots-open"], "covered")
        self.assertEqual(statuses["sitemap-covers-pages"], "covered")
        self.assertFalse(os.path.exists(ledger.finding_path(self.d, "proof-seo", "seo-index-not-in-sitemap")))

    # ------------------------------------------------------------------ site

    def write_site(self, robots="User-agent: *\nDisallow: /\n", sitemap=None):
        for name, canon in (("index.html", "https://example.test/"), ("about.html", "https://example.test/about"),
                            ("pricing.html", "https://example.test/pricing")):
            self.tp.write("evidence/proof-seo/" + name, make_page(canonical='<link rel="canonical" href="%s">' % canon))
        if robots is not None:
            self.tp.write("evidence/proof-seo/robots.txt", robots)
        if sitemap is None:
            sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                       '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                       '<url><loc>https://example.test/</loc></url>\n'
                       '<url><loc>https://example.test/about</loc></url>\n</urlset>\n')
        self.tp.write("evidence/proof-seo/sitemap.xml", sitemap)
        return os.path.join(self.d, "evidence", "proof-seo")

    def test_site_technical_checks(self):
        folder = self.write_site()
        rc, out = run("--dir", self.d, "site", "--pages", folder, "--json")
        self.assertEqual(rc, 0, out)
        r = json.loads(out)
        self.assertEqual(len(r["pages"]), 3)
        self.assertEqual(r["page_mean"], 100)
        tech = dict((c["id"], c) for c in r["technical"])
        self.assertEqual(tech["robots-present"]["status"], "pass")
        self.assertEqual((tech["robots-open"]["status"], tech["robots-open"]["severity"]), ("fail", "medium"))
        self.assertEqual(tech["sitemap-valid"]["status"], "pass")
        self.assertEqual(tech["sitemap-covers-pages"]["status"], "fail")
        self.assertEqual(r["missing_from_sitemap"], ["pricing.html"])
        self.assertEqual(r["technical_score"], 60)
        self.assertEqual(r["score"], 90)  # 0.75 * 100 + 0.25 * 60
        rc, text = run("--dir", self.d, "site", "--pages", folder)
        self.assertEqual(rc, 0)
        self.assertIn("Site score: 90/100", text)
        self.assertIn("pricing.html", text)

    def test_site_malformed_sitemap_and_missing_robots(self):
        folder = self.write_site(robots=None, sitemap="<urlset><url><loc>https://example.test/</loc></url>")
        rc, out = run("--dir", self.d, "site", "--pages", folder, "--json")
        self.assertEqual(rc, 0, out)
        tech = dict((c["id"], c) for c in json.loads(out)["technical"])
        self.assertEqual(tech["robots-present"]["status"], "fail")
        self.assertEqual(tech["robots-open"]["status"], "na")
        self.assertEqual(tech["sitemap-valid"]["status"], "fail")
        self.assertIn("not well-formed", tech["sitemap-valid"]["problem"])
        self.assertEqual(tech["sitemap-covers-pages"]["status"], "na")

    def test_site_needs_pages(self):
        rc, out = run("--dir", self.d, "site", "--pages", self.tp.base)
        self.assertNotEqual(rc, 0)
        self.assertIn("no .html files", out)

    def test_robots_parse(self):
        r = seo.parse_robots("# comment\nUser-agent: Googlebot\nDisallow: /private\n\nUser-agent: *\nDisallow: /\nAllow: /\nSitemap: https://example.test/sitemap.xml\n")
        self.assertEqual(r["blocked"], [])
        self.assertEqual(r["sitemaps"], ["https://example.test/sitemap.xml"])
        self.assertEqual(seo.parse_robots("User-agent: *\nDisallow: /")["blocked"], ["*"])

    # ------------------------------------------------------------ lighthouse

    def lighthouse_report(self, score=0.92):
        report = {"lighthouseVersion": "12.0.0", "requestedUrl": "https://example.test/",
                  "categories": {"seo": {"score": score, "auditRefs": [{"id": "document-title", "weight": 1},
                                                                        {"id": "crawlable-anchors", "weight": 1}]}},
                  "audits": {"document-title": {"score": 0, "title": "Document has a title", "scoreDisplayMode": "binary"},
                             "crawlable-anchors": {"score": 1, "title": "Links are crawlable", "scoreDisplayMode": "binary"}}}
        return os.path.join(self.d, self.tp.write("evidence/proof-seo/lighthouse.json", json.dumps(report)))

    def test_lighthouse_report_folds_in(self):
        path = self.page_file(make_page())
        rep = self.lighthouse_report()
        rc, out = run("--dir", self.d, "page", path, "--lighthouse", rep, "--json")
        self.assertEqual(rc, 0, out)
        lh = json.loads(out)["lighthouse"]
        self.assertEqual((lh["score"], lh["version"]), (92, "12.0.0"))
        self.assertEqual([f["id"] for f in lh["failing"]], ["document-title"])
        rc, out = run("--dir", self.d, "record", path, "--lighthouse", rep)
        self.assertEqual(rc, 0, out)
        by = dict((m["id"], m) for m in [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-seo"][0]["metrics"])
        self.assertEqual((by["lighthouse_seo"]["value"], by["lighthouse_seo"]["budget"]), (92, 90))
        self.assertEqual(by["lighthouse_seo"]["evidence"], ["evidence/proof-seo/lighthouse.json"])
        statuses = dict((c["id"], c["status"]) for c in ledger.load_coverage(self.d, "proof-seo")["checks"])
        self.assertEqual(statuses["lighthouse-seo"], "covered")

    def test_bad_lighthouse_report_refused(self):
        path = self.page_file(make_page())
        bad = os.path.join(self.d, self.tp.write("evidence/proof-seo/not-lh.json", '{"categories": {}}'))
        rc, out = run("--dir", self.d, "page", path, "--lighthouse", bad)
        self.assertNotEqual(rc, 0)
        self.assertIn("categories.seo.score", out)


if __name__ == "__main__":
    unittest.main()
