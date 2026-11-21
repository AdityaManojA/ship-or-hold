#!/usr/bin/env python3
"""Compile a Proof run into the main result: a business plan and a one-pager.

Standard library only, no network. The plan is built from what the other skills
recorded, never from guesses: the computed verdict, the board's decision, the
measured numbers, each business section under proof/business/, and one page on
the engineering. A section nobody produced is listed as not done, with the skill
that produces it; the tool never fills one in.

    python3 plan.py check                 # the verdict, what is there, what is missing
    python3 plan.py compile               # business-plan.md, one-pager.md, report.pdf and appendix.pdf
    python3 plan.py compile --no-pdf      # the Markdown only
    python3 plan.py pdf                   # print report.pdf and appendix.pdf again
    python3 plan.py teaser                # the free teaser: tech score and the one bluntest veto
    python3 plan.py refire --previous old/proof   # what changed since an earlier run
    python3 plan.py idea-template         # write an idea file to fill in

Exit code follows the verdict: 0 Ship, 1 Not yet, 2 Incomplete.
"""

import argparse
import base64
import html
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile

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
    (os.path.join(BUSINESS_DIR, "intake.md"), "The numbers we started from", "proof-intake"),
    (os.path.join(BUSINESS_DIR, "market.md"), "Market and competition", "proof-market"),
    (os.path.join(BUSINESS_DIR, "buyers.md"), "Buyer panel", "proof-buyers"),
    (os.path.join(BUSINESS_DIR, "pricing.md"), "Pricing", "proof-pricing"),
    (os.path.join(BUSINESS_DIR, "offer.md"), "The offer", "proof-offer"),
    (os.path.join(BUSINESS_DIR, "ads.md"), "Ad teardown", "proof-ads"),
    ("finance.md", "The numbers", "proof-finance"),
    (os.path.join(BUSINESS_DIR, "go-to-market.md"), "Go to market", "proof-go-to-market"),
    (os.path.join(BUSINESS_DIR, "ops.md"), "Operations", "proof-ops"),
    (os.path.join(BUSINESS_DIR, "fix-kit.md"), "14-Day Fix Kit", "proof-fixkit"),
]
# Client-facing files the report reads beyond the sections.
DEBATE_FILE = os.path.join("board", "debate.md")
SCREENS_FILE = os.path.join(BUSINESS_DIR, "screens", "annotations.json")
NOTES_FILE = os.path.join(BUSINESS_DIR, "research-notes.md")
REFIRE_FILE = "refire.md"
BRANDING_FILE = "branding.json"
DESCOPED_PREFIX = "Out of scope for this audit"
BUCKETS = [("do_today", "Do today"), ("freelancer", "Hire a freelancer"), ("professional", "Ask a CA or lawyer")]
REPORT_MAX_PAGES = 8
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
    target = r.get("target") or {}
    return {"r": r, "sections": sections, "idea": idea, "idea_from": idea_from, "summary": summary,
            "decision": decision, "directors": load_directors(root, decision), "root": root,
            "debate": _read(os.path.join(root, DEBATE_FILE)), "notes": _read(os.path.join(root, NOTES_FILE)),
            "refire": _read(os.path.join(root, REFIRE_FILE)),
            "screens": _load_json(os.path.join(root, SCREENS_FILE)),
            "branding": _load_json(os.path.join(root, BRANDING_FILE)),
            "stage": (decision or {}).get("stage") or target.get("stage") or "team",
            "descoped": descoped(r)}


def descoped(r):
    """[(skill, check title, reason)] for every check marked out of scope for this audit."""
    out = []
    for c in r["coverage"]:
        for ch in c["checks"]:
            note = ch.get("note") or ""
            if ch["status"] == "not_applicable" and note.startswith(DESCOPED_PREFIX):
                out.append((c["skill"], ch.get("title") or ch["id"], note[len(DESCOPED_PREFIX):].lstrip(": ")))
    return out


SEATS_DOC = os.path.join(HERE, "..", "proof-board", "seats.md")
VOTE_WORDS = {"go": "Go", "go_with_conditions": "Go with conditions", "hold": "Hold"}


def _seats():
    """[(seat id, title)] in the board's own order, from proof-board/seats.md."""
    text = _read(SEATS_DOC) or ""
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S)
    try:
        return [(s["id"], s.get("title") or s["id"].upper()) for s in json.loads(m.group(1))] if m else []
    except (ValueError, KeyError):
        return []


def load_directors(root, decision):
    """Each director's own answer, in seat order, with the team's answers to their questions.

    Only seats the board counted are included: a seat that is not in the decision's
    'answered' list gave no valid answer, and its file is left out rather than shown.
    """
    if not decision:
        return []
    counted = set(decision.get("answered") or [])
    qa = _load_json(os.path.join(root, "board", "qa.json")) or {}
    order = _seats()
    known = [sid for sid, _ in order]
    titles = dict(order)
    ans_dir = os.path.join(root, "board", "answers")
    found = {}
    if os.path.isdir(ans_dir):
        for name in os.listdir(ans_dir):
            if name.endswith(".json"):
                a = _load_json(os.path.join(ans_dir, name))
                if isinstance(a, dict) and a.get("seat") in counted:
                    found[a["seat"]] = a
    out = []
    for sid in known + sorted(set(found) - set(known)):
        a = found.get(sid)
        if not a:
            continue
        team = qa.get(sid) or {}
        questions = []
        for i, q in enumerate(a.get("questions") or []):
            text = q if isinstance(q, str) else (q or {}).get("question", "")
            questions.append((text, team.get(str(i))))
        out.append({"seat": sid, "title": titles.get(sid, sid.upper()), "vote": a.get("vote"),
                    "confidence": a.get("confidence"), "summary": a.get("summary") or "",
                    "risks": a.get("risks") or [], "conditions": a.get("conditions") or [],
                    "questions": questions})
    return out


def _cell(text):
    """Text that is safe inside one Markdown table cell."""
    return re.sub(r"\s+", " ", str(text or "")).replace("|", "/").strip()


def _directors_block(directors):
    """Every director's own answer: the view, the risks with the evidence they cite, conditions, questions."""
    if not directors:
        return []
    L = ["### Seat by seat", "",
         "Each seat is a sub-agent weighing this evidence from one angle. Every risk names the ledger entry it "
         "rests on.", ""]
    for d in directors:
        conf = d["confidence"]
        conf = " (confidence %d%%)" % round(100 * float(conf)) if isinstance(conf, (int, float)) else ""
        L += ["#### %s: %s%s" % (d["title"], VOTE_WORDS.get(d["vote"], d["vote"]), conf), "", d["summary"].strip(), ""]
        if d["risks"]:
            L += ["| severity | risk | evidence |", "| --- | --- | --- |"]
            for x in d["risks"]:
                risk = _cell(x.get("risk"))
                if x.get("quote"):
                    risk += ' Quote: "%s"' % _cell(x["quote"])
                L.append("| %s | %s | %s |" % (_cell(x.get("severity")), risk,
                                               ", ".join("`%s`" % _cell(e) for e in (x.get("evidence") or [])) or "none"))
            L.append("")
        if d["conditions"]:
            L += ["| condition | who | by |", "| --- | --- | --- |"]
            for c in d["conditions"]:
                who = dict(BUCKETS).get(c.get("bucket")) or _cell(c.get("owner")).upper()
                L.append("| %s | %s | %s |" % (_cell(c.get("action")), who, _cell(c.get("by") or "before launch")))
            L.append("")
        if d["questions"]:
            L += ["Questions for the team:", ""]
            for n, (q, ans) in enumerate(d["questions"], 1):
                L.append("%d. %s" % (n, _cell(q)))
                L.append("   Team: %s" % (_cell(ans) if ans else "not answered yet"))
            L.append("")
    return L


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


def _action_line(c):
    bucket = dict(BUCKETS).get(c.get("bucket"))
    who = bucket or "owner %s" % str(c.get("owner") or "team").upper()
    return "- %s (%s, %s)" % (c.get("action"), who, c.get("by") or "before launch")


def _board_block(dec, short=False):
    if not dec:
        return ["The board has not met. Run /proof-board."]
    votes = dec.get("votes") or {}
    L = ["**Board decision: %s.** Votes: %d go, %d go with conditions, %d hold." % (
        dec.get("decision"), len(votes.get("go") or []), len(votes.get("go_with_conditions") or []),
        len(votes.get("hold") or []))]
    if dec.get("panel_name") and dec.get("panel_name") != "Board":
        L[0] = L[0].replace("**Board decision:", "**%s decision:" % dec["panel_name"])
    if dec.get("reasons"):
        L.append("")
        L += ["- %s" % x for x in dec["reasons"][:(2 if short else 6)]]
    conds = dec.get("conditions") or []
    if conds:
        L += ["", "Top actions the board set:" if short else "The actions the board set, most-raised first:", ""]
        for c in conds[:(5 if short else 12)]:
            L.append(_action_line(c))
        if len(conds) > (5 if short else 12):
            L.append("- ... %d more in board/minutes.md" % (len(conds) - (5 if short else 12)))
    if not short:
        L += ["", "The whole meeting is in `board/minutes.md` and plays itself in `board/meeting.html`. The "
              "seats are sub-agents weighing the evidence from four angles; their votes are advice. The decision "
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
    if g.get("descoped"):
        L += ["", "Not covered in this audit, by decision:", ""]
        L += ["- %s, %s: %s" % (sk.replace("proof-", ""), title, why) for sk, title, why in g["descoped"]]
    if g.get("notes"):
        L += ["", _demote(_strip_title(g["notes"]), 2)]
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
    if g["debate"]:
        L += [_demote(_strip_title(g["debate"]), 2), ""]
    L += _directors_block(g["directors"])

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
    if not a.no_pdf:
        # The PDFs are part of the result. A machine with no browser still gets the HTML,
        # and the verdict, not the printing, decides the exit code. An Incomplete run gets
        # no client PDF at all, only the list of what to finish or descope.
        make_pdfs(a.dir, a.browser, a.force, a.draft)
    if a.open:
        ledger.open_page(plan_path, "the plan")
    return verdict.EXIT[g["r"]["verdict"]]


# ------------------------------------------------------------------ PDF

BROWSER_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge",
                 "microsoft-edge"]
BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s\"]+)\)", r'<a href="\2">\1</a>', s)
    return s


def md_to_html(text):
    """Render the Markdown Proof writes (headings, tables, lists, code, rules) to HTML.

    Everything is escaped first; only links to http(s) addresses become links.
    """
    out, para, lines, i = [], [], text.splitlines(), 0

    def flush():
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(para)))
            del para[:]

    while i < len(lines):
        line, s = lines[i], lines[i].strip()
        if s.startswith("```"):
            flush()
            j, block = i + 1, []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                block.append(lines[j])
                j += 1
            out.append("<pre>%s</pre>" % html.escape("\n".join(block)))
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush()
            out.append("<h%d>%s</h%d>" % (len(m.group(1)), _inline(m.group(2)), len(m.group(1))))
            i += 1
            continue
        if s in ("---", "***"):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows = [r for r in rows if not all(re.match(r"^:?-{2,}:?$", c or "-") for c in r)]
            if rows:
                out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                    "".join("<th>%s</th>" % _inline(c) for c in rows[0]),
                    "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r) for r in rows[1:])))
            continue
        if re.match(r"^\s*([-*]|\d+\.)\s+", line):
            flush()
            ordered, items = bool(re.match(r"^\s*\d+\.\s", line)), []
            same = r"^\s*\d+\.\s+" if ordered else r"^\s*[-*]\s+"  # a change of list type starts a new list
            while i < len(lines) and (re.match(same, lines[i]) or
                                      (items and lines[i].startswith("   ") and lines[i].strip())):
                m2 = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", lines[i])
                if m2:
                    items.append([m2.group(2)])
                else:
                    items[-1].append(lines[i].strip())
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join(
                "<li>%s</li>" % "<br>".join(_inline(p) for p in it) for it in items), tag))
            continue
        if not s:
            flush()
        else:
            para.append(s)
        i += 1
    flush()
    return "\n".join(out)


PDF_CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1d1d1f; line-height: 1.45; }
h1 { font-size: 20pt; margin: 0 0 8px; border-bottom: 2px solid #444; padding-bottom: 4px; }
h2 { font-size: 15pt; margin: 22px 0 6px; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 14px 0 4px; page-break-after: avoid; }
h4 { font-size: 11pt; margin: 14px 0 4px; padding-top: 6px; border-top: 1px solid #ddd; page-break-after: avoid; }
h5, h6 { font-size: 10.5pt; margin: 10px 0 4px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9pt; table-layout: fixed; }
th, td { border: 1px solid #d0d0d0; padding: 4px 6px; vertical-align: top; text-align: left;
         overflow-wrap: anywhere; word-break: break-word; hyphens: auto; }
td code { overflow-wrap: anywhere; word-break: break-all; }
.shot { position: relative; margin: 8px 0 4px; border: 1px solid #ccc; page-break-inside: avoid; }
.shot img { display: block; width: 100%; }
.box { position: absolute; border: 3px solid #d0342c; border-radius: 4px; }
.box.medium { border-color: #d98c00; } .box.low { border-color: #2f6fb3; }
.pin { position: absolute; top: -12px; left: -12px; background: #d0342c; color: #fff; font-weight: 700;
       border-radius: 50%; width: 22px; height: 22px; line-height: 22px; text-align: center; font-size: 10pt; }
.box.medium .pin { background: #d98c00; } .box.low .pin { background: #2f6fb3; }
.legend { list-style: none; padding-left: 0; }
.legend li { margin: 3px 0; }
.legend li.good { color: #1f6b43; }
.box.good { border-color: #2e8b57; } .box.good .pin { background: #2e8b57; }
figure { margin: 0 0 10px; page-break-inside: avoid; }
.phones { display: flex; gap: 12px; align-items: flex-start; page-break-inside: avoid; }
.phones figure { flex: 0 0 31%; min-width: 0; } .phones figure.ad { flex: 0 0 46%; }
.phones h3 { font-size: 10.5pt; }
.phones .legend { font-size: 8.5pt; }
.draft { color: #d0342c; font-weight: 700; }
.footer { position: fixed; bottom: -10mm; left: 0; right: 0; text-align: center; font-size: 8pt; color: #888; }
th { background: #f2f2f2; }
tr { page-break-inside: avoid; }
code { font-family: Consolas, Menlo, monospace; font-size: 8.5pt; background: #f5f5f5; padding: 0 2px; }
pre { background: #f5f5f5; padding: 6px; font-size: 8.5pt; white-space: pre-wrap; }
.cover { page-break-after: always; padding-top: 70px; }
.cover .name { font-size: 30pt; font-weight: 600; margin-bottom: 6px; }
.cover .sub { font-size: 14pt; color: #444; margin-bottom: 28px; }
.cover .call { font-size: 15pt; margin: 4px 0; }
.cover .note { color: #555; margin-top: 36px; max-width: 34em; }
"""


def _brand(branding):
    """(cover subtitle, footer text, logo html, accent colour) for this report, white-labelled when asked."""
    b = branding if isinstance(branding, dict) else {}
    colour = b.get("colour") if re.match(r"^#[0-9a-fA-F]{6}$", str(b.get("colour") or "")) else "#444444"
    logo = ""
    if b.get("logo_data"):
        logo = "<img src='%s' style='max-height:60px;margin-bottom:18px' alt=''>" % b["logo_data"]
    if b.get("name"):
        return ("Business plan and board report", "Prepared by %s" % b["name"], logo, colour)
    return ("Ship or hold: business plan and board report", "", logo, colour)


def _cover(name, verdict_word, decision, date, branding=None, inside=None, panel="Board"):
    """The first page: the business, the computed verdict, the board's decision and what is inside."""
    sub, footer, logo, _ = _brand(branding)
    board = ("<div class='call'><strong>%s: %s</strong></div>" % (html.escape(panel), html.escape(decision))) \
        if decision else "<div class='call'>The board has not met.</div>"
    inside = inside or ("the half-page summary, what the board decided and each seat's answer with the evidence "
                        "behind every risk, the key numbers, every section of the plan, the product itself, and "
                        "what is not done yet")
    by = ("<div class='note'>%s</div>" % html.escape(footer)) if footer else ""
    return ("<div class='cover'>%s<div class='name'>%s</div><div class='sub'>%s</div><div class='call'><strong>"
            "Verdict: %s</strong></div>%s<div class='note'>%s. Inside: %s. Simulated buyers and board seats are not "
            "customers or investors; estimates are labelled where they are used.</div>%s</div>") % (
        logo, html.escape(name), html.escape(sub), html.escape(verdict_word or "not computed"), board,
        html.escape(date), html.escape(inside), by)


def plan_html(plan_text, title, cover=""):
    return ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title><style>%s</style></head>"
            "<body>%s%s</body></html>\n") % (html.escape(title), PDF_CSS, cover, md_to_html(plan_text))


def find_browser(given=None):
    """A Chrome, Chromium or Edge that can print to PDF: --browser, $PROOF_BROWSER, PATH, then the usual places."""
    for cand in (given, os.environ.get("PROOF_BROWSER")):
        if cand:
            return cand if os.path.isfile(cand) else shutil.which(cand)
    for name in BROWSER_NAMES:
        hit = shutil.which(name)
        if hit:
            return hit
    for path in BROWSER_PATHS:
        if os.path.isfile(path):
            return path
    return None


def print_pdf(browser, html_path, pdf_path, timeout=120):
    """Print a local HTML file to PDF with a headless browser, in a throwaway profile. Returns (ok, detail)."""
    profile = tempfile.mkdtemp(prefix="proof-pdf-")
    url = "file:///" + os.path.abspath(html_path).replace(os.sep, "/").lstrip("/")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
           "--user-data-dir=%s" % profile, "--print-to-pdf=%s" % os.path.abspath(pdf_path), url]
    try:
        if os.path.isfile(pdf_path):
            os.remove(pdf_path)
        subprocess.run(cmd, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
        return False, "the browser ran but wrote no PDF"
    return True, "%d bytes" % os.path.getsize(pdf_path)


LEDGER_REF = re.compile(r"`?\[?\b(proof-[a-z0-9-]+/[^\s`\]\),;]+|[a-z0-9_-]+\.json)\]?`?")


def _client_clean(text):
    """Text for client pages: no ledger codes, no evidence file names, no 'Sources' tail."""
    text = re.split(r"\n#+\s*Sources\s*\n", text)[0]
    text = re.sub(r"\s*\[(?:proof-[a-z0-9-]+/[^\]]+|product|verdict)\]", "", text)
    text = LEDGER_REF.sub("", text)
    text = re.sub(r"\(\s*[,;]?\s*\)", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _data_uri(path):
    kind = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as fh:
        return "data:%s;base64,%s" % (kind, base64.b64encode(fh.read()).decode("ascii"))


def load_branding(root):
    b = _load_json(os.path.join(root, BRANDING_FILE))
    if not isinstance(b, dict):
        return None
    logo = b.get("logo")
    if logo and os.path.isfile(os.path.join(root, logo)):
        b = dict(b, logo_data=_data_uri(os.path.join(root, logo)))
    return b


NOTE_SEVERITIES = ("high", "medium", "low", "good")
DEVICES = ("desktop", "phone", "ad")  # an ad screenshot is narrow and sits beside its landing page
SCREENS_MAX = 10           # more than this and the section stops being a quick read
SCREEN_PAGES = 2           # pages the screenshots may add to the report's page target
PIN_SEVERITIES = ("critical", "high", "medium")  # verified findings that belong on a screenshot when visible
# Skills whose findings a reader can see on a page; the rest (money, buyers, operations) have nothing to point at.
SCREEN_SKILLS = ("proof-a11y", "proof-seo", "proof-privacy", "proof-offer", "proof-ads", "proof-web-e2e",
                 "proof-compat", "proof-visual", "proof-design-match", "proof-slop", "proof-explore",
                 "proof-mobile-a11y", "proof-mobile-e2e", "proof-mobile-explore", "proof-pwa", "proof-i18n")


def screen_notes(shot, i, refs=None):
    """The valid notes of one screenshot, and the problems with the rest. `refs` is the set of finding
    references ("skill/id") a note may point at; None skips that check."""
    notes, problems = [], []
    for n, note in enumerate(shot.get("notes") or [], 1):
        where = "screenshot %d (%s) note %d" % (i + 1, shot.get("image"), n)
        try:
            x, y, w, h = (float(note[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            problems.append("%s: x, y, w and h are percentages of the image" % where)
            continue
        if not (0 <= x < 100 and 0 <= y < 100 and 0 < w <= 100 and 0 < h <= 100) or x + w > 100.5 or y + h > 100.5:
            problems.append("%s: box outside the image" % where)
            continue
        sev = note.get("severity", "high")
        if sev not in NOTE_SEVERITIES:
            problems.append("%s: severity must be one of %s" % (where, ", ".join(NOTE_SEVERITIES)))
            continue
        label = " ".join(str(note.get("label") or "").split())
        if not label:
            problems.append("%s: label it in plain words" % where)
            continue
        ref = note.get("ref")
        if ref and refs is not None and ref not in refs:
            problems.append("%s: ref %r is not a finding in the ledger" % (where, ref))
            continue
        notes.append({"x": x, "y": y, "w": w, "h": h, "severity": sev, "label": label, "ref": ref})
    if not notes and not problems:
        problems.append("screenshot %d (%s): no notes; say what the reader should see" % (i + 1, shot.get("image")))
    return notes, problems


def screens_html(root, screens, refs=None):
    """Screenshots of the store with numbered boxes over what the findings point at, and a legend under each.
    Phone screenshots sit side by side; a "good" note marks what works, in green."""
    if not isinstance(screens, list):
        return "", []
    blocks, problems = [], []
    if len(screens) > SCREENS_MAX:
        problems.append("%d screenshots: keep the %d that show the most" % (len(screens), SCREENS_MAX))
    for i, shot in enumerate(screens[:SCREENS_MAX]):
        img = os.path.join(root, BUSINESS_DIR, "screens", str(shot.get("image") or ""))
        if not shot.get("image") or not os.path.isfile(img):
            problems.append("screenshot %d: image %r not found under business/screens/" % (i + 1, shot.get("image")))
            continue
        device = shot.get("device", "desktop")
        if device not in DEVICES:
            problems.append("screenshot %d: device must be desktop or phone" % (i + 1))
            device = "desktop"
        notes, bad = screen_notes(shot, i, refs)
        problems += bad
        boxes, legend = [], []
        for n, note in enumerate(notes, 1):
            boxes.append("<div class='box %s' style='left:%.2f%%;top:%.2f%%;width:%.2f%%;height:%.2f%%'>"
                         "<span class='pin'>%d</span></div>" % (note["severity"], note["x"], note["y"], note["w"],
                                                                note["h"], n))
            lead = "Works: " if note["severity"] == "good" else ""
            legend.append("<li class='%s'><strong>%d.</strong> %s%s</li>" % (note["severity"], n, lead,
                                                                             _inline(note["label"])))
        blocks.append((device, "<figure class='%s'><h3>%s</h3><div class='shot'><img src='%s' alt=''>%s</div>"
                               "<ol class='legend'>%s</ol></figure>" % (
                                   device, _inline(str(shot.get("title") or "Your store")), _data_uri(img),
                                   "".join(boxes), "".join(legend))))
    out, row = [], []
    for device, block in blocks:  # consecutive phone screenshots share a row
        if device in ("phone", "ad"):
            row.append(block)
            if len(row) == 3:
                out.append("<div class='phones'>%s</div>" % "".join(row))
                row = []
            continue
        if row:
            out.append("<div class='phones'>%s</div>" % "".join(row))
            row = []
        out.append(block)
    if row:
        out.append("<div class='phones'>%s</div>" % "".join(row))
    return "\n".join(out), problems


def finding_refs(d):
    """Every finding reference a screenshot note may use, and the verified ones that should be pinned."""
    refs, pin = set(), []
    for f in ledger.load_findings(d):
        ref = "%s/%s" % (f["skill"], f["id"])
        refs.add(ref)
        if f.get("status") == "verified" and f.get("severity") in PIN_SEVERITIES and f["skill"] in SCREEN_SKILLS:
            pin.append((ref, f["severity"], f.get("title", "")))
    return refs, pin


def screens_report(d, screens):
    """Problems with the annotations, and the verified high and medium findings no screenshot points at."""
    refs, pin = finding_refs(d)
    _, problems = screens_html(ledger.root(d), screens, refs)
    pinned = {n.get("ref") for s in (screens if isinstance(screens, list) else [])
              for n in (s.get("notes") or []) if isinstance(n, dict)}
    return problems, [p for p in pin if p[0] not in pinned]


MEASURE_JS = r"""// Paste into the page's console (or a javascript tool) at the size you screenshot, scrolled
// to where the screenshot starts. Each pin is [CSS selector, severity, label, ref]; ref is optional.
// It prints the notes for annotations.json, measured from the page, never guessed.
((pins) => {
  const W = innerWidth, H = innerHeight, r1 = (v) => Math.round(v * 10) / 10;
  return JSON.stringify(pins.map(([sel, severity, label, ref]) => {
    const el = document.querySelector(sel);
    if (!el) return {missing: sel};
    const b = el.getBoundingClientRect();
    const x = Math.max(0, b.left), y = Math.max(0, b.top);
    if (y >= H || b.bottom <= 0) return {offscreen: sel};
    const note = {x: r1(x / W * 100), y: r1(y / H * 100), w: r1((Math.min(b.right, W) - x) / W * 100),
                  h: r1((Math.min(b.bottom, H) - y) / H * 100), severity, label};
    if (ref) note.ref = ref;
    return note;
  }), null, 1);
})([
  ["h1, .product__title", "medium", "The product name is faint and is not the page's main heading", "proof-seo/example-id"],
  ["form[action*='cart'] button[type=submit]", "good", "Add to cart is clear and high in the page"],
]);
"""


def report_parts(g):
    """The client report, in order: summary, debate, screenshots, the 14-day kit, a re-fire when there is one."""
    r, root = g["r"], g["root"]
    parts = []
    parts.append(("Executive summary", md_to_html(_client_clean(_demote(_strip_title(g["summary"] or ""), 1)))
                  if g["summary"] else "<p>The summary is not written yet.</p>"))
    if g["debate"]:
        debate = md_to_html(_client_clean(_demote(_strip_title(g["debate"]), 1)))
    elif g["decision"]:
        lines = _board_block(g["decision"], short=True)
        for d in g["directors"]:
            first = re.split(r"(?<=[.!?])\s", d["summary"].strip(), 1)[0]
            lines.append("- **%s, %s:** %s" % (d["title"], VOTE_WORDS.get(d["vote"], d["vote"]).lower(), first))
        debate = md_to_html(_client_clean("\n".join(lines)))
    else:
        debate = "<p>The board has not met.</p>"
    name = (g["decision"] or {}).get("panel_name") or ("Advisory Panel" if g["stage"] == "solo" else "Board")
    parts.append(("%s debate" % ("Advisory Panel" if name == "Advisory Panel" else "Boardroom"), debate))
    shots, _ = screens_html(root, g["screens"])
    if shots:
        parts.append(("What we saw on your store", shots))
    kit = next((s["text"] for s in g["sections"] if s["skill"] == "proof-fixkit"), None)
    parts.append(("14-Day Fix Kit", md_to_html(_client_clean(_demote(_strip_title(kit), 1))) if kit else
                  "<p>The Fix Kit has not been made yet. Run /proof-fixkit.</p>"))
    if g["refire"]:
        parts.append(("Re-Fire: what changed", md_to_html(_client_clean(_demote(_strip_title(g["refire"]), 1)))))
    return parts


def report_html(g, draft=False):
    r = g["r"]
    name = (r["target"] or {}).get("name") or "The business"
    dec = g["decision"] or {}
    panel = dec.get("panel_name") or ("Advisory Panel" if g["stage"] == "solo" else "Board")
    branding = g["branding"]
    _, footer, _, colour = _brand(branding)
    cover = _cover(name, r["verdict"], dec.get("decision"), ledger.now()[:10], branding, panel=panel,
                   inside="the executive summary, the %s debate, what we saw on your store, and the 14-day fix "
                          "kit; the evidence behind every point is in the appendix" % panel.lower())
    body = [cover]
    if draft:
        body.append("<p class='draft'>DRAFT: the verdict is %s. Not for the client until every check is finished "
                    "or descoped.</p>" % html.escape(r["verdict"]))
    for title, inner in report_parts(g):
        cls = " class='kit'" if title == "14-Day Fix Kit" else ""
        body.append("<section%s><h1>%s</h1>%s</section>" % (cls, html.escape(title), inner))
    if footer:
        body.append("<div class='footer'>%s</div>" % html.escape(footer))
    css = PDF_CSS.replace("border-bottom: 2px solid #444", "border-bottom: 2px solid %s" % colour)
    return ("<!doctype html><html><head><meta charset='utf-8'><title>%s: report</title><style>%s"
            "section { page-break-before: always; } section:first-of-type { page-break-before: auto; }"
            ".kit table { table-layout: auto; } .kit td:nth-child(3) { width: 48%%; } .kit td { padding: 3px 5px; }"
            ".shot { max-width: 86%%; }</style>"
            "</head><body>%s<!-- %s --></body></html>\n") % (html.escape(name), css, "\n".join(body), MARK)


def appendix_html(g, plan_text):
    r = g["r"]
    name = (r["target"] or {}).get("name") or "The business"
    _, footer, _, colour = _brand(g["branding"])
    text = plan_text
    if footer:  # white-label: the appendix does not name the tool that made it
        text = re.sub(r"\n---\n\n_%s.*$" % re.escape(MARK), "\n", text, flags=re.S)
    cover = _cover(name, r["verdict"], (g["decision"] or {}).get("decision"), ledger.now()[:10], g["branding"],
                   inside="the full plan, every seat's answer with the ledger entries behind each risk, every "
                          "section in full, checks not covered and the research notes")
    css = PDF_CSS.replace("border-bottom: 2px solid #444", "border-bottom: 2px solid %s" % colour)
    foot = ("<div class='footer'>%s</div>" % html.escape(footer)) if footer else ""
    return ("<!doctype html><html><head><meta charset='utf-8'><title>%s: appendix</title><style>%s</style></head>"
            "<body>%s%s%s<!-- %s --></body></html>\n") % (html.escape(name), css, cover, md_to_html(text), foot, MARK)


def pdf_pages(path):
    """How many pages a PDF has, counted from its page objects."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return 0
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))


def render_pdf(d, stem, page, browser=None, force=False):
    """Write <stem>.html under the proof folder and print it to <stem>.pdf. Returns (ok, message, pages)."""
    root = ledger.root(d)
    html_path, pdf_path = os.path.join(root, stem + ".html"), os.path.join(root, stem + ".pdf")
    _write(html_path, page, d, force)
    exe = find_browser(browser)
    if not exe:
        return False, ("wrote %s. No Chrome, Chromium or Edge was found to print it, so there is no PDF: open the "
                       "HTML and print it to PDF, or pass --browser <path> (or set PROOF_BROWSER)." % html_path), 0
    ok, detail = print_pdf(exe, html_path, pdf_path)
    if not ok:
        return False, "wrote %s but could not print it with %s: %s" % (html_path, exe, detail), 0
    return True, "wrote %s (%s)" % (pdf_path, detail), pdf_pages(pdf_path)


def make_pdfs(d, browser=None, force=False, draft=False, appendix=True):
    """The client report and the appendix as PDFs. Refuses an Incomplete client report unless it is a draft.

    Returns 0 when everything asked for was printed, 2 otherwise.
    """
    g = gather(d)
    g["branding"] = load_branding(g["root"])
    plan_text = _read(os.path.join(g["root"], PLAN_FILE))
    if not plan_text:
        print("error: %s does not exist yet; run plan.py compile first." % os.path.join(g["root"], PLAN_FILE),
              file=sys.stderr)
        return 2
    if g["r"]["verdict"] == verdict.INCOMPLETE and not draft:
        print("%s no client PDF: the verdict is Incomplete, and a client report never leads with that. Finish "
              "each item below, or descope it with check.py descope <skill> <check> --reason \"...\" when it "
              "does not apply to this client:" % ledger.BAD)
        for gap in g["r"]["gaps"]:
            print("  .. %s" % gap)
        return 2
    problems, unpinned = screens_report(d, g["screens"])
    for p in problems:
        print("%s %s" % (ledger.BAD, p))
    if unpinned:
        print("%s %d verified high or medium finding(s) are not on a screenshot; pin the ones a reader can see "
              "(plan.py screens lists them)" % (ledger.BAD, len(unpinned)))
    code = 0
    ok, msg, pages = render_pdf(d, "report", report_html(g, draft), browser, force)
    print("%s %s" % (ledger.OK if ok else ledger.BAD, msg))
    if ok:
        target = REPORT_MAX_PAGES + (SCREEN_PAGES if g["screens"] else 0)
        flag = "" if pages <= target else ": over the %d-page target, trim the summary or the kit" % target
        print("%s report.pdf is %d page(s)%s" % (ledger.OK if not flag else ledger.BAD, pages, flag))
    else:
        code = 2
    if appendix:
        ok2, msg2, _ = render_pdf(d, "appendix", appendix_html(g, plan_text), browser, force)
        print("%s %s" % (ledger.OK if ok2 else ledger.BAD, msg2))
        code = code or (0 if ok2 else 2)
    return code


def cmd_screens(a):
    """Check the annotated screenshots and list the findings a screenshot should still point at."""
    root = ledger.root(a.dir)
    screens = _load_json(os.path.join(root, SCREENS_FILE))
    if screens is None:
        print("%s no %s yet. Save screenshots under %s and describe them there; plan.py screens-js prints "
              "a snippet that measures the boxes." % (ledger.BAD, SCREENS_FILE, os.path.join(BUSINESS_DIR, "screens")))
        return 2
    if not isinstance(screens, list):
        print("%s %s must be a list of screenshots" % (ledger.BAD, SCREENS_FILE))
        return 2
    problems, unpinned = screens_report(a.dir, screens)
    notes = [n for s_ in screens for n in (s_.get("notes") or []) if isinstance(n, dict)]
    by = {k: sum(1 for n in notes if n.get("severity", "high") == k) for k in NOTE_SEVERITIES}
    phones = sum(1 for s_ in screens if s_.get("device") == "phone")
    print("%d screenshot(s), %d on a phone; %d note(s): %s" % (
        len(screens), phones, len(notes), ", ".join("%d %s" % (v, k) for k, v in by.items() if v) or "none"))
    for p in problems:
        print("%s %s" % (ledger.BAD, p))
    if unpinned:
        print("Verified findings no screenshot points at (pin each one a reader can see, with its ref):")
        for ref, sev, title in unpinned:
            print("  .. [%s] %s: %s" % (sev, ref, title))
    if not phones:
        print("%s no phone screenshot: most shoppers arrive on a phone; add one at 390x844" % ledger.BAD)
    if not by["good"]:
        print("%s no 'good' note: mark one or two things that work, so the page is fair" % ledger.BAD)
    if not problems:
        print("%s the annotations are valid" % ledger.OK)
    return 2 if problems else 0


def cmd_screens_js(a):
    print(MEASURE_JS)
    return 0


def cmd_pdf(a):
    return make_pdfs(a.dir, a.browser, a.force, a.draft)


# ------------------------------------------------------------------ teaser and re-fire

TEASER_FILE = "teaser.md"
NOT_TECH = set(BUSINESS_SKILLS) | {"proof-release", SKILL, "proof-board"}
SEV_POINTS = {"critical": 25, "high": 15, "medium": 5, "low": 1, "info": 0}


def tech_score(r):
    """0 to 100 from the technical checks alone: 100 less points for each verified finding and missed budget."""
    skills = sorted({c["skill"] for c in r["coverage"]} - NOT_TECH)
    if not skills:
        return None, skills
    score = 100
    for f in r["findings"]:
        if f["skill"] in skills and f["status"] == "verified":
            score -= SEV_POINTS.get(f["severity"], 0)
    for s_ in r["scores"]:
        if s_["skill"] in skills:
            score -= 5 * sum(1 for m in s_["metrics"] if not ledger.metric_passes(m))
    return max(0, score), skills


def blunt_veto(g):
    """The one sentence a free teaser leads with: a veto, else the first hold reason, else the worst blocker."""
    dec = g["decision"] or {}
    for v in dec.get("vetoes") or []:
        return "%s veto: %s." % (str(v.get("seat", "")).capitalize(), _client_clean(str(v.get("why", ""))).strip())
    for x in dec.get("reasons") or []:
        if dec.get("decision") == "Hold":
            return _client_clean(str(x)).strip()
    blockers = sorted(g["r"]["blockers"], key=lambda b: SEV_ORDER.get(b.get("severity", "low"), 9))
    if blockers:
        b = blockers[0]
        return "What would stop a board: %s." % (b.get("title") or "%s is %s, the bar is %s %s" % (
            b["id"].replace("_", " "), _fmt(b.get("value")), b.get("op"), _fmt(b.get("budget"))))
    return "Nothing verified blocks a launch on the checks run so far."


def teaser_md(g):
    r = g["r"]
    name = (r["target"] or {}).get("name") or "The business"
    score, skills = tech_score(r)
    L = ["# %s: free check" % name, ""]
    if score is None:
        L += ["No technical check has run yet, so there is no tech score.", ""]
    else:
        L += ["**Tech score: %d / 100**, from %d automated check(s): %s." % (
            score, len(skills), ", ".join(sk.replace("proof-", "") for sk in skills)), ""]
    L += ["**The one thing:** %s" % blunt_veto(g), ""]
    top = _top_findings(r, set(skills), 3)
    if top:
        L += ["The worst of what the checks found:", ""] + ["- %s (%s)" % (f["title"], f["severity"]) for f in top]
        L.append("")
    L += ["The full audit adds the numbers from your own store, a board debate, your ads, and a 14-day fix kit "
          "you can paste in.", ""]
    return "\n".join(L)


def cmd_teaser(a):
    g = gather(a.dir)
    if not os.path.isdir(g["root"]):
        print("error: no %s folder. Run /proof-plan first." % a.dir, file=sys.stderr)
        return 2
    g["branding"] = load_branding(g["root"])
    text = teaser_md(g)
    _write(os.path.join(g["root"], TEASER_FILE), text + "\n_%s_\n" % MARK, a.dir, a.force)
    print("%s wrote %s" % (ledger.OK, os.path.join(g["root"], TEASER_FILE)))
    if a.no_pdf:
        return 0
    page = ("<!doctype html><html><head><meta charset='utf-8'><title>Free check</title><style>%s</style></head>"
            "<body>%s<!-- %s --></body></html>\n") % (PDF_CSS, md_to_html(text), MARK)
    ok, msg, _ = render_pdf(a.dir, "teaser", page, a.browser, a.force)
    print("%s %s" % (ledger.OK if ok else ledger.BAD, msg))
    return 0 if ok else 2


def _verified(d):
    return {(f["skill"], f["id"]): f for f in ledger.load_findings(d) if f["status"] == "verified"}


def refire_md(previous, current):
    """What changed between two runs: fixed, still open, new, and how the verdict and votes moved."""
    old, new = _verified(previous), _verified(current)
    old_title = {(f["skill"], f["title"]): k for k, f in old.items()}
    fixed, still, fresh = [], [], []
    for k, f in old.items():
        if k in new or (f["skill"], f["title"]) in {(x["skill"], x["title"]) for x in new.values()}:
            still.append(f)
        else:
            fixed.append(f)
    for k, f in new.items():
        if k not in old and (f["skill"], f["title"]) not in old_title:
            fresh.append(f)
    v_old, v_new = verdict.compute(previous)["verdict"], verdict.compute(current)["verdict"]
    d_old = _load_json(os.path.join(ledger.root(previous), "board", "decision.json")) or {}
    d_new = _load_json(os.path.join(ledger.root(current), "board", "decision.json")) or {}
    key = lambda f: (SEV_ORDER.get(f["severity"], 9), f["title"])
    L = ["# Re-Fire: what changed", "",
         "| | before | now |", "| --- | --- | --- |",
         "| Verdict | %s | %s |" % (v_old, v_new),
         "| Board | %s | %s |" % (d_old.get("decision") or "not met", d_new.get("decision") or "not met"),
         "| Verified findings | %d | %d |" % (len(old), len(new)), "",
         "**Fixed: %d. Still open: %d. New: %d.**" % (len(fixed), len(still), len(fresh)), ""]
    for title, rows in (("Fixed", fixed), ("Still open", still), ("New since last time", fresh)):
        if rows:
            L += ["## %s" % title, ""] + ["- %s (%s)" % (f["title"], f["severity"]) for f in sorted(rows, key=key)] + [""]
    return "\n".join(L), {"fixed": len(fixed), "still": len(still), "new": len(fresh)}


def cmd_refire(a):
    if not os.path.isdir(ledger.root(a.previous)):
        print("error: no previous run at %s" % a.previous, file=sys.stderr)
        return 2
    text, counts = refire_md(a.previous, a.dir)
    _write(os.path.join(ledger.root(a.dir), REFIRE_FILE), text + "\n_%s_\n" % MARK, a.dir, a.force)
    print("%s wrote %s: %d fixed, %d still open, %d new. Compile again to put it in the report." % (
        ledger.OK, os.path.join(ledger.root(a.dir), REFIRE_FILE), counts["fixed"], counts["still"], counts["new"]))
    return 0


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
    c = sub.add_parser("compile", help="write the business plan, the one-pager and the plan as a PDF")
    c.add_argument("--force", action="store_true", help="overwrite files Proof did not write")
    c.add_argument("--open", action="store_true", help="open the plan when it is written")
    c.add_argument("--pdf", action="store_true", help="kept for older instructions; the PDFs are always written now")
    c.add_argument("--no-pdf", action="store_true", help="skip the PDFs (and their HTML) this time")
    c.add_argument("--draft", action="store_true", help="print the report even when Incomplete, marked DRAFT")
    c.add_argument("--browser", help="the Chrome, Chromium or Edge to print with (default: found automatically)")
    c.set_defaults(fn=cmd_compile)
    c = sub.add_parser("pdf", help="print report.pdf (the client report) and appendix.pdf from the compiled plan")
    c.add_argument("--browser", help="the Chrome, Chromium or Edge to print with (default: found automatically)")
    c.add_argument("--force", action="store_true", help="overwrite files Proof did not write")
    c.add_argument("--draft", action="store_true", help="print the report even when Incomplete, marked DRAFT")
    c.set_defaults(fn=cmd_pdf)
    c = sub.add_parser("teaser", help="the free teaser: the automated tech score and the one bluntest veto")
    c.add_argument("--browser", help="the Chrome, Chromium or Edge to print with")
    c.add_argument("--force", action="store_true")
    c.add_argument("--no-pdf", action="store_true")
    c.set_defaults(fn=cmd_teaser)
    c = sub.add_parser("refire", help="compare this run with an earlier one: fixed, still open, new")
    c.add_argument("--previous", required=True, help="the earlier run's proof folder")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_refire)
    c = sub.add_parser("screens", help="check the annotated screenshots; list findings no screenshot shows")
    c.set_defaults(fn=cmd_screens)
    c = sub.add_parser("screens-js", help="print a browser snippet that measures annotation boxes on the page")
    c.set_defaults(fn=cmd_screens_js)
    c = sub.add_parser("idea-template", help="write an idea file to fill in")
    c.add_argument("--out", help="where to write it (default proof/business/idea.md)")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_idea_template)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.error("give a command: check, compile, pdf, teaser, refire, screens, screens-js or idea-template")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
