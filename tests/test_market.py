import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

market = load_tool("proof-market", "market.py")
ledger = load_tool("proof-core", "ledger.py")

HEADER = "name,type,url,where,price,what_it_sells,their_line,rating,review_count,source_date"
ROWS = [
    "Alpha Meals,direct,https://example.com/alpha,Pune,120,Lunch per meal,Lunch like home,4.1,212,2026-11-03",
    "Beta Plans,direct,https://example.com/beta,Pune,Rs 90,Monthly plan,Never think about lunch,3.8,540,2026-11-03",
    "Gamma App,indirect,https://example.com/gamma,Nationwide,180,Restaurant delivery,Anything in 30 minutes,4.4,not found,2026-11-04",
    "Packed lunch,substitute,https://example.com/thread,Anywhere,free,Lunch from home,not found,not found,not found,2026-11-05",
]


def src(n):
    return {"quote": "Late again, number %d." % n, "url": "https://example.com/r/%d" % n, "date": "2026-10-0%d" % n}


def base_market():
    return {
        "business": "Lunch delivery", "currency": "Rs ",
        "complaints": [
            {"theme": "Late delivery", "product_fixes": True, "core": True, "sources": [src(1), src(2), src(3)]},
            {"theme": "Dull menu", "product_fixes": False, "core": False, "sources": [src(4)]},
        ],
        "gap": "Nobody promises a delivery time.", "threat": "The app adds a lunch plan.",
        "research_notes": ["One review page needed a login, so its reviews were not read."],
    }


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = market.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Market(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def files(self, rows=None, spec=None, header=HEADER):
        c = os.path.join(self.tp.base, "competitors.csv")
        m = os.path.join(self.tp.base, "market.json")
        with open(c, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join([header] + (ROWS if rows is None else rows)) + "\n")
        with open(m, "w", encoding="utf-8") as fh:
            json.dump(base_market() if spec is None else spec, fh)
        return c, m

    def record(self, rows=None, spec=None):
        c, m = self.files(rows, spec)
        return run("--dir", self.d, "record", c, m)

    def ids(self):
        return sorted(f["id"] for f in ledger.load_findings(self.d, "proof-market"))

    def test_happy_path_writes_section_and_ledger(self):
        code, out = self.record()
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.d, "business", "market.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Market and competition\n"))
        self.assertIn("4 compared: 2 direct, 1 indirect, 1 substitute.", text)
        self.assertNotIn("looked at but", text)
        # prices 0, 90, 120, 180 -> median 105
        self.assertIn("Lowest Rs 0, median Rs 105, highest Rs 180, across 4 competitor(s).", text)
        # research notes go to the appendix, never to the market section
        self.assertNotIn("login", text)
        self.assertNotIn("could not be verified", text.lower())
        with open(os.path.join(self.d, "business", "research-notes.md"), encoding="utf-8") as fh:
            notes = fh.read()
        self.assertTrue(notes.startswith("# Research notes\n"))
        self.assertIn("Appendix only", notes)
        self.assertIn("- One review page needed a login, so its reviews were not read.", notes)
        self.assertIn("[source](https://example.com/r/1)", text)
        self.assertIn("**Dull menu**: 1 source(s) (thin: under 3 sources)", text)
        self.assertLess(text.index("Alpha Meals"), text.index("Gamma App"))
        self.assertLess(text.index("Gamma App"), text.index("Packed lunch"))
        self.assertEqual(self.ids(), [])
        metric = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual((metric["id"], metric["value"], metric["op"], metric["budget"]),
                         ("sources_linked_pct", 100.0, "==", 100))
        self.assertTrue(ledger.metric_passes(metric))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-market")["checks"]}
        self.assertEqual(set(cov.values()), {"covered"})
        self.assertEqual(len(cov), 7)
        self.assertIn("research-notes", cov)
        for name in ("competitors.csv", "market.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", "proof-market", name)))

    def test_check_prints_and_records_nothing(self):
        c, m = self.files()
        code, out = run("--dir", self.d, "check", c, m)
        self.assertEqual(code, 0, out)
        self.assertIn("# Market and competition", out)
        self.assertEqual(os.listdir(self.d), [])

    def test_template_is_runnable(self):
        out_dir = os.path.join(self.tp.base, "inputs")
        code, out = run("template", "--out", out_dir)
        self.assertEqual(code, 0, out)
        code, out = run("record", os.path.join(out_dir, "competitors.csv"), os.path.join(out_dir, "market.json"),
                        "--dir", self.d)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "business", "market.md")))

    def test_currency_is_required(self):
        spec = base_market()
        del spec["currency"]
        code, out = self.record(spec=spec)
        self.assertEqual(code, 2)
        self.assertIn("currency", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "business")))

    def test_missing_inputs_are_errors(self):
        for key in ("business", "complaints", "gap", "threat", "research_notes"):
            spec = base_market()
            del spec[key]
            code, out = self.record(spec=spec)
            self.assertEqual(code, 2, key)
            self.assertIn(key, out)
        c, m = self.files(header=HEADER.replace(",source_date", ""))
        code, out = run("--dir", self.d, "record", c, m)
        self.assertEqual(code, 2)
        self.assertIn("source_date", out)
        code, out = run("--dir", self.d, "record", os.path.join(self.tp.base, "nope.csv"), m)
        self.assertEqual(code, 2)
        code, out = self.record(rows=[])
        self.assertEqual(code, 2)

    def test_bad_rows_are_errors(self):
        code, out = self.record(rows=[ROWS[0].replace(",direct,", ",rival,")])
        self.assertEqual(code, 2)
        self.assertIn("type must be", out)
        code, out = self.record(rows=[ROWS[0].replace(",120,", ",about 120 a meal,")])
        self.assertEqual(code, 2)
        self.assertIn("price", out)

    def test_quote_without_link_is_refused(self):
        spec = base_market()
        spec["complaints"][0]["sources"][0]["url"] = ""
        code, out = self.record(spec=spec)
        self.assertEqual(code, 2)
        self.assertIn("no link", out)
        spec = base_market()
        spec["complaints"][0]["sources"][0]["quote"] = "x" * 301
        self.assertEqual(self.record(spec=spec)[0], 2)

    def test_core_problem_thin(self):
        spec = base_market()
        spec["complaints"][0]["sources"] = [src(1), src(2)]
        self.assertEqual(self.record(spec=spec)[0], 0)
        self.assertIn("market-core-thin", self.ids())
        f = ledger.load_finding(self.d, "proof-market", "market-core-thin")
        self.assertEqual((f["severity"], f["status"], f["title"]),
                         ("medium", "verified", "The core problem is thinly evidenced"))
        # no theme marked core at all
        spec = base_market()
        spec["complaints"][0]["core"] = False
        self.record(spec=spec)
        self.assertIn("market-core-thin", self.ids())

    def test_loudest_complaint_not_fixed(self):
        spec = base_market()
        spec["complaints"][1]["sources"] = [src(4), src(5), src(6), src(7)]
        self.record(spec=spec)
        self.assertEqual(self.ids(), ["market-loudest-unfixed"])
        self.assertEqual(ledger.load_finding(self.d, "proof-market", "market-loudest-unfixed")["severity"], "medium")

    def test_every_alternative_free(self):
        rows = [ROWS[0].replace(",120,", ",0,"), ROWS[3], ROWS[2].replace(",180,", ",not found,")]
        code, out = self.record(rows=rows)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ids(), ["market-all-free"])
        self.assertEqual(ledger.load_finding(self.d, "proof-market", "market-all-free")["severity"], "low")
        with open(os.path.join(self.d, "business", "market.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("2 compared: 1 direct, 0 indirect, 1 substitute.", text)
        self.assertIn("1 more was looked at but publishes no comparable price.", text)
        self.assertNotIn("Gamma App", text)
        self.assertIn("across 2 competitor(s)", text)
        self.assertIn("Every alternative with a price found is free to the buyer.", text)
        with open(os.path.join(self.d, "business", "research-notes.md"), encoding="utf-8") as fh:
            notes = fh.read()
        self.assertIn("- Gamma App (indirect, line 4, https://example.com/gamma)", notes)

    def test_row_without_link_or_date(self):
        rows = list(ROWS)
        rows[0] = rows[0].replace("https://example.com/alpha", "not found")
        rows[2] = rows[2].replace(",2026-11-04", ",")
        self.record(rows=rows)
        self.assertEqual(self.ids(), ["market-unlinked-alpha-meals", "market-unlinked-gamma-app"])
        f = ledger.load_finding(self.d, "proof-market", "market-unlinked-alpha-meals")
        self.assertEqual((f["severity"], f["status"]), ("low", "verified"))
        metric = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual(metric["value"], 50.0)
        self.assertFalse(ledger.metric_passes(metric))

    def test_no_gap(self):
        for gap in ("none", ""):
            spec = base_market()
            spec["gap"] = gap
            self.record(spec=spec)
            self.assertEqual(self.ids(), ["market-no-gap"])
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-market")["checks"]}
        self.assertEqual(cov["gap-stated"], "not_run")  # the last run had an empty gap

    def test_rerecord_replaces_stale_findings(self):
        spec = base_market()
        spec["gap"] = "none"
        rows = list(ROWS)
        rows[0] = rows[0].replace("https://example.com/alpha", "")
        self.record(rows=rows, spec=spec)
        self.assertEqual(self.ids(), ["market-no-gap", "market-unlinked-alpha-meals"])
        code, _ = self.record()
        self.assertEqual(code, 0)
        self.assertEqual(self.ids(), [])

    def test_coverage_left_not_run_when_part_is_absent(self):
        spec = base_market()
        spec["complaints"] = []
        spec["research_notes"] = []
        self.record(rows=ROWS[:1], spec=spec)
        checks = {c["id"]: c for c in ledger.load_coverage(self.d, "proof-market")["checks"]}
        cov = {k: c["status"] for k, c in checks.items()}
        self.assertEqual(cov["competitors-listed"], "covered")
        for cid in ("substitutes-listed", "prices-compared", "complaints-themed"):
            self.assertEqual(cov[cid], "not_run", cid)
        self.assertEqual(cov["research-notes"], "not_applicable")
        self.assertIn("no walls", checks["research-notes"]["note"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "business", "research-notes.md")))

    def test_could_not_verify_is_an_alias(self):
        spec = base_market()
        spec["could_not_verify"] = spec.pop("research_notes")
        code, out = self.record(spec=spec)
        self.assertEqual(code, 0, out)
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-market")["checks"]}
        self.assertEqual(cov["research-notes"], "covered")
        self.assertTrue(os.path.isfile(os.path.join(self.d, "business", "research-notes.md")))
        spec["research_notes"] = ["x"]
        code, out = self.record(spec=spec)
        self.assertEqual(code, 2)
        self.assertIn("both", out)

    def test_scraping_failures_are_refused_in_client_text(self):
        for phrase in ("refused the reader", "Timed out", "time out", "blocked by the site", "login wall",
                       "could not be reached", "could not be read", "page did not load", "CAPTCHA", "HTTP 403",
                       "error 429"):
            rows = list(ROWS)
            rows[2] = rows[2].replace("Restaurant delivery", "Restaurant delivery (%s)" % phrase)
            code, out = self.record(rows=rows)
            self.assertEqual(code, 2, phrase)
            self.assertIn("line 4 (Gamma App)", out)
            self.assertIn("'what_it_sells'", out)
            self.assertIn("research_notes", out)
        for field in ("gap", "threat"):
            spec = base_market()
            spec[field] = "Their page timed out."
            code, out = self.record(spec=spec)
            self.assertEqual(code, 2, field)
            self.assertIn("'%s'" % field, out)
        spec = base_market()
        spec["complaints"][1]["theme"] = "Review page blocked"
        code, out = self.record(spec=spec)
        self.assertEqual(code, 2)
        self.assertIn("complaint 2, field 'theme'", out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "business", "market.md")))
        # a url with 403 in it is not prose and is fine
        rows = list(ROWS)
        rows[0] = rows[0].replace("https://example.com/alpha", "https://example.com/alpha/403")
        self.assertEqual(self.record(rows=rows)[0], 0)

    def test_ordinary_business_words_are_not_mistaken_for_scraping_failures(self):
        # Found on a real run: a complaint theme about refunds being refused was rejected.
        spec = base_market()
        spec["complaints"][0]["theme"] = "Returns, exchanges and refunds are refused or drag on"
        spec["threat"] = "Marketplace sellers undercut at Rs 429; a card blocked at checkout loses the sale"
        self.assertEqual(self.record(spec=spec)[0], 0)


if __name__ == "__main__":
    unittest.main()
