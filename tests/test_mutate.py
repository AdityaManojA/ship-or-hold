import contextlib
import json
import io
import os
import sys
import unittest

from _load import TempProof, load_tool

mutate = load_tool("proof-mutation", "mutate.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = mutate.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class Mutate(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.proj = os.path.join(self.tp.base, "proj")
        os.makedirs(self.proj)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_find_sites(self):
        p = os.path.join(self.proj, "m.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("def f(a, b):\n    return a < b and b > 0\n")
        sites = mutate.find_sites(p)
        kinds = {(o, r) for _, _, o, r, _ in sites}
        self.assertIn(("<", ">="), kinds)
        self.assertIn(("and", "or"), kinds)

    def test_apply_single_span(self):
        lines = ["return a < b\n"]
        out = mutate._apply(lines, (1, 9), (1, 10), ">=")
        self.assertEqual("".join(out), "return a >= b\n")

    def test_run_scores_and_restores(self):
        # A module whose test only checks one branch, so some mutants survive.
        mod = os.path.join(self.proj, "calc.py")
        original = "def sign(n):\n    if n > 0:\n        return 1\n    return 0\n"
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write(original)
        test = os.path.join(self.proj, "check.py")
        with open(test, "w", encoding="utf-8") as fh:
            fh.write("import calc\nassert calc.sign(5) == 1\n")
        cmd = '%s "%s"' % (_q(sys.executable), test)
        rc, out = run("--dir", self.d, "run", "--src", mod, "--test-cmd", cmd,
                      "--max", "10", "--timeout", "30", "--min-score", "0.1")
        self.assertEqual(rc, 0, out)
        # the file is restored byte-for-byte
        with open(mod, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original)
        scores = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-mutation"]
        self.assertTrue(scores)
        self.assertIn("mutation_score", {m["id"] for m in scores[0]["metrics"]})

    def _calc_project(self):
        mod = os.path.join(self.proj, "calc.py")
        original = "def sign(n):\n    if n > 0:\n        return 1\n    return 0\n"
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write(original)
        test = os.path.join(self.proj, "check.py")
        # The check also fails if the real calc.py ever changes, proving the run used a copy.
        with open(test, "w", encoding="utf-8") as fh:
            fh.write("import calc, sys\nassert calc.sign(5) == 1 and calc.sign(-1) == 0\n"
                     "real = open(%r, encoding='utf-8').read()\n"
                     "open(%r, 'a').write('touched\\n') if real != %r else None\n"
                     % (mod, os.path.join(self.tp.base, "marker.txt"), original))
        return mod, original, '%s "%s"' % (_q(sys.executable), test)

    def test_sandbox_never_touches_the_real_file(self):
        mod, original, cmd = self._calc_project()
        before = os.stat(mod).st_mtime_ns
        rc, out = run("--dir", self.d, "run", "--src", mod, "--test-cmd", cmd, "--max", "10", "--timeout", "30")
        self.assertEqual(rc, 0, out)
        self.assertIn("not touched", out)
        self.assertEqual(os.stat(mod).st_mtime_ns, before)
        with open(mod, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original)
        self.assertFalse(os.path.exists(os.path.join(self.tp.base, "marker.txt")))
        m = [s for s in ledger.load_all_scores(self.d) if s["skill"] == "proof-mutation"][0]["metrics"][0]
        self.assertGreater(m["value"], 0)  # the mutants were really applied, in the copy

    def test_in_place_recovers_after_a_crash(self):
        mod, original, cmd = self._calc_project()
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write(original.replace(">", "<="))  # what a crash mid-run would leave
        with open(mutate._journal(self.d), "w", encoding="utf-8") as fh:
            json.dump({mod: original}, fh)
        rc, out = run("--dir", self.d, "run", "--src", mod, "--test-cmd", cmd, "--max", "4", "--in-place")
        self.assertEqual(rc, 0, out)
        self.assertIn("restored 1 file", out)
        with open(mod, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original)
        self.assertFalse(os.path.exists(mutate._journal(self.d)))

    def test_refuses_red_baseline(self):
        mod = os.path.join(self.proj, "calc.py")
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write("def f():\n    return 1 > 0\n")
        failing = '%s -c "import sys; sys.exit(1)"' % _q(sys.executable)
        rc, out = run("--dir", self.d, "run", "--src", mod, "--test-cmd", failing, "--max", "3")
        self.assertNotEqual(rc, 0)
        self.assertIn("green tests", out.lower())


def _q(s):
    return '"%s"' % s if " " in s else s


if __name__ == "__main__":
    unittest.main()
