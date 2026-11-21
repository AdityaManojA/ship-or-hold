import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

board = load_tool("proof-board", "board.py")
ledger = load_tool("proof-core", "ledger.py")

SEATS = ["investor", "growth", "builder", "operator"]


def run(*argv, stdin=None):
    out = io.StringIO()
    old = board.sys.stdin
    if stdin is not None:
        board.sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = board.main(list(argv))
    finally:
        board.sys.stdin = old
    return rc, out.getvalue()


def answer(seat, vote="go", risks=None, conditions=None, questions=None, summary=None):
    a = {"seat": seat, "vote": vote, "confidence": 0.7, "summary": summary or "As %s I have looked at it." % seat,
         "risks": risks or [], "conditions": conditions or [], "questions": questions or []}
    if vote == "go_with_conditions" and not a["conditions"]:
        a["conditions"] = [{"action": "Do the thing", "owner": seat, "by": "before launch"}]
    return a


def debate(seat, to, about="vote", final="go", challenge="That vote ignores the checkout evidence.",
           evidence=None, concessions=None, reason="The evidence stands as it is."):
    return {"seat": seat,
            "challenges": [{"to": to, "about": about, "challenge": challenge,
                            "evidence": evidence if evidence is not None else ["proof-web-e2e/coverage:checkout"]}],
            "concessions": concessions if concessions is not None else [], "final_vote": final,
            "final_reason": reason}


class Board(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.target()
        ledger.init_coverage(self.d, "proof-web-e2e", [("checkout", "Checkout")])
        self.tp.write("evidence/proof-web-e2e/c.png", b"png")
        ledger.mark_coverage(self.d, "proof-web-e2e", "checkout", "covered", evidence=["evidence/proof-web-e2e/c.png"])
        self.seats = [s["id"] for s in board.load_seats()]

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def target(self, required=("proof-web-e2e",), stage=None):
        t = {"name": "Shop", "kind": "web", "authorised": True, "required_skills": list(required)}
        if stage:
            t["stage"] = stage
        ledger.write_target(self.d, t)

    def write_all(self, vote="go", **over):
        for s in self.seats:
            a = over.get(s) or answer(s, vote)
            path = board._answer_path(self.d, s)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(a, fh)

    def write_debate(self, files):
        for seat, obj in files.items():
            path = board._debate_path(self.d, seat)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(obj, fh)

    def full_debate(self, **over):
        files = {"investor": debate("investor", "growth"), "growth": debate("growth", "builder"),
                 "builder": debate("builder", "operator"), "operator": debate("operator", "investor")}
        files.update(over)
        self.write_debate(files)

    def read(self, *parts):
        with open(os.path.join(self.d, *parts), encoding="utf-8") as fh:
            return fh.read()

    def decision(self):
        rc, out = run("--dir", self.d, "tally")
        return json.loads(self.read("board", "decision.json")), out

    def sec_finding(self, severity="critical"):
        f = ledger.new_finding("proof-sec-web", "Account takeover via reset token", severity,
                               evidence=["evidence/proof-web-e2e/c.png"])
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-sec-web", f["id"], by="evidence-file")
        return f

    # ------------------------------------------------------------ seats

    def test_four_seats_with_their_areas_and_vetoes(self):
        self.assertEqual(self.seats, SEATS)
        seats = {s["id"]: s for s in board.load_seats()}
        for sid in SEATS:
            self.assertTrue(seats[sid]["lens"], sid)
        self.assertEqual(seats["investor"]["veto"], ["proof-finance", "proof-pricing"])
        self.assertIn("proof-intake", seats["investor"]["skills"])
        self.assertEqual(seats["growth"]["veto"], [])
        for sk in ("proof-ads", "proof-fixkit", "proof-market", "proof-buyers"):
            self.assertIn(sk, seats["growth"]["skills"])
        for sk in ("proof-sec-web", "proof-web-e2e", "proof-a11y", "proof-fixkit"):
            self.assertIn(sk, seats["builder"]["skills"])
        for sk in ("proof-sec-web", "proof-web-e2e", "proof-api", "proof-ai", "proof-infra"):
            self.assertIn(sk, seats["builder"]["veto"])
        for sk in ("proof-ops", "proof-privacy", "proof-release"):
            self.assertIn(sk, seats["operator"]["veto"])

    def test_no_named_people_in_the_board(self):
        here = os.path.dirname(board.__file__)
        self.assertFalse(os.path.exists(os.path.join(here, "personas.json")))
        self.assertFalse(hasattr(board, "load_personas"))
        run("--dir", self.d, "brief")
        brief = self.read("board", "briefs", "panel.md")
        self.assertIn("archetype, not a person", brief)
        self.assertNotIn("LENS YOU APPLY", brief)
        self.assertNotIn('"quote"', brief)

    def test_brief_writes_presentation_and_briefs(self):
        rc, out = run("--dir", self.d, "brief")
        self.assertEqual(rc, 0, out)
        self.assertIn("[verdict]", self.read("board", "presentation.md"))
        for s in self.seats:
            self.assertTrue(os.path.exists(os.path.join(self.d, "board", "briefs", s + ".md")))
        self.assertIn("Convene 4 seat(s) of the Board in ONE Agent call", out)

    def test_dir_works_after_the_subcommand(self):
        rc, out = run("brief", "--dir", self.d)
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.d, "board", "presentation.md")))

    def test_each_seat_sees_its_area_in_full_and_the_rest_in_one_line(self):
        with open(os.path.join(self.d, "product.md"), "w", encoding="utf-8") as fh:
            fh.write("Shop sells hand-made mugs to gift buyers in the UK.")
        f = ledger.new_finding("proof-sec-deps", "pillow has a known heap overflow", "high")
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-sec-deps", f["id"], by="manual", note="checked by hand")
        run("--dir", self.d, "brief", "--separate")
        builder = self.read("board", "briefs", "builder.md")
        investor = self.read("board", "briefs", "investor.md")
        for text in (builder, investor):
            self.assertIn("hand-made mugs", text)
        self.assertIn("### proof-sec-deps", builder)
        self.assertNotIn("### proof-sec-deps", investor)
        self.assertIn("Outside your area, in one line each", investor)
        self.assertIn("- proof-sec-deps: [proof-sec-deps/%s] HIGH" % f["id"], investor)

    # --------------------------------------------------------- decision

    def test_all_go_on_ship_is_go(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Go")
        self.assertEqual(dec["panel_name"], "Board")
        self.assertEqual(dec["stage"], "team")
        self.assertEqual(dec["debate"], [])
        self.assertEqual(dec["vote_changes"], [])

    def test_conditions_vote_and_merge(self):
        run("--dir", self.d, "brief")
        c1 = [{"action": "Add a cookie consent banner that blocks trackers", "owner": "operator", "by": "before launch"}]
        c2 = [{"action": "Add cookie consent banner blocking the trackers", "owner": "operator", "by": "before launch"}]
        self.write_all("go", operator=answer("operator", "go_with_conditions", conditions=c1),
                       growth=answer("growth", "go_with_conditions", conditions=c2))
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Go with conditions")
        merged = [c for c in dec["conditions"] if c["from"] == "board" and "consent" in c["action"]]
        self.assertEqual(len(merged), 1)
        self.assertEqual(sorted(merged[0]["raised_by"]), ["growth", "operator"])
        self.assertEqual(merged[0]["owner"], "operator")

    def test_majority_hold(self):
        run("--dir", self.d, "brief")
        self.write_all("go", **{s: answer(s, "hold") for s in self.seats[:3]})
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        page = self.read("board", "meeting.html")
        self.assertIn("HOLD!", page)
        self.assertNotIn('class="tick"', page)

    def test_ledger_veto_overrides_votes(self):
        self.sec_finding()
        run("--dir", self.d, "brief")
        self.write_all("go")
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        self.assertTrue(any(v["seat"] == "builder" for v in dec["vetoes"]))
        fix = next(c for c in dec["conditions"] if c["from"] == "evidence")
        self.assertEqual(fix["owner"], "builder")

    def test_business_blockers_are_vetoes_from_the_ledger(self):
        for skill, fid in (("proof-pricing", "no-live-line"), ("proof-ops", "no-permission"),
                           ("proof-privacy", "trackers")):
            self.tp.write("evidence/%s/x.json" % skill, b"{}")
            f = ledger.new_finding(skill, "Blocking %s" % fid, "high", id=fid, evidence=["evidence/%s/x.json" % skill])
            ledger.write_finding(self.d, f)
            ledger.verify(self.d, skill, fid, by="evidence-file")
        ledger.write_scores(self.d, "proof-finance", [{"id": "contribution_per_unit", "value": -5, "unit": "Rs",
                                                       "budget": 0, "op": ">"}])
        r, _ = board.evidence(self.d)
        fired = {(v["seat"], v["ref"]) for v in board.vetoes(r, board.load_seats())}
        self.assertIn(("investor", "proof-pricing/no-live-line"), fired)
        self.assertIn(("investor", "proof-finance/score:contribution_per_unit"), fired)
        self.assertIn(("operator", "proof-ops/no-permission"), fired)
        self.assertIn(("operator", "proof-privacy/trackers"), fired)

    def test_incomplete_evidence_holds(self):
        self.target(required=("proof-web-e2e", "proof-api"))
        run("--dir", self.d, "brief")
        self.write_all("go")
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        self.assertIn("incomplete", " ".join(dec["reasons"]).lower())

    def test_no_quorum(self):
        run("--dir", self.d, "brief")
        path = board._answer_path(self.d, "investor")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(answer("investor"), fh)
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        self.assertIn("quorum", " ".join(dec["reasons"]).lower())

    # ------------------------------------------------------------ check

    def check_fails_with(self, seat, obj, msg):
        self.write_all("go", **{seat: obj})
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 1, out)
        self.assertIn(msg, out)

    def test_check_rejects_invented_and_uncited_evidence(self):
        run("--dir", self.d, "brief")
        self.check_fails_with("builder", answer("builder", "hold", risks=[
            {"risk": "made up", "severity": "high", "evidence": ["proof-x/nope"]}]), "not in the evidence")
        self.check_fails_with("builder", answer("builder", "hold", risks=[
            {"risk": "feels unsafe", "severity": "high", "evidence": []}]), "cites no evidence")

    def test_valid_citation_accepted_and_empty_quote_tolerated(self):
        run("--dir", self.d, "brief")
        ok = answer("builder", "go", risks=[{"risk": "only one flow tested", "severity": "high",
                                             "evidence": ["proof-web-e2e/coverage:checkout"], "quote": ""}])
        self.write_all("go", builder=ok)
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 0, out)

    def test_check_rejects_a_quote(self):
        run("--dir", self.d, "brief")
        self.check_fails_with("investor", answer("investor", "go", risks=[
            {"risk": "spend", "severity": "low", "evidence": [], "quote": "some-quote"}]), "quote no one")

    def test_check_rejects_rules_attributed_to_people(self):
        run("--dir", self.d, "brief")
        for text in ("Following Buffett's rule, never lose money on a sale.",
                     "As Peter Drucker put it, what gets measured gets managed.",
                     "Ogilvy said the consumer is not a moron.",
                     "According to Bezos, start from the customer."):
            self.check_fails_with("growth", answer("growth", "go", summary=text), "attributes a rule or saying")
        self.check_fails_with("growth", answer("growth", "go", risks=[
            {"risk": "Jobs once said focus means saying no.", "severity": "low", "evidence": []}]),
            "risk 1 attributes a rule or saying")
        # Seat names and plain sentences are not people.
        for text in ("The Investor's rule of thumb here is that a sale must earn money.",
                     "Buyers said the price was too high.", "As the Builder put it, checkout holds."):
            self.assertEqual(board.named_rules(text), [], text)

    def test_team_owners_are_the_four_seats(self):
        run("--dir", self.d, "brief")
        self.check_fails_with("growth", answer("growth", "go_with_conditions", conditions=[
            {"action": "Fix the hero copy", "owner": "cmo", "by": "before launch"}]), "is not a seat")

    def test_save_and_answer_question(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        q = answer("investor", "go", questions=["What is payback?"])
        rc, _ = run("--dir", self.d, "save", "investor", stdin=json.dumps(q))
        self.assertEqual(rc, 0)
        rc, _ = run("--dir", self.d, "answer", "investor", "1", "Month 8.")
        self.assertEqual(rc, 0)
        run("--dir", self.d, "tally")
        page = self.read("board", "meeting.html")
        self.assertIn("What is payback?", page)
        self.assertIn("Month 8.", page)

    # ----------------------------------------------------------- rebrief

    def test_new_brief_clears_answers_when_evidence_changes(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        f = ledger.new_finding("proof-web-e2e", "New bug", "low")
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-web-e2e", f["id"], by="manual", note="checked by hand")
        rc, out = run("--dir", self.d, "brief")
        self.assertIn("Cleared", out)
        self.assertFalse(os.path.exists(board._answer_path(self.d, "builder")))

    def test_rebrief_keeps_answers_whose_evidence_is_unchanged(self):
        first = ledger.new_finding("proof-finance", "Margin assumption noted", "low")
        ledger.write_finding(self.d, first)
        ledger.verify(self.d, "proof-finance", first["id"], by="manual", note="checked by hand")
        run("--dir", self.d, "brief")
        self.write_all("go")
        f = ledger.new_finding("proof-finance", "Small rounding note", "low")
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-finance", f["id"], by="manual", note="checked by hand")
        rc, out = run("--dir", self.d, "brief")
        self.assertEqual(rc, 0, out)
        self.assertIn("Kept 3 answer(s)", out)
        self.assertFalse(os.path.exists(board._answer_path(self.d, "investor")))
        self.assertTrue(os.path.exists(board._answer_path(self.d, "builder")))
        self.assertIn("id investor", self.read("board", "briefs", "panel.md"))
        self.assertNotIn("id builder", self.read("board", "briefs", "panel.md"))
        run("--dir", self.d, "brief", "--fresh")
        self.assertFalse(os.path.exists(board._answer_path(self.d, "builder")))

    def test_rebrief_with_nothing_changed_needs_no_meeting(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        rc, out = run("--dir", self.d, "brief")
        self.assertIn("No seat needs to meet again", out)

    def test_separate_briefs_on_request(self):
        rc, out = run("--dir", self.d, "brief", "--separate")
        self.assertIn("one Agent call per seat", out)
        self.assertIn("investor.md", out)

    def test_seat_subset(self):
        rc, out = run("--dir", self.d, "brief", "--seats", "investor,builder,operator")
        self.assertEqual(rc, 0)
        self.assertEqual(board._load_meta(self.d)["seats"], ["investor", "builder", "operator"])
        rc, out = run("--dir", self.d, "brief", "--seats", "investor,cfo")
        self.assertEqual(rc, 2)

    # ------------------------------------------------------------ debate

    def test_debate_brief_shows_the_other_answers(self):
        run("--dir", self.d, "brief")
        rk = {"risk": "Checkout copy promises next-day delivery", "severity": "medium",
              "evidence": ["proof-web-e2e/coverage:checkout"]}
        self.write_all("go", growth=answer("growth", "hold", risks=[rk]))
        rc, out = run("--dir", self.d, "debate-brief")
        self.assertEqual(rc, 0, out)
        text = self.read("board", "briefs", "debate.md")
        self.assertIn("### Growth (id growth): voted Hold", text)
        self.assertIn("- risk 1 (medium): Checkout copy promises next-day delivery "
                      "[evidence: proof-web-e2e/coverage:checkout]", text)
        for s in SEATS:
            self.assertIn(os.path.abspath(board._debate_path(self.d, s)), text)
        self.assertIn("final_vote", text)

    def test_debate_brief_needs_answers(self):
        run("--dir", self.d, "brief")
        rc, out = run("--dir", self.d, "debate-brief")
        self.assertEqual(rc, 2)
        self.assertIn("at least two valid", out)

    def test_debate_check_validation_errors(self):
        run("--dir", self.d, "brief")
        rk = {"risk": "Checkout is slow", "severity": "low", "evidence": []}
        self.write_all("go", growth=answer("growth", "go", risks=[rk]))
        run("--dir", self.d, "debate-brief")
        rc, out = run("--dir", self.d, "debate-check")
        self.assertEqual(rc, 1)
        self.assertIn("no debate file yet", out)
        cases = [
            (debate("investor", "investor"), "targets this seat itself"),
            (debate("investor", "legal"), "not a seat that answered"),
            (debate("investor", "growth", about=2), "raised 1 risk(s)"),
            (debate("investor", "builder", about=1), "raised no risks"),
            (debate("investor", "growth", evidence=["proof-x/nope"]), "not in the evidence"),
            (debate("investor", "growth", evidence=[]), "cites no evidence"),
            (debate("investor", "growth", final="maybe"), "final_vote must be one of"),
            (debate("investor", "growth", reason=""), "final_reason is required"),
            (debate("investor", "growth", challenge="Munger's rule says invert, always invert."),
             "attributes a rule or saying"),
            (dict(debate("investor", "growth"), challenges=[]), "at least one challenge"),
        ]
        for obj, msg in cases:
            self.full_debate(investor=obj)
            rc, out = run("--dir", self.d, "debate-check")
            self.assertEqual(rc, 1, msg)
            self.assertIn(msg, out)
        self.full_debate(investor=debate("investor", "growth", about=1))
        rc, out = run("--dir", self.d, "debate-check")
        self.assertEqual(rc, 0, out)
        self.assertIn("4 of 4 seats debated validly", out)

    def test_final_votes_count_and_vote_changes_are_recorded(self):
        run("--dir", self.d, "brief")
        self.write_all("go", investor=answer("investor", "hold"), growth=answer("growth", "hold"),
                       operator=answer("operator", "hold"))
        rc, out = run("--dir", self.d, "debate-brief")
        self.assertEqual(rc, 0, out)
        self.full_debate(
            investor=debate("investor", "builder", final="go_with_conditions",
                            reason="Checkout is covered; my hold was about a test that has now run.",
                            concessions=["The Builder is right that checkout works end to end."]),
            growth=debate("growth", "builder", final="go", reason="The flow evidence answers my concern."),
            operator=debate("operator", "investor", final="hold", reason="Nothing in the debate changes my view."))
        dec, out = self.decision()
        self.assertEqual(dec["decision"], "Go with conditions")  # first round was a 3 to 1 hold
        self.assertEqual(sorted(dec["votes"]["hold"]), ["operator"])
        self.assertEqual(dec["first_votes"]["investor"], "hold")
        changes = {c["seat"]: c for c in dec["vote_changes"]}
        self.assertEqual(sorted(changes), ["growth", "investor"])
        self.assertEqual(changes["investor"]["from"], "hold")
        self.assertEqual(changes["investor"]["to"], "go_with_conditions")
        self.assertIn("my hold was about", changes["investor"]["why"])
        self.assertEqual(len(dec["debate"]), 4)
        self.assertEqual(set(dec["debate"][0]), {"from", "to", "about", "challenge", "evidence"})
        self.assertIn("THE DEBATE", out)
        self.assertIn("Investor moves from HOLD to GO WITH CONDITIONS", out)
        page = self.read("board", "meeting.html")
        self.assertIn("The seats cross-examine", page)
        self.assertLess(page.index('aria-label="The debate"'), page.index('aria-label="The vote"'))
        self.assertIn("moved from Hold", page)
        self.assertIn("## The debate", self.read("board", "minutes.md"))

    def test_ledger_veto_still_overrides_the_debate(self):
        self.sec_finding()
        run("--dir", self.d, "brief")
        self.write_all("go")
        run("--dir", self.d, "debate-brief")
        self.full_debate()
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        self.assertTrue(any(v["seat"] == "builder" for v in dec["vetoes"]))
        self.assertIn("stand whatever the debate concluded", self.read("board", "debate.md"))

    def test_debate_md_is_one_short_page(self):
        run("--dir", self.d, "brief")
        rk = [{"risk": "Risk %d about the checkout" % i, "severity": "low", "evidence": []} for i in range(3)]
        self.write_all("go", growth=answer("growth", "go", risks=rk))
        run("--dir", self.d, "debate-brief")
        long = "This challenge goes on and on about the checkout. " * 20
        many = debate("investor", "growth", about=1, challenge=long)
        many["challenges"] += [{"to": "growth", "about": n, "challenge": "Another point %d." % n,
                                "evidence": ["verdict"]} for n in (2, 3)]
        self.full_debate(investor=many)
        run("--dir", self.d, "tally")
        text = self.read("board", "debate.md")
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Boardroom debate")
        self.assertEqual(sum(1 for x in lines if x.startswith("# ")), 1)
        for part in ("## Who challenged whom", "## What was conceded", "## Votes that moved", "## The decision",
                     "No vote moved"):
            self.assertIn(part, text)
        self.assertEqual(sum(1 for x in lines if x.startswith("- **Investor** to")), 2)
        self.assertIn("raised 1 more challenge (in the appendix)", text)
        self.assertNotIn(long.strip(), text)
        self.assertLess(len(lines), 45)

    def test_a_new_first_round_makes_the_debate_stale(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        run("--dir", self.d, "debate-brief")
        self.full_debate()
        self.write_all("go", investor=answer("investor", "go", summary="I changed my answer."))
        rc, out = run("--dir", self.d, "debate-check")
        self.assertEqual(rc, 1)
        self.assertIn("stale", out)
        dec, _ = self.decision()
        self.assertEqual(dec["debate"], [])
        self.assertFalse(os.path.exists(os.path.join(self.d, "board", "debate.md")))

    # ------------------------------------------------------------- stage

    def test_solo_stage_is_an_advisory_panel_with_buckets(self):
        self.target(stage="solo")
        self.sec_finding("high")
        rc, out = run("--dir", self.d, "brief")
        self.assertIn("Advisory Panel", out)
        panel = self.read("board", "briefs", "panel.md")
        self.assertIn("Advisory Panel", panel)
        for words in ("Do today", "Hire a freelancer", "Ask a CA or lawyer"):
            self.assertIn(words, panel)
        no_bucket = answer("growth", "go_with_conditions",
                           conditions=[{"action": "Rewrite the hero line", "owner": "growth", "by": "before launch"}])
        self.check_fails_with("growth", no_bucket, "needs a bucket")
        good = answer("growth", "go_with_conditions",
                      conditions=[{"action": "Rewrite the hero line", "bucket": "do_today", "by": "today"}])
        self.write_all("go", growth=good)
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 0, out)
        dec, out = self.decision()
        self.assertEqual(dec["panel_name"], "Advisory Panel")
        self.assertEqual(dec["stage"], "solo")
        self.assertIn("ADVISORY PANEL MEETING: Shop", out)
        for c in dec["conditions"]:
            self.assertIn(c["bucket"], ("do_today", "freelancer", "professional"))
        fix = next(c for c in dec["conditions"] if c["from"] == "evidence")
        self.assertEqual(fix["bucket"], "freelancer")
        self.assertNotIn("owner", fix)
        rewrite = next(c for c in dec["conditions"] if "hero" in c["action"])
        self.assertEqual(rewrite["bucket"], "do_today")
        self.assertIn("# Advisory Panel minutes: Shop", self.read("board", "minutes.md"))
        page = self.read("board", "meeting.html")
        self.assertIn("Advisory Panel meeting", page)
        self.assertIn("Hire a freelancer", page)

    def test_missing_stage_reads_as_team(self):
        r, _ = board.evidence(self.d)
        self.assertEqual(board.stage_of(r), "team")
        self.assertEqual(board.panel_name("team"), "Board")
        self.assertEqual(board.panel_name("company"), "Board")

    # ------------------------------------------------------- the meeting

    def test_meeting_page_is_interactive_and_escaped(self):
        run("--dir", self.d, "brief")
        self.write_all("go", builder=answer("builder", "go", risks=[{"risk": "<script>x</script>", "severity": "low",
                                                                    "evidence": []}]))
        run("--dir", self.d, "debate-brief")
        self.full_debate(growth=debate("growth", "builder", about=1, challenge="<b>bold</b> claim"))
        self.decision()
        page = self.read("board", "meeting.html")
        for part in ("Opening", "At the table", "Rules of the meeting", "Product and user flows", "The board asks",
                     "The debate", "The vote", "The decision", "Actions", "Full points", "<details", "Watch again",
                     'id="go-on"', "Paused while you read", 'class="flip"', "Cleared to launch.", 'class="tick"'):
            self.assertIn(part, page)
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)
        self.assertNotIn("<b>bold</b> claim", page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; claim", page)
        self.assertNotIn(" lens", page)
        self.assertTrue(all(ord(c) < 128 for c in page))

    def test_avatars_distinct(self):
        svgs = {s: board.avatar_svg(s, 56, "t") for s in self.seats}
        self.assertEqual(len(set(svgs.values())), 4)
        for s, svg in svgs.items():
            self.assertTrue(svg.startswith("<svg"))
            self.assertIn('id="h-%st"' % s, svg)
            self.assertTrue(all(ord(c) < 128 for c in svg))
        self.assertEqual(sorted(board.AVATAR), sorted(SEATS))
        self.assertEqual(len({board.AVATAR[s][1] for s in self.seats}), 4)

    def test_tally_prints_the_meeting(self):
        run("--dir", self.d, "brief")
        self.write_all("go", investor=answer("investor", "go", questions=["What is payback?"]))
        rc, out = run("--dir", self.d, "tally")
        for part in ("BOARD MEETING: Shop", "ITEM 1 of", "THE VOTE", "DECISION: GO", "/proof-business-plan"):
            self.assertIn(part, out)
        rc, quiet = run("--dir", self.d, "tally", "--quiet")
        self.assertNotIn("BOARD MEETING", quiet)
        rc, played = run("--dir", self.d, "play", "--speed", "0")
        self.assertIn("DECISION: GO", played)

    def test_product_points_lead_the_agenda(self):
        with open(os.path.join(self.d, "product.md"), "w", encoding="utf-8") as fh:
            fh.write("Shop sells mugs.")
        run("--dir", self.d, "brief")
        point = {"risk": "Gift buyers will not find it before Christmas", "severity": "high", "evidence": ["product"]}
        self.write_all("go", growth=answer("growth", "hold", risks=[point]))
        dec, out = self.decision()
        self.assertIn("THE PRODUCT AND THE MARKET", out)

    def test_agenda_walks_the_audits(self):
        self.sec_finding("high")
        ledger.write_scores(self.d, "proof-seo", [{"id": "seo_score", "value": 50, "unit": "points", "budget": 80, "op": ">="}])
        r, _ = board.evidence(self.d)
        seats = board.load_seats()
        builder = answer("builder", "hold", risks=[{"risk": "checkout untested at scale", "severity": "medium",
                                                    "evidence": ["proof-web-e2e/coverage:checkout"]}])
        investor = answer("investor", "hold", risks=[{"risk": "general unease", "severity": "low", "evidence": []}])
        items, general, untested = board.agenda(r, seats, {"builder": builder, "investor": investor},
                                                board.vetoes(r, seats))
        by = {it["id"]: it for it in items}
        self.assertEqual([it["id"] for it in items], ["flows", "security", "reach"])
        self.assertEqual(by["flows"]["outcome"], "Clear")
        self.assertEqual(by["security"]["outcome"], "Blocker")
        self.assertEqual(by["reach"]["outcome"], "Concerns")
        self.assertEqual([sp["seat"] for sp in by["flows"]["speakers"]], ["builder"])
        self.assertEqual([sp["seat"] for sp in general], ["investor"])
        self.assertIn("Finance", untested)

    def test_the_idea_file_stands_in_for_a_missing_product_dossier(self):
        self.assertIsNone(board.load_product(self.d))
        self.tp.write("business/idea.md", b"# Shop\n\n- What it is: a shop.\n")
        self.assertIn("What it is: a shop.", board.load_product(self.d))
        self.tp.write("product.md", b"The dossier wins.")
        self.assertEqual(board.load_product(self.d), "The dossier wins.")


class ClientText(unittest.TestCase):
    """Lessons from a real run: the debate and the veto are read by a founder, not by a developer."""

    def test_a_missing_number_reads_plainly_in_the_decision(self):
        gap = "proof-intake still has 1 check not run: unit-cost. Run them, or mark each not_applicable with a reason."
        self.assertEqual(board._plain_gap(gap), "the landed cost of one unit is still missing")
        other = "proof-a11y still has 2 checks not run: zoom-200, form-errors. Run them, or mark each not_applicable."
        self.assertEqual(board._plain_gap(other), "a11y: zoom 200 is not checked yet; form errors is not checked yet")
        self.assertNotIn("not_applicable", board._plain_gap(other))

    def test_trim_never_leaves_half_a_citation(self):
        text = ("A hold stops the only source of real conversion data; the loss comes from the ad budget, which a "
                "capped test fixes. [product, proof-finance/score:year1_operating_profit] More words follow here.")
        out = board._trim(text, 120)
        self.assertNotIn("[", out)
        self.assertNotIn("proof-", out)
        self.assertTrue(out.endswith("."))

    def test_a_missed_score_reads_in_words(self):
        why = board._plain_score({"id": "year1_operating_profit", "value": -580368.0, "op": ">", "budget": 0})
        self.assertNotIn("year1_operating_profit", why)
        self.assertIn("year-one operating profit", why)
        self.assertIn("above", why)


if __name__ == "__main__":
    unittest.main()
