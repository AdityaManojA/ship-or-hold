#!/usr/bin/env python3
"""Scope a security review to what a branch changed. Standard library only.

Reads a unified diff (from a file, from stdin, or by running git against a base
branch) and reports the changed files, the added lines, and which weakness classes
each change most likely touches, so a review or a CI gate can look only at what
moved instead of the whole tree. It changes nothing and, when it runs git, runs
only read-only commands.

    python3 diffscope.py changed --base main
    python3 diffscope.py changed --diff changes.patch
    git diff main... | python3 diffscope.py changed --stdin
    python3 diffscope.py record --base main
    python3 diffscope.py status
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-sec-diff"

# Signals in added code that hint at a weakness class worth a closer look. These
# point a reviewer at the change; they are not judgements on their own.
SIGNALS = [
    ("authz", re.compile(r"(?i)\b(is_admin|role|permission|current_user|owner|can_|authoriz|access)\b")),
    ("authn", re.compile(r"(?i)\b(login|password|session|jwt|token|oauth|bcrypt|hash_password)\b")),
    ("injection", re.compile(r"(?i)(execute\(|cursor\.|\bquery\(|f\".*select |format\(.*select|exec\(|os\.system|subprocess|child_process|eval\()")),
    ("xss", re.compile(r"(?i)(innerhtml|dangerouslysetinnerhtml|document\.write|v-html|render_template_string|\|safe)")),
    ("ssrf", re.compile(r"(?i)(requests\.get\(|fetch\(|urlopen\(|httpclient|axios\.)")),
    ("secrets", re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]")),
    ("deserialization", re.compile(r"(?i)(pickle\.loads|yaml\.load\b|marshal\.loads|unserialize|readObject)")),
    ("upload-path", re.compile(r"(?i)(open\(.*\+|os\.path\.join\(.*request|sendfile|\.\./|multipart|upload)")),
    ("crypto", re.compile(r"(?i)\b(md5|sha1|random\.random|math\.random|des|ecb)\b")),
    ("cors-headers", re.compile(r"(?i)(access-control-allow-origin|cors\(|helmet|content-security-policy|set-cookie)")),
]


def _run_git(args, cwd):
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30, universal_newlines=True)
    except (OSError, subprocess.SubprocessError) as e:
        raise SystemExit("could not run git: %s" % e)
    if p.returncode != 0:
        raise SystemExit("git failed: %s" % (p.stderr.strip() or "unknown error"))
    return p.stdout


def _get_diff(a):
    if a.stdin:
        return sys.stdin.read()
    if a.diff:
        with open(a.diff, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    base = a.base or "main"
    # Three-dot: changes on this branch since it diverged from base.
    return _run_git(["diff", "--unified=0", "%s..." % base], a.repo)


def parse_diff(text):
    """Return [{file, added_lines:[str], count}] from a unified diff."""
    files = []
    current = None
    for line in text.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r" b/(.+)$", line)
            current = {"file": m.group(1) if m else "?", "added_lines": []}
            files.append(current)
        elif line.startswith("+++ "):
            if current is not None:
                path = line[4:].strip()
                if path.startswith("b/"):
                    path = path[2:]
                if path != "/dev/null":
                    current["file"] = path
        elif line.startswith("+") and not line.startswith("+++"):
            if current is None:
                current = {"file": "?", "added_lines": []}
                files.append(current)
            current["added_lines"].append(line[1:])
    for f in files:
        f["count"] = len(f["added_lines"])
    return [f for f in files if f["count"] or f["file"] != "?"]


def classify(files):
    """Attach the weakness classes each file's added lines hint at."""
    for f in files:
        blob = "\n".join(f["added_lines"])
        f["signals"] = sorted({cid for cid, rx in SIGNALS if rx.search(blob)})
    return files


def _is_code(path):
    return os.path.splitext(path)[1].lower() in {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".php", ".java", ".cs",
        ".rs", ".c", ".cpp", ".sql", ".html", ".vue", ".yml", ".yaml", ".env"}


def cmd_changed(a):
    files = classify(parse_diff(_get_diff(a)))
    code = [f for f in files if _is_code(f["file"])]
    if a.json:
        print(json.dumps(files, indent=2))
        return 0
    if not files:
        print("No changes found. Check the base branch, or pass a diff.")
        return 0
    print("%d changed file(s), %d of them code:" % (len(files), len(code)))
    for f in files:
        sig = (" -> look at: " + ", ".join(f["signals"])) if f.get("signals") else ""
        print("  %s (+%d)%s" % (f["file"], f["count"], sig))
    flagged = sorted({s for f in files for s in f.get("signals", [])})
    if flagged:
        print("\nWeakness classes the diff touches: %s" % ", ".join(flagged))
        print("Scope /proof-sec-web to these on the changed files, rather than the whole app.")
    else:
        print("\nNo obvious security-sensitive signals in the added lines. A human should still skim them.")
    return 0


def cmd_record(a):
    files = classify(parse_diff(_get_diff(a)))
    flagged = sorted({s for f in files for s in f.get("signals", [])})
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "diff-scope.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Diff scope (base %s)\n%d changed file(s)\n\n" % (a.base or "main", len(files)))
        for f in files:
            fh.write("%s (+%d) %s\n" % (f["file"], f["count"], ",".join(f.get("signals", []))))
    rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")
    checks = [("changed-files", "List the files the branch changed")]
    checks += [("review-%s" % c, "Review changed code for %s" % c) for c in flagged]
    ledger.init_coverage(a.dir, SKILL, checks)
    ledger.mark_coverage(a.dir, SKILL, "changed-files", "covered", evidence=[rel],
                         note="%d file(s)" % len(files))
    print("%s recorded the diff scope: %d file(s), classes to review: %s"
          % (ledger.OK, len(files), ", ".join(flagged) or "none obvious"))
    print("Each review-<class> check starts not-run. As you review the changed code for that")
    print("class with /proof-sec-web, mark it covered (or not_applicable). Evidence: %s" % rel)
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no diff scope recorded yet. Run: diffscope.py record --base <branch>")
        return 0
    for c in cov["checks"]:
        print("  %s %s: %s" % ({"covered": ledger.OK, "not_applicable": "--", "not_run": "..."}[c["status"]],
                               c["id"], c["title"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    ap.add_argument("--repo", default=".", help="the git repo to diff in")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, func, helptext in [("changed", cmd_changed, "list changed files and the classes they touch"),
                                 ("record", cmd_record, "write the diff scope to the ledger")]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--base", help="base branch to diff against (default main)")
        src = p.add_mutually_exclusive_group()
        src.add_argument("--diff", help="read a unified diff from this file")
        src.add_argument("--stdin", action="store_true", help="read a unified diff from stdin")
        if name == "changed":
            p.add_argument("--json", action="store_true")
        p.set_defaults(func=func)
    sub.add_parser("status", help="recorded scope").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
