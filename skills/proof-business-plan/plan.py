#!/usr/bin/env python3
"""Compile a Proof run into the main result: a business plan and a one-pager.

Standard library only, no network. The plan is built from what the other skills
recorded, never from guesses: the computed verdict, the board's decision, the
measured numbers, each business section under proof/business/, and one page on
the engineering. A section nobody produced is listed as not done, with the skill
that produces it; the tool never fills one in.

    python3 plan.py check                 # the verdict, what is there, what is missing
    python3 plan.py compile               # write business-plan.md, one-pager.md and business-plan.pdf
    python3 plan.py compile --open        # and open the plan
    python3 plan.py compile --no-pdf      # the Markdown only
    python3 plan.py pdf                   # print an already compiled plan to PDF
    python3 plan.py idea-template         # write an idea file to fill in

Exit code follows the verdict: 0 Ship, 1 Not yet, 2 Incomplete.
"""

import argparse
import html
import importlib.util
import json
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
            "decision": decision, "directors": load_directors(root, decision), "root": root}


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
    L = ["### Director by director", "",
         "Each director is a sub-agent applying one person's documented thinking to this evidence, not that "
         "person. Every risk names the ledger entry it rests on.", ""]
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
            L += ["| condition | owner | by |", "| --- | --- | --- |"]
            for c in d["conditions"]:
                L.append("| %s | %s | %s |" % (_cell(c.get("action")), _cell(c.get("owner")).upper(),
                                               _cell(c.get("by") or "before launch")))
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
        # The PDF is part of the result. A machine with no browser still gets the HTML,
        # and the verdict, not the printing, decides the exit code.
        make_pdf(a.dir, a.browser, a.force)
    if a.open:
        ledger.open_page(plan_path, "the plan")
    return verdict.EXIT[g["r"]["verdict"]]


# ------------------------------------------------------------------ PDF

PDF_FILE, HTML_FILE = "business-plan.pdf", "business-plan.html"
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
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9pt; }
th, td { border: 1px solid #d0d0d0; padding: 4px 6px; vertical-align: top; text-align: left; word-break: break-word; }
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


def _cover(name, verdict_word, decision, date):
    """The first page: the business, the computed verdict, the board's decision and what is inside."""
    board = ("<div class='call'><strong>Board: %s</strong></div>" % html.escape(decision)) if decision else \
        "<div class='call'>The board has not met.</div>"
    return ("<div class='cover'><div class='name'>%s</div><div class='sub'>Ship or hold: business plan and board "
            "report</div><div class='call'><strong>Verdict: %s</strong></div>%s<div class='note'>%s. Inside: the "
            "half-page summary, what the board decided and each director's answer with the evidence behind every "
            "risk, the key numbers, every section of the plan, the product itself, and what is not done yet. "
            "Simulated buyers and directors are not customers or investors; estimates are labelled where they are "
            "used.</div></div>") % (html.escape(name), html.escape(verdict_word or "not computed"), board,
                                     html.escape(date))


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


def make_pdf(d, browser=None, force=False):
    """Write business-plan.html next to the plan and print it to business-plan.pdf. Returns an exit code."""
    root = ledger.root(d)
    plan_path = os.path.join(root, PLAN_FILE)
    text = _read(plan_path)
    if not text:
        print("error: %s does not exist yet; run plan.py compile first." % plan_path, file=sys.stderr)
        return 2
    m = re.match(r"#\s+(.*)", text)
    html_path, pdf_path = os.path.join(root, HTML_FILE), os.path.join(root, PDF_FILE)
    v = re.search(r"\*\*Verdict: ([^*.]+)", text)
    b = re.search(r"\*\*Board decision: ([^*.]+)", text)
    name = re.sub(r":\s*business plan\s*$", "", m.group(1)) if m else "The business"
    cover = _cover(name, v.group(1).strip() if v else None, b.group(1).strip() if b else None, ledger.now()[:10])
    _write(html_path, plan_html(text, m.group(1) if m else "Business plan", cover).replace(
        "</body>", "<!-- %s --></body>" % MARK), d, force)
    exe = find_browser(browser)
    if not exe:
        print("%s wrote %s. No Chrome, Chromium or Edge was found to print it, so there is no PDF: open the "
              "HTML and print it to PDF, or pass --browser <path> (or set PROOF_BROWSER)." % (ledger.BAD, html_path))
        return 2
    ok, detail = print_pdf(exe, html_path, pdf_path)
    if not ok:
        print("%s wrote %s but could not print it with %s: %s" % (ledger.BAD, html_path, exe, detail))
        return 2
    print("%s wrote %s (%s), from %s" % (ledger.OK, pdf_path, detail, html_path))
    return 0


def cmd_pdf(a):
    return make_pdf(a.dir, a.browser, a.force)


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
    c.add_argument("--pdf", action="store_true", help="kept for older instructions; the PDF is always written now")
    c.add_argument("--no-pdf", action="store_true", help="skip the PDF (and its HTML) this time")
    c.add_argument("--browser", help="the Chrome, Chromium or Edge to print with (default: found automatically)")
    c.set_defaults(fn=cmd_compile)
    c = sub.add_parser("pdf", help="print the compiled plan to proof/business-plan.pdf")
    c.add_argument("--browser", help="the Chrome, Chromium or Edge to print with (default: found automatically)")
    c.add_argument("--force", action="store_true", help="overwrite files Proof did not write")
    c.set_defaults(fn=cmd_pdf)
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
