import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

ledger = load_tool("proof-core", "ledger.py")
plan = load_tool("proof-business-plan", "plan.py")

SUMMARY = ("Demo is a cafe for office workers. " * 12) + "The verdict is %s. One thing to do this week: ask ten buyers."


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = plan.main(list(argv))
    return code, out.getvalue()


class Plan(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        # compile always prints a PDF; keep the suite from launching a real browser.
        self._browser = os.environ.get("PROOF_BROWSER")
        os.environ["PROOF_BROWSER"] = "no-such-browser-for-proof-tests"

    def tearDown(self):
        if self._browser is None:
            os.environ.pop("PROOF_BROWSER", None)
        else:
            os.environ["PROOF_BROWSER"] = self._browser
        self.tp.__exit__(None, None, None)

    def base(self, required=("proof-finance",)):
        ledger.write_target(self.d, {"name": "Demo", "kind": "web", "authorised": True,
                                     "required_skills": list(required)})
        self.tp.write("finance.md", b"# Demo - unit economics\n\n| key number | |\n")
        ledger.init_coverage(self.d, "proof-finance", [("unit-economics", "Unit economics")])
        ledger.mark_coverage(self.d, "proof-finance", "unit-economics", "covered", evidence=["finance.md"])

    def scores(self, value=2.5):
        ledger.write_scores(self.d, "proof-finance", [
            {"id": "contribution_per_unit", "value": value, "unit": "currency", "budget": 0, "op": ">",
             "evidence": ["finance.md"]}])

    def section(self, name, text):
        self.tp.write("business/%s" % name, text.encode("utf-8"))

    def read(self, name):
        with open(os.path.join(self.d, name), encoding="utf-8") as fh:
            return fh.read()

    def test_no_folder_is_an_error(self):
        code, out = run("--dir", os.path.join(self.tp.base, "nope"), "compile")
        self.assertEqual(code, 2)
        self.assertIn("no", out.lower())

    def test_check_lists_every_missing_section_with_its_skill(self):
        self.base()
        code, out = run("--dir", self.d, "check")
        for skill in ("proof-market", "proof-buyers", "proof-pricing", "proof-offer", "proof-go-to-market",
                      "proof-ops", "proof-board"):
            self.assertIn("/" + skill, out)
        self.assertIn("summary.md is not written yet", out)

    def test_exit_code_follows_the_verdict(self):
        self.base()
        self.scores()
        self.assertEqual(run("--dir", self.d, "check")[0], 0)        # Ship
        self.scores(-1)
        self.assertEqual(run("--dir", self.d, "check")[0], 1)        # Not yet
        ledger.write_target(self.d, {"name": "Demo", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-finance", "proof-market"]})
        self.assertEqual(run("--dir", self.d, "check")[0], 2)        # Incomplete

    def test_compile_includes_sections_and_demotes_their_headings(self):
        self.base()
        self.scores()
        self.section("market.md", "# Market and competition\n\n## The gap\n\nNobody ranks by view.\n")
        self.section("idea.md", "# Demo\n\n- What it is: a cafe.\n")
        run("--dir", self.d, "compile")
        text = self.read("business-plan.md")
        self.assertTrue(text.startswith("# Demo: business plan"))
        self.assertIn("## Market and competition", text)
        self.assertIn("### The gap", text)
        self.assertIn("Nobody ranks by view.", text)
        self.assertEqual(text.count("\n# "), 0, "an included section kept a top-level heading")
        self.assertIn("What it is: a cafe.", text)

    def test_a_missing_section_is_named_not_filled(self):
        self.base()
        run("--dir", self.d, "compile")
        text = self.read("business-plan.md")
        self.assertIn("Not done. Run /proof-buyers.", text)
        self.assertIn("## What is not done yet", text)
        self.assertIn("run /proof-ops", text)

    def test_key_numbers_show_the_bar_and_a_miss(self):
        self.base()
        self.scores(-1)
        run("--dir", self.d, "compile")
        for name in ("business-plan.md", "one-pager.md"):
            text = self.read(name)
            self.assertIn("finance: contribution per unit", text)
            self.assertIn("MISSES", text)
            self.assertIn("**Verdict: Not yet", text)

    def test_one_pager_repeats_the_numbers_word_for_word(self):
        self.base()
        self.scores(3)
        run("--dir", self.d, "compile")
        row = [line for line in self.read("business-plan.md").splitlines() if line.startswith("| finance:")][0]
        self.assertIn(row, self.read("one-pager.md"))

    def test_summary_must_state_the_verdict_and_fit_half_a_page(self):
        self.assertTrue(plan.summary_problems(None, "Ship"))
        self.assertTrue(plan.summary_problems("Too short. Ship.", "Ship"))
        self.assertTrue(plan.summary_problems(SUMMARY % "Ship", "Not yet"))
        self.assertTrue(plan.summary_problems(("word " * 500) + "Ship", "Ship"))
        self.assertEqual(plan.summary_problems(SUMMARY % "Not yet", "Not yet"), [])

    def test_summary_and_board_decision_are_carried_into_the_plan(self):
        self.base()
        self.scores()
        self.tp.write("summary.md", (SUMMARY % "Ship").encode("utf-8"))
        self.tp.write("board/decision.json", json.dumps({
            "decision": "Go with conditions", "verdict": "Ship", "reasons": ["A director raised conditions."],
            "votes": {"go": ["ceo"], "go_with_conditions": ["cfo", "cmo"], "hold": []},
            "conditions": [{"action": "Ask ten buyers", "owner": "sales", "by": "before launch"}]}).encode("utf-8"))
        run("--dir", self.d, "compile")
        text = self.read("business-plan.md")
        self.assertIn("Board decision: Go with conditions", text)
        self.assertIn("1 go, 2 go with conditions, 0 hold", text)
        self.assertIn("Ask ten buyers (owner SALES, before launch)", text)
        self.assertIn("One thing to do this week", text)
        one = self.read("one-pager.md")
        self.assertIn("**Board: Go with conditions.**", one)
        self.assertIn("- Ask ten buyers", one)

    def test_a_board_that_met_on_an_older_verdict_is_flagged(self):
        self.base()
        self.scores()
        self.tp.write("board/decision.json", json.dumps({
            "decision": "Hold", "verdict": "Incomplete", "reasons": [], "votes": {}, "conditions": []}).encode("utf-8"))
        _, out = run("--dir", self.d, "check")
        self.assertIn("run /proof-board again", out)

    def test_engineering_gets_one_line_per_check_and_its_top_findings(self):
        self.base(required=("proof-finance", "proof-privacy"))
        self.tp.write("evidence/proof-privacy/home.html", b"<html>")
        ledger.init_coverage(self.d, "proof-privacy", [("trackers", "Trackers wait for consent")])
        ledger.mark_coverage(self.d, "proof-privacy", "trackers", "covered",
                             evidence=["evidence/proof-privacy/home.html"])
        f = ledger.new_finding("proof-privacy", "A tracker loads before consent", "high", id="tracker",
                               evidence=["evidence/proof-privacy/home.html"])
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-privacy", "tracker", by="evidence-file")
        run("--dir", self.d, "compile")
        text = self.read("business-plan.md")
        self.assertIn("| privacy | 1 of 1 checks done; verified findings: 1 high |", text)
        self.assertIn("- A tracker loads before consent (high, privacy)", text)
        engineering = text.split("engineering and quality")[1].split("## What is not done")[0]
        self.assertNotIn("| finance |", engineering)

    def test_idea_falls_back_to_the_product_dossier(self):
        self.base()
        self.tp.write("product.md", b"# Demo\n\nA cafe that sells matcha.\n")
        run("--dir", self.d, "compile")
        self.assertIn("A cafe that sells matcha.", self.read("business-plan.md"))

    def test_idea_template_refuses_to_replace_a_file(self):
        self.assertEqual(run("--dir", self.d, "idea-template")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "business", "idea.md")))
        self.assertEqual(run("--dir", self.d, "idea-template")[0], 2)
        self.assertEqual(run("--dir", self.d, "idea-template", "--force")[0], 0)

    def board(self, answered=("cfo",)):
        self.tp.write("board/decision.json", json.dumps({
            "decision": "Hold", "verdict": "Ship", "reasons": [], "answered": list(answered),
            "votes": {"go": [], "go_with_conditions": [], "hold": list(answered)}, "conditions": []}).encode("utf-8"))
        for seat, vote in (("cfo", "hold"), ("ceo", "go")):
            self.tp.write("board/answers/%s.json" % seat, json.dumps({
                "seat": seat, "vote": vote, "confidence": 0.85, "summary": "As %s: the year loses money." % seat,
                "risks": [{"risk": "The year | loses money", "severity": "high",
                           "evidence": ["proof-finance/score:year1_operating_profit"], "quote": ""}],
                "conditions": [{"action": "Replace the estimates", "owner": seat, "by": "before launch"}],
                "questions": ["What does one set cost?", "How many orders last month?"]}).encode("utf-8"))
        self.tp.write("board/qa.json", json.dumps({"cfo": {"0": "Not covered; the owner has to answer."}}).encode("utf-8"))

    def test_every_counted_director_is_in_the_plan_with_risks_conditions_and_questions(self):
        self.base()
        self.scores()
        self.board(answered=("cfo",))
        run("--dir", self.d, "compile")
        text = self.read("business-plan.md")
        self.assertIn("### Director by director", text)
        self.assertIn("#### Chief Financial Officer: Hold (confidence 85%)", text)
        self.assertIn("As cfo: the year loses money.", text)
        # a pipe inside the risk cannot break the table, and the evidence is named
        self.assertIn("| high | The year / loses money | `proof-finance/score:year1_operating_profit` |", text)
        self.assertIn("| Replace the estimates | CFO | before launch |", text)
        self.assertIn("1. What does one set cost?\n   Team: Not covered; the owner has to answer.", text)
        self.assertIn("2. How many orders last month?\n   Team: not answered yet", text)
        # a seat the board did not count is left out, not shown
        self.assertNotIn("Chief Executive Officer", text)

    def test_directors_follow_the_board_seat_order(self):
        self.base()
        self.board(answered=("cfo", "ceo"))
        run("--dir", self.d, "compile")
        text = self.read("business-plan.md")
        self.assertLess(text.index("#### Chief Executive Officer"), text.index("#### Chief Financial Officer"))

    def test_no_board_means_no_director_section(self):
        self.base()
        run("--dir", self.d, "compile")
        self.assertNotIn("Director by director", self.read("business-plan.md"))

    def test_html_rendering_escapes_and_keeps_structure(self):
        out = plan.md_to_html("# Title\n\nA <script>x</script> **bold** [ok](https://e.example) [no](javascript:x)\n\n"
                              "| a | b |\n| --- | --- |\n| 1 | `c` |\n\n1. one\n   Team: two\n- dot\n")
        self.assertIn("<h1>Title</h1>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)
        self.assertIn("<strong>bold</strong>", out)
        self.assertIn('<a href="https://e.example">ok</a>', out)
        self.assertNotIn('href="javascript', out)
        self.assertIn("<th>a</th>", out)
        self.assertIn("<td><code>c</code></td>", out)
        self.assertIn("<ol><li>one<br>Team: two</li></ol>", out)
        self.assertIn("<ul><li>dot</li></ul>", out)

    def test_pdf_needs_a_compiled_plan(self):
        self.base()
        code, out = run("--dir", self.d, "pdf")
        self.assertEqual(code, 2)
        self.assertIn("compile first", out)

    def test_compile_always_makes_the_pdf_page_and_never_fails_on_printing(self):
        self.base()
        self.scores()
        code, out = run("--dir", self.d, "compile")
        self.assertEqual(code, 0)  # the verdict decides the exit code, not the printer
        page = self.read("business-plan.html")
        self.assertIn("<div class='cover'>", page)
        self.assertIn("<strong>Verdict: Ship</strong>", page)
        self.assertIn("The board has not met.", page)
        self.assertIn("No Chrome, Chromium or Edge", out)
        os.remove(os.path.join(self.d, "business-plan.html"))
        run("--dir", self.d, "compile", "--no-pdf")
        self.assertFalse(os.path.exists(os.path.join(self.d, "business-plan.html")))

    def test_pdf_without_a_browser_writes_the_html_and_says_so(self):
        self.base()
        run("--dir", self.d, "compile", "--no-pdf")
        code, out = run("--dir", self.d, "pdf")
        self.assertEqual(code, 2)
        self.assertIn("No Chrome, Chromium or Edge", out)
        page = self.read("business-plan.html")
        self.assertIn("<h1>Demo: business plan</h1>", page)
        self.assertFalse(os.path.exists(os.path.join(self.d, "business-plan.pdf")))

    def test_an_explicit_browser_path_wins(self):
        fake = os.path.join(self.tp.base, "chrome.exe")
        with open(fake, "w") as fh:
            fh.write("x")
        self.assertEqual(plan.find_browser(fake), fake)

    def test_headings_inside_code_fences_are_left_alone(self):
        self.assertEqual(plan._demote("# A\n```\n# not a heading\n```\n## B"),
                         "## A\n```\n# not a heading\n```\n### B")


if __name__ == "__main__":
    unittest.main()
