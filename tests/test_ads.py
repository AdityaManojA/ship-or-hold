import contextlib
import io
import json
import os
import unittest

from _load import SKILLS_DIR, TempProof, load_tool

ads = load_tool("proof-ads", "ads.py")
ledger = load_tool("proof-core", "ledger.py")
SKILL = "proof-ads"
EXAMPLE = os.path.join(SKILLS_DIR, "proof-ads", "ads.example.json")


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = ads.main(list(argv))
    return code, out.getvalue()


PAGE = """<!doctype html><html><head><title>%s</title><style>.x{}</style>
<script>var hidden = "rs 9999 free delivery";</script></head>
<body><h1>%s</h1><p>%s</p><button>Add to cart</button></body></html>"""


def brand_ad(n, headline, copy="Plain copy.", **kw):
    ad = {"advertiser": "Loom and Lane", "is_brand": True, "library_url": "https://lib.example/%d" % n,
          "started": "2026-11-01", "format": "image", "headline": headline, "copy": copy, "cta": "Shop now",
          "landing_url": "https://shop.example/p%d" % n}
    ad.update(kw)
    return ad


def comp_ad(n, headline, copy):
    return {"advertiser": "Rival %d" % n, "is_brand": False, "library_url": "https://lib.example/c%d" % n,
            "started": "2026-10-01", "format": "video", "headline": headline, "copy": copy, "cta": "Shop now",
            "landing_url": "https://rival.example/%d" % n}


class Ads(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.inp = os.path.join(self.tp.base, "input")
        os.makedirs(os.path.join(self.inp, "landing"))

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def page(self, n, title, h1, body):
        rel = "landing/p%d.html" % n
        with open(os.path.join(self.inp, rel), "w", encoding="utf-8") as fh:
            fh.write(PAGE % (title, h1, body))
        return {"url": "https://shop.example/p%d" % n, "file": rel}

    def spec(self, ads_list, pages):
        return {"business": "Test store", "currency": "Rs ", "brand": "Loom and Lane", "captured": "2026-11-20",
                "ads": ads_list, "landing_pages": pages}

    def write(self, spec):
        path = os.path.join(self.inp, "ads.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        return path

    def record(self, spec):
        code, out = run("--dir", self.tp.dir, "record", self.write(spec))
        self.assertEqual(code, 0, out)
        return out

    def findings(self):
        return {f["id"]: f for f in ledger.load_findings(self.tp.dir, SKILL)}

    def section(self):
        with open(os.path.join(self.tp.dir, "business", "ads.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_example_records_section_and_verified_findings(self):
        code, out = run("--dir", self.tp.dir, "record", EXAMPLE)
        self.assertEqual(code, 0, out)
        text = self.section()
        self.assertEqual(text.splitlines()[0], "# Ad teardown")
        f = self.findings()
        self.assertEqual(f["ads-offer-missing-2"]["severity"], "high")
        self.assertEqual(f["ads-offer-missing-2"]["title"], "The ad promises an offer the landing page does not show")
        self.assertEqual(f["ads-no-landing-3"]["severity"], "low")
        self.assertIn("ads-gap-sizing-fit", f)
        self.assertTrue(all(x["status"] == "verified" for x in f.values()))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.tp.dir, SKILL)["checks"]}
        self.assertEqual(set(cov), {"brand-ads-read", "competitor-ads-read", "landing-match-checked", "gaps-listed"})
        self.assertTrue(all(v == "covered" for v in cov.values()))

    def test_check_records_nothing(self):
        code, out = run("--dir", self.tp.dir, "check", EXAMPLE)
        self.assertEqual(code, 0, out)
        self.assertIn("# Ad teardown", out)
        self.assertFalse(os.path.exists(os.path.join(self.tp.dir, "business", "ads.md")))
        self.assertEqual(ledger.load_findings(self.tp.dir, SKILL), [])

    def test_matching_ad_scores_full_and_passes_the_bar(self):
        p = self.page(1, "Sage linen kurta", "Sage linen kurta", "Rs 1,499 pure linen")
        self.record(self.spec([brand_ad(1, "Sage linen kurta", price=1499)], [p]))
        self.assertEqual(self.findings(), {})
        m = ledger.load_all_scores(self.tp.dir)[0]["metrics"][0]
        self.assertEqual((m["id"], m["value"], m["op"], m["budget"]), ("match_pct", 100.0, ">=", 60))

    def test_different_things_is_medium_below_forty_percent(self):
        p = self.page(1, "Steel water bottle", "Steel bottle", "Keeps water cold")
        self.record(self.spec([brand_ad(1, "Linen kurta in sage green")], [p]))
        f = self.findings()["ads-mismatch-1"]
        self.assertEqual(f["severity"], "medium")
        self.assertEqual(f["title"], "Ad and landing page talk about different things")

    def test_price_in_ad_differs_from_page(self):
        p = self.page(1, "Sage linen kurta", "Sage linen kurta", "Now Rs 1,799")
        self.record(self.spec([brand_ad(1, "Sage linen kurta", copy="Only Rs 1,499.")], [p]))
        f = self.findings()["ads-price-differs-1"]
        self.assertEqual(f["severity"], "medium")
        self.assertIn("Rs 1,799", f["description"])

    def test_offer_shown_on_page_raises_nothing_and_script_text_is_ignored(self):
        p = self.page(1, "Sage linen kurta", "Sage linen kurta", "Get 10% off your first order")
        self.record(self.spec([brand_ad(1, "Sage linen kurta", offer="10% off")], [p]))
        self.assertNotIn("ads-offer-missing-1", self.findings())
        # "free delivery" sits only inside a script tag, which is not visible text.
        p2 = self.page(2, "Sage linen kurta", "Sage linen kurta", "Linen")
        self.record(self.spec([brand_ad(2, "Sage linen kurta", offer="free delivery")], [p2]))
        self.assertEqual(self.findings()["ads-offer-missing-1"]["severity"], "high")  # ids follow the ad's position

    def test_gaps_need_two_competitors_and_no_brand_ad_and_cap_at_five(self):
        loud = ("True to size, cotton, rated 5 stars, 20% off, free delivery, cash on delivery, easy returns, "
                "so comfy, a perfect gift, new arrivals, limited stock.")
        comps = [comp_ad(1, "Kurtas", loud), comp_ad(2, "Kurtas", loud), comp_ad(3, "Kurtas", "Cotton only")]
        p = self.page(1, "Kurta", "Kurta", "Kurta")
        self.record(self.spec([brand_ad(1, "Kurta", copy="Pure cotton kurta.")] + comps, [p]))
        gaps = [k for k in self.findings() if k.startswith("ads-gap-")]
        self.assertEqual(len(gaps), 5)
        self.assertNotIn("ads-gap-fabric-material", gaps)  # the brand mentions cotton
        self.assertIn("Competitors lean on sizing/fit; your ads never mention it",
                      [f["title"] for f in self.findings().values()])
        one = [comp_ad(1, "Kurtas", "Free delivery")]
        self.record(self.spec([brand_ad(1, "Kurta")] + one, [p]))
        self.assertFalse([k for k in self.findings() if k.startswith("ads-gap-")])

    def test_zero_ads_is_a_low_finding_and_no_score(self):
        self.record(self.spec([], []))
        f = self.findings()["ads-none-found"]
        self.assertEqual(f["severity"], "low")
        self.assertEqual(f["title"], "No active ads found in the public library on 2026-11-20")
        self.assertIn("No active ads were found", self.section())
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.tp.dir, SKILL)["checks"]}
        self.assertEqual(cov["landing-match-checked"], "not_applicable")
        self.assertEqual(ledger.load_all_scores(self.tp.dir)[0]["metrics"], [])

    def test_missing_page_file_is_low_no_landing(self):
        spec = self.spec([brand_ad(1, "Kurta")], [{"url": "https://shop.example/p1", "file": "landing/gone.html"}])
        self.record(spec)
        self.assertEqual(self.findings()["ads-no-landing-1"]["severity"], "low")

    def test_rerecording_replaces_earlier_findings(self):
        p = self.page(1, "Steel bottle", "Steel bottle", "Water")
        self.record(self.spec([brand_ad(1, "Linen kurta")], [p]))
        self.assertIn("ads-mismatch-1", self.findings())
        p = self.page(1, "Linen kurta", "Linen kurta", "Linen")
        self.record(self.spec([brand_ad(1, "Linen kurta")], [p]))
        self.assertNotIn("ads-mismatch-1", self.findings())

    def test_missing_inputs_exit_two(self):
        spec = self.spec([], [])
        del spec["currency"]
        code, out = run("--dir", self.tp.dir, "check", self.write(spec))
        self.assertEqual(code, 2)
        self.assertIn("currency", out)
        spec = self.spec([brand_ad(1, "Kurta", format="banner")], [])
        self.assertEqual(run("check", self.write(spec))[0], 2)
        spec = self.spec([], [])
        spec["captured"] = "20 Nov"
        self.assertEqual(run("check", self.write(spec))[0], 2)

    def test_dir_works_after_the_command_and_template_runs(self):
        out_dir = os.path.join(self.tp.base, "tmpl")
        code, out = run("template", "--out", out_dir, "--dir", self.tp.dir)
        self.assertEqual(code, 0, out)
        code, out = run("record", os.path.join(out_dir, "ads.json"), "--dir", self.tp.dir)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.tp.dir, "business", "ads.md")))
        self.assertTrue(os.listdir(os.path.join(self.tp.dir, "evidence", SKILL, "landing")))


if __name__ == "__main__":
    unittest.main()
