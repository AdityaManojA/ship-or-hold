import contextlib
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
