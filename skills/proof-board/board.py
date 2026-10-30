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
     "quote": "almost always empty; see the quoting rule in your brief"}
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
        L.append("Quoting rule: most answers quote nothing. Quote only when %s's exact words are the reason "
                 "for a high-severity call that cites evidence, so the board would weigh it differently without "
                 "the quote. Never quote for colour, to open or close, or because a quote is loosely related. "
                 "At most one quote in your whole answer: put its id in that risk's \"quote\" field."
                 % persona["name"].split()[-1])
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
        elif qid and (rk.get("severity") != "high" or not ev):
            problems.append("risk %d quotes on a %s point without evidence or below high severity; quote only "
                            "when the words decide a high, evidenced call" % (i + 1, rk.get("severity")))
    quoted = [rk for rk in obj.get("risks") or [] if isinstance(rk, dict) and rk.get("quote")]
    if len(quoted) > 1:
        problems.append("%d quotes; use at most one, on the call it decides" % len(quoted))
    if obj.get("quotes_used"):
        problems.append("quotes_used is not accepted; attach the one quote that decides a high risk to that risk, or none")
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
     ["proof-web-e2e", "proof-mobile-e2e", "proof-mobile-explore", "proof-explore", "proof-compat", "proof-visual",
      "proof-design-match", "proof-pwa"]),
    ("reliability", "Reliability and performance",
     ["proof-api", "proof-load", "proof-web-perf", "proof-watch", "proof-llm-device"]),
    ("security", "Security",
     ["proof-sec-web", "proof-sec-secrets", "proof-sec-deps", "proof-sec-diff", "proof-mobile-audit",
      "proof-react-native"]),
    ("infra", "Infrastructure and deployment", ["proof-infra"]),
    ("privacy", "Privacy and legal", ["proof-privacy"]),
    ("reach", "Accessibility, languages and reach",
     ["proof-a11y", "proof-mobile-a11y", "proof-i18n", "proof-seo", "proof-geo"]),
    ("ai", "AI features", ["proof-ai"]),
    ("quality", "Code health and test strength", ["proof-mutation", "proof-restraint", "proof-slop"]),
    ("finance", "Finance", ["proof-finance"]),
    ("launch", "Launch readiness and operations", ["proof-release", "proof-email"]),
]
SKILL_AREA = {sk: aid for aid, _, sks in AREAS for sk in sks}
# One word per step for the meeting page's progress bar; the full title is the tooltip.
SHORT = {"flows": "Flows", "reliability": "Performance", "security": "Security", "infra": "Infra",
         "privacy": "Privacy", "reach": "Reach", "ai": "AI", "quality": "Code", "finance": "Finance",
         "launch": "Launch"}
SHORT_BY_TITLE = dict((title, SHORT[aid]) for aid, title, _ in AREAS)
SHORT_BY_TITLE.update({"Other points": "Other", "The vote": "Vote"})
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
  --canvas:#f3f1ec;--panel:#ffffff;--ink:#14171c;--soft:#3d434c;--muted:#6f747c;--line:#e2ded6;--line2:#d2ccc1;
  --accent:#c4472b;--quote:#f7f3ea;
  --blocker:#b3261e;--concerns:#a15c00;--clear:#1f6f4a;--none:#7a7f86;
  --display:"Bahnschrift","DIN Alternate","DIN 2014","Avenir Next Condensed","Roboto Condensed","Arial Narrow",sans-serif;
  --text:"Sitka Text","Charter","Bitstream Charter","Iowan Old Style","Source Serif Pro",Cambria,Georgia,serif;
  --quote-font:"Sitka Display","Charter","Iowan Old Style",Cambria,Georgia,serif;
  --mono:"Cascadia Mono",ui-monospace,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --canvas:#111316;--panel:#191c20;--ink:#eceae5;--soft:#c9c6bf;--muted:#8f939a;--line:#262a30;--line2:#333840;
  --accent:#e3694c;--quote:#1f2226;--blocker:#ef7166;--concerns:#e3a33f;--clear:#5fb88a;--none:#8f939a;}}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);font:17px/1.6 var(--text)}
a{color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.top{position:sticky;top:0;z-index:3;background:var(--canvas);padding:18px clamp(16px,4vw,48px) 10px}
.brand{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.brand h1{font:600 22px/1.2 var(--display);letter-spacing:.01em;margin:0}
.brand .meta{font:500 13px var(--display);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.segments{display:flex;gap:4px;margin-top:14px;list-style:none;padding:0}
.segments li{flex:1;min-width:0}
.segments button{all:unset;cursor:pointer;display:block;width:100%}
.segments .bar{display:block;height:4px;background:var(--line2);border-radius:2px;overflow:hidden}
.segments .bar i{display:block;height:100%;width:0;background:var(--ink)}
.segments li.done .bar i{width:100%}
.segments li.now .bar i{width:100%;background:var(--accent);animation:fill var(--dur,8s) linear}
@keyframes fill{from{width:0}to{width:100%}}
.segments .lab{display:block;font:500 11px/1.3 var(--display);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.segments li.done .lab{color:var(--soft)}
.segments li.now .lab{color:var(--accent);font-weight:700}
.segments button:hover .lab{color:var(--ink)}
@media (max-width:900px){.segments .lab{display:none}.segments li.now .lab{display:block;position:absolute;left:clamp(16px,4vw,48px);right:0}
  .segments{position:relative;padding-bottom:18px}}
main{max-width:860px;margin:0 auto;padding:12px clamp(16px,4vw,48px) 80px}
.step{display:none}.step.on{display:block}
.reveal{opacity:0;transform:translateY(8px);transition:opacity .45s ease,transform .45s ease}
.reveal.in{opacity:1;transform:none}
.kicker{font:600 12px var(--display);letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
h2{font:600 clamp(30px,5vw,46px)/1.05 var(--display);letter-spacing:-.005em;margin:8px 0 14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.pill{font:600 13px var(--display);letter-spacing:.08em;text-transform:uppercase;padding:4px 10px;border-radius:3px;color:#fff;background:var(--none)}
.pill.blocker{background:var(--blocker)}.pill.concerns{background:var(--concerns)}.pill.clear{background:var(--clear)}
.lede{font-size:19px;color:var(--soft);margin:0 0 18px;max-width:58ch}
.stats{display:flex;flex-wrap:wrap;gap:26px;margin:6px 0 22px}
.stat b{display:block;font:600 34px/1 var(--display)}
.stat span{font:500 12px var(--display);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.av{display:inline-flex;border-radius:50%;background:var(--quote);overflow:hidden;flex:none;align-self:start}
.av svg{display:block}
.team{border-top:2px solid var(--ink);padding-top:12px;margin:4px 0 20px}
.team .label{font:600 12px var(--display);letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.team ul{margin:8px 0 0;padding-left:18px}
.team li{margin:3px 0}
.sev{font:600 11px var(--display);letter-spacing:.08em;text-transform:uppercase;margin-right:6px}
.sev.critical,.sev.high{color:var(--blocker)}.sev.medium{color:var(--concerns)}.sev.low,.sev.info{color:var(--none)}
.speaker{display:grid;grid-template-columns:52px 1fr;gap:14px;padding:16px 0;border-top:1px solid var(--line)}
.speaker .name{font:600 16px var(--display);letter-spacing:.02em}
.speaker .name small{font:400 13px var(--display);color:var(--muted);letter-spacing:0;margin-left:6px}
.speaker .say{margin:4px 0 0}
blockquote{margin:10px 0 0;padding:12px 16px;background:var(--quote);border-left:3px solid var(--accent);font:italic 18px/1.5 var(--quote-font)}
blockquote footer{font:400 12px/1.4 var(--display);font-style:normal;color:var(--muted);margin-top:6px;letter-spacing:.02em}
blockquote footer a{color:var(--muted)}
details{margin-top:10px}
summary{cursor:pointer;font:500 13px var(--display);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"+ ";color:var(--accent)}
details[open] summary::before{content:"- "}
details .more{margin-top:8px;font-size:16px;color:var(--soft)}
.ref{font:12px var(--mono);color:var(--muted);word-break:break-all;text-decoration:none}
.outcome{margin-top:16px;font:500 15px var(--display);letter-spacing:.02em;color:var(--soft)}
.outcome b{text-transform:uppercase;letter-spacing:.08em}
.outcome.blocker b{color:var(--blocker)}.outcome.concerns b{color:var(--concerns)}.outcome.clear b{color:var(--clear)}
.small{font:400 14px var(--display);color:var(--muted);letter-spacing:.01em}
.areas{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.areas span{font:500 13px var(--display);padding:4px 10px;border:1px solid var(--line2)}
.areas span b{margin-left:6px}
.actions label{display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid var(--line);cursor:pointer}
.actions input{margin-top:6px;accent-color:var(--accent)}
.actions label.done span{text-decoration:line-through;color:var(--muted)}
.actions .owner{font:600 12px var(--display);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:16px 0 4px}
.replay{all:unset;cursor:pointer;font:600 13px var(--display);letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin-top:28px;display:inline-block}
.fine{max-width:860px;margin:0 auto;padding:0 clamp(16px,4vw,48px) 40px;font:400 12px/1.5 var(--display);color:var(--muted)}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes popin{0%{opacity:0;transform:scale(.6)}70%{opacity:1;transform:scale(1.08)}100%{opacity:1;transform:none}}
@keyframes slidel{from{opacity:0;transform:translateX(-28px)}to{opacity:1;transform:none}}
@keyframes slider{from{opacity:0;transform:translateX(28px)}to{opacity:1;transform:none}}
@keyframes turn{0%{transform:rotateY(0)}100%{transform:rotateY(180deg)}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes slam{0%{opacity:0;transform:scale(4) rotate(-16deg)}55%{opacity:1;transform:scale(.9) rotate(-7deg)}72%{transform:scale(1.06) rotate(-7deg)}100%{opacity:1;transform:scale(1) rotate(-7deg)}}
@keyframes bloom{0%{opacity:0;transform:scale(.2)}60%{opacity:1;transform:scale(1.12)}100%{opacity:1;transform:scale(1)}}
@keyframes shake{0%,100%{transform:none}15%{transform:translate(-10px,4px)}30%{transform:translate(9px,-5px)}45%{transform:translate(-7px,3px)}60%{transform:translate(5px,-2px)}75%{transform:translate(-3px,1px)}}
@keyframes ring{from{opacity:.7;transform:translate(-50%,-50%) scale(.3)}to{opacity:0;transform:translate(-50%,-50%) scale(2.6)}}
@keyframes ray{0%{opacity:0;transform:rotate(var(--r)) translateY(-40px) scaleY(.2)}40%{opacity:1}100%{opacity:0;transform:rotate(var(--r)) translateY(-190px) scaleY(1)}}
@keyframes draw{to{stroke-dashoffset:0}}
@keyframes flashbg{0%{background:var(--panel)}12%{background:var(--flash)}100%{background:var(--tint)}}
.step.on .late{animation:rise .5s 2s both}
.step.on .pop{animation:popin .5s calc(.5s + var(--i,0)*.12s) both}
.opening{padding:10px 0 4px}
.hero{font:700 clamp(36px,7vw,64px)/1 var(--display);letter-spacing:-.01em;display:block;margin:10px 0 18px}
.hero span{display:block;font:400 .45em/1.3 var(--text);font-style:italic;color:var(--soft);letter-spacing:0;margin-top:6px}
.going-in{display:flex;align-items:baseline;gap:12px;margin:0 0 14px}
.vword{font:700 22px var(--display);letter-spacing:.06em;text-transform:uppercase}
.vword.hold{color:var(--blocker)}.vword.go{color:var(--clear)}.vword.go_with_conditions{color:var(--concerns)}
.label-line{display:flex;align-items:center;gap:12px;font:600 12px var(--display);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:26px 0 12px}
.label-line::after{content:"";flex:1;height:1px;background:var(--line2)}
.roster{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.person{display:flex;gap:12px;align-items:center;background:var(--panel);border:1px solid var(--line);padding:10px 12px}
.step.on .person{animation:rise .5s calc(.9s + var(--i)*.14s) both}
.person .role{font:600 11px var(--display);letter-spacing:.1em;text-transform:uppercase;color:var(--accent)}
.person .full{font:600 18px/1.2 var(--display)}
.person .from{font:400 13px/1.35 var(--display);color:var(--muted)}
.agenda{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px}
.agenda li{font:500 14px var(--display);border:1px solid var(--line2);padding:6px 12px;background:var(--panel)}
.agenda li span{color:var(--accent);font-weight:700;margin-right:8px}
.step.on .agenda li{animation:popin .45s calc(2.2s + var(--i)*.12s) both}
.thread{display:flex;flex-direction:column;gap:10px;margin:8px 0 16px}
.ask,.reply{display:flex;gap:10px;align-items:flex-end;max-width:88%}
.reply{align-self:flex-end;flex-direction:row-reverse}
.bubble{background:var(--panel);border:1px solid var(--line);padding:10px 14px;font-size:16px;line-height:1.5}
.bubble b{display:block;font:600 12px var(--display);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.reply .bubble{background:var(--ink);color:var(--canvas);border-color:var(--ink)}
.reply .bubble b{color:inherit;opacity:.7}
.reply.none .bubble{background:transparent;color:var(--muted);border-style:dashed;border-color:var(--line2)}
.step.on .ask{animation:slidel .5s calc(.6s + var(--i)*1.1s) both}
.step.on .reply{animation:slider .5s calc(.6s + var(--i)*1.1s) both}
.ballots{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin:8px 0;perspective:900px}
.ballot{height:132px}
.flip{position:relative;height:100%;transform-style:preserve-3d}
.step.on .flip{animation:turn .7s cubic-bezier(.3,1.4,.5,1) calc(.5s + var(--i)*.45s) both}
.face{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:2px;border:1px solid var(--line);background:var(--panel);text-align:center;padding:8px}
.face .who{font:600 14px var(--display);letter-spacing:.04em}
.front{transform:rotateY(180deg);color:#fff;border:0;background:var(--none)}
.front.go{background:var(--clear)}.front.go_with_conditions{background:var(--concerns)}.front.hold{background:var(--blocker)}
.front .call{font:700 19px/1.1 var(--display);letter-spacing:.06em;text-transform:uppercase}
.front .small{color:rgba(255,255,255,.85)}
.tally{display:flex;height:10px;background:var(--line);margin:18px 0 10px;transform-origin:left}
.tally span{display:block;height:100%}
.step.on .tally{animation:grow .9s ease-out calc(.9s + var(--lag)*.45s) both}
.counts{display:flex;gap:28px;flex-wrap:wrap}
.step.on .counts{animation:rise .5s calc(1.5s + var(--lag)*.45s) both}
.step.on details.late[style]{animation-delay:calc(2s + var(--lag)*.45s)}
.count b{display:block;font:700 36px/1 var(--display)}
.count span{font:500 12px var(--display);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.count.go b{color:var(--clear)}.count.go_with_conditions b{color:var(--concerns)}.count.hold b{color:var(--blocker)}
.stage{--tint:var(--panel);--flash:var(--panel);--c:var(--ink);position:relative;overflow:hidden;min-height:clamp(260px,44vh,420px);
  display:flex;flex-direction:column;align-items:center;justify-content:center;margin:10px 0 22px;border:1px solid var(--line);background:var(--tint)}
.stage.hold{--tint:#fbe9e6;--flash:#f4b8ae;--c:var(--blocker)}
.stage.go{--tint:#e5f3eb;--flash:#b5e0c8;--c:var(--clear)}
.stage.go_with_conditions{--tint:#fbf0dc;--flash:#f3d9a4;--c:var(--concerns)}
.step.on .stage{animation:flashbg 1.6s ease-out .2s both}
.step.on .stage.hold{animation:flashbg 1.6s ease-out .2s both,shake .55s ease-out .75s}
.stamp{position:relative;z-index:2;font:800 clamp(72px,16vw,150px)/1 var(--display);letter-spacing:.02em;color:var(--c);
  border:8px double var(--c);padding:.02em .24em .06em;border-radius:6px}
.stage.hold .stamp{transform:rotate(-7deg)}
.step.on .stage.hold .stamp{animation:slam .6s cubic-bezier(.2,.8,.3,1.2) .3s both}
.step.on .stage.go .stamp,.step.on .stage.go_with_conditions .stamp{animation:bloom .7s cubic-bezier(.2,.9,.3,1.3) .4s both}
.stage:not(.hold) .stamp{border:0;padding:0}
.sub{position:relative;z-index:2;font:600 clamp(18px,3vw,26px) var(--display);letter-spacing:.08em;text-transform:uppercase;color:var(--c);margin-top:18px}
.step.on .sub{animation:rise .5s 1.1s both}
.rings i{position:absolute;left:50%;top:50%;width:260px;height:260px;border:3px solid var(--c);border-radius:50%;opacity:0}
.step.on .rings i{animation:ring 1.6s ease-out calc(.75s + var(--k,0)*.35s) 2 both}
.rings i:nth-child(2){--k:1}.rings i:nth-child(3){--k:2}
.rays i{position:absolute;left:50%;top:50%;width:4px;height:60px;margin-left:-2px;background:var(--c);transform-origin:50% 0;opacity:0}
.step.on .rays i{animation:ray 1.1s ease-out .7s both}
.tick{width:64px;height:64px;position:relative;z-index:2;margin-bottom:6px}
.tick path{fill:none;stroke:var(--c);stroke-width:5;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:60;stroke-dashoffset:60}
.step.on .tick path{animation:draw .5s ease-out 1s forwards}
@media (prefers-color-scheme:dark){.stage.hold{--tint:#2b1513;--flash:#5e1d17}.stage.go{--tint:#11251a;--flash:#1d4a31}
  .stage.go_with_conditions{--tint:#2a2112;--flash:#5a4214}}
@media (prefers-reduced-motion:reduce){.reveal{opacity:1;transform:none;transition:none}.segments li.now .bar i{animation:none}
  .step.on *{animation:none!important}.flip{transform:rotateY(180deg)}.tick path{stroke-dashoffset:0}.rings,.rays{display:none}}
@media print{.top{position:static}.step{display:block!important;margin-bottom:40px}.reveal{opacity:1!important;transform:none!important}
  details{display:block}details .more{display:block}}
"""

MEETING_JS = """
(function(){
  var steps = Array.prototype.slice.call(document.querySelectorAll('.step'));
  var segs = Array.prototype.slice.call(document.querySelectorAll('.segments li'));
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var cur = -1, timer = null, reading = false;
  function show(i){
    cur = i;
    steps.forEach(function(s, k){ s.classList.toggle('on', k === i); });
    var dur = parseInt(steps[i].getAttribute('data-dur'), 10) || 8000;
    segs.forEach(function(li, k){ li.classList.toggle('now', k === i); li.classList.toggle('done', k < i);
      if (k === i) { li.style.setProperty('--dur', (dur / 1000) + 's'); var b = li.querySelector('i');
        b.style.animation = 'none'; void b.offsetWidth; b.style.animation = ''; } });
    var parts = Array.prototype.slice.call(steps[i].querySelectorAll('.reveal'));
    if (reduce || document.hidden) { parts.forEach(function(p){ p.classList.add('in'); }); }
    else { parts.forEach(function(p, k){ p.classList.remove('in'); setTimeout(function(){ p.classList.add('in'); }, 150 + k * 260); }); }
    try { window.scrollTo(0, 0); } catch (e) {}
    return dur;
  }
  function next(){
    clearTimeout(timer);
    if (reading || cur >= steps.length - 1) return;
    var dur = show(cur + 1);
    if (cur < steps.length - 1) timer = setTimeout(next, dur);
  }
  segs.forEach(function(li, k){ li.querySelector('button').addEventListener('click', function(){
    clearTimeout(timer); reading = false; cur = k - 1; next(); }); });
  // Opening a dropdown holds the meeting on that step until every dropdown on it is closed again.
  document.querySelectorAll('details').forEach(function(d){ d.addEventListener('toggle', function(){
    var open = steps[cur] && steps[cur].querySelector('details[open]');
    if (open) { reading = true; clearTimeout(timer); }
    else if (reading) { reading = false; timer = setTimeout(next, 2500); } }); });
  var replay = document.getElementById('replay');
  if (replay) replay.addEventListener('click', function(){ clearTimeout(timer); reading = false; cur = -1; next(); });
  var key = 'proof-board-actions-' + document.body.getAttribute('data-fp');
  var saved = {}; try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) { saved = {}; }
  document.querySelectorAll('.actions input').forEach(function(cb){ var id = cb.getAttribute('data-id');
    if (saved[id]) { cb.checked = true; cb.parentNode.classList.add('done'); }
    cb.addEventListener('change', function(){ cb.parentNode.classList.toggle('done', cb.checked); saved[id] = cb.checked;
      try { localStorage.setItem(key, JSON.stringify(saved)); } catch (e) {} }); });
  next();
})();
"""


def _anchor(ref):
    return "ev-" + re.sub(r"[^a-z0-9-]+", "-", ref.lower())


def _ref_link(ref):
    return '<span class="ref">%s</span>' % e(ref)


def _short(text, n=115):
    """The first sentence, cut to n characters on a word boundary."""
    t = " ".join(str(text or "").split())
    m = re.search(r"^(.+?[.!?])(\s|$)", t)
    if m and len(m.group(1)) <= n:
        return m.group(1)
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def _quote_html(q, persona_name):
    if not q:
        return ""
    src = e(q.get("source", ""))
    if q.get("date"):
        src += ", " + e(q["date"])
    if q.get("url"):
        src = '<a href="%s" rel="noopener">%s</a>' % (e(q["url"]), src)
    return '<blockquote>&ldquo;%s&rdquo;<footer>%s &middot; %s</footer></blockquote>' % (e(q["text"]), e(persona_name), src)


def _speech(seat, persona, risks, suffix):
    """A director: the short form of their top points, the quote right under the one call it decides, if any;
    the full points in a dropdown."""
    qmap = dict((q["id"], q) for q in (persona or {}).get("quotes", []))
    pname = (persona or {}).get("name")
    out = ['<div class="speaker reveal"><div class="av">%s</div><div>' % avatar_svg(seat["id"], 52, suffix),
           '<div class="name">%s%s</div>' % (e(seat["short"]), ("<small>%s lens</small>" % e(pname)) if pname else "")]
    rank = dict((s, k) for k, s in enumerate(ledger.SEVERITIES))
    ordered = sorted(risks, key=lambda x: (not x.get("quote"), rank.get(x["severity"], 9)))
    for rk in ordered[:2]:
        out.append('<p class="say"><span class="sev %s">%s</span>%s</p>' % (e(rk["severity"]), e(rk["severity"]), e(_short(rk["risk"]))))
        if rk.get("quote") in qmap:
            out.append(_quote_html(qmap[rk["quote"]], pname))
    more = []
    for rk in risks:
        refs = ", ".join(_ref_link(x) for x in rk.get("evidence") or []) or "judgement call"
        more.append("<p>%s<br>%s</p>" % (e(rk["risk"]), refs))
    if more:
        out.append('<details><summary>Full points</summary><div class="more">%s</div></details>' % "".join(more))
    out.append("</div></div>")
    return "".join(out)


def _dur(speakers=0, base=6500):
    return min(16000, base + 1600 * speakers)


def meeting_html(r, seats, answers, dec, qa, fp, items, general, untested, personas=None):
    personas = personas or {}
    t = r.get("target") or {}
    name = t.get("name") or "The product"
    seat_by = dict((s["id"], s) for s in seats)
    vsev = [f for f in r["findings"] if f["status"] == "verified"]
    sev = dict((s, sum(1 for f in vsev if f["severity"] == s)) for s in ledger.SEVERITIES)
    metrics = [m for s in r["scores"] for m in s["metrics"]]
    passing = sum(1 for m in metrics if ledger.metric_passes(m))
    oclass = {BLOCKER: "blocker", CONCERNS: "concerns", CLEAR: "clear", NOT_COVERED: "none"}
    steps = []  # (label, dur, html)

    # Opening: who is presenting, the numbers, who is at the table
    vcls = {"Ship": "go", "Not yet": "hold"}.get(r["verdict"], "go_with_conditions")
    S = ['<div class="opening"><div class="kicker">Board meeting &middot; Opening</div>',
         '<h2 class="hero">%s <span>presents to the board</span></h2>' % e(name),
         '<div class="going-in"><span class="small">Proof verdict going in</span><b class="vword %s">%s</b></div>' % (vcls, e(r["verdict"])),
         '<div class="stats">']
    for k, (lab, n) in enumerate((("high or critical", sev["critical"] + sev["high"]), ("medium", sev["medium"]),
                                  ("low", sev["low"]))):
        S.append('<div class="stat pop" style="--i:%d"><b>%d</b><span>%s</span></div>' % (k, n, lab))
    S.append('<div class="stat pop" style="--i:3"><b>%d/%d</b><span>scores in budget</span></div></div></div>' % (passing, len(metrics)))
    S.append('<div class="label-line">At the table</div><div class="roster">')
    for k, seat in enumerate(seats):
        p = personas.get(seat["id"])
        S.append('<div class="person" style="--i:%d"><div class="av">%s</div><div><div class="role">%s</div>'
                 '<div class="full">%s</div><div class="from">%s</div></div></div>' % (
                     k, avatar_svg(seat["id"], 56, "o"), e(seat["title"]), e(p["name"]) if p else e(seat["short"]),
                     e(p.get("known_for", "")) if p else ""))
    S.append('</div><div class="label-line">Agenda</div><ol class="agenda">%s</ol>' % "".join(
        '<li style="--i:%d"><span>%02d</span>%s</li>' % (k, k + 1, e(it["title"])) for k, it in enumerate(items)))
    more = []
    if untested:
        more.append("<p><b>Not tested, so not discussed:</b> %s.</p>" % e(", ".join(untested)))
    if t.get("done_means"):
        more.append("<p><b>The team's bar for done:</b> %s</p>" % e(t["done_means"]))
    more.append("<p><b>Vetoes:</b> %s.</p>" % e("; ".join("%s over %s" % (s["short"], ", ".join(x.replace("proof-", "") for x in s["veto"]))
                                                              for s in seats if s["veto"])))
    S.append('<details class="late"><summary>Rules of the meeting</summary><div class="more">%s</div></details>' % "".join(more))
    steps.append(("Opening", 4000 + 450 * len(seats) + 400 * len(items), "".join(S)))

    # One step per audit area
    for i, it in enumerate(items, 1):
        S = ['<div class="kicker">Item %d of %d</div><h2>%s<span class="pill %s">%s</span></h2>' % (
            i, len(items), e(it["title"]), oclass[it["outcome"]], e(it["outcome"]))]
        hi = [f for f in it["verified"] if f["severity"] in ("critical", "high")]
        failing = [m for _, m in it["metrics"] if not ledger.metric_passes(m)]
        bits = []
        if it["verified"]:
            bits.append("%d finding%s (%d high or critical)" % (len(it["verified"]), "" if len(it["verified"]) == 1 else "s", len(hi)))
        if it["metrics"]:
            bits.append("%d score%s, %d over budget" % (len(it["metrics"]), "" if len(it["metrics"]) == 1 else "s", len(failing)))
        if it["missing"]:
            bits.append("required but not run: %s" % ", ".join(it["missing"]))
        S.append('<div class="team reveal"><div class="label">The team presents</div><p style="margin:6px 0 0">%s.</p>' % e(
            "; ".join(bits) if bits else "Covered, with nothing verified"))
        top = it["verified"][:3]
        if top:
            S.append("<ul>%s</ul>" % "".join('<li><span class="sev %s">%s</span>%s</li>' % (
                e(f["severity"]), e(f["severity"]), e(_short(f["title"], 90))) for f in top))
        more = []
        for f in it["verified"]:
            more.append('<p><span class="sev %s">%s</span>%s<br>%s</p>' % (e(f["severity"]), e(f["severity"]), e(f["title"]),
                                                                           _ref_link("%s/%s" % (f["skill"], f["id"]))))
        for sk, m in it["metrics"]:
            more.append("<p>%s: %s %s, budget %s %s (%s)</p>" % (e(m["id"]), e(verdict._n(m["value"])), e(m["unit"]), e(m["op"]),
                                                               e(verdict._n(m["budget"])),
                                                               "in budget" if ledger.metric_passes(m) else "over budget"))
        c = it["coverage"]
        if it["skills"]:
            more.append("<p>Tested with %s: %d covered, %d not applicable, %d not run.</p>" % (
                e(", ".join(it["skills"])), c["covered"], c["not_applicable"], c["not_run"]))
        if more:
            S.append('<details><summary>Everything the team showed</summary><div class="more">%s</div></details>' % "".join(more))
        S.append("</div>")
        for k, sp in enumerate(it["speakers"]):
            S.append(_speech(seat_by[sp["seat"]], personas.get(sp["seat"]), sp["risks"], "i%d%d" % (i, k)))
        if not it["speakers"]:
            S.append('<p class="small reveal">No director raised a point here.</p>')
        S.append('<p class="outcome %s reveal"><b>%s</b> &middot; %s</p>' % (
            oclass[it["outcome"]], e(it["outcome"]), e(it["reasons"][0] if it["reasons"] else "")))
        steps.append((it["title"], _dur(len(it["speakers"])), "".join(S)))

    # Points not tied to one audit
    extra, n_extra = [], 0
    for k, seat in enumerate(seats):
        loose = next((g["risks"] for g in general if g["seat"] == seat["id"]), [])
        if loose and seat["id"] in answers:
            extra.append(_speech(seat, personas.get(seat["id"]), loose, "g%d" % k))
            n_extra += 1
    if extra:
        steps.append(("Other points", _dur(n_extra, 5000),
                      '<div class="kicker">Other points</div><h2>Before the vote</h2>' + "".join(extra)))

    # Questions
    qs = []
    for seat in seats:
        for i, q in enumerate((answers.get(seat["id"]) or {}).get("questions") or []):
            ans = qa.get(seat["id"], {}).get(str(i))
            qs.append((seat, q, ans))
    answered = [x for x in qs if x[2]]
    shown_q = (answered + [x for x in qs if not x[2]])[:3]
    S = ['<div class="kicker">Questions</div><h2>The board asks</h2>',
         '<p class="lede reveal">%d question%s, %d answered by the team.</p><div class="thread">' % (
             len(qs), "" if len(qs) == 1 else "s", len(answered))]
    for k, (seat, q, ans) in enumerate(shown_q):
        p = personas.get(seat["id"])
        S.append('<div class="ask" style="--i:%d"><div class="av">%s</div><div class="bubble"><b>%s</b>%s</div></div>' % (
            2 * k, avatar_svg(seat["id"], 40, "q%d" % k), e(p["name"] if p else seat["short"]) + " &middot; " + e(seat["short"]), e(q)))
        S.append('<div class="reply%s" style="--i:%d"><div class="bubble"><b>The team</b>%s</div></div>' % (
            "" if ans else " none", 2 * k + 1, e(ans or "No answer recorded.")))
    S.append("</div>")
    rest = [x for x in qs if x not in shown_q]
    if rest:
        S.append('<details class="late"><summary>All %d questions</summary><div class="more">%s</div></details>' % (len(qs), "".join(
            "<p><b>%s:</b> %s<br><span class=\"small\">%s</span></p>" % (e(s["short"]), e(q), e("Team: " + a) if a else "No answer recorded")
            for s, q, a in rest)))
    steps.append(("Questions", 3500 + 2400 * len(shown_q), "".join(S)))

    # The vote
    nv = dict((k, len(v)) for k, v in dec["votes"].items())
    tot = max(1, sum(nv.values()) + len(dec["missing"]))
    # Ballots turn over one at a time; the tally fills once the last one is face up.
    S = ['<div class="kicker">The vote</div><h2>The board votes</h2><div class="ballots">']
    for k, seat in enumerate(seats):
        a = answers.get(seat["id"])
        vote = a["vote"] if a else "none"
        p = personas.get(seat["id"])
        S.append('<div class="ballot" style="--i:%d"><div class="flip">'
                 '<div class="face back"><div class="av">%s</div><div class="who">%s</div><div class="small">votes</div></div>'
                 '<div class="face front %s"><div class="who">%s</div><div class="call">%s</div><div class="small">%s</div></div>'
                 '</div></div>' % (k, avatar_svg(seat["id"], 44, "v%d" % k), e(seat["short"]), vote, e(seat["short"]),
                                   e(VOTE_LABEL.get(vote, "No answer")), e(p["name"]) if p else ""))
    lag = "--lag:%d" % len(seats)
    S.append('</div><div class="tally" style="%s">' % lag)
    for key, var in (("go", "--clear"), ("go_with_conditions", "--concerns"), ("hold", "--blocker")):
        if nv[key]:
            S.append('<span style="width:%.1f%%;background:var(%s)"></span>' % (100.0 * nv[key] / tot, var))
    if dec["missing"]:
        S.append('<span style="width:%.1f%%;background:var(--none)"></span>' % (100.0 * len(dec["missing"]) / tot))
    S.append('</div><div class="counts" style="%s">' % lag)
    for key, label in (("go", "go"), ("go_with_conditions", "with conditions"), ("hold", "hold")):
        S.append('<div class="count %s"><b>%d</b><span>%s</span></div>' % (key, nv[key], label))
    if dec["missing"]:
        S.append('<div class="count none"><b>%d</b><span>no answer</span></div>' % len(dec["missing"]))
    S.append("</div>")
    S.append('<details class="late" style="%s"><summary>What each director said</summary><div class="more">%s</div></details>' % (
        lag, "".join("<p><b>%s:</b> %s</p>" % (e(s["short"]), e(answers[s["id"]]["summary"])) for s in seats if s["id"] in answers)))
    steps.append(("The vote", 4500 + 450 * len(seats), "".join(S)))

    # The decision lands like a stamp; Hold shakes the stage, Go draws a tick.
    dcls = {GO: "go", GO_COND: "go_with_conditions", HOLD: "hold"}[dec["decision"]]
    n_block = sum(1 for c in dec["conditions"] if c["from"] == "evidence")
    stamp, sub = {
        "hold": ("HOLD!", "Do not proceed."),
        "go": ("GO", "Cleared to launch."),
        "go_with_conditions": ("GO", "With conditions: %d to meet first." % len(dec["conditions"])),
    }[dcls]
    S = ['<div class="kicker">The decision</div><div class="stage %s" role="img" aria-label="%s. %s">' % (dcls, e(dec["decision"]), e(sub)),
         '<div class="rings"><i></i><i></i><i></i></div>']
    if dcls != "hold":
        S.append('<div class="rays">%s</div>' % "".join('<i style="--r:%ddeg"></i>' % (k * 30) for k in range(12)))
        S.append('<svg class="tick" viewBox="0 0 52 52" aria-hidden="true"><path d="M14 27 l8 8 l16 -18"/></svg>')
    S.append('<div class="stamp">%s</div><div class="sub">%s</div></div>' % (e(stamp), e(sub)))
    S.append('<p class="lede late">%s</p>' % e(_short(dec["reasons"][0], 160) if dec["reasons"] else ""))
    S.append('<div class="areas late">%s</div>' % "".join('<span>%s<b class="sev %s">%s</b></span>' % (
        e(it["title"]), "high" if it["outcome"] == BLOCKER else ("medium" if it["outcome"] == CONCERNS else "low"),
        e(it["outcome"])) for it in items))
    if len(dec["reasons"]) > 1:
        S.append('<details class="late"><summary>Every reason</summary><div class="more">%s</div></details>' % "".join(
            "<p>%s</p>" % e(x) for x in dec["reasons"]))
    S.append('<p class="small late">Computed from the evidence and the votes, not written by hand%s.</p>' % (
        " &middot; %d blocker%s from the evidence" % (n_block, "" if n_block == 1 else "s") if n_block else ""))
    steps.append(("Decision", 9000, "".join(S)))

    # Actions
    groups = [("From the evidence", [c for c in dec["conditions"] if c["from"] == "evidence"])]
    owners = []
    for c in dec["conditions"]:
        if c["from"] != "evidence" and c["owner"] not in owners:
            owners.append(c["owner"])
    for o in owners:
        groups.append((seat_by[o]["short"] if o in seat_by else o.upper(),
                       [c for c in dec["conditions"] if c["from"] != "evidence" and c["owner"] == o]))
    S = ['<div class="kicker">Actions</div><h2>What happens next</h2>',
         '<p class="lede reveal">%d action%s. The blockers from the evidence come first.</p><div class="actions reveal">' % (
             len(dec["conditions"]), "" if len(dec["conditions"]) == 1 else "s")]
    n = 0
    first = [c for c in groups[0][1]][:6]
    for c in first:
        S.append('<label><input type="checkbox" data-id="a%d"><span>%s</span></label>' % (n, e(_short(c["action"], 110))))
        n += 1
    S.append("</div>")
    rest_html = []
    for title, group in groups:
        items_ = [c for c in group if c not in first]
        if not items_:
            continue
        rest_html.append('<div class="owner">%s</div>' % e(title))
        for c in items_:
            src = "raised by %s" % ", ".join(x.upper() for x in c.get("raised_by", [])) if c["from"] != "evidence" else c.get("ref", "")
            rest_html.append('<label><input type="checkbox" data-id="a%d"><span>%s <span class="small">(%s)</span></span></label>' % (
                n, e(c["action"]), e(src)))
            n += 1
    if rest_html:
        S.append('<details class="reveal"><summary>All actions by owner</summary><div class="more actions">%s</div></details>' %
                 "".join(rest_html))
    S.append('<button class="replay" id="replay" type="button">Watch again</button>')
    steps.append(("Actions", 8000, "".join(S)))

    segs = "".join('<li><button type="button" aria-label="Go to %s" title="%s"><span class="bar"><i></i></span>'
                   '<span class="lab">%s</span></button></li>' % (e(label), e(label), e(SHORT_BY_TITLE.get(label, label)))
                   for label, _, _ in steps)
    body = "".join('<section class="step" data-dur="%d" aria-label="%s">%s</section>' % (dur, e(label), html_)
                   for label, dur, html_ in steps)
    report_link = '<p class="fine"><a href="../report.html">Open the full Proof report</a></p>' if r.get("report_html") else ""
    return "\n".join([
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex">',
        "<title>%s: board meeting</title><style>%s</style></head>" % (e(name), MEETING_CSS),
        '<body data-fp="%s">' % e(fp),
        '<header class="top"><div class="brand"><h1>%s</h1><span class="meta">Board meeting &middot; %s</span></div>'
        '<ol class="segments" aria-label="Meeting progress">%s</ol></header>' % (e(name), e(ledger.now()[:10]), segs),
        "<main>%s</main>" % body,
        report_link,
        '<p class="fine">The directors are sub-agents applying the documented thinking of the people named, quoting them with '
        'sources. None is that person or speaks for them. The evidence and the computed decision are the record.</p>',
        "<script>%s</script></body></html>" % MEETING_JS,
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

    def speak(seat_id, risks):
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
    extra = []
    for seat in seats:
        loose = next((g["risks"] for g in general if g["seat"] == seat["id"]), [])
        if loose and seat["id"] in answers:
            extra += speak(seat["id"], loose)
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
    r["report_html"] = os.path.isfile(os.path.join(ledger.root(d), "report.html"))
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
    if a.open:
        open_page(os.path.join(bdir, "meeting.html"))
    return {GO: 0, GO_COND: 0, HOLD: 1}[dec["decision"]]


def open_page(path):
    return ledger.open_page(path, "the board meeting")


def cmd_meeting(a):
    _, _, _, bdir, _ = _build(a.dir)
    print("%s wrote %s" % (ledger.OK, os.path.join(bdir, "meeting.html")))
    if a.open:
        open_page(os.path.join(bdir, "meeting.html"))
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
    t.add_argument("--open", action="store_true", help="open the meeting page in the default browser")
    t.set_defaults(func=cmd_tally)
    m = sub.add_parser("meeting", help="rebuild the meeting page")
    m.add_argument("--open", action="store_true", help="open it in the default browser")
    m.set_defaults(func=cmd_meeting)
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
