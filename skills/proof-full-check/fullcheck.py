#!/usr/bin/env python3
"""Run every applicable Proof skill as one full check. Standard library only.

This tool does not test anything itself. It works out which skills apply to the
target, writes them into target.json as the required skills (so the verdict waits
for all of them), and then tracks progress: which skills have reported, which
still have checks not run, and which one to run next. The verdict at the end comes
from proof-report as usual.

    python3 fullcheck.py plan                     # every applicable skill
    python3 fullcheck.py plan --no-seo            # the same, without SEO and GEO
    python3 fullcheck.py plan --has-llm --skip proof-finance
    python3 fullcheck.py status
    python3 fullcheck.py next
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

PLAN_FILE = "fullcheck.json"

# The run order: functional flows first, then security, then performance and
# quality, then discoverability and finance.
ORDER = [
    ("proof-web-e2e", "end-to-end user flows"),
    ("proof-api", "API contract and awkward inputs"),
    ("proof-sec-web", "web security checklist"),
    ("proof-sec-secrets", "committed secrets"),
    ("proof-sec-deps", "vulnerable dependencies"),
    ("proof-infra", "deployment and CI config"),
    ("proof-privacy", "trackers, consent and personal data"),
    ("proof-web-perf", "performance budget"),
    ("proof-a11y", "accessibility"),
    ("proof-compat", "cross-browser and device matrix"),
    ("proof-visual", "visual regression"),
    ("proof-i18n", "translations and locales"),
    ("proof-email", "transactional emails"),
    ("proof-load", "behaviour under load"),
    ("proof-mutation", "test-suite strength"),
    ("proof-explore", "exploratory persona swarm"),
    ("proof-restraint", "dead code and bloat"),
    ("proof-slop", "generated-looking copy, UI and code"),
    ("proof-mobile-e2e", "mobile user flows"),
    ("proof-mobile-audit", "mobile build audit"),
    ("proof-ai", "LLM feature safety"),
    ("proof-seo", "search engine optimisation"),
    ("proof-geo", "AI-search citability"),
    ("proof-finance", "unit economics"),
    ("proof-release", "launch readiness"),
]
SEO = {"proof-seo", "proof-geo"}
MOBILE = {"proof-mobile-e2e", "proof-mobile-audit"}
WEB_ONLY = {"proof-web-e2e", "proof-web-perf", "proof-a11y", "proof-seo", "proof-geo", "proof-privacy",
            "proof-compat", "proof-visual"}
REPO = {"proof-sec-secrets", "proof-sec-deps", "proof-infra", "proof-mutation", "proof-restraint", "proof-slop"}
BY_KIND = {
    "web": lambda s: s not in MOBILE,
    "api": lambda s: s not in MOBILE and s not in WEB_ONLY,
    "mobile": lambda s: s not in WEB_ONLY,
    "repo": lambda s: s in REPO,
    "mixed": lambda s: True,
}
SKILL_RE = re.compile(r"^proof-[a-z0-9-]+$")


def _installed():
    base = os.path.join(HERE, "..")
    return {n for n in os.listdir(base)
            if SKILL_RE.match(n) and os.path.isfile(os.path.join(base, n, "SKILL.md"))}


def select(kind, no_seo=False, has_llm=False, mobile=False, skip=(), installed=None):
    """Return [(skill, reason)] that apply, in run order."""
    installed = _installed() if installed is None else installed
    keep = BY_KIND.get(kind, BY_KIND["mixed"])
    out = []
    for skill, why in ORDER:
        if skill not in installed or skill in skip:
            continue
        if no_seo and skill in SEO:
            continue
        if skill == "proof-ai" and not has_llm:
            continue
        if skill in MOBILE and mobile:
            out.append((skill, why))
            continue
        if keep(skill):
            out.append((skill, why))
    return out


def _plan_path(d):
    return os.path.join(ledger.root(d), PLAN_FILE)


def cmd_plan(a):
    t = ledger.load_target(a.dir)
    if t is None:
        print("error: no proof/target.json. Run /proof-plan first to describe the target "
              "and confirm you are authorised to test it.", file=sys.stderr)
        return 2
    for s in a.skip or []:
        if not SKILL_RE.match(s):
            print("error: %r is not a skill name" % s, file=sys.stderr)
            return 2
    chosen = select(t["kind"], a.no_seo, a.has_llm, bool(a.mobile), set(a.skip or []))
    if not chosen:
        print("error: no applicable skills are installed for a %s target." % t["kind"], file=sys.stderr)
        return 2
    t["required_skills"] = [s for s, _ in chosen]
    ledger.write_target(a.dir, t)
    mode = "full check without SEO" if a.no_seo else "full check"
    with open(_plan_path(a.dir), "w", encoding="utf-8") as fh:
        json.dump({"mode": mode, "kind": t["kind"], "skills": [s for s, _ in chosen],
                   "created": ledger.now()}, fh, indent=2)
    print("%s %s for a %s target: %d skills, now required by the verdict." % (ledger.OK, mode, t["kind"], len(chosen)))
    for i, (s, why) in enumerate(chosen, 1):
        print("  %2d. /%s - %s" % (i, s, why))
    if not t.get("authorised"):
        print("\nNote: target.json says authorised: no. The security and load skills will refuse to "
              "act against a live target until it is yes.")
    print("\nRun them in order (fullcheck.py next tells you which), then /proof-report.")
    return 0


def _progress(d):
    path = _plan_path(d)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        plan = json.load(fh)
    reported = set(ledger.skills_reported(d))
    rows = []
    for s in plan["skills"]:
        cov = ledger.load_coverage(d, s)
        not_run = sum(1 for c in cov["checks"] if c["status"] == "not_run") if cov else None
        blockers = sum(1 for f in ledger.load_findings(d, s)
                       if f["status"] == "verified" and f["severity"] in ("critical", "high"))
        if s not in reported or cov is None:
            state = "not started"
        elif not_run:
            state = "in progress"
        else:
            state = "done"
        rows.append({"skill": s, "state": state, "not_run": not_run, "blockers": blockers})
    return plan, rows


def cmd_status(a):
    got = _progress(a.dir)
    if got is None:
        print("no full check planned yet. Run: fullcheck.py plan [--no-seo]")
        return 0
    plan, rows = got
    done = sum(1 for r in rows if r["state"] == "done")
    print("%s: %d of %d skills done" % (plan["mode"], done, len(rows)))
    for r in rows:
        mark = {"done": ledger.OK, "in progress": "..", "not started": "  "}[r["state"]]
        extra = ""
        if r["state"] == "in progress":
            extra = " (%d check(s) not run)" % r["not_run"]
        if r["blockers"]:
            extra += " [%d verified high/critical]" % r["blockers"]
        print("  %s %-20s %s%s" % (mark, r["skill"], r["state"], extra))
    if done == len(rows):
        print("\nAll done. Compile the verdict with /proof-report.")
    return 0


def cmd_next(a):
    got = _progress(a.dir)
    if got is None:
        print("no full check planned yet. Run: fullcheck.py plan [--no-seo]")
        return 0
    _, rows = got
    for r in rows:
        if r["state"] != "done":
            print("Next: /%s (%s)" % (r["skill"], r["state"]))
            return 0
    print("Every skill in the full check is done. Next: /proof-report")
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="pick every applicable skill and require them")
    p.add_argument("--no-seo", action="store_true", help="leave out SEO and GEO")
    p.add_argument("--has-llm", action="store_true", help="include the LLM safety skill")
    p.add_argument("--mobile", choices=["ios", "android"], help="include the mobile skills")
    p.add_argument("--skip", action="append", help="leave out a skill (repeatable)")
    p.set_defaults(func=cmd_plan)
    sub.add_parser("status", help="progress through the full check").set_defaults(func=cmd_status)
    sub.add_parser("next", help="the next skill to run").set_defaults(func=cmd_next)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
