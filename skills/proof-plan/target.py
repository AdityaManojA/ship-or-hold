#!/usr/bin/env python3
"""Describe what is under test and which skills the verdict waits for.

Standard library only, no network. Writes proof/target.json (validated against the
evidence schema) and a readable proof/target.md. proof-report needs target.json to
know what it is reporting on; every testing skill may read it to learn the target
and whether the user is authorised to test it.

    python3 target.py init --name "My app" --kind web --authorised yes --url http://localhost:3000
    python3 target.py require add proof-web-e2e proof-sec-web
    python3 target.py require remove proof-geo
    python3 target.py recommend --kind web --public --has-llm
    python3 target.py show

required_skills is the user's own list. The tool never adds to it on its own. A
skill not on the list is optional: it can still be run alone and never counts
against the verdict.
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL_RE = re.compile(r"^proof-[a-z0-9-]+$")

# The relevance map: kind -> ordered (skill, reason). recommend() filters this to
# skills that actually exist as sibling folders, so the advice grows as skills land
# and never points at something that is not installed.
RECO = {
    "web": [
        ("proof-web-e2e", "click through every critical user flow and prove each one works"),
        ("proof-sec-web", "check the web app and its API for the common security weaknesses"),
        ("proof-api", "test the API against its contract and with awkward inputs"),
        ("proof-web-perf", "measure load speed against a budget"),
        ("proof-a11y", "check it against WCAG so a screen-reader user can use it"),
        ("proof-sec-secrets", "make sure no key or token is committed or shipped to the browser"),
        ("proof-sec-deps", "audit dependencies for known-vulnerable versions"),
        ("proof-infra", "check Dockerfiles, deployment and CI config for risky settings"),
        ("proof-privacy", "check trackers wait for consent and personal data does not leak"),
        ("proof-pwa", "if it installs as an app: check the manifest, service worker and offline page"),
        ("proof-compat", "prove the key flows work across browsers and screen sizes"),
        ("proof-visual", "catch layout changes against a screenshot baseline"),
        ("proof-i18n", "check translations are complete and placeholders match"),
        ("proof-email", "check signup, reset and receipt emails send and link correctly"),
        ("proof-load", "see how it holds up under many users at once"),
        ("proof-mutation", "measure whether the tests actually catch bugs"),
        ("proof-explore", "turn a swarm of personas loose to find the odd cases"),
        ("proof-restraint", "flag code, features and dependencies that should not exist"),
        ("proof-slop", "find copy, styling and code that read as generated"),
        ("proof-seo", "audit on-page and technical SEO"),
        ("proof-geo", "check how well AI search engines can read and cite it"),
        ("proof-ai", "red-team and evaluate the model the app embeds"),
        ("proof-finance", "the unit economics behind the product"),
        ("proof-release", "walk the launch-readiness checklist before going live"),
        ("proof-watch", "re-run checks on every change and track the verdict over time"),
        ("proof-audit-trail", "package the whole run into a signed evidence folder"),
    ],
    "api": [
        ("proof-api", "test every endpoint against its contract and with awkward inputs"),
        ("proof-sec-web", "check the API for the common security weaknesses"),
        ("proof-sec-secrets", "make sure no key or token is committed"),
        ("proof-sec-deps", "audit dependencies for known-vulnerable versions"),
        ("proof-infra", "check Dockerfiles, deployment and CI config for risky settings"),
        ("proof-email", "check transactional emails send and link correctly"),
        ("proof-load", "see how it holds up under load"),
        ("proof-mutation", "measure whether the tests actually catch bugs"),
        ("proof-restraint", "flag endpoints and dependencies that should not exist"),
        ("proof-slop", "find docs and code that read as generated"),
        ("proof-ai", "red-team and evaluate the model the API embeds"),
        ("proof-audit-trail", "package the whole run into a signed evidence folder"),
    ],
    "mobile": [
        ("proof-mobile-e2e", "drive the app through its critical flows on a device or emulator"),
        ("proof-mobile-audit", "check the build for permission, config and data-handling problems"),
        ("proof-api", "test the backend API the app talks to"),
        ("proof-sec-web", "check that backend API for the common security weaknesses"),
        ("proof-sec-secrets", "make sure no key is baked into the app bundle"),
        ("proof-sec-deps", "audit the app's dependencies"),
        ("proof-restraint", "flag code, screens and dependencies that should not exist"),
        ("proof-slop", "find copy and code that read as generated"),
        ("proof-ai", "red-team and evaluate any model the app embeds (plan --mobile adds the phone checks)"),
        ("proof-react-native", "for a React Native or Expo app: secrets in the bundle, token storage, permissions"),
        ("proof-mobile-explore", "let an agent explore the real app and record crashes and dead ends"),
        ("proof-mobile-a11y", "check labels, touch targets and large text from the app's UI trees"),
        ("proof-design-match", "compare screens with the design and spot new or missing elements"),
        ("proof-llm-device", "measure the on-device model's speed, memory, heat and battery"),
        ("proof-audit-trail", "package the whole run into a signed evidence folder"),
    ],
    "repo": [
        ("proof-sec-secrets", "scan the history and tree for committed secrets"),
        ("proof-sec-deps", "audit dependencies for known-vulnerable versions"),
        ("proof-sec-diff", "scope a security review to what a branch changed"),
        ("proof-infra", "check Dockerfiles, deployment and CI config for risky settings"),
        ("proof-mutation", "measure whether the tests actually catch bugs"),
        ("proof-restraint", "flag dead code, needless features and risky dependencies"),
        ("proof-slop", "find comments, docs and code habits that read as generated"),
        ("proof-audit-trail", "package the whole run into a signed evidence folder"),
    ],
}
RECO["mixed"] = RECO["web"]

PUBLIC_ONLY = {"proof-seo", "proof-geo"}
LLM_ONLY = {"proof-ai"}
MOBILE_ONLY = {"proof-mobile-e2e", "proof-mobile-audit"}


def _skills_present():
    skills_dir = os.path.join(HERE, "..")
    found = set()
    if os.path.isdir(skills_dir):
        for name in os.listdir(skills_dir):
            if SKILL_RE.match(name) and os.path.isfile(os.path.join(skills_dir, name, "SKILL.md")):
                found.add(name)
    return found


# --------------------------------------------------------------------- init

def _target_md(t):
    L = ["# %s" % t["name"], "", "- Kind: %s" % t["kind"]]
    if t.get("urls"):
        L.append("- URLs: %s" % ", ".join(t["urls"]))
    if t.get("repo"):
        L.append("- Repo: %s" % t["repo"])
    if t.get("platforms"):
        L.append("- Platforms: %s" % ", ".join(t["platforms"]))
    L.append("- Authorised to test: %s" % ("yes" if t.get("authorised") else "NO"))
    L.append("- Blocks Ship at: %s and above" % t.get("fail_on", "high"))
    if t.get("done_means"):
        L.append("- Done means: %s" % t["done_means"])
    L.append("")
    L.append("## Required skills")
    L.append("")
    if t.get("required_skills"):
        for s in t["required_skills"]:
            L.append("- %s" % s)
    else:
        L.append("_None chosen yet. The verdict waits for the skills you list here._")
    L.append("")
    L.append("_Every other proof skill is optional: it can be run on its own and not "
             "running it never counts against the verdict._")
    return "\n".join(L) + "\n"


def cmd_init(a):
    if a.authorised not in ("yes", "no"):
        print("error: --authorised must be yes or no", file=sys.stderr)
        return 2
    path = ledger.target_path(a.dir)
    if os.path.exists(path) and not a.force:
        existing = ledger.load_target(a.dir)
        print("error: %s already describes %r. Use --force to replace it." % (path, existing.get("name")), file=sys.stderr)
        return 2
    for s in a.require or []:
        if not SKILL_RE.match(s):
            print("error: %r is not a skill name like proof-web-e2e" % s, file=sys.stderr)
            return 2
    target = {"name": a.name, "kind": a.kind, "authorised": a.authorised == "yes",
              "urls": a.url or [], "required_skills": a.require or []}
    if a.repo:
        target["repo"] = a.repo
    if a.platform:
        target["platforms"] = a.platform
    if a.done:
        target["done_means"] = a.done
    if a.fail_on:
        target["fail_on"] = a.fail_on
    ledger.write_target(a.dir, target)
    with open(os.path.join(ledger.root(a.dir), "target.md"), "w", encoding="utf-8") as fh:
        fh.write(_target_md(ledger.load_target(a.dir)))
    print("%s wrote %s" % (ledger.OK, path))
    if a.authorised == "no":
        print("Note: authorised is NO. The testing skills will refuse to act against a live target "
              "until you confirm you own it or have written permission, and set it to yes.")
    present = _skills_present()
    missing = [s for s in (a.require or []) if s not in present]
    for s in missing:
        print("  (heads up: %s is in required_skills but its folder is not installed yet)" % s)
    return 0


# ------------------------------------------------------------------ require

def cmd_require(a):
    t = ledger.load_target(a.dir)
    if t is None:
        print("error: no target.json. Run init first.", file=sys.stderr)
        return 2
    req = list(t.get("required_skills") or [])
    present = _skills_present()
    for s in a.skills:
        if not SKILL_RE.match(s):
            print("error: %r is not a skill name like proof-web-e2e" % s, file=sys.stderr)
            return 2
    if a.action == "add":
        for s in a.skills:
            if s not in req:
                req.append(s)
            if s not in present:
                print("  (heads up: %s is not installed yet)" % s)
    else:
        req = [s for s in req if s not in a.skills]
    t["required_skills"] = req
    ledger.write_target(a.dir, t)
    with open(os.path.join(ledger.root(a.dir), "target.md"), "w", encoding="utf-8") as fh:
        fh.write(_target_md(ledger.load_target(a.dir)))
    print("%s required skills: %s" % (ledger.OK, ", ".join(req) or "none"))
    return 0


# ---------------------------------------------------------------- recommend

def cmd_recommend(a):
    order = RECO.get(a.kind, RECO["web"])
    present = _skills_present()
    shown, planned = [], []
    for skill, reason in order:
        if skill in PUBLIC_ONLY and not a.public:
            continue
        if skill in LLM_ONLY and not a.has_llm:
            continue
        if skill in MOBILE_ONLY and a.kind not in ("mobile", "mixed") and not a.mobile:
            continue
        (shown if skill in present else planned).append((skill, reason))
    print("Suggested order for a %s target (all optional; you choose what to require):\n" % a.kind)
    for i, (skill, reason) in enumerate(shown, 1):
        print("  %2d. /%s - %s" % (i, skill, reason))
    if planned:
        print("\n  Planned, not installed in this copy yet:")
        for skill, reason in planned:
            print("      %s - %s" % (skill, reason))
    print("\nPick the ones the verdict should wait for with: require add <skill> ...")
    print("Anything you leave out can still be run on its own at any time.")
    return 0


def cmd_show(a):
    t = ledger.load_target(a.dir)
    if t is None:
        print("No target.json yet. Run init.", file=sys.stderr)
        return 2
    print("%s (%s)" % (t["name"], t["kind"]))
    print("  authorised: %s" % ("yes" if t.get("authorised") else "no"))
    print("  urls: %s" % (", ".join(t.get("urls", [])) or "none"))
    if t.get("repo"):
        print("  repo: %s" % t["repo"])
    print("  fail_on: %s" % t.get("fail_on", "high"))
    print("  required: %s" % (", ".join(t.get("required_skills", [])) or "none"))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="write target.json and target.md")
    i.add_argument("--name", required=True)
    i.add_argument("--kind", required=True, choices=["web", "mobile", "api", "repo", "mixed"])
    i.add_argument("--authorised", required=True, choices=["yes", "no"],
                   help="do you own this target or have written permission to test it?")
    i.add_argument("--url", action="append", help="repeatable")
    i.add_argument("--repo")
    i.add_argument("--platform", action="append", choices=["web", "ios", "android", "api", "desktop"])
    i.add_argument("--require", action="append", help="a skill the verdict waits for (repeatable)")
    i.add_argument("--done", help="what done means for this target")
    i.add_argument("--fail-on", choices=["critical", "high", "medium", "low"])
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    r = sub.add_parser("require", help="edit the required-skills list")
    r.add_argument("action", choices=["add", "remove"])
    r.add_argument("skills", nargs="+")
    r.set_defaults(func=cmd_require)

    rc = sub.add_parser("recommend", help="suggest an order (all optional)")
    rc.add_argument("--kind", default="web", choices=["web", "mobile", "api", "repo", "mixed"])
    rc.add_argument("--public", action="store_true", help="the product is or will be publicly discoverable")
    rc.add_argument("--has-llm", action="store_true", help="the product embeds a language model")
    rc.add_argument("--mobile", choices=["ios", "android"], help="also suggest mobile skills")
    rc.set_defaults(func=cmd_recommend)

    sub.add_parser("show", help="print the target").set_defaults(func=cmd_show)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
