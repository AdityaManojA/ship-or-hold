#!/usr/bin/env python3
"""Measure whether a project's tests actually catch bugs, by mutation testing.

Standard library only, no network. It makes one small change to the source at a
time (flip a comparison, swap an operator, invert a boolean), runs the test
command, and sees whether the tests notice. A change the tests catch is "killed"; a
change they miss "survives" and points at a gap. The mutation score is the share
killed.

Your files are never touched: it copies the project to a temporary folder, mutates
and tests the copy, then deletes it. --in-place edits the real files instead (for a
project that cannot be copied); it keeps a restore journal under proof/ and puts
back anything a crash left changed on the next run.

    python3 mutate.py sites src/thing.py
    python3 mutate.py run --src src/thing.py --test-cmd "python -m pytest -q" --max 40
    python3 mutate.py status
"""

import argparse
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
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


def _run_tests(cmd, timeout, cwd=None):
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": True, "cwd": cwd}
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


# Folders that are never copied into the sandbox, and heavy ones linked instead of copied.
NO_COPY = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".tox"}
LINK = {"node_modules", "venv", ".venv", "env"}


def _project_root(src):
    """The git top level above src, or its folder when it is not in a repository."""
    start = src if os.path.isdir(src) else os.path.dirname(src) or "."
    try:
        p = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=15, universal_newlines=True)
        if p.returncode == 0 and p.stdout.strip():
            return os.path.abspath(p.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.abspath(start)


def _inside(root, path):
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:  # different drives
        return False


def _copy_project(root, dest, skip):
    """Copy root to dest without version control, caches or the proof folder; link heavy folders."""
    def ignore(folder, names):
        out = set(n for n in names if n in NO_COPY or n in LINK)
        out.update(n for n in names if os.path.abspath(os.path.join(folder, n)) in skip)
        return out
    shutil.copytree(root, dest, ignore=ignore, symlinks=True)
    for base, dirs, _ in os.walk(root):
        keep = []
        for d in dirs:
            full = os.path.abspath(os.path.join(base, d))
            if d in NO_COPY or full in skip:
                continue
            if d in LINK:
                try:
                    os.symlink(full, os.path.join(dest, os.path.relpath(full, root)), target_is_directory=True)
                except (OSError, NotImplementedError):
                    pass  # tests that need it will fail the baseline, which says so
                continue
            keep.append(d)
        dirs[:] = keep


def _journal(d):
    return os.path.join(ledger.evidence_dir(d, SKILL), "restore.json")


def recover(d):
    """Put back any file an interrupted --in-place run left mutated. Returns the paths restored."""
    path = _journal(d)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    for f, text in saved.items():
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    os.remove(path)
    return list(saved)


def cmd_run(a):
    restored = recover(a.dir)
    if restored:
        print("%s restored %d file(s) an interrupted run left changed: %s" % (
            ledger.OK, len(restored), ", ".join(restored)))
    src = os.path.abspath(a.src)
    files = [src] if os.path.isfile(src) else _py_under(src)
    if not files:
        raise SystemExit("no .py files at %s" % a.src)
    if a.in_place:
        return _mutate(a, files, a.test_cmd, None, None)

    root = os.path.abspath(a.root) if a.root else _project_root(src)
    if not _inside(root, src):
        raise SystemExit("%s is not inside the project root %s (set --root)" % (a.src, root))
    work = tempfile.mkdtemp(prefix="proof-mutation-")
    try:
        copy = os.path.join(work, os.path.basename(root.rstrip("\\/")) or "project")
        print("copying %s to a temporary folder; your files are not touched..." % root)
        _copy_project(root, copy, {os.path.abspath(ledger.root(a.dir))})
        files = [os.path.join(copy, os.path.relpath(f, root)) for f in files]
        # Paths in the command that point into the project now point into the copy.
        cmd = a.test_cmd
        for form in sorted({root, root.replace(os.sep, "/")}, key=len, reverse=True):
            cmd = cmd.replace(form, copy)
        here = os.path.abspath(os.getcwd())
        cwd = os.path.join(copy, os.path.relpath(here, root)) if _inside(root, here) else copy
        return _mutate(a, files, cmd, cwd, (copy, root))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _mutate(a, files, cmd, cwd, sandbox):
    def shown(f):
        # Report paths as the real project's, not the temporary copy's.
        if sandbox:
            return os.path.join(sandbox[1], os.path.relpath(f, sandbox[0]))
        return f

    # Baseline: the tests must pass before mutating, or the score is meaningless.
    print("running the test command once to confirm a green baseline...")
    base = _run_tests(cmd, a.timeout, cwd)
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
        with open(f, encoding="utf-8", newline="") as fh:
            original = fh.read()
        lines = original.splitlines(keepends=True)
        mutated = "".join(_apply(lines, start, end, repl))
        if not sandbox:
            with open(_journal(a.dir), "w", encoding="utf-8") as fh:
                json.dump({f: original}, fh)
        try:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(mutated)
            rc = _run_tests(cmd, a.timeout, cwd)
        finally:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(original)
            if not sandbox and os.path.exists(_journal(a.dir)):
                os.remove(_journal(a.dir))
        if rc is None:
            errored += 1
        elif rc != 0:
            killed += 1
        else:
            survived += 1
            survivors.append((shown(f), start[0], orig, repl))
        if i % 10 == 0:
            print("  %d/%d (killed %d, survived %d)" % (i, len(sites), killed, survived))

    src = a.src

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
    r.add_argument("--root", help="project folder to copy (default: the git top level above --src)")
    r.add_argument("--in-place", action="store_true",
                   help="mutate the real files instead of a copy (keeps a restore journal under proof/)")
    r.set_defaults(func=cmd_run)
    sub.add_parser("status", help="recorded mutation score").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
