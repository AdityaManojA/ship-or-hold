#!/usr/bin/env python3
"""Report which optional external tools are installed, and what each one unlocks.

Standard library only. No network. Detection is by PATH (and, where cheap, a
version command with a short timeout). A tool found here might still be broken;
this only answers "is it on PATH?".

    python3 doctor.py                 # the full table
    python3 doctor.py --json          # machine-readable
    python3 doctor.py --for proof-web-e2e   # only the tools that skill can use

Nothing here is required. Every Proof skill has a slower path that needs only
Python 3.8.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

# name, how to find it, version argv (or None), skills it helps, what you lose without it, install hint
TOOLS = [
    ("python", "python", ["--version"], ["everything"], "nothing; this is the floor", "already here"),
    ("node", "node", ["--version"], ["proof-web-e2e", "proof-a11y", "proof-mutation"],
     "the JavaScript-based testers fall back to slower paths", "Node.js"),
    ("npm", "npm", ["--version"], ["proof-sec-deps", "proof-a11y"], "npm audit for dependency and a11y tooling", "Node.js"),
    ("npx", "npx", None, ["proof-web-e2e", "proof-a11y", "proof-mutation"],
     "running JS tools without installing them", "Node.js"),
    ("git", "git", ["--version"], ["proof-sec-diff", "proof-sec-secrets", "proof-restraint", "proof-audit-trail"],
     "diff-scoped review and history scanning", "Git"),
    ("docker", "docker", ["--version"], ["proof-mobile-audit", "proof-sec-web"],
     "tools that only ship as containers", "Docker"),
    ("playwright", None, None, ["proof-web-e2e", "proof-compat", "proof-visual"],
     "scripted browser runs; falls back to the built-in browser tools", "Playwright (pip install playwright, or npm i -D @playwright/test)"),
    ("lighthouse", "lighthouse", None, ["proof-web-perf", "proof-seo", "proof-a11y"],
     "measured performance, SEO and a11y scores; falls back to coarser checks", "Lighthouse (npm i -g lighthouse)"),
    ("axe", "axe", None, ["proof-a11y"], "the full WCAG rule engine; falls back to the built-in contrast and structure checks",
     "axe CLI (npm i -g @axe-core/cli)"),
    ("semgrep", "semgrep", ["--version"], ["proof-sec-web", "proof-sec-diff"],
     "pattern-based static analysis; falls back to model-driven code review", "Semgrep"),
    ("trufflehog", "trufflehog", None, ["proof-sec-secrets"],
     "verified secret detection; falls back to the built-in entropy and pattern scan", "TruffleHog"),
    ("gitleaks", "gitleaks", None, ["proof-sec-secrets"], "a second secret scanner", "Gitleaks"),
    ("k6", "k6", ["version"], ["proof-load"], "scripted load tests; falls back to a simple concurrent driver", "k6"),
    ("maestro", "maestro", None, ["proof-mobile-e2e"], "mobile flow automation; falls back to manual-guided steps", "Maestro"),
    ("adb", "adb", None, ["proof-mobile-e2e", "proof-mobile-audit"], "talking to an Android device or emulator", "Android platform-tools"),
    ("xcrun", "xcrun", None, ["proof-mobile-e2e", "proof-mobile-audit"], "talking to an iOS simulator (macOS only)", "Xcode command line tools"),
    ("schemathesis", None, None, ["proof-api"], "property-based API fuzzing; falls back to example-based checks", "Schemathesis (pip install schemathesis)"),
    ("promptfoo", "promptfoo", None, ["proof-ai"], "LLM evaluation and red-team runs; falls back to the swarm", "promptfoo (npm i -g promptfoo)"),
    ("mutmut", None, None, ["proof-mutation"], "Python mutation testing; falls back to the built-in AST mutator", "mutmut (pip install mutmut)"),
    ("stryker", "stryker", None, ["proof-mutation"], "JavaScript mutation testing", "Stryker (npm i -D @stryker-mutator/core)"),
    ("garak", None, None, ["proof-ai"], "LLM vulnerability probes; falls back to the deck and the swarm", "garak (pip install garak)"),
    ("trivy", "trivy", ["--version"], ["proof-infra", "proof-sec-deps"],
     "deeper config and dependency scanning; falls back to the built-in rules", "Trivy"),
    ("checkov", "checkov", None, ["proof-infra"], "policy checks for Terraform, Kubernetes and more", "Checkov (pip install checkov)"),
]

PY_IMPORT = {"playwright": "playwright", "schemathesis": "schemathesis", "mutmut": "mutmut", "garak": "garak"}


def _run(argv):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=10, universal_newlines=True)
        return (p.stdout or "").strip().splitlines()[0][:40] if p.stdout else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _py_has(module):
    try:
        p = subprocess.run([sys.executable, "-c", "import %s" % module],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect():
    rows = []
    for name, which, version_argv, skills, lose, hint in TOOLS:
        found, version = False, ""
        if which and shutil.which(which):
            found = True
            if version_argv:
                version = _run([which] + version_argv)
        elif name in PY_IMPORT and _py_has(PY_IMPORT[name]):
            found, version = True, "python module"
        elif name == "playwright":
            if shutil.which("npx"):
                v = _run(["npx", "--no-install", "playwright", "--version"])
                if v:
                    found, version = True, v
        rows.append({"name": name, "found": found, "version": version, "skills": skills,
                     "without": lose, "install": hint})
    return rows


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--for", dest="for_skill", help="only tools this proof skill can use")
    a = ap.parse_args(argv)
    rows = detect()
    if a.for_skill:
        rows = [r for r in rows if a.for_skill in r["skills"]]
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    name_w = max((len(r["name"]) for r in rows), default=4)
    ver_w = max((len(r["version"]) for r in rows), default=7)
    print("%-*s  %-5s  %-*s  %s" % (name_w, "tool", "found", ver_w, "version", "unlocks"))
    print("-" * (name_w + ver_w + 24))
    for r in rows:
        mark = ledger.OK if r["found"] else ledger.BAD
        print("%-*s  %-5s  %-*s  %s" % (name_w, r["name"], mark, ver_w, r["version"],
                                        ", ".join(s for s in r["skills"] if s != "everything") or "the floor"))
    missing = [r for r in rows if not r["found"]]
    if missing:
        print("\nNot found, and what you would gain:")
        for r in missing:
            print("  %s: %s  (install: %s)" % (r["name"], r["without"], r["install"]))
    print("\nNothing here is required. Every Proof skill falls back to a slower path without these.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
