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
     "evidence": ["refs exactly as written in square brackets in the presentation, e.g. proof-sec-web/some-id"]}
  ],
  "conditions": [
    {"action": "what must happen", "owner": "a seat id (ceo, cto, ciso, cfo, cmo, sales, coo, cpo, legal)",
     "by": "before launch" or "within 30 days" or a date}
  ],
  "questions": ["a question for the presenting team, if you have one"]
}"""


def seat_brief(seat, pres, answer_path, seat_vetoes):
    L = ["You are the %s (%s) on the board of directors. The team is presenting their product to the board "
         "and asking whether it can launch." % (seat["title"], seat["short"]), "",
         "== YOUR ROLE ==", seat["lens"], ""]
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


def validate_answer(obj, seat_id, refs):
    problems = []
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


def load_answers(d, meta, refs):
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
        problems = validate_answer(obj, sid, refs)
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


# -------------------------------------------------------------- minutes

def minutes_md(r, seats, answers, dec, qa):
    t = r.get("target") or {}
    L = ["# Board minutes: %s" % (t.get("name") or "the product"), "",
         "**Decision: %s**" % dec["decision"], ""]
    for x in dec["reasons"]:
        L.append("- %s" % x)
    L += ["", "Proof verdict: %s. Votes: %d go, %d go with conditions, %d hold%s." % (
        dec["verdict"], len(dec["votes"]["go"]), len(dec["votes"]["go_with_conditions"]), len(dec["votes"]["hold"]),
        (", %d did not answer" % len(dec["missing"])) if dec["missing"] else ""), ""]
    for seat in seats:
        a = answers.get(seat["id"])
        L.append("## %s (%s): %s" % (seat["title"], seat["short"], VOTE_LABEL[a["vote"]] if a else "did not answer"))
        if not a:
            L.append("")
            continue
        L.append("")
        L.append(a["summary"])
        for rk in a.get("risks") or []:
            L.append("- Risk (%s): %s %s" % (rk["severity"], rk["risk"],
                                             " ".join("[%s]" % e for e in rk.get("evidence") or [])))
        for i, q in enumerate(a.get("questions") or []):
            ans = qa.get(seat["id"], {}).get(str(i))
            L.append("- Question: %s" % q)
            L.append("  - Answer: %s" % (ans or "not yet answered"))
        L.append("")
    L.append("## Actions")
    L.append("")
    for c in dec["conditions"]:
        src = ("evidence [%s]" % c["ref"]) if c["from"] == "evidence" else "raised by %s" % ", ".join(
            x.upper() for x in c["raised_by"])
        L.append("- [ ] %s (owner: %s, %s; %s)" % (c["action"], c["owner"].upper(), c["by"], src))
    return "\n".join(L) + "\n"


# --------------------------------------------------------- meeting page

def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


MEETING_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5d6675;--line:#dfe3ea;--go:#1a7f37;--cond:#9a6700;--hold:#cf222e;--accent:#3355cc}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;--muted:#8d96a0;--line:#30363d;--accent:#7c9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,Segoe UI,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0}h2{font-size:18px;margin:36px 0 12px}h3{margin:0;font-size:16px}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
.badge{display:inline-block;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600;color:#fff}
.go{background:var(--go)}.go_with_conditions{background:var(--cond)}.hold{background:var(--hold)}.none{background:#6e7781}
.decision{border-radius:12px;padding:20px;color:#fff;margin:16px 0}
.decision .big{font-size:28px;font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.kpi .n{font-size:24px;font-weight:700}
.room{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.seat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;cursor:pointer;text-align:left;color:inherit;font:inherit}
.seat:hover,.seat:focus{border-color:var(--accent);outline:none}
.av{display:inline-flex;align-items:center;justify-content:center;border-radius:16px;background:rgba(127,127,127,.08);padding:4px;margin-bottom:8px;flex:none}
.av svg{display:block}
.statement{display:block}.hidden{display:none !important}
.statement.reveal{animation:fade .5s ease}@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.risk{border-left:3px solid var(--line);padding:2px 0 2px 10px;margin:6px 0}
.risk.high{border-color:var(--hold)}.risk.medium{border-color:var(--cond)}.risk.low{border-color:#6e7781}
code{background:rgba(127,127,127,.15);padding:1px 5px;border-radius:4px;font-size:12px}
a.ref{text-decoration:none}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
button.ctl{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit}
button.ctl.on{border-color:var(--accent);color:var(--accent)}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--line)}
.bar span{display:block;height:100%}
.actions label{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--line)}
.actions label.done span{text-decoration:line-through;color:var(--muted)}
.qa{margin:6px 0 6px 0;padding:8px 10px;border-radius:8px;background:rgba(127,127,127,.08)}
details summary{cursor:pointer}
"""

MEETING_JS = """
(function(){
  var order = JSON.parse(document.getElementById('order').textContent);
  var stmts = order.map(function(id){return document.getElementById('st-'+id);});
  var replayBtn = document.getElementById('replay'), nextBtn = document.getElementById('next'), allBtn = document.getElementById('showall');
  var idx = order.length;
  function showUpTo(n){ stmts.forEach(function(el,i){ if(!el) return; el.classList.toggle('hidden', i>=n); if(i===n-1){el.classList.remove('reveal'); void el.offsetWidth; el.classList.add('reveal'); el.scrollIntoView({behavior:'smooth',block:'start'});} });
    nextBtn.textContent = n>=order.length ? 'All directors have spoken' : 'Next director ('+(n+1)+' of '+order.length+')'; nextBtn.disabled = n>=order.length; idx=n; }
  replayBtn.addEventListener('click', function(){ document.getElementById('decision-block').classList.add('hidden'); showUpTo(0); document.getElementById('meeting').scrollIntoView({behavior:'smooth'}); });
  nextBtn.addEventListener('click', function(){ showUpTo(idx+1); if(idx>=order.length){ var d=document.getElementById('decision-block'); d.classList.remove('hidden'); d.classList.add('reveal'); } });
  allBtn.addEventListener('click', function(){ showUpTo(order.length); document.getElementById('decision-block').classList.remove('hidden'); });
  document.querySelectorAll('.seat').forEach(function(b){ b.addEventListener('click', function(){ var id=b.getAttribute('data-seat'); var i=order.indexOf(id); if(i>=idx) showUpTo(i+1); var el=document.getElementById('st-'+id); if(el) el.scrollIntoView({behavior:'smooth',block:'start'}); }); });
  document.querySelectorAll('[data-filter]').forEach(function(b){ b.addEventListener('click', function(){ var f=b.getAttribute('data-filter');
    document.querySelectorAll('[data-filter]').forEach(function(x){x.classList.toggle('on', x===b);});
    stmts.forEach(function(el){ if(!el) return; var v=el.getAttribute('data-vote'); el.style.display = (f==='all'||v===f)?'':'none'; }); }); });
  var key='proof-board-actions-'+document.body.getAttribute('data-fp');
  var saved={}; try{ saved=JSON.parse(localStorage.getItem(key)||'{}'); }catch(e){ saved={}; }
  document.querySelectorAll('.actions input[type=checkbox]').forEach(function(cb){ var id=cb.getAttribute('data-id');
    if(saved[id]){ cb.checked=true; cb.parentNode.classList.add('done'); }
    cb.addEventListener('change', function(){ cb.parentNode.classList.toggle('done', cb.checked); saved[id]=cb.checked;
      try{ localStorage.setItem(key, JSON.stringify(saved)); }catch(e){}
      var done=document.querySelectorAll('.actions input:checked').length, all=document.querySelectorAll('.actions input').length;
      document.getElementById('actprog').textContent = done+' of '+all+' done'; }); });
  var done=document.querySelectorAll('.actions input:checked').length, all=document.querySelectorAll('.actions input').length;
  var p=document.getElementById('actprog'); if(p) p.textContent = done+' of '+all+' done';
})();
"""


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


def _ref_link(ref):
    anchor = "ev-" + re.sub(r"[^a-z0-9-]+", "-", ref.lower())
    return '<a class="ref" href="#%s"><code>%s</code></a>' % (anchor, e(ref))


def meeting_html(r, seats, answers, dec, qa, fp):
    t = r.get("target") or {}
    name = t.get("name") or "The product"
    sev = {s: sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] == s) for s in ledger.SEVERITIES}
    metrics = [m for s in r["scores"] for m in s["metrics"]]
    passing = sum(1 for m in metrics if ledger.metric_passes(m))
    covered = sum(1 for c in r["coverage"] for ch in c["checks"] if ch["status"] == "covered")
    total_checks = sum(len(c["checks"]) for c in r["coverage"])
    dec_class = {GO: "go", GO_COND: "go_with_conditions", HOLD: "hold"}[dec["decision"]]
    V = {"Ship": "go", "Not yet": "go_with_conditions", "Incomplete": "hold"}
    L = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         "<title>%s - Board meeting</title><style>%s</style></head>" % (e(name), MEETING_CSS),
         '<body data-fp="%s"><div class="wrap">' % e(fp),
         '<div class="muted">Board meeting &middot; %s</div>' % e(ledger.now()[:10]),
         "<h1>%s</h1>" % e(name),
         '<p class="muted">The team presented the product and its Proof evidence to the board. '
         'Each director judged it through their own role.</p>']

    # The presentation
    L.append("<h2>1. The presentation</h2>")
    L.append('<div class="card"><div>Proof verdict <span class="badge %s">%s</span></div>' % (V[r["verdict"]], e(r["verdict"])))
    bits = []
    if t.get("kind"):
        bits.append("Kind: %s" % e(t["kind"]))
    if t.get("urls"):
        bits.append("Where: %s" % e(", ".join(t["urls"])))
    if t.get("done_means"):
        bits.append("Bar for done: %s" % e(t["done_means"]))
    if bits:
        L.append('<p class="muted">%s</p>' % " &middot; ".join(bits))
    L.append("</div>")
    L.append('<div class="kpis">')
    for label, n in (("Critical", sev["critical"]), ("High", sev["high"]), ("Medium", sev["medium"]), ("Low", sev["low"])):
        L.append('<div class="kpi"><div class="muted">%s findings</div><div class="n">%d</div></div>' % (label, n))
    L.append('<div class="kpi"><div class="muted">Scores in budget</div><div class="n">%d/%d</div></div>' % (passing, len(metrics)))
    L.append('<div class="kpi"><div class="muted">Checks covered</div><div class="n">%d/%d</div></div>' % (covered, total_checks))
    L.append("</div>")
    skills_run = sorted(set([c["skill"] for c in r["coverage"]] + [s["skill"] for s in r["scores"]] + [f["skill"] for f in r["findings"]]))
    L.append('<details class="card"><summary>What was tested (%d skills)</summary><p>%s</p></details>' % (
        len(skills_run), ", ".join("<code>%s</code>" % e(s) for s in skills_run)))

    # The room
    L.append("<h2>2. The room</h2>")
    L.append('<div class="room">')
    for seat in seats:
        a = answers.get(seat["id"])
        vote = a["vote"] if a else "none"
        L.append('<button class="seat" data-seat="%s"><div class="av">%s</div><h3>%s</h3><div class="muted">%s</div>'
                 '<div style="margin-top:6px"><span class="badge %s">%s</span></div></button>' % (
                     e(seat["id"]), avatar_svg(seat["id"], 84, "r"),
                     e(seat["short"]), e(seat["title"]), vote, e(VOTE_LABEL.get(vote, "Did not answer"))))
    L.append("</div>")

    # The meeting
    L.append('<h2 id="meeting">3. The meeting</h2>')
    L.append('<div class="controls"><button class="ctl" id="replay">Replay the meeting</button>'
             '<button class="ctl" id="next" disabled>All directors have spoken</button>'
             '<button class="ctl" id="showall">Show everyone</button>'
             '<span class="muted" style="align-self:center">Filter:</span>'
             '<button class="ctl on" data-filter="all">All</button><button class="ctl" data-filter="go">Go</button>'
             '<button class="ctl" data-filter="go_with_conditions">Conditions</button><button class="ctl" data-filter="hold">Hold</button></div>')
    order = []
    for seat in seats:
        a = answers.get(seat["id"])
        order.append(seat["id"])
        vote = a["vote"] if a else "none"
        L.append('<div class="card statement" id="st-%s" data-vote="%s">' % (e(seat["id"]), vote))
        L.append('<div style="display:flex;gap:12px;align-items:center"><div class="av">%s</div><div><h3>%s</h3>'
                 '<div class="muted">%s</div></div><div style="margin-left:auto"><span class="badge %s">%s</span></div></div>' % (
                     avatar_svg(seat["id"], 56, "s"), e(seat["title"]),
                     e(", ".join(seat["veto"]) and "Holds a veto over: " + ", ".join(seat["veto"]) or "No veto"),
                     vote, e(VOTE_LABEL.get(vote, "Did not answer"))))
        if not a:
            L.append('<p class="muted">This director did not give a valid answer.</p></div>')
            continue
        L.append('<p>&ldquo;%s&rdquo;</p>' % e(a["summary"]))
        if a.get("confidence") is not None:
            L.append('<div class="muted">Confidence %d%%</div>' % int(round(float(a["confidence"]) * 100)))
        for rk in a.get("risks") or []:
            refs = " ".join(_ref_link(x) for x in rk.get("evidence") or []) or '<span class="muted">judgement call</span>'
            L.append('<div class="risk %s"><strong>%s risk:</strong> %s<div>%s</div></div>' % (
                e(rk["severity"]), e(rk["severity"].capitalize()), e(rk["risk"]), refs))
        for c in a.get("conditions") or []:
            L.append('<div class="muted">Condition: %s (owner %s, %s)</div>' % (
                e(c["action"]), e((c.get("owner") or seat["id"]).upper()), e(c.get("by") or "before launch")))
        for i, q in enumerate(a.get("questions") or []):
            ans = qa.get(seat["id"], {}).get(str(i))
            L.append('<div class="qa"><strong>Q:</strong> %s<br><strong>A:</strong> %s</div>' % (
                e(q), e(ans) if ans else '<span class="muted">not yet answered by the team</span>'))
        L.append("</div>")
    L.append('<script type="application/json" id="order">%s</script>' % json.dumps(order))

    # The vote and decision
    nv = {k: len(v) for k, v in dec["votes"].items()}
    tot = max(1, sum(nv.values()) + len(dec["missing"]))
    L.append('<div id="decision-block">')
    L.append("<h2>4. The vote</h2>")
    L.append('<div class="card"><div class="bar">')
    for k, color in (("go", "var(--go)"), ("go_with_conditions", "var(--cond)"), ("hold", "var(--hold)")):
        if nv[k]:
            L.append('<span style="width:%.1f%%;background:%s" title="%s"></span>' % (100.0 * nv[k] / tot, color, VOTE_LABEL[k]))
    if dec["missing"]:
        L.append('<span style="width:%.1f%%;background:#6e7781" title="Did not answer"></span>' % (100.0 * len(dec["missing"]) / tot))
    L.append("</div>")
    L.append('<p class="muted">%d go &middot; %d go with conditions &middot; %d hold%s</p></div>' % (
        nv["go"], nv["go_with_conditions"], nv["hold"],
        (" &middot; %d did not answer" % len(dec["missing"])) if dec["missing"] else ""))
    L.append("<h2>5. The decision</h2>")
    L.append('<div class="decision %s"><div class="big">%s</div>' % (dec_class, e(dec["decision"])))
    L.append("<ul>%s</ul>" % "".join("<li>%s</li>" % e(x) for x in dec["reasons"]))
    L.append('<div style="opacity:.85;font-size:13px">Computed from the evidence and the votes, not written by hand.</div></div>')
    L.append("</div>")

    # Actions
    L.append('<h2>6. Actions <span class="muted" id="actprog" style="font-size:14px"></span></h2>')
    if not dec["conditions"]:
        L.append('<div class="card"><p class="muted">No actions.</p></div>')
    groups = [("Blockers from the evidence", [c for c in dec["conditions"] if c["from"] == "evidence"])]
    owners = []
    for c in dec["conditions"]:
        if c["from"] != "evidence" and c["owner"] not in owners:
            owners.append(c["owner"])
    titles = {s["id"]: s["title"] for s in seats}
    for o in owners:
        groups.append(("Owned by %s" % titles.get(o, o.upper()), [c for c in dec["conditions"]
                                                                 if c["from"] != "evidence" and c["owner"] == o]))
    i = 0
    for title, items in groups:
        if not items:
            continue
        L.append('<div class="card actions"><h3>%s <span class="muted">(%d)</span></h3>' % (e(title), len(items)))
        for c in items:
            if c["from"] == "evidence":
                src = "from the evidence " + _ref_link(c["ref"])
            else:
                src = "raised by %s" % e(", ".join(x.upper() for x in c["raised_by"]))
            L.append('<label><input type="checkbox" data-id="a%d"><span><strong>%s</strong><br>'
                     '<span class="muted">%s &middot; %s</span></span></label>' % (i, e(c["action"]), e(c["by"]), src))
            i += 1
        L.append("</div>")

    # Evidence appendix
    L.append("<h2>Appendix: the evidence</h2>")
    L.append('<div class="card">')
    for f in sorted([f for f in r["findings"] if f["status"] == "verified"], key=lambda f: verdict.RANK[f["severity"]]):
        ref = "%s/%s" % (f["skill"], f["id"])
        L.append('<div id="ev-%s" class="risk %s"><code>%s</code> <strong>%s</strong> %s</div>' % (
            re.sub(r"[^a-z0-9-]+", "-", ref.lower()), "high" if f["severity"] in ("critical", "high") else f["severity"],
            e(ref), e(f["severity"]), e(f["title"])))
    for s in r["scores"]:
        for m in s["metrics"]:
            ref = "%s/score:%s" % (s["skill"], m["id"])
            L.append('<div id="ev-%s" class="risk %s"><code>%s</code> %s %s against %s %s</div>' % (
                re.sub(r"[^a-z0-9-]+", "-", ref.lower()), "low" if ledger.metric_passes(m) else "high",
                e(ref), e(verdict._n(m["value"])), e(m["unit"]), e(m["op"]), e(verdict._n(m["budget"]))))
    for c in r["coverage"]:
        for ch in c["checks"]:
            ref = "%s/coverage:%s" % (c["skill"], ch["id"])
            L.append('<span id="ev-%s"></span>' % re.sub(r"[^a-z0-9-]+", "-", ref.lower()))
    L.append("</div>")
    L.append('<p class="muted">Directors are simulated by sub-agents reading the same evidence. Their judgement is '
             'advice; the evidence and the computed decision are the record.</p>')
    L.append("</div><script>%s</script></body></html>" % MEETING_JS)
    return "\n".join(L) + "\n"


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
    paths = []
    for seat in seats:
        p = os.path.join(bdir, "briefs", seat["id"] + ".md")
        _write(p, seat_brief(seat, pres, os.path.abspath(_answer_path(a.dir, seat["id"])), triggered))
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
    probs = validate_answer(obj, a.seat, refs)
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
    _write(os.path.join(bdir, "minutes.md"), minutes_md(r, seats, good, dec, qa))
    _write(os.path.join(bdir, "meeting.html"), meeting_html(r, seats, good, dec, qa, meta["fingerprint"]))
    with open(os.path.join(bdir, "decision.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(dec, generated=ledger.now(), invalid=bad), fh, indent=2, ensure_ascii=False)
    return dec, bad, stale, bdir


def cmd_tally(a):
    dec, bad, stale, bdir = _build(a.dir)
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
    _, _, _, bdir = _build(a.dir)
    print("%s wrote %s" % (ledger.OK, os.path.join(bdir, "meeting.html")))
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
    sub.add_parser("tally", help="compute the decision; write minutes and the meeting page").set_defaults(func=cmd_tally)
    sub.add_parser("meeting", help="rebuild the meeting page").set_defaults(func=cmd_meeting)
    sub.add_parser("status", help="who has answered").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except BoardError as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
