#!/usr/bin/env python3
"""Measure whether a project's tests actually catch bugs, by mutation testing.

Standard library only, no network. It makes one small change to the source at a
time (flip a comparison, swap an operator, invert a boolean), runs the test
command, and sees whether the tests notice. A change the tests catch is "killed"; a
change they miss "survives" and points at a gap. The mutation score is the share
killed.

It edits source files in place for the length of one test run and always restores
them, even on error or Ctrl-C. Point it at a clean working tree you can restore
from git in case anything goes wrong.

    python3 mutate.py sites src/thing.py
    python3 mutate.py run --src src/thing.py --test-cmd "python -m pytest -q" --max 40
    python3 mutate.py status
"""

import argparse
import io
import os
import signal
import subprocess
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-mutation"

OP_SWAP = {"<": ">=", ">": "<=", "<=": ">", ">=": "<", "==": "!=", "!=": "==",
           "+": "-", "-": "+", "*": "/", "/": "*", "and": "or", "or": "and"}
NAME_SWAP = {"True": "False", "False": "True", "and": "or", "or": "and"}


def find_sites(path):
    """Return a list of mutation sites: (start, end, original, replacement, kind)."""
    with open(path, "rb") as fh:
        src_bytes = fh.read()
    sites = []
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(src_bytes).readline))
    except tokenize.TokenError:
        return sites
    for tok in tokens:
        text = tok.string
        if tok.type == tokenize.OP and text in OP_SWAP:
            sites.append((tok.start, tok.end, text, OP_SWAP[text], "op"))
        elif tok.type == tokenize.NAME and text in NAME_SWAP:
            sites.append((tok.start, tok.end, text, NAME_SWAP[text], "name"))
    return sites


def _apply(lines, start, end, replacement):
    """Return source text with the span start..end replaced. 1-based rows."""
    (sr, sc), (er, ec) = start, end
    out = list(lines)
    if sr == er:
        line = out[sr - 1]
        out[sr - 1] = line[:sc] + replacement + line[ec:]
    else:
        out[sr - 1] = out[sr - 1][:sc] + replacement
        for r in range(sr, er - 1):
            out[r] = ""
        out[er - 1] = out[er - 1][ec:]
    return out


def cmd_sites(a):
    sites = find_sites(a.path)
    print("%d mutation site(s) in %s" % (len(sites), a.path))
    for (sr, sc), _, orig, repl, kind in sites[:60]:
        print("  line %d: %s -> %s" % (sr, orig, repl))
    return 0


def _run_tests(cmd, timeout):
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        return None
    finally:
        if proc.poll() is None:
            _kill(proc)


def _kill(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


def cmd_run(a):
    src = a.src
    files = [src] if os.path.isfile(src) else _py_under(src)
    if not files:
        raise SystemExit("no .py files at %s" % src)

    # Baseline: the tests must pass before mutating, or the score is meaningless.
    print("running the test command once to confirm a green baseline...")
    base = _run_tests(a.test_cmd, a.timeout)
    if base != 0:
        raise SystemExit("the test command did not pass on the clean tree (exit %s). "
                         "Mutation testing needs green tests to start from." % base)

    sites = []
    for f in files:
        for s in find_sites(f):
            sites.append((f,) + s)
    if a.max and len(sites) > a.max:
        # even spread across the file, not just the first N
        step = len(sites) / float(a.max)
        sites = [sites[int(i * step)] for i in range(a.max)]
    print("testing %d mutation(s)..." % len(sites))

    killed = survived = errored = 0
    survivors = []
    for i, (f, start, end, orig, repl, kind) in enumerate(sites, 1):
        with open(f, encoding="utf-8") as fh:
            original = fh.read()
        lines = original.splitlines(keepends=True)
        mutated = "".join(_apply(lines, start, end, repl))
        try:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            rc = _run_tests(a.test_cmd, a.timeout)
        finally:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(original)
        if rc is None:
            errored += 1
        elif rc != 0:
            killed += 1
        else:
            survived += 1
            survivors.append((f, start[0], orig, repl))
        if i % 10 == 0:
            print("  %d/%d (killed %d, survived %d)" % (i, len(sites), killed, survived))

    tested = killed + survived
    score = (killed / tested) if tested else 0.0
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "mutation.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Mutation testing of %s with: %s\n" % (src, a.test_cmd))
        fh.write("killed %d, survived %d, errored/timed-out %d, score %.0f%%\n\n" % (killed, survived, errored, score * 100))
        fh.write("Survivors (changes the tests did not catch):\n")
        for f, line, orig, repl in survivors:
            fh.write("  %s:%d  %s -> %s\n" % (f, line, orig, repl))
    rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")

    ledger.write_scores(a.dir, SKILL, [{"id": "mutation_score", "value": round(score, 3),
                                        "unit": "fraction", "budget": a.min_score, "op": ">=",
                                        "source": "mutate.py", "evidence": [rel]}])
    ledger.init_coverage(a.dir, SKILL, [("mutation", "Mutation testing of the source")])
    ledger.mark_coverage(a.dir, SKILL, "mutation", "covered", evidence=[rel],
                         note="%d killed, %d survived" % (killed, survived))
    if survived:
        fid = ledger.slug("mutation-survivors")
        f = ledger.new_finding(SKILL, "%d mutation(s) survived: the tests miss these changes" % survived,
                               "low" if score >= a.min_score else "medium", id=fid, category="test-gap",
                               evidence=[rel], fix="Add or strengthen tests so these changes fail.")
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    mark = ledger.OK if score >= a.min_score else ledger.BAD
    print("%s mutation score %.0f%% (killed %d, survived %d, errored %d). Budget %.0f%%. Report: %s"
          % (mark, score * 100, killed, survived, errored, a.min_score * 100, rel))
    return 0


def _py_under(root):
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
        for f in files:
            if f.endswith(".py") and not f.startswith("test_"):
                out.append(os.path.join(base, f))
    return out


def cmd_status(a):
    scores = [s for s in ledger.load_all_scores(a.dir) if s["skill"] == SKILL]
    if not scores:
        print("no mutation run recorded yet.")
        return 0
    m = scores[0]["metrics"][0]
    print("mutation score: %.0f%% (budget %.0f%%)" % (m["value"] * 100, m["budget"] * 100))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sites", help="list mutation sites in a file")
    s.add_argument("path")
    s.set_defaults(func=cmd_sites)
    r = sub.add_parser("run", help="mutate and run the tests")
    r.add_argument("--src", required=True, help="a .py file or a folder of them")
    r.add_argument("--test-cmd", required=True, help="the command that runs the tests")
    r.add_argument("--max", type=int, default=50, help="cap the number of mutations")
    r.add_argument("--timeout", type=int, default=60, help="seconds per test run")
    r.add_argument("--min-score", type=float, default=0.6, help="mutation-score budget (0..1)")
    r.set_defaults(func=cmd_run)
    sub.add_parser("status", help="recorded mutation score").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
