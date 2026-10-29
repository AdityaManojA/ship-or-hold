#!/usr/bin/env python3
"""Exploratory testing with a swarm of user personas. Standard library only.

This skill turns the scenario swarm loose on a target to find the odd cases a
scripted test never covers, then helps turn the problems the swarm reports into
ledger findings the right way: a simulated complaint is a lead, so the model
reproduces it for real and only then records a verified finding.

    python3 explore.py scaffold --target "a web checkout" --out proof/explore
    # run the swarm (see proof-swarm) on the scaffolded profile + brief, then:
    python3 explore.py harvest --results proof/swarm/explore-checkout/results.json
    python3 explore.py status
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-explore"


def _profile(target, name):
    return {
        "name": name,
        "purpose": "Explore %s the way a real, varied user would, and report anything that goes wrong, confuses, or blocks you." % target,
        "dimensions": {
            "mood": [
                {"value": "in a hurry", "share": 0.4, "detail": "Skims, taps fast, abandons at the first snag."},
                {"value": "careful", "share": 0.35, "detail": "Reads labels, double-checks before confirming."},
                {"value": "distracted", "share": 0.25, "detail": "Switches away mid-task and comes back."}],
            "skill": [
                {"value": "new", "share": 0.3, "detail": "Unsure what each control does; makes format mistakes."},
                {"value": "typical", "share": 0.5},
                {"value": "power user", "share": 0.2, "detail": "Uses back button, autofill, shortcuts, multiple tabs."}],
            "device": [
                {"value": "phone", "share": 0.55, "detail": "Small screen, on the move, variable signal."},
                {"value": "desktop", "share": 0.45}],
            "intent": [
                {"value": "trying the main task", "share": 0.6},
                {"value": "poking at edges", "share": 0.4, "detail": "Enters odd values, skips steps, goes out of order."}],
        },
        "answer": {
            "outcome": "found_problem",
            "fields": {
                "found_problem": {"type": "boolean", "prompt": "true if anything went wrong, confused you, or blocked you"},
                "severity": {"type": "enum", "values": ["none", "low", "medium", "high", "critical"],
                             "prompt": "how bad it would be for a real user"},
                "what_happened": {"type": "string", "prompt": "what you did and what you saw, in your own words"},
                "repro_steps": {"type": "string", "prompt": "the exact steps to reproduce it, or empty"},
            },
        },
    }


_BRIEF = """You are {name}, trying {purpose}

About you right now:
- Your mood: {mood}. {mood.detail}
- Your skill with this kind of thing: {skill}. {skill.detail}
- You are on a {device}. {device.detail}
- Today you are {intent}. {intent.detail}

Below is what you can see and do. Go through it the way you really would, with your
mood, your skill and your device. Try the main task, and if you are the kind of
person who pokes at the edges, do that too: odd inputs, skipped steps, the back
button, a second tab. Notice every moment you would hesitate, make a mistake, get
stuck, or give up.

== What you can see and do ==
(The moderator fills this in: the screens, fields, buttons and rules of the target.)
"""


def cmd_scaffold(a):
    name = a.name or ("explore-" + ledger.slug(a.target))
    out = a.out or os.path.join("proof", "explore")
    os.makedirs(out, exist_ok=True)
    prof_path = os.path.join(out, "profile.json")
    brief_path = os.path.join(out, "brief.md")
    with open(prof_path, "w", encoding="utf-8") as fh:
        json.dump(_profile(a.target, name), fh, ensure_ascii=False, indent=2)
    with open(brief_path, "w", encoding="utf-8") as fh:
        fh.write(_BRIEF)
    print("%s wrote %s and %s" % (ledger.OK, prof_path, brief_path))
    print("Fill in the '== What you can see and do ==' section of the brief with the real")
    print("screens and rules, then run the swarm (proof-swarm):")
    print("  python3 skills/proof-swarm/swarm.py init --profile %s --brief %s" % (prof_path, brief_path))
    print("  ... run the waves, then: swarm.py tally")
    print("Then: explore.py harvest --results proof/swarm/%s/results.json" % name)
    return 0


def cmd_harvest(a):
    with open(a.results, encoding="utf-8") as fh:
        res = json.load(fh)
    run_dir = os.path.dirname(a.results)
    answers_dir = os.path.join(run_dir, "answers")
    # Gather the agents that reported a problem, with their own words.
    problems = []
    if os.path.isdir(answers_dir):
        for name in sorted(os.listdir(answers_dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(answers_dir, name), encoding="utf-8") as fh:
                    ans = json.load(fh)
            except (OSError, ValueError):
                continue
            if ans.get("found_problem") is True:
                problems.append(ans)
    ledger.init_coverage(a.dir, SKILL, [("explore", "Exploratory pass with a persona swarm")])
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "explore-problems.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Exploratory swarm problems (%d of %d agents)\n\n" % (len(problems), res.get("answered", 0)))
        for p in problems:
            fh.write("[%s] %s\n  repro: %s\n\n" % (p.get("severity", "?"), p.get("what_happened", ""), p.get("repro_steps", "")))
    rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")
    ledger.mark_coverage(a.dir, SKILL, "explore", "covered", evidence=[rel],
                         note="%d of %d agents reported a problem" % (len(problems), res.get("answered", 0)))
    # Group similar complaints by their exact text so repeats become one candidate.
    seen = {}
    for p in problems:
        key = (p.get("what_happened", "").strip().lower())[:120]
        seen.setdefault(key, []).append(p)
    written = 0
    for key, group in seen.items():
        if not key:
            continue
        rep = max(group, key=lambda x: len(x.get("repro_steps", "")))
        sev = _worst(group)
        fid = ledger.slug("explore-%s" % key)[:60] or ledger.slug("explore-%d" % written)
        f = ledger.new_finding(
            SKILL, (rep.get("what_happened") or "Exploratory issue")[:120], sev, id=fid,
            category="exploratory", repro=rep.get("repro_steps") or None, evidence=[rel],
            fix="Reproduce this against the real app, then fix the cause.")
        ledger.write_finding(a.dir, f)
        written += 1
    print("%s harvested %d problem report(s) into %d candidate finding(s) (unverified)."
          % (ledger.OK, len(problems), written))
    print("These are leads from simulated users, not evidence. For each one that matters:")
    print("  reproduce it against the real app, save the evidence, then verify the finding.")
    print("Report of raw reports: %s" % rel)
    return 0


_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4, "info": 4}


def _worst(group):
    best = "low"
    for p in group:
        s = p.get("severity", "low")
        if s in ("none", "info"):
            s = "low"
        if _RANK.get(s, 3) < _RANK.get(best, 3):
            best = s
    return best


def cmd_status(a):
    findings = ledger.load_findings(a.dir, SKILL)
    print("exploratory: %d candidate finding(s)" % len(findings))
    for f in findings:
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scaffold", help="write a swarm profile and brief for exploratory testing")
    s.add_argument("--target", required=True, help="what to explore, e.g. 'a web checkout'")
    s.add_argument("--name", help="run name (default explore-<target>)")
    s.add_argument("--out", help="output dir (default proof/explore)")
    s.set_defaults(func=cmd_scaffold)
    h = sub.add_parser("harvest", help="turn a swarm tally into candidate findings")
    h.add_argument("--results", required=True, help="path to a swarm results.json")
    h.set_defaults(func=cmd_harvest)
    sub.add_parser("status", help="candidate findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
