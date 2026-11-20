#!/usr/bin/env python3
"""Compile a Proof run into the main result: a business plan and a one-pager.

Standard library only, no network. The plan is built from what the other skills
recorded, never from guesses: the computed verdict, the board's decision, the
measured numbers, each business section under proof/business/, and one page on
the engineering. A section nobody produced is listed as not done, with the skill
that produces it; the tool never fills one in.

    python3 plan.py check                 # the verdict, what is there, what is missing
    python3 plan.py compile               # write proof/business-plan.md and proof/one-pager.md
    python3 plan.py compile --open        # and open the plan
    python3 plan.py idea-template         # write an idea file to fill in

Exit code follows the verdict: 0 Ship, 1 Not yet, 2 Incomplete.
"""

import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

_spec = importlib.util.spec_from_file_location("proof_verdict_plan", os.path.join(HERE, "..", "proof-report", "verdict.py"))
verdict = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verdict)

SKILL = "proof-business-plan"
PLAN_FILE, ONE_PAGER, SUMMARY_FILE = "business-plan.md", "one-pager.md", "summary.md"
BUSINESS_DIR = "business"
IDEA_FILE = os.path.join(BUSINESS_DIR, "idea.md")
MARK = "compiled by proof-business-plan"
SUMMARY_MIN_WORDS, SUMMARY_MAX_WORDS = 80, 450

# The business sections, in the order a reader needs them: who else is there, will
# anyone buy, at what price, what exactly is offered, do the numbers work, how does
# it reach people, how does it run. (file under proof/, heading, the skill that writes it)
SECTIONS = [
    (os.path.join(BUSINESS_DIR, "market.md"), "Market and competition", "proof-market"),
    (os.path.join(BUSINESS_DIR, "buyers.md"), "Buyer panel", "proof-buyers"),
    (os.path.join(BUSINESS_DIR, "pricing.md"), "Pricing", "proof-pricing"),
    (os.path.join(BUSINESS_DIR, "offer.md"), "The offer", "proof-offer"),
    ("finance.md", "The numbers", "proof-finance"),
    (os.path.join(BUSINESS_DIR, "go-to-market.md"), "Go to market", "proof-go-to-market"),
    (os.path.join(BUSINESS_DIR, "ops.md"), "Operations", "proof-ops"),
]
BUSINESS_SKILLS = [sk for _, _, sk in SECTIONS]
# Everything that is not a business section or bookkeeping is engineering and
# product quality: it gets one page in the plan and the full detail in report.md.
NOT_ENGINEERING = set(BUSINESS_SKILLS) | {"proof-release", SKILL}
SEV_ORDER = {s: i for i, s in enumerate(ledger.SEVERITIES)}

IDEA_TEMPLATE = """# <the business, or a working name>

- What it is: one sentence.
- Who it is for: the customer, as specific as you can make it.
- What it sells and at what price: the product and the price, or "free to the user" and who pays instead.
- How it makes money: every revenue line, and whether each is live today.
- Where and how: the city or the channel, and how customers find it.
- What it costs to run: per month, and what was spent to build it.
- What is already decided: budget, deadline, team, anything fixed.
- What is not known yet: say so here rather than guess.
"""


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _demote(text, by=1):
    """Push every Markdown heading down so a section nests under the plan's own."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"^#{1,5} ", line):
            line = "#" * by + line
        out.append(line)
    return "\n".join(out)


def _strip_title(text):
    """Drop a leading '# Title' line; the plan writes its own heading."""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^# ", lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _words(text):
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def _fmt(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return "{:,.2f}".format(v).rstrip("0").rstrip(".")
    if isinstance(v, int):
        return "{:,}".format(v)
    return str(v)


def gather(d):
    """Everything the plan is built from. Reads only; decides nothing."""
    r = verdict.compute(d)
    root = ledger.root(d)
    sections = []
    for rel, title, skill in SECTIONS:
        text = _read(os.path.join(root, rel))
        sections.append({"file": rel.replace(os.sep, "/"), "title": title, "skill": skill, "text": text})
    idea = _read(os.path.join(root, IDEA_FILE))
    idea_from = IDEA_FILE.replace(os.sep, "/")
    if not idea:
        idea = _read(os.path.join(root, "product.md"))
        idea_from = "product.md" if idea else None
    summary = _read(os.path.join(root, SUMMARY_FILE))
    decision = _load_json(os.path.join(root, "board", "decision.json"))
    return {"r": r, "sections": sections, "idea": idea, "idea_from": idea_from, "summary": summary,
            "decision": decision, "root": root}


def summary_problems(summary, verdict_word):
    """Why the half-page summary is not acceptable yet, as a list of plain reasons."""
    if not summary:
        return ["%s is not written yet" % SUMMARY_FILE]
    out = []
    n = _words(_strip_title(summary))
    if n < SUMMARY_MIN_WORDS:
        out.append("%s is %d words; half a page is at least %d" % (SUMMARY_FILE, n, SUMMARY_MIN_WORDS))
    if n > SUMMARY_MAX_WORDS:
        out.append("%s is %d words; half a page is at most %d" % (SUMMARY_FILE, n, SUMMARY_MAX_WORDS))
    if verdict_word and verdict_word.lower() not in summary.lower():
        out.append("%s does not state the computed verdict (%s)" % (SUMMARY_FILE, verdict_word))
    return out


def missing(g):
    """What the plan does not have yet: [(what, how to get it)]."""
    out = []
    if not g["idea"]:
        out.append(("The idea file", "write proof/%s (plan.py idea-template gives the shape)" % IDEA_FILE.replace(os.sep, "/")))
    for s in g["sections"]:
        if not s["text"]:
            out.append((s["title"], "run /%s" % s["skill"]))
    if not g["decision"]:
        out.append(("The board's decision", "run /proof-board"))
    elif g["decision"].get("verdict") != g["r"]["verdict"]:
        out.append(("A board decision on today's evidence",
                    "the board met when the verdict was %s; run /proof-board again" % g["decision"].get("verdict")))
    for p in summary_problems(g["summary"], g["r"]["verdict"]):
        out.append(("The summary", p))
    return out


def key_numbers(r):
    """Every measured number from the business skills, with its bar: [(label, value, bar, ok)]."""
    rows = []
    by_skill = {s["skill"]: s for s in r["scores"]}
    for skill in BUSINESS_SKILLS:
        s = by_skill.get(skill)
        if not s:
            continue
        for m in s["metrics"]:
            label = "%s: %s" % (s["skill"].replace("proof-", ""), m["id"].replace("_", " "))
            unit = m.get("unit") or ""
            value = "%s%s" % (_fmt(m["value"]), "" if unit in ("", "count", "currency") else " " + unit)
            rows.append((label, value, "%s %s" % (m["op"], _fmt(m["budget"])), ledger.metric_passes(m)))
    return rows


def _skill_status(r, skill):
    """One line on a skill, from the ledger alone."""
    fs = [f for f in r["findings"] if f["skill"] == skill and f["status"] == "verified"]
    cov = next((c for c in r["coverage"] if c["skill"] == skill), None)
    parts = []
    if cov:
        done = sum(1 for c in cov["checks"] if c["status"] != "not_run")
        parts.append("%d of %d checks done" % (done, len(cov["checks"])))
    if fs:
        by = {}
        for f in fs:
            by[f["severity"]] = by.get(f["severity"], 0) + 1
        parts.append("verified findings: " + ", ".join("%d %s" % (by[k], k) for k in sorted(by, key=SEV_ORDER.get)))
    else:
        parts.append("no verified findings")
    for s in r["scores"]:
        if s["skill"] == skill:
            bad = [m["id"] for m in s["metrics"] if not ledger.metric_passes(m)]
            if bad:
                parts.append("over budget: " + ", ".join(bad))
    return "; ".join(parts)


def _top_findings(r, skills=None, n=5):
    fs = [f for f in r["findings"] if f["status"] == "verified" and (skills is None or f["skill"] in skills)]
    fs.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["skill"], f["id"]))
    return fs[:n]


def _verdict_block(r):
    L = ["**Verdict: %s**" % r["verdict"], ""]
    if r["gaps"]:
        L.append("The verdict cannot rest on the evidence yet:")
        L += ["- %s" % g for g in r["gaps"]]
    elif r["blockers"]:
        L.append("What blocks it:")
        for b in r["blockers"]:
            if b["kind"] == "finding":
                L.append("- %s finding, %s: %s" % (b["severity"], b["skill"], b["title"]))
            else:
                L.append("- %s: %s is %s, the bar is %s %s" % (b["skill"], b["id"].replace("_", " "), _fmt(b["value"]),
                                                              b["op"], _fmt(b["budget"])))
    else:
        L.append("Every required check ran, nothing verified is at or above the fail-on severity, and every "
                 "measured number is within its bar.")
    return L


def _numbers_block(r):
    rows = key_numbers(r)
    if not rows:
        return ["No business numbers have been measured yet."]
    L = ["Money is in the currency each section states.", "",
         "| key number | value | the bar | |", "| --- | ---: | --- | --- |"]
    for label, value, bar, ok in rows:
        L.append("| %s | %s | %s | %s |" % (label, value, bar, "ok" if ok else "MISSES"))
    return L


def _board_block(dec, short=False):
    if not dec:
        return ["The board has not met. Run /proof-board."]
    votes = dec.get("votes") or {}
    L = ["**Board decision: %s.** Votes: %d go, %d go with conditions, %d hold." % (
        dec.get("decision"), len(votes.get("go") or []), len(votes.get("go_with_conditions") or []),
        len(votes.get("hold") or []))]
    if dec.get("reasons"):
        L.append("")
        L += ["- %s" % x for x in dec["reasons"][:(2 if short else 6)]]
    conds = dec.get("conditions") or []
    if conds:
        L += ["", "Top actions the board set:" if short else "The actions the board set, most-raised first:", ""]
        for c in conds[:(5 if short else 12)]:
            L.append("- %s (owner %s, %s)" % (c.get("action"), str(c.get("owner") or "team").upper(),
                                               c.get("by") or "before launch"))
        if len(conds) > (5 if short else 12):
            L.append("- ... %d more in board/minutes.md" % (len(conds) - (5 if short else 12)))
    if not short:
        L += ["", "The whole meeting is in `board/minutes.md` and plays itself in `board/meeting.html`. The "
              "directors are sub-agents applying documented thinking; their votes are advice. The decision "
              "cannot contradict the evidence: vetoes come from the ledger."]
    return L


def _engineering_block(r):
    reported = sorted({c["skill"] for c in r["coverage"]} | {f["skill"] for f in r["findings"]}
                      | {s["skill"] for s in r["scores"]})
    eng = [sk for sk in reported if sk not in NOT_ENGINEERING]
    if not eng:
        return ["No engineering or product-quality check has reported. A targeted check such as /proof-webapp, "
                "/proof-mobile or /proof-backend covers this side."]
    L = ["One line per check, from the ledger. Every finding with its evidence is in `report.md`.", "",
         "| check | status |", "| --- | --- |"]
    for sk in eng:
        L.append("| %s | %s |" % (sk.replace("proof-", ""), _skill_status(r, sk)))
    top = _top_findings(r, set(eng))
    if top:
        L += ["", "The findings that matter most:", ""]
        L += ["- %s (%s, %s)" % (f["title"], f["severity"], f["skill"].replace("proof-", "")) for f in top]
    return L


def _not_done_block(g):
    r, L = g["r"], []
    miss = missing(g)
    if not miss and not r["gaps"]:
        return ["Nothing: every section is written and every required check ran."]
    for what, how in miss:
        L.append("- %s: %s." % (what, how))
    for gap in r["gaps"]:
        L.append("- %s" % gap)
    if r["optional_not_run"]:
        L.append("- Optional checks not run: %s." % ", ".join(r["optional_not_run"]))
    return L


def plan_md(g):
    r = g["r"]
    name = (r["target"] or {}).get("name") or "The business"
    L = ["# %s: business plan" % name, ""]
    L += _verdict_block(r) + [""]
    L += _board_block(g["decision"], short=True) + [""]
    L += _numbers_block(r) + [""]

    L += ["## Summary", ""]
    if g["summary"]:
        L += [_demote(_strip_title(g["summary"]), 2), ""]
    else:
        L += ["Not written yet. It is half a page in plain words, written last, from the sections below.", ""]

    L += ["## The idea", ""]
    L += [_demote(_strip_title(g["idea"]), 2) if g["idea"] else "No idea file yet.", ""]

    L += ["## What the board decided", ""] + _board_block(g["decision"]) + [""]

    for s in g["sections"]:
        L += ["## %s" % s["title"], ""]
        if s["text"]:
            L += [_demote(_strip_title(s["text"]), 2), ""]
        else:
            L += ["Not done. Run /%s." % s["skill"], ""]

    L += ["## Launch readiness", ""]
    if any(c["skill"] == "proof-release" for c in r["coverage"]):
        L += [_skill_status(r, "proof-release") + ". The item-by-item checklist is in the launch readiness "
              "evidence.", ""]
    else:
        L += ["The launch-day checklist (alerts, backups, rollback, on-call) has not been run. Run /proof-release.", ""]

    L += ["## The product itself: engineering and quality", ""] + _engineering_block(r) + [""]
    L += ["## What is not done yet", ""] + _not_done_block(g) + [""]
    L += ["---", "", "_%s on %s. Built only from what the checks recorded: the verdict is computed, simulated "
          "buyers and directors are not customers or investors, and estimates are labelled where they were "
          "entered. Not financial or legal advice._" % (MARK, ledger.now()[:10])]
    return "\n".join(L) + "\n"


def _first_paragraphs(text, n=2):
    paras = [p.strip() for p in re.split(r"\n\s*\n", _strip_title(text or "")) if p.strip()]
    return [p for p in paras if not p.startswith("#")][:n]


def one_pager_md(g):
    r = g["r"]
    name = (r["target"] or {}).get("name") or "The business"
    L = ["# %s in two minutes" % name, ""]
    L += ["**Verdict: %s.**" % r["verdict"]]
    if g["decision"]:
        L[-1] += " **Board: %s.**" % g["decision"].get("decision")
    L.append("")
    L += ["## What it is", ""] + (_first_paragraphs(g["idea"]) or ["No idea file yet."]) + [""]
    L += ["## The numbers", ""] + _numbers_block(r) + [""]
    L += ["## The evidence", ""]
    any_row = False
    for s in g["sections"]:
        if any(c["skill"] == s["skill"] for c in r["coverage"]) or any(x["skill"] == s["skill"] for x in r["scores"]):
            L.append("- %s: %s." % (s["title"], _skill_status(r, s["skill"])))
            any_row = True
    if not any_row:
        L.append("- No business check has reported yet.")
    L.append("")
    top = _top_findings(r, set(BUSINESS_SKILLS), 5)
    if top:
        L += ["## What stands in the way", ""]
        L += ["- %s (%s)" % (f["title"], f["severity"]) for f in top] + [""]
    L += ["## What happens next", ""]
    conds = (g["decision"] or {}).get("conditions") or []
    if conds:
        L += ["- %s" % c.get("action") for c in conds[:5]]
    elif r["gaps"]:
        L += ["- %s" % x for x in r["gaps"][:5]]
    else:
        L.append("- Put the plan in front of the board: run /proof-board.")
    L += ["", "_Same numbers as the plan, word for word. %s on %s._" % (MARK, ledger.now()[:10])]
    return "\n".join(L) + "\n"


def _write(path, text, d, force):
    ledger.guard_out(path, d, force, ours=MARK)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def cmd_check(a):
    g = gather(a.dir)
    r = g["r"]
    print("Verdict: %s" % r["verdict"])
    for x in r["gaps"]:
        print("  %s %s" % (ledger.BAD, x))
    for b in r["blockers"]:
        if b["kind"] == "finding":
            print("  %s %s finding in %s: %s" % (ledger.BAD, b["severity"], b["skill"], b["title"]))
        else:
            print("  %s %s %s is %s, the bar is %s %s" % (ledger.BAD, b["skill"], b["id"], _fmt(b["value"]), b["op"],
                                                         _fmt(b["budget"])))
    for label, value, bar, ok in key_numbers(r):
        print("  %s %s = %s (bar %s)" % (ledger.OK if ok else ledger.BAD, label, value, bar))
    print("Sections:")
    for s in g["sections"]:
        print("  %s %s%s" % (ledger.OK if s["text"] else "..", s["title"],
                             "" if s["text"] else "  -> /%s" % s["skill"]))
    if g["decision"]:
        print("Board: %s" % g["decision"].get("decision"))
    miss = missing(g)
    if miss:
        print("Not in the plan yet:")
        for what, how in miss:
            print("  .. %s: %s" % (what, how))
    else:
        print("%s every section, the board's decision and the summary are in place." % ledger.OK)
    return verdict.EXIT[r["verdict"]]


def cmd_compile(a):
    g = gather(a.dir)
    root = g["root"]
    if not os.path.isdir(root):
        print("error: no %s folder. Run /proof-plan first." % a.dir, file=sys.stderr)
        return 2
    plan_path, one_path = os.path.join(root, PLAN_FILE), os.path.join(root, ONE_PAGER)
    _write(plan_path, plan_md(g), a.dir, a.force)
    _write(one_path, one_pager_md(g), a.dir, a.force)
    miss = missing(g)
    print("Verdict: %s%s" % (g["r"]["verdict"],
                             ". Board: %s" % g["decision"].get("decision") if g["decision"] else ""))
    print("%s wrote %s and %s" % (ledger.OK, plan_path, one_path))
    if miss:
        print("%s %d part(s) are not in the plan yet; it says so under 'What is not done yet':" % (ledger.BAD, len(miss)))
        for what, how in miss:
            print("  .. %s: %s" % (what, how))
    if a.open:
        ledger.open_page(plan_path, "the plan")
    return verdict.EXIT[g["r"]["verdict"]]


def cmd_idea_template(a):
    path = a.out or os.path.join(ledger.root(a.dir), IDEA_FILE)
    if os.path.isfile(path) and not a.force:
        print("error: %s already exists; pass --force to replace it" % path, file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(IDEA_TEMPLATE)
    print("%s wrote %s. Replace every line with the business's own facts; write 'not known' rather than guess."
          % (ledger.OK, path))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="the verdict, the sections that exist and what is missing")
    c.set_defaults(fn=cmd_check)
    c = sub.add_parser("compile", help="write the business plan and the one-pager")
    c.add_argument("--force", action="store_true", help="overwrite files Proof did not write")
    c.add_argument("--open", action="store_true", help="open the plan when it is written")
    c.set_defaults(fn=cmd_compile)
    c = sub.add_parser("idea-template", help="write an idea file to fill in")
    c.add_argument("--out", help="where to write it (default proof/business/idea.md)")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_idea_template)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: check, compile or idea-template")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
