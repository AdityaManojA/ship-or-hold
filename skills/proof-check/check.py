#!/usr/bin/env python3
"""Plan a targeted Proof run from a profile. Standard library only.

This tool does not test anything itself. It reviews the app, picks the skills a
profile calls for that actually fit it, writes them into target.json as the
required skills (so the verdict waits for them), and tracks progress. Each
profile is deliberately short: a skill joins only when the profile lists it and
the review found what it tests, so nobody spends a run on checks their product
does not need.

    python3 check.py review                          # what kind of app this is and what it uses
    python3 check.py profiles                        # the profiles, and which one fits this app
    python3 check.py plan --profile webapp           # review, then the web-app skills that fit
    python3 check.py plan --profile startup --public # also SEO and AI search, for a public launch
    python3 check.py plan --profile mobile --add proof-resilience --skip proof-finance
    python3 check.py status
    python3 check.py next                            # skips skills whose inputs have not changed
    python3 check.py refresh proof-web-e2e           # run it again anyway (a redeployed live target)
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
sys.path.insert(0, HERE)
import ledger  # noqa: E402
import review as appreview  # noqa: E402

PLAN_FILE = "check.json"

MOBILE_SIG = {"react_native", "native_mobile"}
WEB_SIG = {"web", "pwa"}


def _any(*names):
    return lambda s: bool(set(names) & s)


def _always(_):
    return True


# Every skill a profile can use, in run order (flows first, then security, then
# quality, then reach and money), with what it is for and when it fits the app.
# `s` is the set of review signals, plus "public" when the product is meant to be
# found. A profile only ever runs the skills on its list that fit.
SKILLS = [
    ("proof-web-e2e", "end-to-end user flows", _any("web", "pwa")),
    ("proof-mobile-e2e", "mobile user flows", _any(*MOBILE_SIG)),
    ("proof-api", "API contract and awkward inputs", _any("api")),
    ("proof-sec-secrets", "committed secrets", _always),
    ("proof-sec-deps", "vulnerable dependencies", _always),
    ("proof-sec-web", "web security checklist", _any("web", "pwa", "api")),
    ("proof-auth", "sign-in, sessions, reset, logout, rate limits", _any("auth")),
    ("proof-database", "row-level security, public buckets, risky migrations", _any("database")),
    ("proof-infra", "deployment and CI config", _always),
    ("proof-privacy", "trackers, consent and personal data", _any("web", "pwa")),
    ("proof-mobile-audit", "mobile build audit", _any(*MOBILE_SIG)),
    ("proof-react-native", "React Native and Expo config and code", _any("react_native")),
    ("proof-store-ready", "App Store and Google Play rejection risks", _any(*MOBILE_SIG)),
    ("proof-web-perf", "performance budget", _any("web", "pwa")),
    ("proof-a11y", "accessibility", _any("web", "pwa")),
    ("proof-mobile-a11y", "phone accessibility from UI trees", _any(*MOBILE_SIG)),
    ("proof-pwa", "installable web app: manifest, service worker, offline", _any("pwa")),
    ("proof-email", "transactional emails", _any("email")),
    ("proof-i18n", "translations and locales", _any("i18n")),
    ("proof-ai", "LLM feature safety", _any("llm")),
    ("proof-llm-cost", "LLM cost per user and response times", _any("llm")),
    ("proof-rag", "retrieval answer quality and leaks between users", _any("rag")),
    ("proof-llm-device", "on-device LLM speed, memory, heat and battery", _any("on_device_llm")),
    ("proof-voice", "voice and real-time audio", _any("voice")),
    ("proof-resilience", "offline, slow network, server errors, double submits",
     _any("web", "pwa", "api", *MOBILE_SIG)),
    ("proof-load", "behaviour under load", _any("web", "pwa", "api")),
    ("proof-explore", "exploratory persona swarm", _any("web", "pwa", *MOBILE_SIG)),
    ("proof-mobile-explore", "an agent explores the real app on a device", _any(*MOBILE_SIG)),
    ("proof-compat", "browser and device matrix", _any("web", "pwa")),
    ("proof-visual", "visual regression; means something from the second run", _any("web", "pwa", *MOBILE_SIG)),
    ("proof-design-match", "screens against the design and the last build", _any("web", "pwa", *MOBILE_SIG)),
    ("proof-restraint", "dead code and bloat", _always),
    ("proof-slop", "generated-looking copy, UI and code", _always),
    ("proof-licenses", "dependency licences and a software bill of materials", _always),
    ("proof-seo", "search engine optimisation", lambda s: "public" in s and bool(WEB_SIG & s)),
    ("proof-geo", "AI-search citability", lambda s: "public" in s and bool(WEB_SIG & s)),
    ("proof-release", "launch checklist; items only a person can prove", _always),
    ("proof-finance", "unit economics", _always),
]
WHY = {name: why for name, why, _ in SKILLS}
FITS = {name: fits for name, _, fits in SKILLS}

# The profiles. Each is a short list on purpose: what a product of that kind
# cannot ship without, nothing it merely could check. Everything else stays one
# --add away, and /proof-ultracheck is the opt-in for all of it.
PROFILES = {
    "quick": {
        "title": "quick check",
        "for": "any repo, in minutes: offline scans of secrets, dependencies and deploy config, no running app",
        "skills": ["proof-sec-secrets", "proof-sec-deps", "proof-infra"],
    },
    "startup": {
        "title": "startup launch check",
        "for": "an MVP about to launch: the core flows, the risks that sink a launch, and whether each sale makes money",
        "skills": ["proof-web-e2e", "proof-mobile-e2e", "proof-sec-secrets", "proof-sec-deps", "proof-auth",
                   "proof-database", "proof-privacy", "proof-store-ready", "proof-email", "proof-ai",
                   "proof-seo", "proof-geo", "proof-finance"],
    },
    "webapp": {
        "title": "web app check",
        "for": "a web app or PWA: flows, web security, privacy, speed and accessibility",
        "needs": WEB_SIG,
        "skills": ["proof-web-e2e", "proof-api", "proof-sec-secrets", "proof-sec-deps", "proof-sec-web",
                   "proof-auth", "proof-database", "proof-privacy", "proof-web-perf", "proof-a11y", "proof-pwa",
                   "proof-email", "proof-i18n", "proof-ai", "proof-seo", "proof-geo"],
    },
    "mobile": {
        "title": "mobile app check",
        "for": "an iOS or Android app (native, React Native or Expo): flows, the build, store rules and phone accessibility",
        "needs": MOBILE_SIG,
        "skills": ["proof-mobile-e2e", "proof-api", "proof-sec-secrets", "proof-sec-deps", "proof-auth",
                   "proof-mobile-audit", "proof-react-native", "proof-store-ready", "proof-mobile-a11y",
                   "proof-ai", "proof-llm-device", "proof-voice"],
    },
    "backend": {
        "title": "backend check",
        "for": "an API or backend service: the contract, security, data and deployment",
        "needs": {"api"},
        "skills": ["proof-api", "proof-sec-secrets", "proof-sec-deps", "proof-sec-web", "proof-auth",
                   "proof-database", "proof-infra", "proof-email", "proof-ai", "proof-llm-cost", "proof-rag"],
    },
    "security": {
        "title": "security check",
        "for": "security only, any kind of app: secrets, dependencies, web security, sign-in, data and infra",
        "skills": ["proof-sec-secrets", "proof-sec-deps", "proof-sec-web", "proof-auth", "proof-database",
                   "proof-infra", "proof-privacy", "proof-ai"],
    },
    "ultra": {
        "title": "ultra check",
        "for": "everything that fits the app, optional skills included; long and token-heavy, opt in deliberately",
        "skills": None,  # every skill that fits
    },
}
SKILL_RE = re.compile(r"^proof-[a-z0-9-]+$")
KIND_SIGNAL = {"web": {"web"}, "api": {"api"}, "mobile": {"native_mobile"}, "repo": set(), "mixed": set()}


def _installed():
    base = os.path.join(HERE, "..")
    return {n for n in os.listdir(base)
            if SKILL_RE.match(n) and os.path.isfile(os.path.join(base, n, "SKILL.md"))}


def signals_for(review_signals, kind=None, public=False, has_llm=False, mobile=False):
    """The set the fit rules read: the review's signals, topped up from the target and flags."""
    s = set(review_signals)
    # A target described as web/api/mobile counts even when the review missed it
    # (an app in a folder the review did not reach, for example).
    s |= KIND_SIGNAL.get(kind or "", set())
    if public:
        s.add("public")
    if has_llm:
        s.add("llm")
    if mobile:
        s.add("native_mobile")
    return s


def select(profile, signals, skip=(), add=(), installed=None):
    """Return [(skill, reason)] for a profile, in run order."""
    installed = _installed() if installed is None else installed
    wanted = PROFILES[profile]["skills"]
    # A profile for another kind of product runs nothing rather than half a check.
    fitting = fits_profile(profile, signals)
    out = []
    for name, why, fits in SKILLS:
        if name not in installed or name in skip:
            continue
        if name in add or (fitting and (wanted is None or name in wanted) and fits(signals)):
            out.append((name, why))
    return out


def fits_profile(profile, signals):
    """Whether the app is the kind of product the profile is for."""
    needs = PROFILES[profile].get("needs")
    return not needs or bool(needs & signals)


def suggest(signals):
    """The profile that best fits what the review found."""
    if MOBILE_SIG & signals:
        return "mobile"
    if WEB_SIG & signals:
        return "webapp"
    if "api" in signals:
        return "backend"
    return "quick"


def _review_path(d):
    return os.path.join(ledger.root(d), "review.json")


def run_review(d, projects):
    """Review the project folders, save proof/review.json and print what was found."""
    r = appreview.review(projects or ["."])
    r["reviewed"] = ledger.now()
    os.makedirs(ledger.root(d), exist_ok=True)
    with open(_review_path(d), "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=2)
    print("Review of %s:" % ", ".join(projects or ["."]))
    for line in appreview.summary_lines(r):
        print("  " + line)
    return r


def cmd_review(a):
    r = run_review(a.dir, a.project)
    print("\nSaved to %s. Suggested: check.py plan --profile %s" % (_review_path(a.dir), suggest(set(r["signals"]))))
    return 0


def cmd_profiles(a):
    sig = None
    if os.path.exists(_review_path(a.dir)):
        with open(_review_path(a.dir), encoding="utf-8") as fh:
            sig = set(json.load(fh).get("signals", {}))
    best = suggest(sig) if sig is not None else None
    installed = _installed()
    for name, p in PROFILES.items():
        mark = "  <- fits this app" if name == best else ""
        if sig is None:
            count = "%d skills at most" % len(p["skills"]) if p["skills"] else "every skill that fits"
        elif not fits_profile(name, signals_for(sig)):
            count = "not this kind of app"
        else:
            count = "%d skills for this app" % len(select(name, signals_for(sig), installed=installed))
        print("/proof-%s  %s (%s)%s" % (name if name != "ultra" else "ultracheck", p["for"], count, mark))
    if sig is None:
        print("\nRun check.py review first to see which profile fits and how many skills each would run.")
    return 0


def _plan_path(d):
    return os.path.join(ledger.root(d), PLAN_FILE)


# ------------------------------------------------------------- the cache
#
# A skill that finished on inputs that have not changed since does not need to
# run again. Each finished skill keeps a fingerprint of what it reads; on the
# next run it is reported as unchanged and skipped, so neither its instructions
# nor its tools are loaded. A skill whose inputs moved is reported as stale.

SKIP_DIRS = {".git", "node_modules", ".next", ".nuxt", ".svelte-kit", "dist", "build", "out", ".venv", "venv",
             "env", "__pycache__", ".pytest_cache", ".mypy_cache", ".turbo", ".cache", ".expo", "Pods",
             ".gradle", "target", "coverage", "proof", ".idea", ".vscode"}
DEPS_RE = re.compile(r"^(package(-lock)?\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|"
                     r"requirements[\w.-]*\.txt|constraints[\w.-]*\.txt|pyproject\.toml|poetry\.lock|uv\.lock|"
                     r"Pipfile(\.lock)?|setup\.(py|cfg)|go\.(mod|sum)|Cargo\.(toml|lock)|Gemfile(\.lock)?|"
                     r"composer\.(json|lock)|Podfile(\.lock)?|build\.gradle(\.kts)?|.*\.csproj)$")
INFRA_RE = re.compile(r"^(Dockerfile.*|.*\.dockerfile|\.dockerignore|(docker-)?compose.*\.ya?ml|render\.yaml|"
                      r"vercel\.json|netlify\.toml|fly\.toml|app\.ya?ml|Procfile|.*\.tf|.*\.tfvars|"
                      r"Chart\.yaml|kustomization\.ya?ml)$")
INFRA_DIRS = (".github/workflows/", ".circleci/", "k8s/", "kubernetes/", "helm/", "deploy/")
# What each skill reads. Everything not listed reads the source ("code"); the
# finance skill also reads the docs its inputs come from ("all").
INPUTS = {"proof-sec-deps": "deps", "proof-licenses": "deps", "proof-infra": "infra", "proof-finance": "all"}


def _walk(roots):
    for root in roots:
        root = os.path.abspath(root)
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for n in sorted(files):
                full = os.path.join(dirpath, n)
                yield os.path.relpath(full, root).replace(os.sep, "/"), n, full


def fingerprints(roots, target=None):
    """{"deps", "infra", "code", "all"}: a short hash of what each group of skills reads.

    Dependency and deploy files are hashed by content (they are small). Source
    files are hashed by size and modification time, which is fast; a touch with
    no edit only costs one extra re-run, never a missed change."""
    h = {k: hashlib.sha256() for k in ("deps", "infra", "code", "all")}
    where = json.dumps({"kind": (target or {}).get("kind"), "urls": (target or {}).get("urls")}, sort_keys=True)
    for k in h:
        h[k].update(where.encode("utf-8"))
    for rel, name, full in _walk(roots):
        try:
            st = os.stat(full)
        except OSError:
            continue
        stamp = ("%s %d %d" % (rel, st.st_size, st.st_mtime_ns)).encode("utf-8")
        is_deps = bool(DEPS_RE.match(name))
        is_infra = bool(INFRA_RE.match(name)) or any(("/" + rel).find("/" + x) >= 0 for x in INFRA_DIRS)
        if is_deps or is_infra:
            try:
                with open(full, "rb") as fh:
                    stamp = rel.encode("utf-8") + b" " + hashlib.sha256(fh.read()).digest()
            except OSError:
                pass
            h["deps" if is_deps else "infra"].update(stamp)
        h["all"].update(stamp)
        if not name.lower().endswith((".md", ".rst")):
            h["code"].update(stamp)
    return {k: v.hexdigest()[:16] for k, v in h.items()}


def _cov_mtime(d, skill):
    path = ledger.coverage_path(d, skill)
    return os.path.getmtime(path) if os.path.exists(path) else 0.0


def _load_plan(d):
    path = _plan_path(d)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_plan(d, plan):
    os.makedirs(ledger.root(d), exist_ok=True)
    with open(_plan_path(d), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)


def _created_ts(plan):
    try:
        return datetime.datetime.fromisoformat(plan["created"]).timestamp()
    except (KeyError, ValueError):
        return 0.0


def cmd_plan(a):
    t = ledger.load_target(a.dir)
    if t is None:
        print("error: no proof/target.json. Run /proof-plan first to describe the target "
              "and confirm you are authorised to test it.", file=sys.stderr)
        return 2
    for s in (a.skip or []) + (a.add or []):
        if not SKILL_RE.match(s):
            print("error: %r is not a skill name" % s, file=sys.stderr)
            return 2
    rv = run_review(a.dir, a.project)
    if rv["kind"] != t["kind"]:
        print("  Note: the review reads this as %s; target.json says %s. Using both; re-run "
              "/proof-plan to change the target." % (rv["kind"], t["kind"]))
    sig = signals_for(rv["signals"], t["kind"], a.public, a.has_llm, bool(a.mobile))
    chosen = select(a.profile, sig, set(a.skip or []), set(a.add or []))
    if not chosen:
        why = ("this is not the kind of app the %s profile is for" % a.profile
               if not fits_profile(a.profile, sig) else "no installed skill in the profile fits it")
        print("error: %s. The review suggests --profile %s; check.py profiles compares them."
              % (why, suggest(sig)), file=sys.stderr)
        return 2
    t["required_skills"] = [s for s, _ in chosen]
    ledger.write_target(a.dir, t)
    title = PROFILES[a.profile]["title"]
    # Keep what earlier runs learnt, so a re-plan does not throw the cache away.
    old = _load_plan(a.dir) or {}
    cache = {s: v for s, v in (old.get("cache") or {}).items() if s in dict(chosen)}
    roots = [os.path.abspath(p) for p in (a.project or ["."])]
    _save_plan(a.dir, {"mode": title, "profile": a.profile, "kind": t["kind"], "public": bool(a.public),
                       "skills": [s for s, _ in chosen], "roots": roots, "cache": cache, "created": ledger.now()})
    print("%s %s for a %s target: %d skills, now required by the verdict." % (ledger.OK, title, t["kind"], len(chosen)))
    for i, (s, why) in enumerate(chosen, 1):
        print("  %2d. /%s - %s" % (i, s, why))
    if cache:
        print("  %d of them finished in an earlier run; check.py next skips any whose inputs have not changed."
              % len(cache))
    if not t.get("authorised"):
        print("\nNote: target.json says authorised: no. The security and load skills will refuse to "
              "act against a live target until it is yes.")
    best = suggest(set(rv["signals"]) | KIND_SIGNAL.get(t["kind"], set()))
    if a.profile not in (best, "ultra", "startup", "security", "quick"):
        print("\nThe review suggests the %s profile for this app (check.py plan --profile %s)." % (best, best))
    if a.profile != "ultra":
        have = dict(chosen)
        fits = [(s, WHY[s]) for s, _, f in SKILLS if s in _installed() and s not in have and f(sig)]
        if fits:
            print("\nAlso fits this app (add with --add): %s" % ", ".join(s for s, _ in fits))
    print("\nRun them in order (check.py next tells you which), then /proof-report.")
    return 0


def _progress(d):
    plan = _load_plan(d)
    if plan is None:
        return None
    reported = set(ledger.skills_reported(d))
    cache = plan.setdefault("cache", {})
    fps = None
    changed = False
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
            if fps is None:
                fps = fingerprints(plan.get("roots") or ["."], ledger.load_target(d))
            cur = fps[INPUTS.get(s, "code")]
            entry = cache.get(s)
            if entry is None or _cov_mtime(d, s) > entry["at"]:
                # Finished just now, or run again after its inputs changed: remember what it saw.
                cache[s] = {"fp": cur, "at": time.time()}
                changed = True
                state = "done"
            elif entry["fp"] == cur:
                state = "unchanged" if entry["at"] < _created_ts(plan) else "done"
            else:
                state = "refresh" if entry["fp"] == "refresh" else "stale"
        rows.append({"skill": s, "state": state, "not_run": not_run, "blockers": blockers})
    if changed:
        _save_plan(d, plan)
    return plan, rows


FINISHED = ("done", "unchanged")


def cmd_status(a):
    got = _progress(a.dir)
    if got is None:
        print("no check planned yet. Run: check.py plan --profile <name> (check.py profiles lists them)")
        return 0
    plan, rows = got
    done = sum(1 for r in rows if r["state"] in FINISHED)
    kept = sum(1 for r in rows if r["state"] == "unchanged")
    print("%s: %d of %d skills done%s" % (plan["mode"], done, len(rows),
                                          " (%d unchanged since the last run, not re-run)" % kept if kept else ""))
    for r in rows:
        mark = {"done": ledger.OK, "unchanged": ledger.OK, "in progress": "..", "not started": "  ",
                "stale": "!!", "refresh": "!!"}[r["state"]]
        extra = ""
        if r["state"] == "in progress":
            extra = " (%d check(s) not run)" % r["not_run"]
        if r["state"] == "stale":
            extra = " (its inputs changed since it last ran)"
        if r["blockers"]:
            extra += " [%d verified high/critical]" % r["blockers"]
        print("  %s %-20s %s%s" % (mark, r["skill"], r["state"], extra))
    if done == len(rows):
        print("\nAll done. Compile the verdict with /proof-report.")
    return 0


def cmd_next(a):
    got = _progress(a.dir)
    if got is None:
        print("no check planned yet. Run: check.py plan --profile <name> (check.py profiles lists them)")
        return 0
    plan, rows = got
    kept = [r["skill"] for r in rows if r["state"] == "unchanged"]
    for r in rows:
        if r["state"] not in FINISHED:
            why = {"stale": "inputs changed since it last ran; re-run it",
                   "refresh": "marked to run again; re-run it"}.get(r["state"], r["state"])
            print("Next: /%s (%s)" % (r["skill"], why))
            if kept:
                print("Skipped, unchanged since the last run: %s" % ", ".join(kept))
            return 0
    print("Every skill in the %s is done%s. Next: /proof-report"
          % (plan["mode"], " (%d unchanged and skipped)" % len(kept) if kept else ""))
    return 0


def cmd_refresh(a):
    """Force skills to run again on the next pass, even if their inputs look unchanged."""
    plan = _load_plan(a.dir)
    if plan is None:
        print("no check planned yet.")
        return 0
    names = plan["skills"] if a.all else (a.skill or [])
    for s in names:
        if s in plan.get("cache", {}):
            plan["cache"][s] = {"fp": "refresh", "at": plan["cache"][s]["at"]}
    _save_plan(a.dir, plan)
    print("%s %d skill(s) will run again: %s" % (ledger.OK, len(names), ", ".join(names) or "none"))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rv = sub.add_parser("review", help="what kind of app this is and what it uses")
    rv.add_argument("--project", action="append", help="a project folder to review (repeatable; default .)")
    rv.set_defaults(func=cmd_review)
    sub.add_parser("profiles", help="the profiles, and which one fits this app").set_defaults(func=cmd_profiles)
    p = sub.add_parser("plan", help="review the app, then require the profile's skills that fit it")
    p.add_argument("--profile", required=True, choices=list(PROFILES), help="which check to plan")
    p.add_argument("--public", action="store_true", help="the product is meant to be found: add SEO and AI search")
    p.add_argument("--project", action="append", help="a project folder to review (repeatable; default .)")
    p.add_argument("--has-llm", action="store_true", help="treat the app as calling an LLM (the review also finds it)")
    p.add_argument("--mobile", choices=["ios", "android"], help="treat the target as a mobile app")
    p.add_argument("--add", action="append", help="add a skill the profile leaves out (repeatable)")
    p.add_argument("--skip", action="append", help="leave out a skill (repeatable)")
    p.set_defaults(func=cmd_plan)
    sub.add_parser("status", help="progress through the planned check").set_defaults(func=cmd_status)
    sub.add_parser("next", help="the next skill to run").set_defaults(func=cmd_next)
    rf = sub.add_parser("refresh", help="run skills again next pass even if their inputs look unchanged")
    rf.add_argument("skill", nargs="*", help="skills to refresh (for a live target that was redeployed)")
    rf.add_argument("--all", action="store_true", help="every skill in the plan")
    rf.set_defaults(func=cmd_refresh)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
