import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

swarm = load_tool("proof-swarm", "swarm.py")

PROFILE = {
    "name": "t-run",
    "purpose": "test",
    "dimensions": {
        "device": [{"value": "phone", "share": 0.7, "detail": "small screen"},
                   {"value": "laptop", "share": 0.3}],
    },
    "answer": {
        "outcome": "found",
        "fields": {
            "found": {"type": "boolean", "prompt": "did you find a problem"},
            "sev": {"type": "enum", "values": ["low", "high"]},
            "note": {"type": "string"},
            "minutes": {"type": "number"},
        },
    },
}
BRIEF = "You are {name} on a {device}. {device.detail} Purpose: {purpose}.\n"


def run(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    old = swarm.sys.stdin
    if stdin is not None:
        swarm.sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = swarm.main(list(argv))
    finally:
        swarm.sys.stdin = old
    return rc, out.getvalue() + err.getvalue()


class Swarm(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.profile = os.path.join(self.tp.base, "profile.json")
        self.brief = os.path.join(self.tp.base, "brief.md")
        self.write_inputs(PROFILE, BRIEF)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def write_inputs(self, profile, brief):
        with open(self.profile, "w", encoding="utf-8") as fh:
            json.dump(profile, fh)
        with open(self.brief, "w", encoding="utf-8") as fh:
            fh.write(brief)

    def run_dir(self):
        return os.path.join(self.d, "swarm", "t-run")

    def cards(self):
        with open(os.path.join(self.run_dir(), "cards.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def answer(self, cid, **over):
        a = {"found": True, "sev": "high", "note": "button did nothing", "minutes": 3}
        a.update(over)
        path = os.path.join(self.run_dir(), "answers", cid + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(a, fh)

    def init(self, *extra):
        return run("--dir", self.d, "init", "--profile", self.profile, "--brief", self.brief, *extra)

    # ------------------------------------------------------------- dealing

    def test_same_seed_same_cards(self):
        self.assertEqual(self.init("--n", "30", "--seed", "42")[0], 0)
        first = self.cards()
        self.assertEqual(self.init("--n", "30", "--seed", "42")[0], 0)
        self.assertEqual(first, self.cards())
        self.assertEqual(self.init("--n", "30", "--seed", "43")[0], 0)
        self.assertNotEqual(first, self.cards())

    def test_shares_roughly_respected(self):
        self.init("--n", "200", "--seed", "7")
        phones = sum(1 for c in self.cards() if c["device"] == "phone")
        self.assertAlmostEqual(phones / 200.0, 0.7, delta=0.12)

    def test_quick_is_twenty(self):
        self.init("--quick", "--seed", "1")
        self.assertEqual(len(self.cards()), 20)

    def test_bad_shares_refused(self):
        bad = json.loads(json.dumps(PROFILE))
        bad["dimensions"]["device"][1]["share"] = 0.5
        self.write_inputs(bad, BRIEF)
        rc, out = self.init("--seed", "1")
        self.assertEqual(rc, 2)
        self.assertIn("sum to", out)

    def test_outcome_must_be_boolean(self):
        bad = json.loads(json.dumps(PROFILE))
        bad["answer"]["outcome"] = "note"
        self.write_inputs(bad, BRIEF)
        rc, out = self.init("--seed", "1")
        self.assertEqual(rc, 2)
        self.assertIn("boolean", out)

    def test_locale_share_strings(self):
        p = json.loads(json.dumps(PROFILE))
        p["dimensions"]["device"][0]["share"] = "0,7"
        p["dimensions"]["device"][1]["share"] = "0,3"
        self.write_inputs(p, BRIEF)
        self.assertEqual(self.init("--n", "5", "--seed", "1")[0], 0)

    # -------------------------------------------------------- stale state

    def test_reinit_clears_answers(self):
        self.init("--n", "3", "--seed", "1")
        self.answer("a001")
        rc, out = self.init("--n", "3", "--seed", "1")
        self.assertEqual(rc, 0)
        self.assertIn("Cleared", out)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir(), "answers", "a001.json")))

    def test_keep_answers_needs_identical_brief(self):
        self.init("--n", "3", "--seed", "1")
        self.answer("a001")
        self.assertEqual(self.init("--n", "3", "--seed", "1", "--keep-answers")[0], 0)
        self.assertTrue(os.path.exists(os.path.join(self.run_dir(), "answers", "a001.json")))
        self.write_inputs(PROFILE, BRIEF + "A new line.\n")
        rc, out = self.init("--n", "3", "--seed", "1", "--keep-answers")
        self.assertEqual(rc, 2)
        self.assertIn("brief changed", out)

    # ------------------------------------------------------------- prompts

    def test_prompts_render_and_end_with_honesty_block(self):
        self.init("--n", "2", "--seed", "1")
        self.assertEqual(run("--dir", self.d, "prompts")[0], 0)
        card = self.cards()[0]
        with open(os.path.join(self.run_dir(), "briefs", card["id"] + ".md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("You are %s on a %s." % (card["name"], card["device"]), text)
        self.assertIn("do not be agreeable", text)
        self.assertIn(os.path.abspath(os.path.join(self.run_dir(), "answers", card["id"] + ".json")), text)
        self.assertIn('"found"', text)
        for token in ("{name}", "{device}", "{device.detail}", "{purpose}", "{out}", "{answer_schema}"):
            self.assertNotIn(token, text)

    def test_unknown_placeholder_refused(self):
        self.write_inputs(PROFILE, BRIEF + "Your age is {age}.\n")
        self.init("--n", "2", "--seed", "1")
        rc, out = run("--dir", self.d, "prompts")
        self.assertEqual(rc, 2)
        self.assertIn("age", out)

    def test_max_attempts_excludes(self):
        self.init("--n", "2", "--seed", "1")
        run("--dir", self.d, "prompts")
        run("--dir", self.d, "prompts")
        rc, out = run("--dir", self.d, "prompts")
        self.assertEqual(rc, 0)
        self.assertIn("excluded", out)
        self.assertIn("No briefs left", out)

    # -------------------------------------------------------- check / save

    def test_check_flags_bad_answer(self):
        self.init("--n", "2", "--seed", "1")
        self.answer("a001")
        self.answer("a002", sev="medium")
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 1)
        self.assertIn("a002", out)
        self.assertNotIn("a001:", out)

    def test_wrong_id_refused(self):
        self.init("--n", "2", "--seed", "1")
        self.answer("a001", id="a002")
        rc, out = run("--dir", self.d, "check")
        self.assertEqual(rc, 1)
        self.assertIn("belongs to a001", out)

    def test_save(self):
        self.init("--n", "2", "--seed", "1")
        rc, out = run("--dir", self.d, "save", "a001", stdin='{"found": "yes"}')
        self.assertEqual(rc, 2)
        good = json.dumps({"found": False, "sev": "low", "note": "", "minutes": "2,5"})
        self.assertEqual(run("--dir", self.d, "save", "a001", stdin=good)[0], 0)
        rc, out = run("--dir", self.d, "save", "zzz", stdin=good)
        self.assertEqual(rc, 2)

    # --------------------------------------------------------------- tally

    def test_tally(self):
        self.init("--n", "6", "--seed", "3")
        cards = self.cards()
        found = {}
        for i, c in enumerate(cards[:5]):
            yes = i % 2 == 0
            found[c["id"]] = yes
            self.answer(c["id"], found=yes, note="same words" if yes else "fine", minutes=i)
        self.assertEqual(run("--dir", self.d, "tally")[0], 0)
        with open(os.path.join(self.run_dir(), "results.json"), encoding="utf-8") as fh:
            res = json.load(fh)
        self.assertEqual(res["answered"], 5)
        self.assertEqual(res["excluded"], 1)
        self.assertEqual(res["outcome_yes"], 3)
        self.assertAlmostEqual(res["outcome_rate"], 0.6)
        self.assertEqual(res["seed"], 3)
        for val, row in res["by_dimension"]["device"].items():
            ids = [c["id"] for c in cards[:5] if c["device"] == val]
            self.assertEqual(row["count"], len(ids))
            self.assertEqual(row["yes"], sum(1 for i in ids if found[i]))
        top = res["fields"]["note"]["top"]
        self.assertEqual(top[0], {"text": "same words", "count": 3})
        self.assertEqual(res["fields"]["minutes"]["min"], 0)
        self.assertEqual(res["fields"]["minutes"]["max"], 4)
        self.assertEqual(res["fields"]["minutes"]["median"], 2)
        self.assertTrue(os.path.exists(os.path.join(self.run_dir(), "results.md")))


if __name__ == "__main__":
    unittest.main()
