#!/usr/bin/env python3
"""Re-run checks when the code changes and track how the verdict moves over time.

Standard library only, no network. Every snapshot is one line in
proof/watch/history.jsonl, computed from the ledger by proof-report's verdict.py.

    python3 watch.py snapshot --label "before fix"
    python3 watch.py history --last 10
    python3 watch.py run --cmd "python -m pytest -q" --label "after fix"
    python3 watch.py watch --path src --cmd "python -m pytest -q" --interval 2

Exit codes for run and watch follow the verdict: 0 Ship, 1 Not yet, 2 Incomplete.
"""

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-watch"
IS_WIN = os.name == "nt"
SEV = ["critical", "high", "medium", "low"]
SHORT = {"critical": "crit", "high": "high", "medium": "med", "low": "low"}
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
EXIT_TIMEOUT = 124

_verdict = None


def verdict_module():
    """proof-report's verdict.py, imported by path so this skill stays standalone."""
    global _verdict
    if _verdict is None:
        path = os.path.join(HERE, "..", "proof-report", "verdict.py")
        if not os.path.isfile(path):
            raise RuntimeError("proof-report/verdict.py not found next to this skill: %s" % path)
        spec = importlib.util.spec_from_file_location("proof_watch_verdict", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _verdict = mod
    return _verdict


# --------------------------------------------------------------- history

def watch_dir(d):
    return os.path.join(ledger.root(d), "watch")


def history_path(d):
    return os.path.join(watch_dir(d), "history.jsonl")


def load_history(d):
    """Every snapshot in order. Lines that do not parse are skipped and counted."""
    path = history_path(d)
    rows, bad = [], 0
    if not os.path.exists(path):
        return rows, bad
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(row, dict) and "verdict" in row:
                rows.append(row)
            else:
                bad += 1
    return rows, bad


def take_snapshot(d, label="", fail_on=None, extra=None):
    """Compute the verdict now and append one row to history.jsonl. Returns (row, result)."""
    vm = verdict_module()
    r = vm.compute(d, fail_on)
    verified = [f for f in r.get("findings", []) if f.get("status") == "verified"]
    counts = {s: sum(1 for f in verified if f.get("severity") == s) for s in SEV}
    failing = []
    for s in r.get("scores", []):
        for m in s.get("metrics", []):
            if not ledger.metric_passes(m):
                failing.append("%s/%s" % (s["skill"], m["id"]))
    row = {"time": ledger.now(), "label": label or "", "verdict": r["verdict"],
           "findings": counts, "info": sum(1 for f in verified if f.get("severity") == "info"),
           "gaps": len(r.get("gaps", [])), "failing_scores": len(failing),
           "failing_score_ids": sorted(failing),
           "fingerprint": sorted("%s/%s" % (f["skill"], f["id"]) for f in verified)}
    if extra:
        row.update(extra)
    os.makedirs(watch_dir(d), exist_ok=True)
    with open(history_path(d), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    return row, r


def summary_line(row):
    c = row["findings"]
    return "%s  %s  crit %d high %d med %d low %d  gaps %d  failing scores %d%s" % (
        row["time"], row["verdict"], c.get("critical", 0), c.get("high", 0), c.get("medium", 0),
        c.get("low", 0), row["gaps"], row["failing_scores"],
        ("  [%s]" % row["label"]) if row.get("label") else "")


def changes(prev, row):
    """What moved since the previous row, as short text."""
    if prev is None:
        return "first"
    out = []
    if prev.get("verdict") != row.get("verdict"):
        out.append("verdict %s -> %s" % (prev.get("verdict"), row.get("verdict")))
    before, after = set(prev.get("fingerprint", [])), set(row.get("fingerprint", []))
    added, removed = len(after - before), len(before - after)
    if added:
        out.append("findings +%d" % added)
    if removed:
        out.append("findings -%d" % removed)
    if prev.get("gaps") != row.get("gaps"):
        out.append("gaps %s -> %s" % (prev.get("gaps"), row.get("gaps")))
    if prev.get("failing_scores") != row.get("failing_scores"):
        out.append("failing scores %s -> %s" % (prev.get("failing_scores"), row.get("failing_scores")))
    return ", ".join(out) or "-"


def table_rows(rows):
    out = []
    for i, row in enumerate(rows):
        c = row.get("findings", {})
        out.append({"time": (row.get("time") or "")[:19].replace("T", " "),
                    "label": row.get("label") or "",
                    "verdict": row.get("verdict"),
                    "c/h/m/l": "/".join(str(c.get(s, 0)) for s in SEV),
                    "gaps": str(row.get("gaps", 0)),
                    "failing": str(row.get("failing_scores", 0)),
                    "changes": changes(rows[i - 1] if i else None, row)})
    return out


COLUMNS = ["time", "label", "verdict", "c/h/m/l", "gaps", "failing", "changes"]


def ascii_table(trows):
    widths = {c: max([len(c)] + [len(r[c]) for r in trows]) for c in COLUMNS}
    line = "  ".join(c.ljust(widths[c]) for c in COLUMNS).rstrip()
    rule = "  ".join("-" * widths[c] for c in COLUMNS)
    body = ["  ".join(r[c].ljust(widths[c]) for c in COLUMNS).rstrip() for r in trows]
    return "\n".join([line, rule] + body)


def markdown(trows):
    L = ["# Verdict history", "",
         "Each row is one snapshot of the computed verdict. Counts are verified findings "
         "by severity (critical/high/medium/low). Changes compare a row with the one before it.", "",
         "| " + " | ".join(COLUMNS) + " |",
         "| " + " | ".join("---" for _ in COLUMNS) + " |"]
    for r in trows:
        L.append("| " + " | ".join(r[c].replace("|", "/") for c in COLUMNS) + " |")
    if not trows:
        L.append("")
        L.append("No snapshots yet.")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ run

def _spawn(cmd, cwd, log_handle):
    kwargs = {"cwd": cwd or None, "stdout": log_handle, "stderr": subprocess.STDOUT,
              "stdin": subprocess.DEVNULL, "shell": True}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    return subprocess.Popen(cmd, **kwargs)


def _kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _next_run_number(evdir):
    n = 0
    for name in os.listdir(evdir):
        if name.startswith("run-") and name.endswith(".log"):
            try:
                n = max(n, int(name[4:-4]))
            except ValueError:
                pass
    return n + 1


def run_commands(d, cmds, timeout, cwd=None):
    """Run each command with a timeout into one log. Returns (log_rel, results)."""
    evdir = ledger.evidence_dir(d, SKILL)
    n = _next_run_number(evdir)
    log_path = os.path.join(evdir, "run-%d.log" % n)
    results = []
    with open(log_path, "w", encoding="utf-8") as log:
        for cmd in cmds:
            log.write("$ %s\n" % cmd)
            log.flush()
            start = time.time()
            proc = _spawn(cmd, cwd, log)
            timed_out = False
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                rc, timed_out = EXIT_TIMEOUT, True
            except KeyboardInterrupt:
                _kill_tree(proc)
                log.write("\n[interrupted]\n")
                raise
            secs = round(time.time() - start, 2)
            log.flush()
            log.write("\n[exit %s%s after %.2fs]\n\n" % (rc, ", timed out after %ds" % timeout if timed_out else "", secs))
            log.flush()
            results.append({"cmd": cmd, "exit": rc, "timed_out": timed_out, "seconds": secs})
            mark = ledger.OK if rc == 0 else ledger.BAD
            print("  %s exit %s%s  %s" % (mark, rc, " (timeout)" if timed_out else "", cmd))
    rel = os.path.relpath(log_path, ledger.root(d)).replace(os.sep, "/")
    return n, rel, results


def do_run(d, cmds, label, timeout, fail_on=None, cwd=None, fail_on_cmd=False):
    n, rel, results = run_commands(d, cmds, timeout, cwd)
    row, r = take_snapshot(d, label or "run %d" % n, fail_on,
                           extra={"run": n, "log": rel, "commands": results})
    print("log: %s" % rel)
    print(summary_line(row))
    rc = verdict_module().EXIT[r["verdict"]]
    failed = [x for x in results if x["exit"] != 0]
    if failed:
        print("%s %d of %d command%s failed. A failed command changes the verdict only if it wrote to the ledger."
              % (ledger.BAD, len(failed), len(results), "" if len(results) == 1 else "s"))
        if fail_on_cmd and rc == 0:
            rc = 1
    return rc


# ---------------------------------------------------------------- watch

def tree_fingerprint(path, ignore_abs, ignore_names):
    """{relative path: (mtime_ns, size)} for every file under path, skipping ignored folders."""
    out = {}
    base = os.path.abspath(path)
    for root, dirs, files in os.walk(base):
        keep = []
        for name in dirs:
            full = os.path.abspath(os.path.join(root, name))
            if name in SKIP_DIRS or name in ignore_names or full in ignore_abs:
                continue
            keep.append(name)
        dirs[:] = keep
        for name in files:
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out[os.path.relpath(full, base)] = (st.st_mtime_ns, st.st_size)
    return out


def diff_trees(old, new):
    changed = [p for p in new if old.get(p) != new[p]]
    changed += [p for p in old if p not in new]
    return sorted(changed)


def do_watch(d, path, cmds, interval, debounce, max_runs, ignores, timeout, fail_on=None,
             cwd=None, fail_on_cmd=False):
    if not os.path.isdir(path):
        print("error: --path %s is not a folder" % path, file=sys.stderr)
        return 2
    ignore_abs = {ledger.root(d)}
    ignore_names = set()
    for item in ignores:
        if os.sep in item or "/" in item or os.path.isdir(os.path.join(path, item)):
            ignore_abs.add(os.path.abspath(os.path.join(path, item)))
        ignore_names.add(os.path.basename(item.rstrip("/\\")))
    print("watching %s every %ss (debounce %ss); Ctrl-C to stop" % (os.path.abspath(path), interval, debounce))
    runs, rc = 0, 2
    try:
        current = tree_fingerprint(path, ignore_abs, ignore_names)
        while max_runs is None or runs < max_runs:
            time.sleep(interval)
            fresh = tree_fingerprint(path, ignore_abs, ignore_names)
            changed = diff_trees(current, fresh)
            if not changed:
                continue
            # Wait until the tree is quiet for one debounce period before running.
            while True:
                time.sleep(debounce)
                settled = tree_fingerprint(path, ignore_abs, ignore_names)
                more = diff_trees(fresh, settled)
                fresh = settled
                if not more:
                    break
                changed = sorted(set(changed) | set(more))
            current = fresh
            runs += 1
            shown = ", ".join(p.replace(os.sep, "/") for p in changed[:3])
            label = "change: %s%s" % (shown, " (+%d)" % (len(changed) - 3) if len(changed) > 3 else "")
            print("%d changed file%s: %s" % (len(changed), "" if len(changed) == 1 else "s", shown))
            rc = do_run(d, cmds, label, timeout, fail_on, cwd, fail_on_cmd)
    except KeyboardInterrupt:
        print("\nstopped after %d run%s" % (runs, "" if runs == 1 else "s"))
        return rc if runs else 0
    print("stopped after %d run%s (--max-runs)" % (runs, "" if runs == 1 else "s"))
    return rc


# ----------------------------------------------------------------- main

def _positive(text):
    v = float(text)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return v


def main(argv=None):
    ledger.console()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default="proof", help="the proof/ folder (default proof)")
    common.add_argument("--fail-on", choices=ledger.SEVERITIES[:-1],
                        help="override target.json fail_on when computing the verdict")
    runner = argparse.ArgumentParser(add_help=False)
    runner.add_argument("--cmd", action="append", default=[], required=True,
                        help="a non-interactive command to run (repeatable, run in order)")
    runner.add_argument("--timeout", type=_positive, default=600, help="seconds per command (default 600)")
    runner.add_argument("--cwd", help="working directory for the commands (default: current)")
    runner.add_argument("--fail-on-cmd", action="store_true",
                        help="exit 1 instead of 0 when the verdict is Ship but a command failed")

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="action")
    sp = sub.add_parser("snapshot", parents=[common], help="compute the verdict and append it to the history")
    sp.add_argument("--label", default="")
    hp = sub.add_parser("history", parents=[common], help="print the timeline and write proof/watch/history.md")
    hp.add_argument("--last", type=int, help="show only the last N rows")
    hp.add_argument("--json", action="store_true", help="print the rows as JSON")
    rp = sub.add_parser("run", parents=[common, runner], help="run commands, then snapshot")
    rp.add_argument("--label", default="")
    wp = sub.add_parser("watch", parents=[common, runner], help="poll a folder and run on every change")
    wp.add_argument("--path", default=".", help="folder to watch (default .)")
    wp.add_argument("--interval", type=_positive, default=2, help="seconds between scans (default 2)")
    wp.add_argument("--debounce", type=float, default=1, help="quiet seconds before a run (default 1)")
    wp.add_argument("--max-runs", type=int, help="stop after N runs")
    wp.add_argument("--ignore", action="append", default=[],
                    help="folder name or path to skip (repeatable); the proof dir is always skipped")
    a = ap.parse_args(argv)

    if a.action is None:
        ap.print_help()
        return 2
    try:
        if a.action == "snapshot":
            row, _ = take_snapshot(a.dir, a.label, a.fail_on)
            print("%s %s" % (ledger.OK, summary_line(row)))
            return 0
        if a.action == "history":
            rows, bad = load_history(a.dir)
            trows = table_rows(rows)
            os.makedirs(watch_dir(a.dir), exist_ok=True)
            with open(os.path.join(watch_dir(a.dir), "history.md"), "w", encoding="utf-8") as fh:
                fh.write(markdown(trows))
            shown_rows, shown = rows, trows
            if a.last is not None:
                k = max(a.last, 0)
                shown_rows, shown = (rows[-k:], trows[-k:]) if k else ([], [])
            if a.json:
                out = []
                for raw, t in zip(shown_rows, shown):
                    item = dict(raw)
                    item["changes"] = t["changes"]
                    out.append(item)
                print(json.dumps(out, indent=2, ensure_ascii=True))
            elif not rows:
                print("No snapshots yet. Run: watch.py snapshot")
            else:
                print(ascii_table(shown))
            if bad:
                print("%s skipped %d unreadable line%s in %s" % (ledger.BAD, bad, "" if bad == 1 else "s",
                                                                history_path(a.dir)), file=sys.stderr)
            return 0
        if a.action == "run":
            return do_run(a.dir, a.cmd, a.label, a.timeout, a.fail_on, a.cwd, a.fail_on_cmd)
        if a.action == "watch":
            if a.debounce < 0:
                print("error: --debounce must not be negative", file=sys.stderr)
                return 2
            if a.max_runs is not None and a.max_runs < 1:
                print("error: --max-runs must be at least 1", file=sys.stderr)
                return 2
            return do_watch(a.dir, a.path, a.cmd, a.interval, a.debounce, a.max_runs, a.ignore,
                            a.timeout, a.fail_on, a.cwd, a.fail_on_cmd)
    except (ledger.LedgerError, RuntimeError, OSError) as e:
        print("%s %s" % (ledger.BAD, e), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
