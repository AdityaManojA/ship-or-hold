#!/usr/bin/env python3
"""Present the project to a board of directors and compute the board's decision.

Standard library only, no network. The board sits on top of the Proof evidence:

    brief    turn the ledger into a presentation (the pitch the board hears) and one
             brief per director (CEO, CTO, CISO, CFO, CMO, Sales, COO, CPO, Legal)
    check    validate the directors' answers; every serious risk must cite evidence
    save     file one director's answer from stdin
    answer   record the presenter's answer to a director's question
    tally    compute the decision (Go, Go with conditions, Hold) and write the
             minutes and an interactive meeting page (proof/board/meeting.html)
    meeting  rebuild the meeting page only
    seats    list the board
    status   who has answered

The decision is computed, never written by hand: an incomplete evidence ledger or a
veto that the ledger itself triggers holds the launch whatever the votes say; the
votes decide the rest.

    python3 board.py brief
    python3 board.py check
    python3 board.py tally
"""

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

_spec = importlib.util.spec_from_file_location("proof_verdict_board", os.path.join(HERE, "..", "proof-report", "verdict.py"))
verdict = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verdict)

SEATS_DOC = os.path.join(HERE, "seats.md")
RELEASE_CHECKLIST = os.path.join(HERE, "..", "proof-release", "checklist.md")
VOTES = ["go", "go_with_conditions", "hold"]
VOTE_LABEL = {"go": "Go", "go_with_conditions": "Go with conditions", "hold": "Hold"}
GO, GO_COND, HOLD = "Go", "Go with conditions", "Hold"
SEV = ["high", "medium", "low"]
REF = re.compile(r"\[([a-z0-9-]+/[^\]\s]+)\]")


class BoardError(ValueError):
    pass


# ------------------------------------------------------------------- seats

def load_seats():
    with open(SEATS_DOC, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S)
    if not m:
        raise BoardError("seats.md has no JSON seat list")
    seats = json.loads(m.group(1))
    for s in seats:
        sec = re.search(r"^## %s\s*\n(.*?)(?=^## |\Z)" % re.escape(s["id"]), text, re.S | re.M)
        s["lens"] = " ".join(sec.group(1).split()) if sec else ""
    return seats


def _pick(seats, only):
    if not only:
        return seats
    ids = [x.strip() for x in only.split(",") if x.strip()]
    unknown = [i for i in ids if i not in {s["id"] for s in seats}]
    if unknown:
        raise BoardError("unknown seat(s): %s" % ", ".join(unknown))
    return [s for s in seats if s["id"] in ids]


# ------------------------------------------------------------- evidence

def _board_dir(d):
    return os.path.join(ledger.root(d), "board")


def evidence(d):
    """Everything the board sees, from the ledger, plus the set of citable refs."""
    r = verdict.compute(d)
    refs = {"verdict"}
    for f in r["findings"]:
        refs.add("%s/%s" % (f["skill"], f["id"]))
    for s in r["scores"]:
        for m in s["metrics"]:
            refs.add("%s/score:%s" % (s["skill"], m["id"]))
    for c in r["coverage"]:
        for ch in c["checks"]:
            refs.add("%s/coverage:%s" % (c["skill"], ch["id"]))
    return r, refs


def fingerprint(r):
    key = {"v": r["verdict"],
           "f": sorted((f["skill"], f["id"], f["status"], f["severity"]) for f in r["findings"]),
           "s": sorted((s["skill"], m["id"], m["value"]) for s in r["scores"] for m in s["metrics"]),
           "c": sorted((c["skill"], ch["id"], ch["status"]) for c in r["coverage"] for ch in c["checks"])}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _release_critical_ids():
    try:
        with open(RELEASE_CHECKLIST, encoding="utf-8") as fh:
            m = re.search(r"```json\s*(\[.*?\])\s*```", fh.read(), re.S)
        return {i["id"] for i in json.loads(m.group(1)) if i.get("critical")} if m else set()
    except (OSError, ValueError):
        return set()


def vetoes(r, seats):
    """Vetoes the ledger itself triggers, per seat, independent of any vote."""
    out = []
    for seat in seats:
        domain = set(seat.get("veto") or [])
        if not domain:
            continue
        for f in r["findings"]:
            if f["skill"] in domain and f["status"] == "verified" and f["severity"] in ("critical", "high"):
                out.append({"seat": seat["id"], "ref": "%s/%s" % (f["skill"], f["id"]),
                            "why": "verified %s finding: %s" % (f["severity"], f["title"])})
        if seat["id"] == "cfo":
            for s in r["scores"]:
                if s["skill"] == "proof-finance":
                    for m in s["metrics"]:
                        if not ledger.metric_passes(m):
                            out.append({"seat": "cfo", "ref": "proof-finance/score:%s" % m["id"],
                                        "why": "%s is %s, budget %s %s" % (m["id"], m["value"], m["op"], m["budget"])})
        if seat["id"] == "coo":
            crit = _release_critical_ids()
            for c in r["coverage"]:
                if c["skill"] == "proof-release":
                    for ch in c["checks"]:
                        if ch["id"] in crit and ch["status"] == "not_run":
                            out.append({"seat": "coo", "ref": "proof-release/coverage:%s" % ch["id"],
                                        "why": "launch-blocking readiness item not done: %s" % ch["title"]})
    return out


# ---------------------------------------------------------- presentation

def _skill_lines(r, skills):
    L = []
    by_skill = {}
    for f in r["findings"]:
        by_skill.setdefault(f["skill"], []).append(f)
    names = sorted(set([f["skill"] for f in r["findings"]] + [c["skill"] for c in r["coverage"]] +
                       [s["skill"] for s in r["scores"]]))
    for sk in names:
        if skills != ["*"] and sk not in skills:
            continue
        cov = next((c for c in r["coverage"] if c["skill"] == sk), None)
        counts = ""
        if cov:
            n = {st: sum(1 for c in cov["checks"] if c["status"] == st) for st in ledger.COVERAGE_STATUSES}
            counts = " (%d checks covered, %d not applicable, %d not run)" % (n["covered"], n["not_applicable"], n["not_run"])
        tag = "required" if sk in r["required"] else "optional"
        L.append("### %s, %s%s" % (sk, tag, counts))
        fs = [f for f in by_skill.get(sk, []) if f["status"] == "verified"]
        for f in sorted(fs, key=lambda f: verdict.RANK[f["severity"]]):
            L.append("- [%s/%s] %s: %s%s" % (sk, f["id"], f["severity"].upper(), f["title"],
                                             (" Fix: " + f["fix"]) if f.get("fix") else ""))
        unv = [f for f in by_skill.get(sk, []) if f["status"] == "unverified"]
        if unv:
            L.append("- %d unverified lead(s), not counted: %s" % (len(unv), "; ".join(f["title"] for f in unv[:5])))
        sc = next((s for s in r["scores"] if s["skill"] == sk), None)
        for m in (sc["metrics"] if sc else []):
            L.append("- [%s/score:%s] %s %s against budget %s %s: %s" % (
                sk, m["id"], verdict._n(m["value"]), m["unit"], m["op"], verdict._n(m["budget"]),
                "within budget" if ledger.metric_passes(m) else "OVER BUDGET"))
        if cov:
            notrun = [c for c in cov["checks"] if c["status"] == "not_run"]
            for c in notrun[:10]:
                L.append("- [%s/coverage:%s] not yet checked: %s" % (sk, c["id"], c["title"]))
        if not fs and not unv and not (sc and sc["metrics"]):
            L.append("- no verified findings")
        L.append("")
    return L


def presentation(r, seats_vetoes):
    t = r.get("target") or {}
    sev = {s: sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] == s) for s in ledger.SEVERITIES}
    L = ["# %s: presentation to the board" % (t.get("name") or "The product"), ""]
    L.append("## The product")
    L.append("- Kind: %s" % t.get("kind", "unknown"))
    if t.get("urls"):
        L.append("- Where: %s" % ", ".join(t["urls"]))
    if t.get("done_means"):
        L.append("- The team's own bar for done: %s" % t["done_means"])
    L.append("")
    L.append("## The headline")
    L.append("- [verdict] The computed Proof verdict is **%s**." % r["verdict"])
    L.append("- Verified findings: %d critical, %d high, %d medium, %d low." % (
        sev["critical"], sev["high"], sev["medium"], sev["low"]))
    for c in r["checks"]:
        L.append("- %s %s" % ("OK" if c["ok"] else "PROBLEM:", c["text"]))
    if seats_vetoes:
        L.append("- The evidence already triggers %d veto(es) on this board:" % len(seats_vetoes))
        for v in seats_vetoes:
            L.append("  - %s: [%s] %s" % (v["seat"].upper(), v["ref"], v["why"]))
    L.append("")
    L.append("## What was tested, and what it found")
    L.append("")
    L.extend(_skill_lines(r, ["*"]))
    L.append("## The ask")
    L.append("Decide whether this ships now (Go), ships once named conditions are met (Go with conditions), "
             "or does not ship yet (Hold).")
    return "\n".join(L) + "\n"


SCHEMA = """{
  "seat": "%(seat)s",
  "vote": "go" or "go_with_conditions" or "hold",
  "confidence": a number from 0 to 1,
  "summary": "two or three sentences, in your own voice as %(short)s, as you would say it in the room",
  "risks": [
    {"risk": "what could go wrong", "severity": "high" or "medium" or "low",
     "evidence": ["refs exactly as written in square brackets in the presentation, e.g. proof-sec-web/some-id"],
     "quote": "optional: the id of one quote from your lens that this point rests on, e.g. q-id, or empty"}
  ],
  "quotes_used": [
    {"id": "a quote id from your lens", "why": "one sentence: how it applies to this product"}
  ],
  "conditions": [
    {"action": "what must happen", "owner": "a seat id (ceo, cto, ciso, cfo, cmo, sales, coo, cpo, legal)",
     "by": "before launch" or "within 30 days" or a date}
  ],
  "questions": ["a question for the presenting team, if you have one"]
}"""


PERSONAS_PATH = os.path.join(HERE, "personas.json")


def load_personas():
    """Researched lenses per seat, keeping only quotes marked verified."""
    if not os.path.exists(PERSONAS_PATH):
        return {}
    with open(PERSONAS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for seat_id, p in (data.get("seats") or {}).items():
        p = dict(p)
        p["quotes"] = [q for q in p.get("quotes", []) if q.get("confidence", "verified") == "verified"]
        out[seat_id] = p
    return out


def lens_section(persona):
    """The part of a director's brief that carries the researched lens."""
    if not persona:
        return []
    L = ["== THE LENS YOU APPLY: %s ==" % persona["name"].upper(),
         "You apply the documented thinking of %s (%s). You are not %s: never write in the first person as "
         "them or claim they would endorse anything. Refer to them in the third person (\"%s's rule was...\")."
         % (persona["name"], persona.get("known_for", ""), persona["name"], persona["name"].split()[-1]), ""]
    if persona.get("principles"):
        L.append("How they decided (each documented):")
        for pr in persona["principles"]:
            L.append("- %s (%s)" % (pr["text"], pr.get("source", "")))
        L.append("")
    if persona.get("board_style"):
        L.append("How they behaved in reviews like this one:")
        for b in persona["board_style"]:
            L.append("- %s (%s)" % (b["text"], b.get("source", "")))
        L.append("")
    if persona.get("quotes"):
        L.append("Their own words. Quote ONLY these, by id, and never alter or extend the text:")
        for q in persona["quotes"]:
            L.append('- [%s] "%s" (%s, %s)' % (q["id"], q["text"], q.get("source", ""), q.get("date", "")))
        L.append("")
        L.append("When one of these quotes is the reason for a point, put its id in that risk's \"quote\" field. "
                 "Use \"quotes_used\" for quotes that shape your overall view. Use them where they genuinely "
                 "apply to this evidence, not as decoration.")
        L.append("")
    return L


def seat_brief(seat, pres, answer_path, seat_vetoes, persona=None):
    L = ["You are the %s (%s) on the board of directors. The team is presenting their product to the board "
         "and asking whether it can launch." % (seat["title"], seat["short"]), "",
         "== YOUR ROLE ==", seat["lens"], ""]
    L += lens_section(persona)
    if seat["skills"] != ["*"]:
        L.append("The evidence closest to your role comes from: %s." % ", ".join(seat["skills"]))
    if seat.get("veto"):
        L.append("You hold a veto over: %s. A verified high or critical finding there blocks the launch "
                 "on its own." % ", ".join(seat["veto"]))
    mine = [v for v in seat_vetoes if v["seat"] == seat["id"]]
    if mine:
        L.append("The evidence already triggers your veto: %s." % "; ".join("[%s] %s" % (v["ref"], v["why"]) for v in mine))
    L += ["", "== THE PRESENTATION ==", pres, "== END OF PRESENTATION ==", "",
          "How to answer:",
          "- Judge from the presentation only. Do not invent facts, numbers or findings that are not in it.",
          "- Every high-severity risk must cite at least one ref from the presentation, copied exactly as it "
          "appears inside square brackets (for example proof-sec-web/some-id or proof-finance/score:x). "
          "Medium and low risks may be judgement calls with an empty evidence list.",
          "- Be the director, not a cheerleader. A polite yes is worth less than a clear concern.",
          "- Conditions are concrete actions with an owner seat.",
          "",
          "Create the file %s with your file-writing tool (do not only print it) containing ONLY this JSON, "
          "no markdown fence:" % answer_path, "",
          SCHEMA % {"seat": seat["id"], "short": seat["short"]}]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------ answers

def _answer_path(d, seat_id):
    return os.path.join(_board_dir(d), "answers", seat_id + ".json")


def validate_answer(obj, seat_id, refs, quote_ids=None):
    problems = []
    quote_ids = set(quote_ids or [])
    if not isinstance(obj, dict):
        return ["answer is not a JSON object"]
    if obj.get("seat") != seat_id:
        problems.append("seat should be %r" % seat_id)
    if obj.get("vote") not in VOTES:
        problems.append("vote must be one of %s" % ", ".join(VOTES))
    try:
        c = float(obj.get("confidence"))
        if not 0 <= c <= 1:
            problems.append("confidence must be between 0 and 1")
    except (TypeError, ValueError):
        problems.append("confidence must be a number from 0 to 1")
    if not isinstance(obj.get("summary"), str) or not obj["summary"].strip():
        problems.append("summary is required")
    for key in ("risks", "conditions", "questions"):
        if not isinstance(obj.get(key, []), list):
            problems.append("%s must be a list" % key)
    for i, rk in enumerate(obj.get("risks") or []):
        if not isinstance(rk, dict) or not rk.get("risk"):
            problems.append("risk %d needs a 'risk'" % (i + 1))
            continue
        if rk.get("severity") not in SEV:
            problems.append("risk %d severity must be high, medium or low" % (i + 1))
        ev = rk.get("evidence") or []
        if not isinstance(ev, list):
            problems.append("risk %d evidence must be a list" % (i + 1))
            continue
        bad = [e for e in ev if e not in refs]
        if bad:
            problems.append("risk %d cites refs that are not in the evidence: %s" % (i + 1, ", ".join(map(str, bad))))
        if rk.get("severity") == "high" and not ev:
            problems.append("risk %d is high severity but cites no evidence" % (i + 1))
        qid = rk.get("quote")
        if qid and qid not in quote_ids:
            problems.append("risk %d quotes %r, which is not one of this seat's researched quotes" % (i + 1, qid))
    if not isinstance(obj.get("quotes_used", []), list):
        problems.append("quotes_used must be a list")
    for i, qu in enumerate(obj.get("quotes_used") or []):
        if not isinstance(qu, dict) or qu.get("id") not in quote_ids:
            problems.append("quotes_used %d is not one of this seat's researched quotes" % (i + 1))
    for i, cd in enumerate(obj.get("conditions") or []):
        if not isinstance(cd, dict) or not cd.get("action"):
            problems.append("condition %d needs an 'action'" % (i + 1))
    if obj.get("vote") == "go_with_conditions" and not obj.get("conditions"):
        problems.append("a go_with_conditions vote needs at least one condition")
    return problems


def _load_meta(d):
    path = os.path.join(_board_dir(d), "board.json")
    if not os.path.exists(path):
        raise BoardError("no board brief yet. Run: board.py brief")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _quote_ids(personas, sid):
    return [q["id"] for q in (personas.get(sid) or {}).get("quotes", [])]


def load_answers(d, meta, refs, personas=None):
    personas = load_personas() if personas is None else personas
    good, bad = {}, {}
    for sid in meta["seats"]:
        path = _answer_path(d, sid)
        if not os.path.exists(path):
            bad[sid] = ["no answer yet"]
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except ValueError as e:
            bad[sid] = ["not valid JSON (%s)" % e]
            continue
        problems = validate_answer(obj, sid, refs, _quote_ids(personas, sid))
        if problems:
            bad[sid] = problems
        else:
            good[sid] = obj
    return good, bad


def _load_qa(d):
    path = os.path.join(_board_dir(d), "qa.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


# ------------------------------------------------------------ decision

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "before", "after", "launch", "must",
         "should", "all", "any", "each", "every", "have", "has", "are", "was", "then", "them", "their", "our"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in _STOP}

def decide(r, seats, answers, triggered):
    reasons, conditions = [], []
    n_seats = len(seats)
    votes = {v: [s for s, a in answers.items() if a["vote"] == v] for v in VOTES}
    if r["verdict"] == "Incomplete":
        decision = HOLD
        reasons.append("The evidence is incomplete, so the board cannot approve: %s" % "; ".join(r["gaps"][:3]))
    elif triggered:
        decision = HOLD
        for v in triggered:
            reasons.append("%s veto from the evidence: %s [%s]" % (v["seat"].upper(), v["why"], v["ref"]))
    elif len(answers) * 2 < n_seats:
        decision = HOLD
        reasons.append("No quorum: only %d of %d directors gave a valid answer." % (len(answers), n_seats))
    elif len(votes["hold"]) * 2 > len(answers):
        decision = HOLD
        reasons.append("A majority voted to hold (%d of %d)." % (len(votes["hold"]), len(answers)))
    else:
        any_conditions = any(a.get("conditions") for a in answers.values())
        if r["verdict"] == "Not yet" or votes["go_with_conditions"] or votes["hold"] or any_conditions:
            decision = GO_COND
            if r["verdict"] == "Not yet":
                reasons.append("The Proof verdict is Not yet, so the open blockers become conditions.")
            if votes["hold"]:
                reasons.append("%d director(s) voted to hold; their concerns become conditions." % len(votes["hold"]))
            if votes["go_with_conditions"]:
                reasons.append("%d director(s) voted go with conditions." % len(votes["go_with_conditions"]))
        else:
            decision = GO
            reasons.append("Every director who answered voted go and the Proof verdict is Ship.")
    # The action list: blockers from the evidence first, then the directors' conditions.
    owner_for = {}
    for seat in seats:
        for sk in seat.get("veto") or []:
            owner_for.setdefault(sk, seat["id"])
        for sk in seat["skills"]:
            owner_for.setdefault(sk, seat["id"])
    for b in r["blockers"]:
        ref = "%s/%s" % (b["skill"], b["id"]) if b["kind"] == "finding" else "%s/score:%s" % (b["skill"], b["id"])
        what = b.get("title") or ("%s back within budget" % b["id"])
        conditions.append({"action": "Fix: %s" % what, "owner": owner_for.get(b["skill"], "ceo"),
                           "by": "before launch", "from": "evidence", "ref": ref})
    raised = []
    for sid in sorted(answers):
        for c in answers[sid].get("conditions") or []:
            toks = _tokens(c["action"])
            match = None
            for prev in raised:
                inter = len(toks & prev["_t"])
                union = len(toks | prev["_t"]) or 1
                if inter / float(union) >= 0.45:
                    match = prev
                    break
            if match:
                if sid not in match["raised_by"]:
                    match["raised_by"].append(sid)
                continue
            raised.append({"action": c["action"], "owner": (c.get("owner") or sid).lower(),
                           "by": c.get("by") or "before launch", "from": "board", "raised_by": [sid], "_t": toks})
    for r_ in raised:
        r_.pop("_t")
    # Conditions raised by more directors come first.
    raised.sort(key=lambda c: -len(c["raised_by"]))
    conditions.extend(raised)
    return {"decision": decision, "reasons": reasons, "votes": {k: sorted(v) for k, v in votes.items()},
            "answered": sorted(answers), "missing": sorted(set(s["id"] for s in seats) - set(answers)),
            "vetoes": triggered, "conditions": conditions, "verdict": r["verdict"]}


# --------------------------------------------------------------- agenda

# The meeting walks the audits area by area, in the order a board would take them.
AREAS = [
    ("flows", "Product and user flows",
     ["proof-web-e2e", "proof-mobile-e2e", "proof-explore", "proof-compat", "proof-visual"]),
    ("reliability", "Reliability and performance", ["proof-api", "proof-load", "proof-web-perf", "proof-watch"]),
    ("security", "Security",
     ["proof-sec-web", "proof-sec-secrets", "proof-sec-deps", "proof-sec-diff", "proof-mobile-audit"]),
    ("infra", "Infrastructure and deployment", ["proof-infra"]),
    ("privacy", "Privacy and legal", ["proof-privacy"]),
    ("reach", "Accessibility, languages and reach", ["proof-a11y", "proof-i18n", "proof-seo", "proof-geo"]),
    ("ai", "AI features", ["proof-ai"]),
    ("quality", "Code health and test strength", ["proof-mutation", "proof-restraint"]),
    ("finance", "Finance", ["proof-finance"]),
    ("launch", "Launch readiness and operations", ["proof-release", "proof-email"]),
]
SKILL_AREA = {sk: aid for aid, _, sks in AREAS for sk in sks}
BLOCKER, CONCERNS, CLEAR, NOT_COVERED = "Blocker", "Concerns", "Clear", "Not covered"
OUTCOME_CLASS = {BLOCKER: "hold", CONCERNS: "go_with_conditions", CLEAR: "go", NOT_COVERED: "none"}


def _ref_area(ref):
    return SKILL_AREA.get(ref.split("/")[0])


def agenda(r, seats, answers, triggered):
    """Split the meeting into agenda items, one per audit area that has evidence or is required."""
    present = (set(f["skill"] for f in r["findings"]) | set(c["skill"] for c in r["coverage"]) |
               set(s["skill"] for s in r["scores"]))
    items = []
    for aid, title, sks in AREAS:
        run = [s for s in sks if s in present]
        missing = [s for s in sks if s in r["required"] and s not in present]
        if not run and not missing:
            continue
        fs = [f for f in r["findings"] if f["skill"] in sks]
        ver = sorted([f for f in fs if f["status"] == "verified"], key=lambda f: verdict.RANK[f["severity"]])
        unv = [f for f in fs if f["status"] == "unverified"]
        ruled = [f for f in fs if f["status"] == "false_positive"]
        metrics = [(s["skill"], m) for s in r["scores"] if s["skill"] in sks for m in s["metrics"]]
        cov = dict((st, 0) for st in ledger.COVERAGE_STATUSES)
        notrun = []
        for c in r["coverage"]:
            if c["skill"] in sks:
                for ch in c["checks"]:
                    cov[ch["status"]] += 1
                    if ch["status"] == "not_run":
                        notrun.append((c["skill"], ch))
        here = [v for v in triggered if v["ref"].split("/")[0] in sks]
        high = [f for f in ver if f["severity"] in ("critical", "high")]
        medium = [f for f in ver if f["severity"] == "medium"]
        failing = [(sk, m) for sk, m in metrics if not ledger.metric_passes(m)]
        reasons = []
        if not run:
            outcome = NOT_COVERED
            reasons.append("Required by the target but not run: %s." % ", ".join(missing))
        elif high or here:
            outcome = BLOCKER
            if high:
                reasons.append("%d verified high or critical finding(s)." % len(high))
            if here:
                reasons.append("Veto triggered for %s." % ", ".join(sorted(set(v["seat"].upper() for v in here))))
        elif medium or failing or [1 for sk, _ in notrun if sk in r["required"]]:
            outcome = CONCERNS
            if medium:
                reasons.append("%d verified medium finding(s)." % len(medium))
            if failing:
                reasons.append("%d score(s) over budget." % len(failing))
            req_notrun = [ch for sk, ch in notrun if sk in r["required"]]
            if req_notrun:
                reasons.append("%d required check(s) not yet run." % len(req_notrun))
        else:
            outcome = CLEAR
            reasons.append("Covered, and nothing verified at medium or above.")
        if run and missing:
            reasons.append("Also required but not run: %s." % ", ".join(missing))
        speakers = []
        for seat in seats:
            a = answers.get(seat["id"])
            if not a:
                continue
            mine = [rk for rk in a.get("risks") or [] if any(_ref_area(x) == aid for x in rk.get("evidence") or [])]
            if mine:
                speakers.append({"seat": seat["id"], "risks": mine})
        items.append({"id": aid, "title": title, "skills": run, "missing": missing, "verified": ver,
                      "unverified": unv, "ruled_out": ruled, "metrics": metrics, "coverage": cov,
                      "notrun": notrun, "vetoes": here, "outcome": outcome, "reasons": reasons,
                      "speakers": speakers})
    general = []
    for seat in seats:
        a = answers.get(seat["id"])
        if not a:
            continue
        mine = [rk for rk in a.get("risks") or [] if not any(_ref_area(x) for x in rk.get("evidence") or [])]
        if mine:
            general.append({"seat": seat["id"], "risks": mine})
    untested = [title for aid, title, sks in AREAS if not any(s in present or s in r["required"] for s in sks)]
    return items, general, untested


# -------------------------------------------------------------- minutes

def minutes_md(r, seats, answers, dec, qa, items, general, untested):
    t = r.get("target") or {}
    title_of = dict((s["id"], s["title"]) for s in seats)
    L = ["# Board minutes: %s" % (t.get("name") or "the product"), "",
         "**Decision: %s**" % dec["decision"], ""]
    for x in dec["reasons"]:
        L.append("- %s" % x)
    L += ["", "Proof verdict: %s. Votes: %d go, %d go with conditions, %d hold%s." % (
        dec["verdict"], len(dec["votes"]["go"]), len(dec["votes"]["go_with_conditions"]), len(dec["votes"]["hold"]),
        (", %d did not answer" % len(dec["missing"])) if dec["missing"] else ""), "", "## Agenda", ""]
    for i, it in enumerate(items, 1):
        L.append("%d. %s: %s" % (i, it["title"], it["outcome"]))
    if untested:
        L.append("")
        L.append("Not tested, so not discussed: %s." % ", ".join(untested))
    for i, it in enumerate(items, 1):
        L += ["", "## %d. %s: %s" % (i, it["title"], it["outcome"]), ""]
        for x in it["reasons"]:
            L.append("- %s" % x)
        for f in it["verified"]:
            L.append("- [%s/%s] %s: %s" % (f["skill"], f["id"], f["severity"], f["title"]))
        for sk, m in it["metrics"]:
            L.append("- [%s/score:%s] %s %s, budget %s %s" % (sk, m["id"], verdict._n(m["value"]), m["unit"], m["op"],
                                                              verdict._n(m["budget"])))
        for sp in it["speakers"]:
            for rk in sp["risks"]:
                L.append("- %s (%s): %s" % (title_of[sp["seat"]], rk["severity"], rk["risk"]))
    if general:
        L += ["", "## Other concerns", ""]
        for sp in general:
            for rk in sp["risks"]:
                L.append("- %s (%s): %s" % (title_of[sp["seat"]], rk["severity"], rk["risk"]))
    L += ["", "## Questions", ""]
    for seat in seats:
        a = answers.get(seat["id"])
        for i, q in enumerate((a or {}).get("questions") or []):
            ans = qa.get(seat["id"], {}).get(str(i))
            L.append("- %s: %s" % (seat["short"], q))
            L.append("  - Answer: %s" % (ans or "not yet answered"))
    L += ["", "## The vote", ""]
    for seat in seats:
        a = answers.get(seat["id"])
        L.append("- %s: %s. %s" % (seat["title"], VOTE_LABEL[a["vote"]] if a else "did not answer",
                                   a["summary"] if a else ""))
    L += ["", "## Actions", ""]
    for c in dec["conditions"]:
        src = ("evidence [%s]" % c["ref"]) if c["from"] == "evidence" else "raised by %s" % ", ".join(
            x.upper() for x in c["raised_by"])
        L.append("- [ ] %s (owner: %s, %s; %s)" % (c["action"], c["owner"].upper(), c["by"], src))
    return "\n".join(L) + "\n"


# --------------------------------------------------------- meeting page

def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


# Each seat has its own gradient (light top, deeper bottom), clothing colour, and a
# hat and outfit, so the directors are told apart at a glance.
AVATAR = {
    "ceo":   ("#e9d5ff", "#8b5cf6", "#3b2a6b"),
    "cto":   ("#b4f1ff", "#5b8cff", "#1f3b73"),
    "ciso":  ("#d9e2f2", "#64748b", "#1e293b"),
    "cfo":   ("#c7f9d4", "#2fa36b", "#14532d"),
    "cmo":   ("#ffd1ea", "#e24a9b", "#7a1f52"),
    "sales": ("#ffe0c7", "#f2703a", "#7c2d12"),
    "coo":   ("#fff3b0", "#eab308", "#713f12"),
    "cpo":   ("#c2f5ee", "#14b8a6", "#134e4a"),
    "legal": ("#e5e7eb", "#6b7280", "#111827"),
}
_HEAD = '<circle cx="50" cy="35" r="16" fill="url(#h-{id})"/>'
_BODY = '<path d="M15 96 C15 67 30 57 50 57 C70 57 85 67 85 96 Z" fill="url(#b-{id})"/>'

_BEHIND = {  # drawn before the head
    "cto": '<path d="M30 42 C30 17 70 17 70 42 L70 54 C63 60 37 60 30 54 Z" fill="{d}" opacity=".85"/>',
}
_OUTFIT = {  # drawn over the body
    "ceo": ('<path d="M33 60 L50 96 L67 60 C62 58 56 57 50 57 C44 57 38 58 33 60 Z" fill="{d}"/>'
            '<path d="M44 57 L50 70 L56 57 Z" fill="#ffffff"/>'
            '<path d="M48.5 60 L51.5 60 L53 79 L50 83 L47 79 Z" fill="#c0262d"/>'),
    "cto": ('<path d="M44 58 L45 70 M56 58 L55 70" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round"/>'
            '<rect x="38" y="76" width="24" height="9" rx="3" fill="{d}" opacity=".55"/>'),
    "ciso": ('<path d="M50 66 L60 70 L60 78 C60 84 55 88 50 90 C45 88 40 84 40 78 L40 70 Z" fill="{d}"/>'
             '<path d="M45.5 78 L49 81.5 L55 74.5" stroke="#ffffff" stroke-width="2" fill="none" stroke-linecap="round"/>'),
    "cfo": ('<path d="M33 60 L50 96 L67 60 C62 58 56 57 50 57 C44 57 38 58 33 60 Z" fill="{d}"/>'
            '<path d="M44 57 L50 68 L56 57 Z" fill="#ffffff"/>'
            '<path d="M42 59 L50 63 L42 67 Z M58 59 L50 63 L58 67 Z" fill="#facc15"/>'
            '<circle cx="50" cy="63" r="1.8" fill="#ca8a04"/>'),
    "cmo": ('<path d="M34 59 C42 64 58 64 66 59 L66 65 C58 70 42 70 34 65 Z" fill="{d}"/>'
            '<path d="M58 64 L62 64 L64 84 L58 86 Z" fill="{d}"/>'),
    "sales": ('<path d="M42 57 L50 66 L39 66 Z M58 57 L50 66 L61 66 Z" fill="#ffffff"/>'
              '<circle cx="50" cy="72" r="1.4" fill="{d}"/><circle cx="50" cy="78" r="1.4" fill="{d}"/>'),
    "coo": ('<path d="M32 62 L42 59 L44 96 L33 96 Z M68 62 L58 59 L56 96 L67 96 Z" fill="#f97316"/>'
            '<rect x="33" y="76" width="11" height="3.5" fill="#e5e7eb"/><rect x="56" y="76" width="11" height="3.5" fill="#e5e7eb"/>'),
    "cpo": '<rect x="39" y="51" width="22" height="11" rx="5" fill="{d}"/>',
    "legal": ('<path d="M33 60 L50 96 L67 60 C62 58 56 57 50 57 C44 57 38 58 33 60 Z" fill="{d}"/>'
              '<rect x="45.5" y="57" width="4" height="13" rx="1" fill="#ffffff"/>'
              '<rect x="50.5" y="57" width="4" height="13" rx="1" fill="#ffffff"/>'),
}
_HAT = {  # drawn over the head
    "ceo": ('<rect x="29" y="19" width="42" height="5" rx="2.5" fill="{d}"/>'
            '<rect x="36" y="2" width="28" height="19" rx="2" fill="{d}"/>'
            '<rect x="36" y="15" width="28" height="3.5" fill="#fbbf24"/>'),
    "cto": ('<circle cx="43" cy="36" r="4.6" fill="none" stroke="{d}" stroke-width="1.8"/>'
            '<circle cx="57" cy="36" r="4.6" fill="none" stroke="{d}" stroke-width="1.8"/>'
            '<path d="M47.6 36 L52.4 36" stroke="{d}" stroke-width="1.8"/>'),
    "ciso": ('<path d="M33 31 C33 14 67 14 67 31 Z" fill="{d}"/>'
             '<path d="M62 29 C70 29 77 31 78 33 L62 33 Z" fill="{d}"/>'
             '<circle cx="50" cy="23" r="2.4" fill="#93c5fd"/>'),
    "cfo": ('<path d="M35 27 C35 9 65 9 65 27 Z" fill="{d}"/>'
            '<ellipse cx="50" cy="27" rx="21" ry="3.6" fill="{d}"/>'
            '<rect x="35.5" y="21" width="29" height="3" fill="#4ade80" opacity=".8"/>'),
    "cmo": ('<ellipse cx="46" cy="22" rx="21" ry="7.5" fill="{d}" transform="rotate(-12 46 22)"/>'
            '<circle cx="45" cy="14" r="2.2" fill="{d}"/>'),
    "sales": ('<path d="M34 29 C34 12 66 12 66 29 Z" fill="{d}"/>'
              '<path d="M35 28 C27 28 19 30 18 32 C24 33 31 32 37 31 Z" fill="{d}"/>'
              '<circle cx="50" cy="14" r="1.6" fill="#fdba74"/>'),
    "coo": ('<path d="M32 29 C32 11 68 11 68 29 Z" fill="#facc15" stroke="{d}" stroke-width="1"/>'
            '<rect x="27" y="27" width="46" height="5" rx="2.5" fill="#facc15" stroke="{d}" stroke-width="1"/>'
            '<path d="M50 12 L50 27" stroke="{d}" stroke-width="1.6"/>'),
    "cpo": ('<path d="M34 29 C34 9 66 9 66 29 Z" fill="{d}"/>'
            '<rect x="33" y="24" width="34" height="7" rx="3.5" fill="{d}" stroke="#ffffff" stroke-opacity=".35"/>'
            '<circle cx="50" cy="8" r="4" fill="{d}"/>'),
    "legal": ('<rect x="39" y="15" width="22" height="9" rx="1.5" fill="{d}"/>'
              '<path d="M50 5 L77 14 L50 23 L23 14 Z" fill="{d}"/>'
              '<path d="M50 14 L68 18 L68 30" stroke="#fbbf24" stroke-width="1.4" fill="none"/>'
              '<circle cx="68" cy="31" r="1.8" fill="#fbbf24"/>'),
}


def avatar_svg(seat_id, size=56, suffix=""):
    """A person silhouette in the seat's colours, with its own hat and clothing."""
    light, deep, dark = AVATAR.get(seat_id, ("#dbeafe", "#60a5fa", "#1e3a8a"))
    sid = (re.sub(r"[^a-z0-9]", "", seat_id) or "x") + suffix
    parts = ['<svg class="pfp" width="%d" height="%d" viewBox="0 0 100 100" role="img" aria-label="%s">' % (
        size, size, e(seat_id)),
        '<defs><linearGradient id="h-%s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/>'
        '<stop offset="1" stop-color="%s"/></linearGradient>' % (sid, light, deep),
        '<linearGradient id="b-%s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/>'
        '<stop offset="1" stop-color="%s"/></linearGradient></defs>' % (sid, deep, deep)]
    parts.append(_BEHIND.get(seat_id, "").format(d=dark))
    parts.append(_BODY.format(id=sid))
    parts.append(_OUTFIT.get(seat_id, "").format(d=dark))
    parts.append(_HEAD.format(id=sid))
    parts.append(_HAT.get(seat_id, "").format(d=dark))
    parts.append("</svg>")
    return "".join(parts)



MEETING_CSS = """
:root{
  --paper:#f6f3ec;--surface:#fffdf8;--ink:#1d1c1a;--soft:#4a4740;--muted:#76716a;--line:#e3ddd1;--rule:#cfc6b5;
  --accent:#9a6a24;--accent-soft:#f1e6d2;
  --blocker:#a8321e;--concerns:#9a6100;--clear:#2e6f47;--none:#7a756c;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --paper:#14130f;--surface:#1c1a15;--ink:#ece6da;--soft:#cfc7b8;--muted:#9b9486;--line:#2c2922;--rule:#3a362d;
  --accent:#d3a253;--accent-soft:#2b2417;--blocker:#e0705c;--concerns:#d9a441;--clear:#6fb38a;--none:#9b9486;}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 var(--sans);font-variant-numeric:tabular-nums}
a{color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.page{max-width:1180px;margin:0 auto;padding:clamp(16px,3vw,40px)}
header.masthead{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px;
  border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:24px}
.masthead .eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.masthead h1{font-family:var(--serif);font-weight:600;font-size:clamp(26px,4vw,40px);line-height:1.1;margin:4px 0 0}
.status{display:flex;align-items:center;gap:10px;font-size:14px;color:var(--soft)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block}
.playing .dot{animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
button.plain{font:inherit;font-size:14px;background:none;border:1px solid var(--rule);color:var(--ink);border-radius:999px;padding:5px 14px;cursor:pointer}
button.plain:hover{border-color:var(--ink)}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:clamp(20px,4vw,48px);align-items:start}
@media (max-width:860px){.layout{grid-template-columns:1fr}.rail{position:static!important}}
.rail{position:sticky;top:20px}
.rail h2{font-family:var(--serif);font-size:14px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
.timeline{list-style:none;margin:0;padding:0;border-left:1px solid var(--rule)}
.timeline li{position:relative;padding:6px 0 6px 18px;font-size:14px;color:var(--muted)}
.timeline li::before{content:"";position:absolute;left:-5px;top:13px;width:9px;height:9px;border-radius:50%;background:var(--paper);border:1px solid var(--rule)}
.timeline li.done{color:var(--soft)}
.timeline li.done::before{background:var(--rule)}
.timeline li.now{color:var(--ink);font-weight:600}
.timeline li.now::before{background:var(--accent);border-color:var(--accent)}
.timeline .tag{display:block;font-size:12px;font-weight:500}
.timeline li:not(.done):not(.now) .tag{visibility:hidden}
body.transcript .timeline li .tag{visibility:visible}
@media (max-width:860px){.timeline{display:none}.rail h2{display:none}.meter{margin:0 0 18px}}
.tag.blocker{color:var(--blocker)}.tag.concerns{color:var(--concerns)}.tag.clear{color:var(--clear)}.tag.none{color:var(--none)}
.meter{height:2px;background:var(--line);margin:14px 0 0}
.meter span{display:block;height:100%;width:0;background:var(--accent);transition:width .6s ease}
.stage{min-height:60vh}
.step{display:none}
.step.on{display:block}
body.transcript .step{display:block;margin-bottom:56px}
body.transcript .reveal{opacity:1!important;transform:none!important}
.reveal{opacity:0;transform:translateY(6px);transition:opacity .5s ease,transform .5s ease}
.reveal.in{opacity:1;transform:none}
.kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:600}
.step h2{font-family:var(--serif);font-weight:600;font-size:clamp(24px,3vw,32px);line-height:1.2;margin:6px 0 18px}
.step h3{font-family:var(--serif);font-weight:600;font-size:19px;margin:28px 0 10px}
.lede{font-size:18px;color:var(--soft);max-width:62ch}
.sheet{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:clamp(16px,2.4vw,24px)}
.figures{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:0;border-top:1px solid var(--line);border-left:1px solid var(--line);margin:18px 0}
.figure{padding:12px 14px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);background:var(--surface)}
.figure b{display:block;font-family:var(--serif);font-size:28px;font-weight:600;line-height:1.1}
.figure span{font-size:13px;color:var(--muted)}
.verdict{display:inline-block;font-weight:600;border-bottom:2px solid currentColor}
.v-ship{color:var(--clear)}.v-notyet{color:var(--concerns)}.v-incomplete{color:var(--blocker)}
.room{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:8px}
.member{display:flex;gap:12px;align-items:center;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--surface)}
.member .who{font-size:14px;line-height:1.35}
.member .who b{display:block;font-family:var(--serif);font-size:16px}
.member .who small{color:var(--muted)}
.av{flex:none;display:inline-flex;align-self:start;border-radius:50%;background:var(--accent-soft);overflow:hidden}
.av svg{display:block}
.agenda{margin:0;padding-left:22px}.agenda li{padding:3px 0}
.finding{display:grid;grid-template-columns:86px 1fr;gap:10px;padding:10px 0;border-top:1px solid var(--line)}
.finding:first-child{border-top:0}
.sev{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;padding-top:2px}
.sev.critical,.sev.high{color:var(--blocker)}.sev.medium{color:var(--concerns)}.sev.low,.sev.info{color:var(--none)}
.ref{font-family:var(--mono);font-size:12px;color:var(--muted);text-decoration:none;word-break:break-all}
.ref:hover{color:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:15px}
td,th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
th{font-weight:600;font-size:13px;color:var(--muted)}
.speech{display:grid;grid-template-columns:56px 1fr;gap:14px;margin:22px 0}
.speech .name{font-family:var(--serif);font-size:17px;font-weight:600}
.speech .lens{font-size:13px;color:var(--muted)}
.speech p{margin:6px 0}
.point{margin:8px 0 0;padding-left:12px;border-left:2px solid var(--rule)}
.point.high,.point.critical{border-color:var(--blocker)}.point.medium{border-color:var(--concerns)}
blockquote{margin:10px 0 0;padding:10px 14px;background:var(--accent-soft);border-radius:4px;font-family:var(--serif);font-size:16px}
blockquote footer{font-family:var(--sans);font-size:12px;color:var(--muted);margin-top:4px}
blockquote footer a{color:var(--muted)}
.outcome{margin-top:26px;padding:14px 16px;border-left:4px solid var(--none);background:var(--surface)}
.outcome.blocker{border-color:var(--blocker)}.outcome.concerns{border-color:var(--concerns)}.outcome.clear{border-color:var(--clear)}
.outcome b{font-family:var(--serif);font-size:19px}
.qa{margin:14px 0;padding:12px 14px;border:1px solid var(--line);border-radius:6px;background:var(--surface)}
.qa p{margin:4px 0}
.votes{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.ballot{padding:14px;border:1px solid var(--line);border-radius:6px;background:var(--surface)}
.ballot .head{display:flex;gap:10px;align-items:center}
.ballot .call{font-weight:600;font-size:14px}
.call.go{color:var(--clear)}.call.go_with_conditions{color:var(--concerns)}.call.hold{color:var(--blocker)}.call.none{color:var(--none)}
.tally{display:flex;height:8px;margin:18px 0 6px;background:var(--line)}
.tally span{display:block;height:100%}
.decision{padding:22px 24px;border:2px solid var(--ink);border-radius:6px;background:var(--surface)}
.decision .word{font-family:var(--serif);font-size:clamp(34px,5vw,52px);font-weight:600;line-height:1}
.decision.hold .word{color:var(--blocker)}.decision.go .word{color:var(--clear)}.decision.go_with_conditions .word{color:var(--concerns)}
.actions h3{margin-top:22px}
.actions label{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--line);cursor:pointer}
.actions input{margin-top:5px;accent-color:var(--accent)}
.actions label.done strong{text-decoration:line-through;color:var(--muted)}
.small{font-size:13px;color:var(--muted)}
.footnote{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}
@media (prefers-reduced-motion:reduce){.reveal{opacity:1;transform:none;transition:none}.playing .dot{animation:none}}
@media print{.rail,.status{display:none}.layout{display:block}.step{display:block!important;break-inside:avoid-page}.reveal{opacity:1!important;transform:none!important}}
"""

MEETING_JS = """
(function(){
  var steps = Array.prototype.slice.call(document.querySelectorAll('.step'));
  var items = Array.prototype.slice.call(document.querySelectorAll('.timeline li'));
  var meter = document.getElementById('meter'), label = document.getElementById('statuslabel');
  var toggle = document.getElementById('toggle'), mode = document.getElementById('mode');
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var cur = -1, timer = null, paused = false, finished = false;
  function words(el){ return (el.textContent || '').split(/\\s+/).length; }
  function show(i){
    cur = i;
    steps.forEach(function(s, k){ s.classList.toggle('on', k === i); });
    items.forEach(function(li, k){ li.classList.toggle('now', k === i); li.classList.toggle('done', k < i); });
    meter.style.width = (100 * (i + 1) / steps.length) + '%';
    var parts = Array.prototype.slice.call(steps[i].querySelectorAll('.reveal'));
    if (document.hidden || reduce) { parts.forEach(function(p){ p.classList.add('in'); }); return 4500; }
    parts.forEach(function(p){ p.classList.remove('in'); });
    var t = 250;
    parts.forEach(function(p){ setTimeout(function(){ p.classList.add('in'); }, t);
      t += Math.min(2600, 500 + words(p) * 45); });
    return Math.max(4500, t + 1800);
  }
  function run(){
    clearTimeout(timer);
    if (paused || finished) return;
    var hold = show(cur + 1);
    label.textContent = 'In session: ' + steps[cur].getAttribute('data-title');
    if (cur >= steps.length - 1) { finished = true; document.body.classList.remove('playing');
      label.textContent = 'Meeting closed'; toggle.textContent = 'Replay'; return; }
    if (document.hidden) { timer = null; label.textContent = 'Waiting for you to come back: ' + steps[cur].getAttribute('data-title'); return; }
    timer = setTimeout(run, hold);
  }
  toggle.addEventListener('click', function(){
    if (finished) { finished = false; paused = false; cur = -1; document.body.classList.add('playing'); toggle.textContent = 'Pause'; run(); return; }
    paused = !paused; toggle.textContent = paused ? 'Resume' : 'Pause';
    document.body.classList.toggle('playing', !paused);
    if (!paused) { cur = cur - 1; run(); } else { clearTimeout(timer); label.textContent = 'Paused at: ' + steps[cur].getAttribute('data-title'); }
  });
  mode.addEventListener('click', function(){
    var on = document.body.classList.toggle('transcript');
    mode.textContent = on ? 'Live view' : 'Full transcript';
    if (on) { clearTimeout(timer); paused = true; toggle.textContent = 'Resume'; document.body.classList.remove('playing');
      label.textContent = 'Full transcript'; }
  });
  document.addEventListener('visibilitychange', function(){
    if (document.hidden && !paused && !finished) { clearTimeout(timer); timer = null; }
    else if (!document.hidden && !paused && !finished && timer === null) {
      label.textContent = 'In session: ' + steps[cur].getAttribute('data-title');
      timer = setTimeout(run, 3500); }
  });
  var key = 'proof-board-actions-' + document.body.getAttribute('data-fp');
  var saved = {}; try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) { saved = {}; }
  function prog(){ var d = document.querySelectorAll('.actions input:checked').length, a = document.querySelectorAll('.actions input').length;
    var p = document.getElementById('actprog'); if (p) p.textContent = d + ' of ' + a + ' done'; }
  document.querySelectorAll('.actions input').forEach(function(cb){ var id = cb.getAttribute('data-id');
    if (saved[id]) { cb.checked = true; cb.parentNode.classList.add('done'); }
    cb.addEventListener('change', function(){ cb.parentNode.classList.toggle('done', cb.checked); saved[id] = cb.checked;
      try { localStorage.setItem(key, JSON.stringify(saved)); } catch (e) {} prog(); }); });
  prog();
  if (reduce) { document.body.classList.add('transcript'); mode.textContent = 'Live view'; label.textContent = 'Full transcript'; toggle.textContent = 'Play';
    paused = true; finished = false; }
  else { document.body.classList.add('playing'); run(); }
})();
"""


def _anchor(ref):
    return "ev-" + re.sub(r"[^a-z0-9-]+", "-", ref.lower())


def _ref_link(ref):
    return '<a class="ref" href="#%s">%s</a>' % (_anchor(ref), e(ref))


def _quote_html(q, persona_name):
    if not q:
        return ""
    src = e(q.get("source", ""))
    if q.get("date"):
        src += ", " + e(q["date"])
    if q.get("url"):
        src = '<a href="%s" rel="noopener">%s</a>' % (e(q["url"]), src)
    return '<blockquote>&ldquo;%s&rdquo;<footer>%s, %s</footer></blockquote>' % (e(q["text"]), e(persona_name), src)


def _speech(seat, persona, risks, quotes_used, suffix):
    """One director speaking: lens, points, and any quotes they invoked."""
    qmap = dict((q["id"], q) for q in (persona or {}).get("quotes", []))
    pname = (persona or {}).get("name")
    lens = ("applies %s's documented thinking" % e(pname)) if pname else e(seat["title"])
    out = ['<div class="speech reveal"><div class="av">%s</div><div>' % avatar_svg(seat["id"], 56, suffix),
           '<div class="name">%s</div><div class="lens">%s &middot; %s</div>' % (e(seat["title"]), e(seat["short"]), lens)]
    shown = set()
    for rk in risks:
        refs = " ".join(_ref_link(x) for x in rk.get("evidence") or []) or '<span class="small">judgement call</span>'
        out.append('<div class="point %s"><p><strong>%s.</strong> %s</p><div>%s</div>' % (
            e(rk["severity"]), e(rk["severity"].capitalize()), e(rk["risk"]), refs))
        q = qmap.get(rk.get("quote"))
        if q:
            out.append(_quote_html(q, pname))
            shown.add(q["id"])
        out.append("</div>")
    for qu in quotes_used or []:
        q = qmap.get(qu.get("id"))
        if q and q["id"] not in shown:
            out.append('<p class="small">%s</p>%s' % (e(qu.get("why", "")), _quote_html(q, pname)))
            shown.add(q["id"])
    out.append("</div></div>")
    return "".join(out)


def meeting_html(r, seats, answers, dec, qa, fp, items, general, untested, personas=None):
    personas = personas or {}
    t = r.get("target") or {}
    name = t.get("name") or "The product"
    seat_by = dict((s["id"], s) for s in seats)
    sev = dict((s, sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] == s))
               for s in ledger.SEVERITIES)
    metrics = [m for s in r["scores"] for m in s["metrics"]]
    passing = sum(1 for m in metrics if ledger.metric_passes(m))
    covered = sum(1 for c in r["coverage"] for ch in c["checks"] if ch["status"] == "covered")
    total_checks = sum(len(c["checks"]) for c in r["coverage"])
    vclass = {"Ship": "v-ship", "Not yet": "v-notyet", "Incomplete": "v-incomplete"}[r["verdict"]]
    oclass = {BLOCKER: "blocker", CONCERNS: "concerns", CLEAR: "clear", NOT_COVERED: "none"}
    steps = []  # (title, tag, tagclass, html)

    # Opening
    S = ['<div class="kicker">Opening</div><h2>The team presents %s</h2>' % e(name)]
    bits = []
    if t.get("kind"):
        bits.append("a %s product" % e(t["kind"]))
    if t.get("done_means"):
        bits.append("the team's own bar for done: %s" % e(t["done_means"]))
    S.append('<p class="lede reveal">The Proof verdict going in is <span class="verdict %s">%s</span>%s.</p>' % (
        vclass, e(r["verdict"]), ("; " + "; ".join(bits)) if bits else ""))
    S.append('<div class="figures reveal">')
    for lab, n in (("critical", sev["critical"]), ("high", sev["high"]), ("medium", sev["medium"]), ("low", sev["low"])):
        S.append('<div class="figure"><b>%d</b><span>%s findings</span></div>' % (n, lab))
    S.append('<div class="figure"><b>%d/%d</b><span>scores in budget</span></div>' % (passing, len(metrics)))
    S.append('<div class="figure"><b>%d/%d</b><span>checks covered</span></div></div>' % (covered, total_checks))
    S.append('<h3 class="reveal">At the table</h3><div class="room reveal">')
    for seat in seats:
        p = personas.get(seat["id"])
        S.append('<div class="member"><div class="av">%s</div><div class="who"><b>%s</b>%s<br><small>%s</small></div></div>' % (
            avatar_svg(seat["id"], 48, "o"), e(seat["title"]),
            ("<small>%s lens</small>" % e(p["name"])) if p else "",
            e(("Veto over " + ", ".join(x.replace("proof-", "") for x in seat["veto"])) if seat["veto"] else "No veto")))
    S.append("</div>")
    S.append('<h3 class="reveal">Agenda</h3><ol class="agenda reveal">%s</ol>' % "".join(
        "<li>%s</li>" % e(it["title"]) for it in items))
    if untested:
        S.append('<p class="small reveal">Not tested, so not on the agenda: %s.</p>' % e(", ".join(untested)))
    steps.append(("Opening", "", "", "".join(S)))

    # Agenda items
    for i, it in enumerate(items, 1):
        S = ['<div class="kicker">Item %d of %d</div><h2>%s</h2>' % (i, len(items), e(it["title"]))]
        S.append('<div class="sheet reveal"><div class="kicker" style="color:var(--muted)">The team presents</div>')
        if it["skills"]:
            c = it["coverage"]
            S.append("<p>Tested with %s. %d check(s) covered, %d not applicable, %d not yet run.</p>" % (
                ", ".join(e(s) for s in it["skills"]), c["covered"], c["not_applicable"], c["not_run"]))
        if it["missing"]:
            S.append('<p class="small">Required but not run: %s.</p>' % e(", ".join(it["missing"])))
        for f in it["verified"]:
            S.append('<div class="finding"><span class="sev %s">%s</span><div>%s<br>%s</div></div>' % (
                e(f["severity"]), e(f["severity"]), e(f["title"]), _ref_link("%s/%s" % (f["skill"], f["id"]))))
        if it["skills"] and not it["verified"]:
            S.append('<p class="small">No verified findings in this area.</p>')
        if it["metrics"]:
            S.append("<table><tr><th>Score</th><th>Measured</th><th>Budget</th><th></th></tr>")
            for sk, m in it["metrics"]:
                ok = ledger.metric_passes(m)
                S.append('<tr><td>%s</td><td>%s %s</td><td>%s %s</td><td class="sev %s">%s</td></tr>' % (
                    _ref_link("%s/score:%s" % (sk, m["id"])), e(verdict._n(m["value"])), e(m["unit"]), e(m["op"]),
                    e(verdict._n(m["budget"])), "low" if ok else "high", "in budget" if ok else "over budget"))
            S.append("</table>")
        if it["notrun"]:
            S.append('<p class="small">Not yet checked: %s.</p>' % e("; ".join(ch["title"] for _, ch in it["notrun"][:6])))
        S.append("</div>")
        if it["speakers"]:
            S.append('<h3 class="reveal">The board</h3>')
            for k, sp in enumerate(it["speakers"]):
                S.append(_speech(seat_by[sp["seat"]], personas.get(sp["seat"]), sp["risks"],
                                 [], "i%d%d" % (i, k)))
        else:
            S.append('<p class="small reveal">No director raised a point on this item.</p>')
        S.append('<div class="outcome %s reveal"><b>%s.</b> %s</div>' % (
            oclass[it["outcome"]], e(it["outcome"]), e(" ".join(it["reasons"]))))
        steps.append((it["title"], it["outcome"], oclass[it["outcome"]], "".join(S)))

    # Other concerns, including quotes a director invoked without tying them to a risk.
    # A quote already shown on an agenda item is not repeated here.
    shown_by = {}
    for it in items:
        for sp in it["speakers"]:
            for rk in sp["risks"]:
                if rk.get("quote"):
                    shown_by.setdefault(sp["seat"], set()).add(rk["quote"])
    extra = []
    for k, seat in enumerate(seats):
        a = answers.get(seat["id"])
        if not a:
            continue
        loose = next((g["risks"] for g in general if g["seat"] == seat["id"]), [])
        used = [q for q in a.get("quotes_used") or [] if q.get("id") not in shown_by.get(seat["id"], set())]
        if loose or used:
            extra.append(_speech(seat, personas.get(seat["id"]), loose, used, "g%d" % k))
    if extra:
        steps.append(("Other points", "", "", '<div class="kicker">Other points</div><h2>Points not tied to one audit</h2>' +
                      "".join(extra)))

    # Questions
    S = ['<div class="kicker">Questions</div><h2>The board asks the team</h2>']
    asked = 0
    for seat in seats:
        a = answers.get(seat["id"]) or {}
        for i, q in enumerate(a.get("questions") or []):
            asked += 1
            ans = qa.get(seat["id"], {}).get(str(i))
            S.append('<div class="qa reveal"><p><strong>%s:</strong> %s</p><p class="%s"><strong>Team:</strong> %s</p></div>' % (
                e(seat["short"]), e(q), "" if ans else "small", e(ans) if ans else "No answer recorded."))
    if not asked:
        S.append('<p class="small">The board had no questions.</p>')
    steps.append(("Questions", "", "", "".join(S)))

    # The vote
    nv = dict((k, len(v)) for k, v in dec["votes"].items())
    tot = max(1, sum(nv.values()) + len(dec["missing"]))
    S = ['<div class="kicker">The vote</div><h2>Each director gives a verdict</h2><div class="votes">']
    for k, seat in enumerate(seats):
        a = answers.get(seat["id"])
        vote = a["vote"] if a else "none"
        p = personas.get(seat["id"])
        S.append('<div class="ballot reveal"><div class="head"><div class="av">%s</div><div><div class="name" '
                 'style="font-family:var(--serif);font-weight:600">%s</div><div class="call %s">%s</div></div></div>' % (
                     avatar_svg(seat["id"], 44, "v%d" % k), e(seat["title"]), vote, e(VOTE_LABEL.get(vote, "Did not answer"))))
        if a:
            S.append('<p>%s</p><div class="small">Confidence %d%%%s</div>' % (
                e(a["summary"]), int(round(float(a["confidence"]) * 100)), (" &middot; %s lens" % e(p["name"])) if p else ""))
        S.append("</div>")
    S.append('</div><div class="tally reveal">')
    for key, var in (("go", "--clear"), ("go_with_conditions", "--concerns"), ("hold", "--blocker")):
        if nv[key]:
            S.append('<span style="width:%.1f%%;background:var(%s)"></span>' % (100.0 * nv[key] / tot, var))
    if dec["missing"]:
        S.append('<span style="width:%.1f%%;background:var(--none)"></span>' % (100.0 * len(dec["missing"]) / tot))
    S.append('</div><p class="small reveal">%d go, %d go with conditions, %d hold%s.</p>' % (
        nv["go"], nv["go_with_conditions"], nv["hold"],
        (", %d did not answer" % len(dec["missing"])) if dec["missing"] else ""))
    steps.append(("The vote", "", "", "".join(S)))

    # The decision
    dcls = {GO: "go", GO_COND: "go_with_conditions", HOLD: "hold"}[dec["decision"]]
    S = ['<div class="kicker">The decision</div><h2>The board decides</h2>',
         '<div class="decision %s reveal"><div class="word">%s</div><ul>%s</ul>'
         '<p class="small">Computed from the evidence and the votes, not written by hand.</p></div>' % (
             dcls, e(dec["decision"]), "".join("<li>%s</li>" % e(x) for x in dec["reasons"]))]
    S.append('<table class="reveal" style="margin-top:20px"><tr><th>Agenda item</th><th>Outcome</th></tr>%s</table>' % "".join(
        '<tr><td>%s</td><td class="tag %s">%s</td></tr>' % (e(it["title"]), oclass[it["outcome"]], e(it["outcome"])) for it in items))
    steps.append(("The decision", dec["decision"], dcls if dcls != "go_with_conditions" else "concerns", "".join(S)))

    # Actions
    S = ['<div class="kicker">Actions</div><h2>What happens next</h2><p class="small" id="actprog"></p><div class="actions reveal">']
    groups = [("Blockers from the evidence", [c for c in dec["conditions"] if c["from"] == "evidence"])]
    owners = []
    for c in dec["conditions"]:
        if c["from"] != "evidence" and c["owner"] not in owners:
            owners.append(c["owner"])
    for o in owners:
        groups.append(("For the %s" % (seat_by[o]["title"] if o in seat_by else o.upper()),
                       [c for c in dec["conditions"] if c["from"] != "evidence" and c["owner"] == o]))
    n = 0
    for title, group in groups:
        if not group:
            continue
        S.append("<h3>%s</h3>" % e(title))
        for c in group:
            src = ("from " + _ref_link(c["ref"])) if c["from"] == "evidence" else \
                "raised by %s" % e(", ".join(x.upper() for x in c["raised_by"]))
            S.append('<label><input type="checkbox" data-id="a%d"><span><strong>%s</strong><br><span class="small">%s, %s</span></span></label>' % (
                n, e(c["action"]), e(c["by"]), src))
            n += 1
    if not n:
        S.append('<p class="small">No actions.</p>')
    S.append("</div>")
    # Evidence appendix lives on the last step so every reference has somewhere to land.
    S.append('<h3>The evidence</h3><div class="sheet">')
    for f in sorted([f for f in r["findings"] if f["status"] == "verified"], key=lambda f: verdict.RANK[f["severity"]]):
        ref = "%s/%s" % (f["skill"], f["id"])
        S.append('<div class="finding" id="%s"><span class="sev %s">%s</span><div>%s<br><span class="ref">%s</span></div></div>' % (
            _anchor(ref), e(f["severity"]), e(f["severity"]), e(f["title"]), e(ref)))
    for s in r["scores"]:
        for m in s["metrics"]:
            ref = "%s/score:%s" % (s["skill"], m["id"])
            S.append('<div class="finding" id="%s"><span class="sev %s">score</span><div>%s %s against %s %s<br><span class="ref">%s</span></div></div>' % (
                _anchor(ref), "low" if ledger.metric_passes(m) else "high", e(verdict._n(m["value"])), e(m["unit"]),
                e(m["op"]), e(verdict._n(m["budget"])), e(ref)))
    for c in r["coverage"]:
        for ch in c["checks"]:
            S.append('<span id="%s"></span>' % _anchor("%s/coverage:%s" % (c["skill"], ch["id"])))
    S.append("</div>")
    steps.append(("Actions", "", "", "".join(S)))

    rail = "".join('<li>%s%s</li>' % (e(title), ('<span class="tag %s">%s</span>' % (cls, e(tag))) if tag else "")
                   for title, tag, cls, _ in steps)
    body = "".join('<section class="step" data-title="%s" aria-label="%s">%s</section>' % (e(title), e(title), html_)
                   for title, _, _, html_ in steps)
    return "\n".join([
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex">',
        "<title>%s: board meeting</title><style>%s</style></head>" % (e(name), MEETING_CSS),
        '<body data-fp="%s"><div class="page">' % e(fp),
        '<header class="masthead"><div><div class="eyebrow">Board meeting, %s</div><h1>%s</h1></div>'
        '<div class="status" role="status" aria-live="polite"><span class="dot" aria-hidden="true"></span>'
        '<span id="statuslabel">Starting</span><button class="plain" id="toggle" type="button">Pause</button>'
        '<button class="plain" id="mode" type="button">Full transcript</button></div></header>' % (
            e(ledger.now()[:10]), e(name)),
        '<div class="layout"><nav class="rail" aria-label="Agenda"><h2>Agenda</h2><ol class="timeline">%s</ol>'
        '<div class="meter"><span id="meter"></span></div></nav><main class="stage">%s</main></div>' % (rail, body),
        '<p class="footnote">The directors are sub-agents. Each applies the documented thinking of the person named, using '
        'quotes reproduced with their sources; none of them is that person or speaks for them. The evidence and the '
        'computed decision are the record.</p>',
        "</div><script>%s</script></body></html>" % MEETING_JS,
    ]) + "\n"


# ------------------------------------------------------------- transcript

def transcript(r, seats, answers, dec, qa, items, general, untested, personas=None, width=88):
    """The same meeting as plain text, for the terminal or a chat."""
    import textwrap
    personas = personas or {}
    t = r.get("target") or {}
    seat_by = dict((s["id"], s) for s in seats)
    W = lambda s, ind="": textwrap.fill(s, width=width, initial_indent=ind, subsequent_indent=ind + "  ")
    L = ["=" * width, "BOARD MEETING: %s" % (t.get("name") or "the product"), "=" * width, ""]
    L.append(W("The team presents. Proof verdict going in: %s. Verified findings: %d high or critical, %d medium." % (
        r["verdict"], sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] in ("critical", "high")),
        sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] == "medium"))))
    L.append("At the table: " + ", ".join(
        "%s (%s lens)" % (s["short"], personas[s["id"]]["name"]) if s["id"] in personas else s["short"] for s in seats))
    L.append("Agenda: " + "; ".join("%d. %s" % (i, it["title"]) for i, it in enumerate(items, 1)))
    if untested:
        L.append(W("Not tested, not discussed: %s." % ", ".join(untested)))

    def speak(seat_id, risks, used=()):
        seat = seat_by[seat_id]
        p = personas.get(seat_id) or {}
        qmap = dict((q["id"], q) for q in p.get("quotes", []))
        lens = " (%s lens)" % p["name"] if p.get("name") else ""
        out = ["  %s%s:" % (seat["short"], lens)]
        for rk in risks:
            out.append(W("- %s: %s" % (rk["severity"].upper(), rk["risk"]), "    "))
            if rk.get("evidence"):
                out.append(W("evidence: %s" % ", ".join(rk["evidence"]), "      "))
            q = qmap.get(rk.get("quote"))
            if q:
                out.append(W('quoting %s: "%s" (%s%s)' % (p["name"], q["text"], q.get("source", ""),
                                                          (", " + q["date"]) if q.get("date") else ""), "      "))
        for qu in used:
            q = qmap.get(qu.get("id"))
            if q:
                out.append(W('%s As %s put it: "%s" (%s%s)' % (qu.get("why", ""), p["name"], q["text"], q.get("source", ""),
                                                             (", " + q["date"]) if q.get("date") else ""), "    "))
        return out

    for i, it in enumerate(items, 1):
        L += ["", "-" * width, "ITEM %d of %d: %s" % (i, len(items), it["title"].upper()), "-" * width]
        if it["skills"]:
            c = it["coverage"]
            L.append(W("Team: tested with %s; %d check(s) covered, %d not applicable, %d not yet run." % (
                ", ".join(it["skills"]), c["covered"], c["not_applicable"], c["not_run"])))
        for f in it["verified"]:
            L.append(W("- [%s] %s  (%s/%s)" % (f["severity"], f["title"], f["skill"], f["id"]), "  "))
        for sk, m in it["metrics"]:
            L.append(W("- score %s: %s %s, budget %s %s, %s" % (m["id"], verdict._n(m["value"]), m["unit"], m["op"],
                                                             verdict._n(m["budget"]),
                                                             "in budget" if ledger.metric_passes(m) else "OVER BUDGET"), "  "))
        if it["speakers"]:
            L.append("Board:")
            for sp in it["speakers"]:
                L += speak(sp["seat"], sp["risks"])
        L.append(W("Outcome: %s. %s" % (it["outcome"].upper(), " ".join(it["reasons"]))))
    shown_by = {}
    for it in items:
        for sp in it["speakers"]:
            for rk in sp["risks"]:
                if rk.get("quote"):
                    shown_by.setdefault(sp["seat"], set()).add(rk["quote"])
    extra = []
    for seat in seats:
        a = answers.get(seat["id"])
        if not a:
            continue
        loose = next((g["risks"] for g in general if g["seat"] == seat["id"]), [])
        used = [q for q in a.get("quotes_used") or [] if q.get("id") not in shown_by.get(seat["id"], set())]
        if loose or used:
            extra += speak(seat["id"], loose, used)
    if extra:
        L += ["", "-" * width, "OTHER POINTS", "-" * width] + extra
    qs = []
    for seat in seats:
        for i, q in enumerate((answers.get(seat["id"]) or {}).get("questions") or []):
            ans = qa.get(seat["id"], {}).get(str(i))
            qs.append(W("%s asks: %s" % (seat["short"], q), "  "))
            qs.append(W("Team: %s" % (ans or "no answer recorded"), "    "))
    if qs:
        L += ["", "-" * width, "QUESTIONS", "-" * width] + qs
    L += ["", "-" * width, "THE VOTE", "-" * width]
    for seat in seats:
        a = answers.get(seat["id"])
        L.append(W("%s: %s. %s" % (seat["short"], VOTE_LABEL[a["vote"]].upper() if a else "DID NOT ANSWER",
                                    a["summary"] if a else ""), "  "))
    L += ["", "=" * width, "DECISION: %s" % dec["decision"].upper(), "=" * width]
    for x in dec["reasons"]:
        L.append(W("- %s" % x))
    top = dec["conditions"][:8]
    if top:
        L += ["", "Top actions:"]
        for c in top:
            L.append(W("- %s (owner %s)" % (c["action"], c["owner"].upper()), "  "))
        if len(dec["conditions"]) > len(top):
            L.append("  ... %d more in the minutes" % (len(dec["conditions"]) - len(top)))
    L.append("")
    return "\n".join(L)


# ------------------------------------------------------------- commands

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def cmd_seats(a):
    seats = load_seats()
    if a.json:
        print(json.dumps([{k: s[k] for k in ("id", "title", "short", "skills", "veto")} for s in seats], indent=2))
        return 0
    for s in seats:
        print("  %-6s %-36s veto: %s" % (s["id"], s["title"], ", ".join(s["veto"]) or "none"))
    return 0


def cmd_brief(a):
    if not os.path.isdir(ledger.root(a.dir)):
        raise BoardError("no proof/ folder. Run /proof-plan and some testing skills first.")
    seats = _pick(load_seats(), a.seats)
    r, refs = evidence(a.dir)
    if r.get("target") is None:
        raise BoardError("no target.json; run /proof-plan first so the board knows what it is judging.")
    fp = fingerprint(r)
    bdir = _board_dir(a.dir)
    meta_path = os.path.join(bdir, "board.json")
    old_fp = None
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            old_fp = json.load(fh).get("fingerprint")
    ans_dir = os.path.join(bdir, "answers")
    if os.path.isdir(ans_dir) and not (a.keep_answers and old_fp == fp):
        removed = 0
        for n in os.listdir(ans_dir):
            if n.endswith(".json"):
                os.remove(os.path.join(ans_dir, n))
                removed += 1
        qa = os.path.join(bdir, "qa.json")
        if os.path.exists(qa):
            os.remove(qa)
        if removed:
            print("Cleared %d answer(s) from an earlier meeting (the board judges the current evidence)." % removed)
    triggered = vetoes(r, seats)
    pres = presentation(r, triggered)
    _write(os.path.join(bdir, "presentation.md"), pres)
    personas = load_personas()
    paths = []
    for seat in seats:
        p = os.path.join(bdir, "briefs", seat["id"] + ".md")
        _write(p, seat_brief(seat, pres, os.path.abspath(_answer_path(a.dir, seat["id"])), triggered,
                             personas.get(seat["id"])))
        paths.append(p)
    os.makedirs(ans_dir, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump({"fingerprint": fp, "seats": [s["id"] for s in seats], "created": ledger.now()}, fh, indent=2)
    print("%s presentation written to %s" % (ledger.OK, os.path.join(bdir, "presentation.md")))
    if triggered:
        print("The evidence already triggers %d veto(es); the board will hold whatever the votes say." % len(triggered))
    print("\nConvene the board: send ONE message with one Agent call per director "
          "(subagent_type general-purpose, prompt 'Read <brief> and follow it exactly. It is your whole brief.'):")
    for p in paths:
        print("  " + p)
    print("\nThen: board.py check, board.py tally")
    return 0


def cmd_check(a):
    meta = _load_meta(a.dir)
    r, refs = evidence(a.dir)
    if fingerprint(r) != meta["fingerprint"]:
        print("%s the evidence changed since the brief. Run board.py brief again." % ledger.BAD)
        return 1
    good, bad = load_answers(a.dir, meta, refs)
    print("%d of %d directors answered validly." % (len(good), len(meta["seats"])))
    for sid, probs in bad.items():
        print("  %s %s: %s" % (ledger.BAD, sid, "; ".join(probs)))
    return 0 if not bad else 1


def cmd_save(a):
    meta = _load_meta(a.dir)
    if a.seat not in meta["seats"]:
        raise BoardError("%s is not on this board" % a.seat)
    _, refs = evidence(a.dir)
    try:
        obj = json.loads(sys.stdin.read())
    except ValueError as ex:
        raise BoardError("stdin is not valid JSON: %s" % ex)
    probs = validate_answer(obj, a.seat, refs, _quote_ids(load_personas(), a.seat))
    if probs:
        raise BoardError("refusing to save %s: %s" % (a.seat, "; ".join(probs)))
    _write(_answer_path(a.dir, a.seat), json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    print("%s saved %s" % (ledger.OK, a.seat))
    return 0


def cmd_answer(a):
    meta = _load_meta(a.dir)
    _, refs = evidence(a.dir)
    good, _ = load_answers(a.dir, meta, refs)
    if a.seat not in good:
        raise BoardError("%s has no valid answer to respond to" % a.seat)
    qs = good[a.seat].get("questions") or []
    if not 1 <= a.number <= len(qs):
        raise BoardError("%s asked %d question(s); pick 1 to %d" % (a.seat, len(qs), len(qs)))
    qa = _load_qa(a.dir)
    qa.setdefault(a.seat, {})[str(a.number - 1)] = a.text
    _write(os.path.join(_board_dir(a.dir), "qa.json"), json.dumps(qa, indent=2, ensure_ascii=False) + "\n")
    print("%s answered %s question %d" % (ledger.OK, a.seat.upper(), a.number))
    return 0


def _build(d):
    meta = _load_meta(d)
    seats = [s for s in load_seats() if s["id"] in meta["seats"]]
    r, refs = evidence(d)
    stale = fingerprint(r) != meta["fingerprint"]
    good, bad = load_answers(d, meta, refs)
    triggered = vetoes(r, seats)
    dec = decide(r, seats, good, triggered)
    qa = _load_qa(d)
    bdir = _board_dir(d)
    personas = load_personas()
    items, general, untested = agenda(r, seats, good, triggered)
    text = transcript(r, seats, good, dec, qa, items, general, untested, personas)
    _write(os.path.join(bdir, "minutes.md"), minutes_md(r, seats, good, dec, qa, items, general, untested))
    _write(os.path.join(bdir, "transcript.txt"), text)
    _write(os.path.join(bdir, "meeting.html"),
           meeting_html(r, seats, good, dec, qa, meta["fingerprint"], items, general, untested, personas))
    with open(os.path.join(bdir, "decision.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(dec, generated=ledger.now(), invalid=bad), fh, indent=2, ensure_ascii=False)
    return dec, bad, stale, bdir, text


def cmd_tally(a):
    dec, bad, stale, bdir, text = _build(a.dir)
    if not a.quiet:
        print(text)
    if stale:
        print("%s the evidence changed since the brief; the decision uses today's evidence but the directors "
              "judged the old one. Run board.py brief again for a fresh meeting." % ledger.BAD)
    print("Board decision: %s" % dec["decision"])
    for x in dec["reasons"]:
        print("  - %s" % x)
    print("Votes: %d go, %d go with conditions, %d hold, %d missing" % (
        len(dec["votes"]["go"]), len(dec["votes"]["go_with_conditions"]), len(dec["votes"]["hold"]), len(dec["missing"])))
    print("%d action(s). Minutes: %s  Meeting: %s" % (
        len(dec["conditions"]), os.path.join(bdir, "minutes.md"), os.path.join(bdir, "meeting.html")))
    return {GO: 0, GO_COND: 0, HOLD: 1}[dec["decision"]]


def cmd_meeting(a):
    _, _, _, bdir, _ = _build(a.dir)
    print("%s wrote %s" % (ledger.OK, os.path.join(bdir, "meeting.html")))
    return 0


def cmd_play(a):
    """Play the meeting in the terminal, line by line, with no input needed."""
    import time
    _, _, _, _, text = _build(a.dir)
    for line in text.splitlines():
        print(line)
        sys.stdout.flush()
        if a.speed > 0 and line.strip():
            time.sleep(min(2.5, 0.25 + len(line) / 260.0) / a.speed)
    return 0


def cmd_status(a):
    meta = _load_meta(a.dir)
    _, refs = evidence(a.dir)
    good, bad = load_answers(a.dir, meta, refs)
    for sid in meta["seats"]:
        if sid in good:
            print("  %s %-6s %s" % (ledger.OK, sid, VOTE_LABEL[good[sid]["vote"]]))
        else:
            print("  %s %-6s %s" % (ledger.BAD, sid, "; ".join(bad.get(sid, []))))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seats", help="list the board")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_seats)
    b = sub.add_parser("brief", help="write the presentation and one brief per director")
    b.add_argument("--seats", help="comma-separated subset, e.g. ceo,cto,cfo")
    b.add_argument("--keep-answers", action="store_true", help="keep answers if the evidence has not changed")
    b.set_defaults(func=cmd_brief)
    sub.add_parser("check", help="validate the directors' answers").set_defaults(func=cmd_check)
    sv = sub.add_parser("save", help="file one director's answer from stdin")
    sv.add_argument("seat")
    sv.set_defaults(func=cmd_save)
    an = sub.add_parser("answer", help="record the team's answer to a director's question")
    an.add_argument("seat")
    an.add_argument("number", type=int, help="question number, starting at 1")
    an.add_argument("text")
    an.set_defaults(func=cmd_answer)
    t = sub.add_parser("tally", help="compute the decision, print the meeting, write minutes and the meeting page")
    t.add_argument("--quiet", action="store_true", help="print only the decision, not the whole meeting")
    t.set_defaults(func=cmd_tally)
    sub.add_parser("meeting", help="rebuild the meeting page").set_defaults(func=cmd_meeting)
    pl = sub.add_parser("play", help="play the meeting in the terminal, paced, with no input needed")
    pl.add_argument("--speed", type=float, default=1.0, help="2 is twice as fast; 0 prints at once")
    pl.set_defaults(func=cmd_play)
    sub.add_parser("status", help="who has answered").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except BoardError as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
