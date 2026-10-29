import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

contrast = load_tool("proof-a11y", "contrast.py")
ledger = load_tool("proof-core", "ledger.py")
validate = load_tool("proof-core", "validate.py")
verdict = load_tool("proof-report", "verdict.py")

SKILL = "proof-a11y"
MANUAL_IDS = {"focus-order", "focus-visible", "names-labels", "alt-text", "headings", "landmarks",
              "form-errors", "motion", "target-size", "zoom-200", "screen-reader"}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = contrast.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


def ratio(fg, bg):
    return contrast.contrast_ratio(contrast.parse_color(fg), contrast.parse_color(bg))


class Maths(unittest.TestCase):
    def test_black_on_white_is_21(self):
        self.assertAlmostEqual(ratio("#000000", "#ffffff"), 21.0, places=6)

    def test_white_on_white_is_1(self):
        self.assertEqual(ratio("#ffffff", "#ffffff"), 1.0)
        self.assertEqual(ratio("#123456", "#123456"), 1.0)

    def test_order_does_not_matter(self):
        self.assertEqual(ratio("#112233", "#ffffff"), ratio("#ffffff", "#112233"))

    def test_known_grey_passes_aa_normal(self):
        r = ratio("#767676", "#ffffff")
        self.assertAlmostEqual(r, 4.54, delta=0.05)
        j = contrast.judge(r, large=False)
        self.assertTrue(j["aa"])
        self.assertFalse(j["aaa"])

    def test_mid_grey_passes_only_as_large_text(self):
        r = ratio("#777777", "#ffffff")  # about 4.48:1: just under the normal-text bar
        self.assertFalse(contrast.judge(r, large=False)["aa"])
        self.assertTrue(contrast.judge(r, large=True)["aa"])
        self.assertFalse(contrast.judge(r, large=True)["aaa"])

    def test_light_grey_fails_both_sizes(self):
        r = ratio("#999999", "#ffffff")  # about 2.85:1
        self.assertFalse(contrast.judge(r, large=False)["aa"])
        self.assertFalse(contrast.judge(r, large=True)["aa"])

    def test_luminance_ends(self):
        self.assertEqual(contrast.luminance((0, 0, 0)), 0.0)
        self.assertAlmostEqual(contrast.luminance((255, 255, 255)), 1.0, places=9)


class Colours(unittest.TestCase):
    def test_hex_forms(self):
        self.assertEqual(contrast.parse_color("#fff"), (255, 255, 255))
        self.assertEqual(contrast.parse_color("fff"), (255, 255, 255))
        self.assertEqual(contrast.parse_color("#FFFFFF"), (255, 255, 255))
        self.assertEqual(contrast.parse_color("#1a2B3c"), (26, 43, 60))
        self.assertEqual(contrast.parse_color("  #abc  "), (170, 187, 204))

    def test_rgb_forms(self):
        self.assertEqual(contrast.parse_color("rgb(118, 118, 118)"), contrast.parse_color("#767676"))
        self.assertEqual(contrast.parse_color("rgb(0,0,0)"), (0, 0, 0))
        self.assertEqual(contrast.parse_color("RGB( 255 , 255 , 255 )"), (255, 255, 255))
        self.assertEqual(contrast.parse_color("rgb(50%, 50%, 50%)"), (128, 128, 128))
        self.assertEqual(contrast.parse_color("rgba(0, 0, 0, 1)"), (0, 0, 0))

    def test_alpha_is_refused(self):
        for bad in ("rgba(0, 0, 0, 0.5)", "#11223344", "#1234"):
            with self.assertRaises(contrast.ColorError):
                contrast.parse_color(bad)

    def test_bad_colours_raise_cleanly(self):
        for bad in ("#12", "#gggggg", "blue", "", "   ", "rgb(1, 2)", "rgb(300, 0, 0)",
                    "rgb(1, 2, 3, 4, 5)", "rgb(1.5, 2, 3)", None, 123):
            with self.assertRaises(contrast.ColorError):
                contrast.parse_color(bad)

    def test_hex_of_round_trip(self):
        self.assertEqual(contrast.hex_of(contrast.parse_color("#1A2B3C")), "#1a2b3c")


class Sizes(unittest.TestCase):
    def test_keywords(self):
        self.assertFalse(contrast.is_large("normal"))
        self.assertFalse(contrast.is_large("normal", bold=True))
        self.assertTrue(contrast.is_large("large"))
        self.assertTrue(contrast.is_large(" LARGE "))

    def test_points(self):
        self.assertTrue(contrast.is_large(18))
        self.assertTrue(contrast.is_large("18pt"))
        self.assertFalse(contrast.is_large(17.9))
        self.assertFalse(contrast.is_large(14))
        self.assertTrue(contrast.is_large(14, bold=True))
        self.assertTrue(contrast.is_large("14pt", bold=True))
        self.assertFalse(contrast.is_large(13.9, bold=True))

    def test_pixels(self):
        self.assertTrue(contrast.is_large("24px"))        # 18pt
        self.assertFalse(contrast.is_large("23px"))
        self.assertTrue(contrast.is_large("19px", bold=True))   # 14.25pt bold
        self.assertFalse(contrast.is_large("18px", bold=True))  # 13.5pt bold

    def test_bad_sizes_raise_cleanly(self):
        for bad in ("huge", "", 0, -1, True, None, "14em"):
            with self.assertRaises(contrast.SizeError):
                contrast.is_large(bad)
        with self.assertRaises(contrast.SizeError):
            contrast.is_large("14pt", bold="yes")


class RatioCommand(unittest.TestCase):
    def test_prints_both_sizes_when_no_size_given(self):
        rc, out = run("ratio", "--fg", "#767676", "--bg", "#ffffff")
        self.assertEqual(rc, 0, out)
        self.assertIn("4.54:1", out)
        self.assertIn("normal text", out)
        self.assertIn("large text", out)
        self.assertIn("PASS", out)
        self.assertIn("FAIL", out)
        self.assertIn(ledger.OK, out)
        self.assertIn(ledger.BAD, out)

    def test_json_and_size_flags(self):
        rc, out = run("ratio", "--fg", "#777", "--bg", "#fff", "--json")
        self.assertEqual(rc, 0, out)
        data = json.loads(out)
        self.assertAlmostEqual(data["ratio"], 4.48, delta=0.05)
        self.assertEqual([s["text"] for s in data["sizes"]], ["normal", "large"])
        self.assertEqual([s["aa"] for s in data["sizes"]], [False, True])

        rc, out = run("ratio", "--fg", "#777", "--bg", "#fff", "--size", "14pt", "--bold", "--json")
        data = json.loads(out)
        self.assertEqual(len(data["sizes"]), 1)
        self.assertEqual(data["sizes"][0]["text"], "large")
        self.assertIn("bold", data["size"])

        rc, out = run("ratio", "--fg", "#777", "--bg", "#fff", "--size", "14pt", "--json")
        self.assertEqual(json.loads(out)["sizes"][0]["text"], "normal")

    def test_exit_zero_even_when_failing(self):
        rc, out = run("ratio", "--fg", "#999999", "--bg", "#ffffff", "--size", "normal")
        self.assertEqual(rc, 0)
        self.assertIn("FAIL", out)

    def test_bad_hex_exits_cleanly(self):
        rc, out = run("ratio", "--fg", "#12", "--bg", "#ffffff")
        self.assertNotEqual(rc, 0)
        self.assertIn("cannot read colour", out)
        rc, out = run("ratio", "--fg", "#000", "--bg", "#fff", "--size", "enormous")
        self.assertNotEqual(rc, 0)
        self.assertIn("cannot read size", out)


PAIRS = {"pairs": [
    {"id": "body", "fg": "#333333", "bg": "#ffffff", "size": "16px", "where": "body copy"},
    {"id": "footer", "fg": "#999999", "bg": "#ffffff", "size": "normal", "where": "footer links"},
    {"id": "hero", "fg": "#777777", "bg": "#ffffff", "size": "24px", "where": "hero heading"},
]}


class CheckCommand(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.pairs = os.path.join(self.tp.base, "pairs.json")
        self.write_pairs(PAIRS)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_pairs(self, obj):
        with open(self.pairs, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def coverage(self):
        cov = ledger.load_coverage(self.d, SKILL)
        return {c["id"]: c for c in cov["checks"]} if cov else None

    def test_table_without_record_writes_nothing(self):
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs)
        self.assertEqual(rc, 0, out)
        self.assertIn("FAIL", out)
        self.assertIn("PASS", out)
        self.assertIn("2 pass, 1 fail", out)
        self.assertIsNone(ledger.load_coverage(self.d, SKILL))
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])
        self.assertEqual(ledger.load_all_scores(self.d), [])

    def test_record_marks_all_pairs_and_writes_findings_only_for_failures(self):
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        self.assertEqual(rc, 0, out)
        cov = self.coverage()
        self.assertEqual(set(cov), {"contrast-body", "contrast-footer", "contrast-hero"})
        self.assertTrue(all(c["status"] == "covered" for c in cov.values()))
        self.assertIn("2.85:1", cov["contrast-footer"]["note"])
        self.assertIn("large text", cov["contrast-hero"]["note"])

        findings = ledger.load_findings(self.d, SKILL)
        self.assertEqual([f["id"] for f in findings], ["contrast-footer"])
        f = findings[0]
        self.assertEqual(f["status"], "verified")
        self.assertEqual(f["verified_by"], "manual")
        self.assertIn("computed", f["verified_note"])
        self.assertEqual(f["severity"], "medium")
        self.assertEqual(f["category"], "contrast")
        self.assertEqual(f["location"], "footer links")
        self.assertIn("Low contrast (2.85:1) at footer links", f["title"])
        self.assertIn("4.5:1", f["fix"])
        self.assertIn("WCAG 1.4.3", f["references"])
        self.assertIn("--fg \"#999999\"", f["repro"])

        scores = ledger.load_all_scores(self.d)
        self.assertEqual(len(scores), 1)
        m = scores[0]["metrics"][0]
        self.assertEqual((m["id"], m["value"], m["budget"], m["op"], m["unit"]),
                         ("contrast_pairs_passing", 2, 3, "==", "pairs"))
        self.assertFalse(ledger.metric_passes(m))

    def test_record_all_passing_has_no_findings_and_score_passes(self):
        self.write_pairs({"pairs": [PAIRS["pairs"][0], PAIRS["pairs"][2]]})
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])
        m = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual((m["value"], m["budget"]), (2, 2))
        self.assertTrue(ledger.metric_passes(m))

    def test_rerun_clears_stale_findings_and_pairs(self):
        run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        self.assertEqual(len(ledger.load_findings(self.d, SKILL)), 1)
        fixed = {"pairs": [dict(PAIRS["pairs"][0]), dict(PAIRS["pairs"][1], fg="#555555")]}  # footer fixed, hero gone
        self.write_pairs(fixed)
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        self.assertEqual(rc, 0, out)
        self.assertIn("replaced 1", out)
        self.assertEqual(ledger.load_findings(self.d, SKILL), [])
        self.assertEqual(set(self.coverage()), {"contrast-body", "contrast-footer"})
        m = ledger.load_all_scores(self.d)[0]["metrics"][0]
        self.assertEqual((m["value"], m["budget"]), (2, 2))

    def test_record_keeps_manual_items_in_either_order(self):
        run("--dir", self.d, "checklist", "--record")
        run("--dir", self.d, "na", "motion", "--note", "nothing moves")
        run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        cov = self.coverage()
        self.assertTrue(MANUAL_IDS <= set(cov))
        self.assertIn("engine", cov)
        self.assertIn("contrast-footer", cov)
        self.assertEqual(cov["motion"]["status"], "not_applicable")
        # and the other way round: checklist --record after check --record keeps the pairs
        run("--dir", self.d, "checklist", "--record")
        cov = self.coverage()
        self.assertIn("contrast-footer", cov)
        self.assertEqual(cov["contrast-footer"]["status"], "covered")
        self.assertEqual(cov["motion"]["status"], "not_applicable")

    def test_bare_list_is_accepted(self):
        self.write_pairs(PAIRS["pairs"])
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs, "--json")
        self.assertEqual(rc, 0, out)
        data = json.loads(out)
        self.assertEqual((data["passing"], data["total"]), (2, 3))

    def test_missing_size_is_an_error_not_a_default(self):
        self.write_pairs({"pairs": [{"id": "x", "fg": "#000", "bg": "#fff", "where": "somewhere"}]})
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs)
        self.assertNotEqual(rc, 0)
        self.assertIn("size", out)

    def test_duplicate_and_bad_ids_refused(self):
        self.write_pairs({"pairs": [PAIRS["pairs"][0], PAIRS["pairs"][0]]})
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs)
        self.assertNotEqual(rc, 0)
        self.assertIn("duplicate", out)
        self.write_pairs({"pairs": [dict(PAIRS["pairs"][0], id="Body Text")]})
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs)
        self.assertNotEqual(rc, 0)
        self.assertIn("lowercase", out)

    def test_bad_colour_in_pairs_names_the_pair(self):
        self.write_pairs({"pairs": [dict(PAIRS["pairs"][0], fg="#12")]})
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        self.assertNotEqual(rc, 0)
        self.assertIn("'body'", out)
        self.assertIsNone(ledger.load_coverage(self.d, SKILL))

    def test_empty_or_missing_file(self):
        self.write_pairs({"pairs": []})
        rc, out = run("--dir", self.d, "check", "--pairs", self.pairs)
        self.assertNotEqual(rc, 0)
        self.assertIn("pairs", out)
        rc, out = run("--dir", self.d, "check", "--pairs", os.path.join(self.tp.base, "nope.json"))
        self.assertNotEqual(rc, 0)
        self.assertIn("cannot read", out)


class Checklist(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_doc_and_tool_agree(self):
        items, text = contrast.items()
        ids = [i["id"] for i in items]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), MANUAL_IDS)
        for i in items:
            self.assertTrue(i["title"] and i["wcag"], i)
            self.assertRegex(i["wcag"], r"^\d+\.\d+\.\d+$")
            self.assertIn("### %s\n" % i["id"], text, "checklist.md has no section for %s" % i["id"])
        self.assertIn("fraction", text.lower())

    def test_prints_items_and_the_human_note(self):
        rc, out = run("--dir", self.d, "checklist")
        self.assertEqual(rc, 0, out)
        for cid in MANUAL_IDS | {"engine"}:
            self.assertIn(cid, out)
        self.assertIn("fraction", out)
        self.assertIsNone(ledger.load_coverage(self.d, SKILL))
        rc, full = run("--dir", self.d, "checklist", "--full")
        self.assertIn("### screen-reader", full)

    def test_record_creates_every_item_not_run(self):
        rc, out = run("--dir", self.d, "checklist", "--record")
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, SKILL)
        self.assertEqual({c["id"] for c in cov["checks"]}, MANUAL_IDS | {"engine"})
        self.assertTrue(all(c["status"] == "not_run" for c in cov["checks"]))

    def test_cover_and_na_and_status_marks(self):
        run("--dir", self.d, "checklist", "--record")
        p = self.tp.write("evidence/proof-a11y/keyboard.txt", "tabbed everything")
        rc, out = run("--dir", self.d, "cover", "focus-order", "--note", "ok", "--evidence", p)
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "na", "motion", "--note", "nothing moves")
        self.assertEqual(rc, 0, out)
        by = {c["id"]: c for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(by["focus-order"]["status"], "covered")
        self.assertEqual(by["focus-order"]["evidence"], [p])
        self.assertEqual(by["motion"]["status"], "not_applicable")
        rc, out = run("--dir", self.d, "checklist")
        self.assertIn("%s  focus-order" % ledger.OK, out)
        self.assertIn("na  motion", out)

    def test_cover_refusals(self):
        rc, out = run("--dir", self.d, "cover", "focus-order", "--note", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("checklist --record", out)
        run("--dir", self.d, "checklist", "--record")
        rc, out = run("--dir", self.d, "cover", "not-an-item", "--note", "x")
        self.assertNotEqual(rc, 0)
        rc, out = run("--dir", self.d, "cover", "alt-text", "--evidence", "evidence/proof-a11y/missing.png")
        self.assertNotEqual(rc, 0)
        self.assertIn("does not exist", out)
        rc, out = run("--dir", self.d, "na", "alt-text")  # no note
        self.assertNotEqual(rc, 0)

    def test_finding_gate(self):
        run("--dir", self.d, "checklist", "--record")
        rc, out = run("--dir", self.d, "finding", "add", "names-labels", "--title",
                      "Search button has no name", "--severity", "high", "--location", "/")
        self.assertEqual(rc, 0, out)
        f = ledger.load_findings(self.d, SKILL)[0]
        self.assertEqual(f["status"], "unverified")
        self.assertEqual(f["category"], "names-labels")
        self.assertEqual(f["references"], ["WCAG 4.1.2"])
        by = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, SKILL)["checks"]}
        self.assertEqual(by["names-labels"], "covered")
        rc, out = run("--dir", self.d, "finding", "verify", f["id"], "--by", "evidence-file")
        self.assertNotEqual(rc, 0)
        self.assertEqual(ledger.load_finding(self.d, SKILL, f["id"])["status"], "unverified")
        rc, out = run("--dir", self.d, "finding", "verify", f["id"], "--by", "manual", "--note", "confirmed by Sam")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_finding(self.d, SKILL, f["id"])["status"], "verified")
        run("--dir", self.d, "finding", "reject", f["id"], "--reason", "the name comes from aria-label")
        self.assertEqual(ledger.load_finding(self.d, SKILL, f["id"])["status"], "false_positive")

    def test_finding_wcag_override_and_unknown_check(self):
        p = self.tp.write("evidence/proof-a11y/axe.json", "{}")
        rc, out = run("--dir", self.d, "finding", "add", "engine", "--title", "Images without alt on /",
                      "--severity", "medium", "--wcag", "WCAG 1.1.1", "--evidence", p)
        self.assertEqual(rc, 0, out)
        f = ledger.load_findings(self.d, SKILL)[0]
        self.assertEqual(f["references"], ["WCAG 1.1.1"])
        rc, out = run("--dir", self.d, "finding", "verify", f["id"], "--by", "evidence-file")
        self.assertEqual(rc, 0, out)
        rc, out = run("--dir", self.d, "finding", "add", "not-a-check", "--title", "x", "--severity", "low")
        self.assertNotEqual(rc, 0)


class EndToEnd(unittest.TestCase):
    """What proof-report makes of a proof-a11y run."""

    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        ledger.write_target(self.d, {"name": "shop", "kind": "web", "authorised": True,
                                     "required_skills": [SKILL]})
        self.pairs = os.path.join(self.tp.base, "pairs.json")

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_pairs(self, obj):
        with open(self.pairs, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def finish_manual(self):
        run("--dir", self.d, "checklist", "--record")
        run("--dir", self.d, "na", "engine", "--note", "no engine installed")
        for cid in MANUAL_IDS:
            run("--dir", self.d, "cover", cid, "--note", "done on sign-in and checkout")

    def test_failing_pair_blocks_through_the_score(self):
        self.write_pairs(PAIRS)
        run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        self.finish_manual()
        self.assertEqual(validate.validate_dir(self.d)[0], [])
        r = verdict.compute(self.d)
        self.assertEqual(r["verdict"], "Not yet", r["checks"])
        kinds = {(b["kind"], b["id"]) for b in r["blockers"]}
        self.assertIn(("score", "contrast_pairs_passing"), kinds)
        self.assertNotIn(("finding", "contrast-footer"), kinds)  # medium is below the default fail_on

    def test_all_passing_ships(self):
        self.write_pairs({"pairs": [PAIRS["pairs"][0], PAIRS["pairs"][2]]})
        run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        self.finish_manual()
        self.assertEqual(validate.validate_dir(self.d)[0], [])
        self.assertEqual(verdict.compute(self.d)["verdict"], "Ship")

    def test_unfinished_manual_items_are_incomplete(self):
        self.write_pairs({"pairs": [PAIRS["pairs"][0]]})
        run("--dir", self.d, "check", "--pairs", self.pairs, "--record")
        run("--dir", self.d, "checklist", "--record")
        r = verdict.compute(self.d)
        self.assertEqual(r["verdict"], "Incomplete")
        self.assertTrue(any("screen-reader" in g or "not run" in g for g in r["gaps"]))


if __name__ == "__main__":
    unittest.main()
