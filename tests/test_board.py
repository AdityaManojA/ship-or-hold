import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

board = load_tool("proof-board", "board.py")
ledger = load_tool("proof-core", "ledger.py")


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


def answer(seat, vote="go", risks=None, conditions=None, questions=None):
    a = {"seat": seat, "vote": vote, "confidence": 0.7, "summary": "As %s I have looked at it." % seat,
         "risks": risks or [], "conditions": conditions or [], "questions": questions or []}
    if vote == "go_with_conditions" and not a["conditions"]:
        a["conditions"] = [{"action": "Do the thing", "owner": seat, "by": "before launch"}]
    return a


class Board(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        ledger.write_target(self.d, {"name": "Shop", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e"]})
        ledger.init_coverage(self.d, "proof-web-e2e", [("checkout", "Checkout")])
        self.tp.write("evidence/proof-web-e2e/c.png", b"png")
        ledger.mark_coverage(self.d, "proof-web-e2e", "checkout", "covered", evidence=["evidence/proof-web-e2e/c.png"])
        self.seats = [s["id"] for s in board.load_seats()]

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_all(self, vote="go", **over):
        for s in self.seats:
            a = over.get(s) or answer(s, vote)
            path = board._answer_path(self.d, s)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(a, fh)

    def decision(self):
        rc, out = run("--dir", self.d, "tally")
        with open(os.path.join(self.d, "board", "decision.json"), encoding="utf-8") as fh:
            return json.load(fh), out

    def test_seats(self):
        self.assertEqual(len(self.seats), 9)
        for must in ("ceo", "cto", "cfo", "cmo", "sales", "coo", "ciso", "cpo", "legal"):
            self.assertIn(must, self.seats)
        seat = next(s for s in board.load_seats() if s["id"] == "cfo")
        self.assertTrue(seat["lens"])

    def test_brief_writes_presentation_and_briefs(self):
        rc, out = run("--dir", self.d, "brief")
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.d, "board", "presentation.md"), encoding="utf-8") as fh:
            pres = fh.read()
        self.assertIn("[verdict]", pres)
        self.assertIn("proof-web-e2e", pres)
        for s in self.seats:
            self.assertTrue(os.path.exists(os.path.join(self.d, "board", "briefs", s + ".md")))

    def test_all_go_on_ship_is_go(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Go")

    def test_conditions_vote(self):
        run("--dir", self.d, "brief")
        self.write_all("go", cmo=answer("cmo", "go_with_conditions"))
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Go with conditions")
        self.assertTrue(any("cmo" in c.get("raised_by", []) for c in dec["conditions"]))

    def test_similar_conditions_merge(self):
        run("--dir", self.d, "brief")
        c1 = [{"action": "Add a cookie consent banner that blocks trackers", "owner": "legal", "by": "before launch"}]
        c2 = [{"action": "Add cookie consent banner blocking the trackers", "owner": "legal", "by": "before launch"}]
        self.write_all("go", legal=answer("legal", "go_with_conditions", conditions=c1),
                       cmo=answer("cmo", "go_with_conditions", conditions=c2))
        dec, _ = self.decision()
        merged = [c for c in dec["conditions"] if c["from"] == "board" and "consent" in c["action"]]
        self.assertEqual(len(merged), 1)
        self.assertEqual(sorted(merged[0]["raised_by"]), ["cmo", "legal"])

    def test_majority_hold(self):
        run("--dir", self.d, "brief")
        over = {s: answer(s, "hold") for s in self.seats[:5]}
        self.write_all("go", **over)
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")

    def test_ledger_veto_overrides_votes(self):
        f = ledger.new_finding("proof-sec-web", "Account takeover via reset token", "critical",
                               evidence=["evidence/proof-web-e2e/c.png"])
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-sec-web", f["id"], by="evidence-file")
        run("--dir", self.d, "brief")
        self.write_all("go")
        dec, out = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        self.assertTrue(any(v["seat"] == "ciso" for v in dec["vetoes"]))

    def test_incomplete_evidence_holds(self):
        ledger.write_target(self.d, {"name": "Shop", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e", "proof-api"]})
        run("--dir", self.d, "brief")
        self.write_all("go")
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        self.assertIn("incomplete", " ".join(dec["reasons"]).lower())

    def test_no_quorum(self):
        run("--dir", self.d, "brief")
        for s in self.seats[:3]:
            path = board._answer_path(self.d, s)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(answer(s), fh)
        dec, _ = self.decision()
        self.assertEqual(dec["decision"], "Hold")
        self.assertIn("quorum", " ".join(dec["reasons"]).lower())

    def test_check_rejects_invented_evidence(self):
        run("--dir", self.d, "brief")
        bad = answer("cto", "hold", risks=[{"risk": "made up", "severity": "high", "evidence": ["proof-x/nope"]}])
        self.write_all("go", cto=bad)
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 1)
        self.assertIn("not in the evidence", out)

    def test_check_rejects_uncited_high_risk(self):
        run("--dir", self.d, "brief")
        bad = answer("ciso", "hold", risks=[{"risk": "feels unsafe", "severity": "high", "evidence": []}])
        self.write_all("go", ciso=bad)
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 1)
        self.assertIn("cites no evidence", out)

    def test_valid_citation_accepted(self):
        run("--dir", self.d, "brief")
        ok = answer("cto", "go", risks=[{"risk": "only one flow tested", "severity": "high",
                                         "evidence": ["proof-web-e2e/coverage:checkout"]}])
        self.write_all("go", cto=ok)
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 0, out)

    def test_save_and_answer_question(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        q = answer("cfo", "go", questions=["What is payback?"])
        rc, _ = run("--dir", self.d, "save", "cfo", stdin=json.dumps(q))
        self.assertEqual(rc, 0)
        rc, _ = run("--dir", self.d, "answer", "cfo", "1", "Month 8.")
        self.assertEqual(rc, 0)
        run("--dir", self.d, "tally")
        with open(os.path.join(self.d, "board", "meeting.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("What is payback?", page)
        self.assertIn("Month 8.", page)

    def test_new_brief_clears_answers_when_evidence_changes(self):
        run("--dir", self.d, "brief")
        self.write_all("go")
        f = ledger.new_finding("proof-web-e2e", "New bug", "low")
        ledger.write_finding(self.d, f)
        rc, out = run("--dir", self.d, "brief", "--keep-answers")
        self.assertIn("Cleared", out)
        self.assertFalse(os.path.exists(board._answer_path(self.d, "ceo")))

    def test_meeting_page_is_interactive_and_escaped(self):
        run("--dir", self.d, "brief")
        self.write_all("go", cpo=answer("cpo", "go", risks=[{"risk": "<script>x</script>", "severity": "low",
                                                              "evidence": []}]))
        self.decision()
        with open(os.path.join(self.d, "board", "meeting.html"), encoding="utf-8") as fh:
            page = fh.read()
        for part in ("Opening", "The room", "Agenda", "Product and user flows", "Item outcome",
                     "Questions from the board", "The vote", "The decision", "Actions", "Show all"):
            self.assertIn(part, page)
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)
        self.assertTrue(all(ord(c) < 128 for c in page))

    def test_agenda_walks_the_audits(self):
        f = ledger.new_finding("proof-sec-web", "Account takeover", "high", evidence=["evidence/proof-web-e2e/c.png"])
        ledger.write_finding(self.d, f)
        ledger.verify(self.d, "proof-sec-web", f["id"], by="evidence-file")
        ledger.write_scores(self.d, "proof-seo", [{"id": "seo_score", "value": 50, "unit": "points", "budget": 80, "op": ">="}])
        r, _ = board.evidence(self.d)
        seats = board.load_seats()
        cto = answer("cto", "hold", risks=[{"risk": "checkout untested at scale", "severity": "medium",
                                            "evidence": ["proof-web-e2e/coverage:checkout"]}])
        ceo = answer("ceo", "hold", risks=[{"risk": "general unease", "severity": "low", "evidence": []}])
        items, general, untested = board.agenda(r, seats, {"cto": cto, "ceo": ceo}, board.vetoes(r, seats))
        by = {it["id"]: it for it in items}
        self.assertEqual([it["id"] for it in items], ["flows", "security", "reach"])  # board order
        self.assertEqual(by["flows"]["outcome"], "Clear")
        self.assertEqual(by["security"]["outcome"], "Blocker")
        self.assertEqual(by["reach"]["outcome"], "Concerns")
        self.assertEqual([sp["seat"] for sp in by["flows"]["speakers"]], ["cto"])
        self.assertEqual([sp["seat"] for sp in general], ["ceo"])
        self.assertIn("Finance", untested)

    def test_required_but_missing_area_is_not_covered(self):
        ledger.write_target(self.d, {"name": "Shop", "kind": "web", "authorised": True,
                                     "required_skills": ["proof-web-e2e", "proof-finance"]})
        r, _ = board.evidence(self.d)
        items, _, _ = board.agenda(r, board.load_seats(), {}, [])
        fin = next(it for it in items if it["id"] == "finance")
        self.assertEqual(fin["outcome"], "Not covered")

    def test_avatars_distinct(self):
        svgs = {s: board.avatar_svg(s, 56, "t") for s in self.seats}
        self.assertEqual(len(set(svgs.values())), len(self.seats))  # every seat looks different
        for s, svg in svgs.items():
            self.assertTrue(svg.startswith("<svg"))
            self.assertIn('id="h-%st"' % s, svg)  # unique gradient ids per suffix
            self.assertTrue(all(ord(c) < 128 for c in svg))
        colours = {board.AVATAR[s][1] for s in self.seats}
        self.assertEqual(len(colours), len(self.seats))  # every seat has its own colour

    def test_seat_subset(self):
        rc, out = run("--dir", self.d, "brief", "--seats", "ceo,cto,cfo")
        self.assertEqual(rc, 0)
        meta = board._load_meta(self.d)
        self.assertEqual(meta["seats"], ["ceo", "cto", "cfo"])
        rc, out = run("--dir", self.d, "brief", "--seats", "ceo,chef")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
