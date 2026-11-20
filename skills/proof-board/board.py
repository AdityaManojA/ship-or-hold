#!/usr/bin/env python3
"""Present the project to a board of directors and compute the board's decision.

Standard library only, no network. The board sits on top of the Proof evidence:

    brief    turn the ledger into a presentation (the pitch the board hears) and one
             brief per seat (Investor, Growth, Builder, Operator)
    check    validate the seats' answers; every serious risk must cite evidence
    save     file one seat's answer from stdin
    answer   record the presenter's answer to a seat's question
    debate-brief  give every seat the other seats' answers for a second round
    debate-check  validate the debate files (challenges, concessions, final votes)
    tally    compute the decision (Go, Go with conditions, Hold) from the final votes
             and write the minutes, the one-page debate and an interactive meeting
             page (proof/board/meeting.html)
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
STAGES = ["solo", "team", "company"]
BUCKETS = [("do_today", "Do today"), ("freelancer", "Hire a freelancer"), ("professional", "Ask a CA or lawyer")]
BUCKET_LABEL = dict(BUCKETS)
BUCKET_HELP = [
    "- do_today: Do today. The owner can do it alone, today, with no money or help (rewrite a line of copy, "
    "change a price, write the policy page, send the message).",
    "- freelancer: Hire a freelancer. A paid hand for a few days: a developer for a bug or a security fix, a "
    "designer, a copywriter, a photographer.",
    "- professional: Ask a CA or lawyer. Anything a qualified accountant or lawyer must check or sign: tax, "
    "registrations, contracts, privacy terms, licences, claims that could bring a regulator.",
]
# Where an evidence blocker lands for a solo founder when no seat put it in a bucket.
PROFESSIONAL_SKILLS = {"proof-privacy", "proof-licenses"}
FREELANCER_SKILLS = {"proof-web-e2e", "proof-mobile-e2e", "proof-api", "proof-load", "proof-web-perf",
                     "proof-resilience", "proof-infra", "proof-restraint", "proof-visual", "proof-compat",
                     "proof-watch", "proof-mobile-audit", "proof-mobile-explore", "proof-react-native",
                     "proof-llm-device", "proof-voice", "proof-rag", "proof-ai", "proof-database", "proof-auth",
                     "proof-sec-web", "proof-sec-secrets", "proof-sec-deps", "proof-sec-diff", "proof-a11y",
                     "proof-mobile-a11y", "proof-design-match", "proof-pwa", "proof-i18n", "proof-store-ready"}

# A rule or saying attributed to a person: "X's rule", "X said", "as X put it", "according to X".
# Seat names and ordinary words that start a sentence are not people.
_CAP = r"[A-Z][a-z]+(?:[-\s][A-Z][a-z]+){0,2}"
_NAMED_RULE = [
    re.compile(r"\b(%s)['\u2019]s?\s+(?:own\s+)?(?:rule|law|maxim|principle|dictum|razor|motto|mantra|test|saying|"
               r"advice|creed|credo|doctrine|playbook|lesson)s?\b" % _CAP),
    re.compile(r"\b(%s)\s+(?:once\s+|famously\s+|always\s+|often\s+)?(?:said|wrote|argued|warned|"
               r"liked to say|used to say|would say|put it|taught|insisted)\b" % _CAP),
    re.compile(r"\b[Aa]s\s+(%s)\s+(?:once\s+|famously\s+)?(?:put it|said|wrote|would say|liked to say|"
               r"used to say|reminds us|taught)\b" % _CAP),
    re.compile(r"\b(?:[Aa]ccording to|[Ii]n the words of|[Tt]o quote|[Qq]uoting)\s+(%s)\b" % _CAP),
]
_NOT_PEOPLE = {"investor", "growth", "builder", "operator", "board", "panel", "advisory", "team", "the", "this",
               "that", "it", "he", "she", "they", "we", "you", "i", "one", "everyone", "someone", "nobody", "no",
               "buyers", "buyer", "customers", "customer", "users", "user", "founder", "owner", "proof", "ledger",
               "evidence", "report", "market", "page", "panelist", "seat", "data", "research", "a", "an",
               "our", "their", "its", "who", "what", "which", "each", "every", "simulated", "advisor", "director",
               "then", "so", "but", "and", "also", "here", "there", "today", "still", "yet", "if", "when",
               "because", "while", "most", "many", "some", "all", "both", "few", "several", "first", "finally",
               "earlier", "later", "everybody", "people", "visitors", "shoppers", "testers", "reviewers"}


def named_rules(text):
    """Phrases in text that attribute a rule or a saying to a named person."""
    found = []
    for rx in _NAMED_RULE:
        for m in rx.finditer(str(text or "")):
            words = re.split(r"[-\s]+", m.group(1).lower())
            if any(w not in _NOT_PEOPLE for w in words):
                found.append(m.group(0))
    return found


def stage_of(r):
    """The client's stage from target.json; missing or unknown is team."""
    st = ((r or {}).get("target") or {}).get("stage")
    return st if st in STAGES else "team"


def panel_name(stage):
    return "Advisory Panel" if stage == "solo" else "Board"


def member_word(stage):
    return "advisor" if stage == "solo" else "seat"


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


PRODUCT_FILE = "product.md"
IDEA_FILE = os.path.join("business", "idea.md")


def load_product(d):
    """The team's product dossier (proof/product.md), else the idea file the business
    plan starts from (proof/business/idea.md), or None."""
    for rel in (PRODUCT_FILE, IDEA_FILE):
        path = os.path.join(ledger.root(d), rel)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                text = fh.read().strip()
            if text:
                return text
    return None


def evidence(d):
    """Everything the board sees, from the ledger, plus the set of citable refs."""
    r = verdict.compute(d)
    refs = {"verdict"}
    if load_product(d):
        refs.add("product")
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
        if seat["id"] == "investor":
            for s in r["scores"]:
                if s["skill"] == "proof-finance":
                    for m in s["metrics"]:
                        if not ledger.metric_passes(m):
                            out.append({"seat": "investor", "ref": "proof-finance/score:%s" % m["id"],
                                        "why": _plain_score(m)})
        if seat["id"] == "operator":
            crit = _release_critical_ids()
            for c in r["coverage"]:
                if c["skill"] == "proof-release":
                    for ch in c["checks"]:
                        if ch["id"] in crit and ch["status"] == "not_run":
                            out.append({"seat": "operator", "ref": "proof-release/coverage:%s" % ch["id"],
                                        "why": "launch-blocking readiness item not done: %s" % ch["title"]})
    return out


# ---------------------------------------------------------- presentation

LOW_SHOWN = 3
NOT_RUN_SHOWN = 5


def _summary_line(r, sk, by_skill):
    """One line for a skill outside a director's area: counts, plus the refs that could decide a call."""
    # Only what could block a launch, so a small change in another area does not
    # change this director's brief (and their answer can be kept).
    blocking = [f for f in by_skill.get(sk, []) if f["status"] == "verified" and f["severity"] in ("critical", "high")]
    sc = next((s for s in r["scores"] if s["skill"] == sk), None)
    over = [m for m in (sc["metrics"] if sc else []) if not ledger.metric_passes(m)]
    keys = ["[%s/%s] %s" % (sk, f["id"], f["severity"].upper()) for f in blocking]
    keys += ["[%s/score:%s] over budget" % (sk, m["id"]) for m in over]
    return "- %s: %s" % (sk, "; ".join(keys) if keys else "nothing blocking")


def _skill_lines(r, skills, compact=False):
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
        lows = 0
        for f in sorted(fs, key=lambda f: verdict.RANK[f["severity"]]):
            if compact and f["severity"] in ("low", "info"):
                lows += 1
                if lows > LOW_SHOWN:
                    continue
            L.append("- [%s/%s] %s: %s%s" % (sk, f["id"], f["severity"].upper(), f["title"],
                                             (" Fix: " + f["fix"]) if f.get("fix") and not compact else ""))
        if lows > LOW_SHOWN:
            L.append("- and %d more low or info finding(s)" % (lows - LOW_SHOWN))
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
            shown = NOT_RUN_SHOWN if compact else 10
            for c in notrun[:shown]:
                L.append("- [%s/coverage:%s] not yet checked: %s" % (sk, c["id"], c["title"]))
            if len(notrun) > shown:
                L.append("- and %d more check(s) not yet run" % (len(notrun) - shown))
        if not fs and not unv and not (sc and sc["metrics"]):
            L.append("- no verified findings")
        L.append("")
    return L


def presentation(r, seats_vetoes, focus=None, stage="team"):
    """The team's presentation. With `focus` (a seat's skills), only that area is shown in
    full and every other skill is one line, so each brief carries what that seat judges."""
    t = r.get("target") or {}
    sev = {s: sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] == s) for s in ledger.SEVERITIES}
    L = ["# %s: presentation to the %s" % (t.get("name") or "The product", panel_name(stage)), ""]
    L.append("## The product")
    L.append("- Kind: %s" % t.get("kind", "unknown"))
    if t.get("urls"):
        L.append("- Where: %s" % ", ".join(t["urls"]))
    if t.get("done_means"):
        L.append("- The team's own bar for done: %s" % t["done_means"])
    L.append("")
    L.append("## The headline")
    L.append("- [verdict] The computed Proof verdict is **%s**." % r["verdict"])
    if focus in (None, ["*"]):
        L.append("- Verified findings: %d critical, %d high, %d medium, %d low." % (
            sev["critical"], sev["high"], sev["medium"], sev["low"]))
        checks = r["checks"]
    else:
        L.append("- Verified critical or high findings, all areas: %d." % (sev["critical"] + sev["high"]))
        # Counts in the shared lines would change every brief on any edit; drop them here.
        checks = [dict(c, text=re.sub(r" \(\d+ below it\)", "", c["text"])) for c in r["checks"]
                  if "proof-" not in c["text"] or any(sk in c["text"] for sk in focus)]
    for c in checks:
        L.append("- %s %s" % ("OK" if c["ok"] else "PROBLEM:", c["text"]))
    if seats_vetoes:
        L.append("- The evidence already triggers %d veto(es):" % len(seats_vetoes))
        for v in seats_vetoes:
            L.append("  - %s: [%s] %s" % (_seat_label(v["seat"]), v["ref"], v["why"]))
    L.append("")
    L.append("## What was tested, and what it found")
    L.append("")
    if focus is None:
        L.extend(_skill_lines(r, ["*"]))
    else:
        L.extend(_skill_lines(r, focus, compact=True))
        if focus != ["*"]:
            by_skill = {}
            for f in r["findings"]:
                by_skill.setdefault(f["skill"], []).append(f)
            names = sorted(set([f["skill"] for f in r["findings"]] + [c["skill"] for c in r["coverage"]] +
                               [x["skill"] for x in r["scores"]]) - set(focus))
            if names:
                L.append("### Outside your area, in one line each")
                L.extend(_summary_line(r, sk, by_skill) for sk in names)
                L.append("")
    L.append("## The ask")
    L.append("Decide whether this ships now (Go), ships once named conditions are met (Go with conditions), "
             "or does not ship yet (Hold).")
    return "\n".join(L) + "\n"


def _present(r):
    return sorted(set([f["skill"] for f in r["findings"]] + [c["skill"] for c in r["coverage"]] +
                      [x["skill"] for x in r["scores"]]))


def _signals_line(d):
    path = os.path.join(ledger.root(d), "review.json") if d else None
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            sig = json.load(fh).get("signals") or {}
    except ValueError:
        return None
    names = {"web": "a web app", "pwa": "installable on phones", "api": "its own API", "react_native": "a mobile app",
             "native_mobile": "a mobile app", "llm": "AI answers", "rag": "answers from your documents",
             "database": "stored user data", "auth": "accounts and sign-in", "voice": "voice", "email": "email",
             "i18n": "several languages", "on_device_llm": "an on-device model"}
    have = []
    for k in sig:
        if k in names and names[k] not in have:
            have.append(names[k])
    return ", ".join(have) if have else None


def _seat_label(seat_id):
    return str(seat_id or "").capitalize()


def seat_presentation(r, seats_vetoes, focus, product, d=None, stage="team"):
    """What a seat judges: the product dossier, its own area in full and every other area in one line."""
    t = r.get("target") or {}
    L = ["# %s: presented to the %s" % (t.get("name") or "The product", panel_name(stage)), ""]
    L.append("## The product [product]")
    L.append(product or "(The team wrote no product dossier. Judge from what is below, and ask what you would "
                        "need to know.)")
    feats = _signals_line(d)
    if feats:
        L.append("")
        L.append("What the code shows it has: %s." % feats)
    if t.get("urls"):
        L.append("Where customers find it: %s" % ", ".join(t["urls"]))
    L.append("")
    blocking = [f for f in r["findings"] if f["status"] == "verified" and f["severity"] in ("critical", "high")]
    L.append("## The headline")
    L.append("- [verdict] The computed Proof verdict is **%s**." % r["verdict"])
    L.append("- Verified critical or high findings, all areas: %d." % len(blocking))
    for v in seats_vetoes:
        L.append("- Veto, %s: [%s] %s" % (_seat_label(v["seat"]), v["ref"], v["why"]))
    L.append("")
    present = _present(r)
    mine = present if focus == ["*"] else [sk for sk in present if sk in focus]
    L.append("## What was tested, and what it found" if focus == ["*"] else "## Your area, in full")
    L.append("")
    L.extend(_skill_lines(r, mine or ["none"], compact=True))
    others = [sk for sk in present if sk not in mine]
    if others:
        by_skill = {}
        for f in r["findings"]:
            by_skill.setdefault(f["skill"], []).append(f)
        L.append("### Outside your area, in one line each")
        L.extend(_summary_line(r, sk, by_skill) for sk in others)
        L.append("")
    L.append("## The ask")
    L.append("Decide whether this ships now (Go), ships once named conditions are met (Go with conditions), "
             "or does not ship yet (Hold).")
    return "\n".join(L) + "\n"


NO_NAMES = ("- Argue as the seat, from the evidence. No seat is a named person and no answer quotes one: never "
            "attribute a rule or a saying to anyone (no \"X's rule\", \"X said\", \"as X put it\"). `board.py "
            "check` rejects an answer that does.")


def answer_rules(stage):
    L = ["- Judge from the presentation only. Do not invent facts, numbers or findings that are not in it.",
         "- Every high-severity risk must cite at least one ref from the presentation, copied exactly as it "
         "appears inside square brackets (for example proof-sec-web/some-id, proof-finance/score:x, or product "
         "for the dossier). Medium and low risks may be judgement calls with an empty evidence list.",
         NO_NAMES,
         "- Be the seat, not a cheerleader. A polite yes is worth less than a clear concern."]
    if stage == "solo":
        L.append("- The client is a solo founder: there are no executives to own an action. Put every condition "
                 "in one of three buckets, in its \"bucket\" field:")
        L += ["  " + b for b in BUCKET_HELP]
    else:
        L.append("- Conditions are concrete actions with an owner, one of the four seats: investor, growth, "
                 "builder or operator.")
    return L


def answer_schema(seat, short, stage):
    if stage == "solo":
        cond = ('{"action": "what must happen", "bucket": "do_today" or "freelancer" or "professional", '
                '"by": "before launch" or "within 30 days" or a date}')
    else:
        cond = ('{"action": "what must happen", "owner": "investor" or "growth" or "builder" or "operator", '
                '"by": "before launch" or "within 30 days" or a date}')
    return """{
  "seat": "%s",
  "vote": "go" or "go_with_conditions" or "hold",
  "confidence": a number from 0 to 1,
  "summary": "two or three sentences, as the %s seat would say it in the room",
  "risks": [
    {"risk": "what could go wrong", "severity": "high" or "medium" or "low",
     "evidence": ["refs exactly as written in square brackets in the presentation, e.g. proof-sec-web/some-id"]}
  ],
  "conditions": [
    %s
  ],
  "questions": ["a question for the presenting team, if you have one"]
}""" % (seat, short, cond)


def _seat_lines(seat, seat_vetoes, you="You"):
    L = []
    if seat["skills"] != ["*"]:
        L.append("%s area is the evidence from: %s." % ("Your" if you == "You" else "This seat's",
                                                         ", ".join(seat["skills"])))
    if seat.get("veto"):
        L.append("%s hold%s a veto over: %s. A verified high or critical finding there blocks the launch on its "
                 "own." % (you, "" if you == "You" else "s", ", ".join(seat["veto"])))
    mine = [v for v in seat_vetoes if v["seat"] == seat["id"]]
    if mine:
        L.append("The evidence already triggers %s veto: %s." % ("your" if you == "You" else "this seat's",
                                                                 "; ".join("[%s] %s" % (v["ref"], v["why"])
                                                                           for v in mine)))
    return L


def seat_brief(seat, pres, answer_path, seat_vetoes, stage="team"):
    pn = panel_name(stage)
    L = ["You sit in the %s seat on the %s. The team is presenting their product and asking whether it can "
         "launch. The seat is an archetype, not a person." % (seat["title"], pn), "",
         "== YOUR SEAT ==", seat["lens"], ""]
    L += _seat_lines(seat, seat_vetoes)
    L += ["", "== THE PRESENTATION ==", pres, "== END OF PRESENTATION ==", "", "How to answer:"]
    L += answer_rules(stage)
    L += ["",
          "Create the file %s with your file-writing tool (do not only print it) containing ONLY this JSON, "
          "no markdown fence:" % answer_path, "",
          answer_schema(seat["id"], seat["short"], stage)]
    return "\n".join(L) + "\n"


def panel_brief(seats, pres, d, seat_vetoes, stage="team"):
    """One brief for one agent to sit as several seats. Every agent starts with a large fixed
    context, so the agent count is the main cost of a meeting; the evidence appears once."""
    pn = panel_name(stage)
    L = ["You sit as %d seats on the %s, one after another. The team is presenting their product and asking "
         "whether it can launch. Each seat is an archetype, not a person." % (len(seats), pn), "",
         "Judge each seat on its own, from its own area first. Do not harmonise the seats: when two seats weigh "
         "things differently, the answers disagree. Write a seat's answer before you start the next seat.", "",
         "== THE PRESENTATION ==", pres, "== END OF PRESENTATION ==", "",
         "How to answer, for every seat:"]
    L += answer_rules(stage)
    L += ["- Write each answer with your file-writing tool to the path given for that seat, containing ONLY "
          "this JSON, no markdown fence (use the seat's id):", "",
          answer_schema("<seat id>", "<seat title>", stage), ""]
    for seat in seats:
        L += ["== SEAT: %s, id %s ==" % (seat["title"], seat["id"]),
              "Write this seat's answer to: %s" % os.path.abspath(_answer_path(d, seat["id"])), "", seat["lens"], ""]
        L += _seat_lines(seat, seat_vetoes, you="This seat")
        L.append("")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------ answers

def _answer_path(d, seat_id):
    return os.path.join(_board_dir(d), "answers", seat_id + ".json")


def _named_problem(where, text):
    hits = named_rules(text)
    if hits:
        return ("%s attributes a rule or saying to a person (%s); no seat quotes anyone, argue from the evidence"
                % (where, "; ".join(sorted(set(hits)))))
    return None


def validate_answer(obj, seat_id, refs, stage="team", owners=None):
    problems = []
    owners = set(owners or [])
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
    else:
        p = _named_problem("summary", obj["summary"])
        if p:
            problems.append(p)
    for key in ("risks", "conditions", "questions"):
        if not isinstance(obj.get(key, []), list):
            problems.append("%s must be a list" % key)
    for i, rk in enumerate(obj.get("risks") or [] if isinstance(obj.get("risks"), list) else []):
        if not isinstance(rk, dict) or not rk.get("risk"):
            problems.append("risk %d needs a 'risk'" % (i + 1))
            continue
        if rk.get("severity") not in SEV:
            problems.append("risk %d severity must be high, medium or low" % (i + 1))
        p = _named_problem("risk %d" % (i + 1), rk.get("risk"))
        if p:
            problems.append(p)
        if rk.get("quote"):
            problems.append("risk %d has a quote; seats quote no one, so leave \"quote\" empty or drop it" % (i + 1))
        ev = rk.get("evidence") or []
        if not isinstance(ev, list):
            problems.append("risk %d evidence must be a list" % (i + 1))
            continue
        bad = [x for x in ev if x not in refs]
        if bad:
            problems.append("risk %d cites refs that are not in the evidence: %s" % (i + 1, ", ".join(map(str, bad))))
        if rk.get("severity") == "high" and not ev:
            problems.append("risk %d is high severity but cites no evidence" % (i + 1))
    if obj.get("quotes_used"):
        problems.append("quotes_used is not accepted; seats quote no one")
    for i, cd in enumerate(obj.get("conditions") or [] if isinstance(obj.get("conditions"), list) else []):
        if not isinstance(cd, dict) or not cd.get("action"):
            problems.append("condition %d needs an 'action'" % (i + 1))
            continue
        bucket = cd.get("bucket")
        if stage == "solo":
            if bucket not in BUCKET_LABEL:
                problems.append("condition %d needs a bucket: do_today, freelancer or professional (the client is "
                                "a solo founder, so actions go in buckets, not to owners)" % (i + 1))
        elif bucket and bucket not in BUCKET_LABEL:
            problems.append("condition %d bucket must be do_today, freelancer or professional" % (i + 1))
        owner = cd.get("owner")
        if owner and stage != "solo" and owners and str(owner).lower() not in owners:
            problems.append("condition %d owner %r is not a seat; use one of %s" % (i + 1, owner,
                                                                                   ", ".join(sorted(owners))))
    if obj.get("vote") == "go_with_conditions" and not obj.get("conditions"):
        problems.append("a go_with_conditions vote needs at least one condition")
    return problems


def _load_meta(d):
    path = os.path.join(_board_dir(d), "board.json")
    if not os.path.exists(path):
        raise BoardError("no board brief yet. Run: board.py brief")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_meta(d, meta):
    _write(os.path.join(_board_dir(d), "board.json"), json.dumps(meta, indent=2) + "\n")


def load_answers(d, meta, refs, stage="team"):
    owners = [s["id"] for s in load_seats()]
    good, bad = {}, {}
    for sid in meta["seats"]:
        path = _answer_path(d, sid)
        if not os.path.exists(path):
            bad[sid] = ["no answer yet"]
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except ValueError as ex:
            bad[sid] = ["not valid JSON (%s)" % ex]
            continue
        problems = validate_answer(obj, sid, refs, stage, owners)
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


# ------------------------------------------------------------- debate

def _debate_dir(d):
    return os.path.join(_board_dir(d), "debate")


def _debate_path(d, seat_id):
    return os.path.join(_debate_dir(d), seat_id + ".json")


def answers_fp(answers):
    return hashlib.sha256(json.dumps(answers, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _clear_debate(d):
    ddir = _debate_dir(d)
    n = 0
    if os.path.isdir(ddir):
        for name in os.listdir(ddir):
            if name.endswith(".json"):
                os.remove(os.path.join(ddir, name))
                n += 1
    path = os.path.join(_board_dir(d), "debate.md")
    if os.path.exists(path):
        os.remove(path)
    return n


DEBATE_SCHEMA = """{
  "seat": "<seat id>",
  "challenges": [
    {"to": "<another seat's id>", "about": the number of that seat's risk you challenge, or "vote",
     "challenge": "one or two sentences: what that seat got wrong, and why",
     "evidence": ["at least one ref, exactly as in square brackets, e.g. proof-finance/score:x, verdict or product"]}
  ],
  "concessions": ["one sentence for each point another seat got right that changes your view"],
  "final_vote": "go" or "go_with_conditions" or "hold",
  "final_reason": "one or two sentences: why your vote stands or moved"
}"""


def debate_brief(seats, answers, d, stage="team"):
    """The second round: every seat reads the others' answers and challenges, concedes and votes again."""
    pn = panel_name(stage)
    at = [s for s in seats if s["id"] in answers]
    L = ["You sit as %d seats on the %s, one after another, for the second round: the debate. Each seat is an "
         "archetype, not a person." % (len(at), pn), "",
         "Every seat has given its first answer; they are all below. For each seat in turn, read the OTHER seats' "
         "answers, challenge what you think is wrong, concede what they got right, and give a final vote. Keep "
         "each seat's own view: a seat that agrees with everything has not done its job.", "",
         "The evidence refs are the ones in square brackets in %s and in the risks below; cite them exactly."
         % os.path.abspath(os.path.join(_board_dir(d), "presentation.md")), "",
         "== FIRST-ROUND ANSWERS =="]
    for seat in at:
        a = answers[seat["id"]]
        L += ["", "### %s (id %s): voted %s" % (seat["title"], seat["id"], VOTE_LABEL[a["vote"]]),
              "Summary: %s" % " ".join(a["summary"].split())]
        risks = a.get("risks") or []
        for i, rk in enumerate(risks, 1):
            ev = ", ".join(rk.get("evidence") or []) or "none, a judgement call"
            L.append("- risk %d (%s): %s [evidence: %s]" % (i, rk.get("severity"), " ".join(rk["risk"].split()), ev))
        if not risks:
            L.append("- raised no risks")
        for c in a.get("conditions") or []:
            L.append("- condition: %s" % " ".join(c["action"].split()))
    L += ["", "== END OF ANSWERS ==", "", "How to answer, for every seat:",
          "- At least one challenge, each to another seat. \"about\" is the number of that seat's risk you "
          "challenge (risk 1 is 1), or \"vote\" to challenge its vote as a whole.",
          "- Every challenge cites at least one evidence ref that exists, copied exactly.",
          "- Concede plainly where another seat is right; one sentence per concession, or none.",
          "- final_vote is the seat's vote after hearing the others. Move it only if the debate gave a reason, "
          "and put that reason in final_reason.",
          "- The ledger's vetoes stand whatever the debate concludes; a debate cannot clear a verified blocker.",
          NO_NAMES.replace("`board.py check`", "`board.py debate-check`"),
          "- Write each seat's file with your file-writing tool to the path given, containing ONLY this JSON, no "
          "markdown fence:", "", DEBATE_SCHEMA, ""]
    for seat in at:
        L += ["== SEAT: %s, id %s ==" % (seat["title"], seat["id"]),
              "Write this seat's debate to: %s" % os.path.abspath(_debate_path(d, seat["id"])),
              "Its first vote was %s. %s" % (VOTE_LABEL[answers[seat["id"]]["vote"]], _short(seat["lens"], 160)), ""]
    return "\n".join(L) + "\n"


def _about(value):
    """A challenge's target: "vote", or a risk number as an int, or None when it is neither."""
    if value == "vote":
        return "vote"
    if isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n


def validate_debate(obj, seat_id, answers, refs):
    if not isinstance(obj, dict):
        return ["debate is not a JSON object"]
    problems = []
    if obj.get("seat") != seat_id:
        problems.append("seat should be %r" % seat_id)
    ch = obj.get("challenges")
    if not isinstance(ch, list) or not ch:
        problems.append("at least one challenge to another seat is required")
        ch = ch if isinstance(ch, list) else []
    for i, c in enumerate(ch, 1):
        if not isinstance(c, dict):
            problems.append("challenge %d is not an object" % i)
            continue
        to = c.get("to")
        if to == seat_id:
            problems.append("challenge %d targets this seat itself; challenge another seat" % i)
        elif to not in answers:
            problems.append("challenge %d targets %r, which is not a seat that answered (%s)" % (
                i, to, ", ".join(sorted(s for s in answers if s != seat_id))))
        else:
            about = _about(c.get("about"))
            n = len(answers[to].get("risks") or [])
            if about is None or (about != "vote" and not 1 <= about <= n):
                problems.append("challenge %d is about %r, but %s raised %d risk(s); use a risk number from 1 to %d "
                                "or \"vote\"" % (i, c.get("about"), to, n, n) if n else
                                "challenge %d is about %r, but %s raised no risks; use \"vote\"" % (
                                    i, c.get("about"), to))
        if not isinstance(c.get("challenge"), str) or not c["challenge"].strip():
            problems.append("challenge %d needs a 'challenge'" % i)
        else:
            p = _named_problem("challenge %d" % i, c["challenge"])
            if p:
                problems.append(p)
        ev = c.get("evidence")
        if not isinstance(ev, list) or not ev:
            problems.append("challenge %d cites no evidence" % i)
        else:
            bad = [x for x in ev if x not in refs]
            if bad:
                problems.append("challenge %d cites refs that are not in the evidence: %s" % (
                    i, ", ".join(map(str, bad))))
    conc = obj.get("concessions", [])
    if not isinstance(conc, list) or any(not isinstance(x, str) for x in conc):
        problems.append("concessions must be a list of sentences")
    else:
        for k, x in enumerate(conc, 1):
            p = _named_problem("concession %d" % k, x)
            if p:
                problems.append(p)
    if obj.get("final_vote") not in VOTES:
        problems.append("final_vote must be one of %s" % ", ".join(VOTES))
    if not isinstance(obj.get("final_reason"), str) or not obj["final_reason"].strip():
        problems.append("final_reason is required")
    else:
        p = _named_problem("final_reason", obj["final_reason"])
        if p:
            problems.append(p)
    return problems


def load_debate(d, meta, answers, refs):
    """(valid debate files by seat, problems by seat, state). State is none, stale or held."""
    expected = meta.get("debate_seats") or []
    if not expected:
        return {}, {}, "none"
    if meta.get("debate_fp") != answers_fp(answers):
        msg = "stale: the first-round answers changed after debate-brief; run board.py debate-brief again"
        return {}, dict((sid, [msg]) for sid in expected), "stale"
    good, bad = {}, {}
    for sid in expected:
        if sid not in answers:
            bad[sid] = ["this seat's first-round answer is no longer valid"]
            continue
        path = _debate_path(d, sid)
        if not os.path.exists(path):
            bad[sid] = ["no debate file yet"]
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except ValueError as ex:
            bad[sid] = ["not valid JSON (%s)" % ex]
            continue
        problems = validate_debate(obj, sid, answers, refs)
        if problems:
            bad[sid] = problems
        else:
            good[sid] = obj
    return good, bad, "held"


def debate_record(seats, answers, debate):
    """decision.json's debate list and vote changes, in seat order."""
    challenges, changes = [], []
    for seat in seats:
        dg = debate.get(seat["id"])
        if not dg:
            continue
        for c in dg["challenges"]:
            challenges.append({"from": seat["id"], "to": c["to"], "about": _about(c.get("about")),
                               "challenge": c["challenge"], "evidence": list(c.get("evidence") or [])})
        first = answers[seat["id"]]["vote"]
        if dg["final_vote"] != first:
            changes.append({"seat": seat["id"], "from": first, "to": dg["final_vote"], "why": dg["final_reason"]})
    return challenges, changes


SCORE_WORDS = {"year1_operating_profit": "year-one operating profit", "contribution_per_unit": "what one sale earns",
               "contrast_pairs_passing": "colour pairs that pass contrast", "buy_rate": "simulated buy rate"}
OP_WORDS = {">": "above", ">=": "at least", "<": "below", "<=": "at most", "==": "exactly"}


def _plain_score(m):
    """A missed score in words a founder reads: 'the year-one operating profit is -580,368; it has to be above 0'."""
    name = SCORE_WORDS.get(m["id"], m["id"].replace("_", " "))

    def num(x):
        x = float(x)
        return "{:,.0f}".format(x) if abs(x) >= 1000 or x == int(x) else "{:,.2f}".format(x)
    return "the %s is %s; it has to be %s %s" % (name, num(m["value"]), OP_WORDS.get(m["op"], m["op"]), num(m["budget"]))


def _trim(text, n):
    """Cut long text at a sentence or clause, never mid-citation: bracketed refs go first."""
    t = " ".join(str(text or "").split())
    t = re.sub(r"\s*\[[^\]]*\]", "", t)
    if len(t) <= n:
        return t
    cut = t[:n]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end >= n // 2:  # a whole sentence or clause, ended with a full stop
        return cut[:end].rstrip() + "."
    return cut.rsplit(" ", 1)[0].rstrip(",;:.") + "..."


def _about_text(answers, to, about, n=70):
    if about == "vote":
        return "its %s vote" % VOTE_LABEL[answers[to]["vote"]]
    risks = answers[to].get("risks") or []
    return 'risk %d, "%s"' % (about, _trim(risks[about - 1]["risk"], n)) if 1 <= about <= len(risks) else "risk %s" % about


DEBATE_SHOWN = 2


def debate_md(r, seats, answers, dec, debate, stage="team"):
    """The one-page debate: who challenged whom, what was conceded, the votes that moved, the decision."""
    t = r.get("target") or {}
    title = dict((s["id"], s["title"]) for s in seats)
    L = ["# Boardroom debate", "",
         "%s for %s. First round: %s." % (panel_name(stage), t.get("name") or "the product", "; ".join(
             "%s %s" % (title[s["id"]], VOTE_LABEL[answers[s["id"]]["vote"]]) for s in seats if s["id"] in answers)),
         "", "## Who challenged whom", ""]
    for seat in seats:
        dg = debate.get(seat["id"])
        if not dg:
            continue
        chs = dg["challenges"]
        for c in chs[:DEBATE_SHOWN]:
            refs = ", ".join((c.get("evidence") or [])[:2])
            L.append("- **%s** to **%s**, on %s: %s%s" % (
                title[seat["id"]], title.get(c["to"], c["to"]), _about_text(answers, c["to"], _about(c["about"])),
                _trim(c["challenge"], 200), (" [%s]" % refs) if refs else ""))
        if len(chs) > DEBATE_SHOWN:
            more = len(chs) - DEBATE_SHOWN
            L.append("- %s raised %d more challenge%s (in the appendix)." % (
                title[seat["id"]], more, "" if more == 1 else "s"))
    L += ["", "## What was conceded", ""]
    n = 0
    for seat in seats:
        for x in ((debate.get(seat["id"]) or {}).get("concessions") or [])[:DEBATE_SHOWN]:
            L.append("- %s: %s" % (title[seat["id"]], _trim(x, 180)))
            n += 1
    if not n:
        L.append("- Nothing was conceded.")
    L += ["", "## Votes that moved", ""]
    for vc in dec.get("vote_changes") or []:
        L.append("- %s: %s to %s. %s" % (title.get(vc["seat"], vc["seat"]), VOTE_LABEL[vc["from"]],
                                         VOTE_LABEL[vc["to"]], _trim(vc["why"], 200)))
    if not dec.get("vote_changes"):
        L.append("- No vote moved; every seat held its first-round vote.")
    L += ["", "## The decision", "", "**%s.** %s" % (dec["decision"], _trim(dec["reasons"][0], 240) if dec["reasons"]
                                                     else "")]
    if dec.get("vetoes"):
        L.append("")
        L.append("The vetoes come from the evidence, not from a vote, so they stand whatever the debate concluded.")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------ decision

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "before", "after", "launch", "must",
         "should", "all", "any", "each", "every", "have", "has", "are", "was", "then", "them", "their", "our"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in _STOP}

def _evidence_bucket(skill):
    if skill in PROFESSIONAL_SKILLS:
        return "professional"
    if skill in FREELANCER_SKILLS:
        return "freelancer"
    return "do_today"


def decide(r, seats, answers, triggered, stage="team"):
    """The decision from the evidence and the votes in `answers` (the final votes after a debate)."""
    reasons, conditions = [], []
    who = member_word(stage)
    n_seats = len(seats)
    votes = {v: [s for s, a in answers.items() if a["vote"] == v] for v in VOTES}
    if r["verdict"] == "Incomplete":
        decision = HOLD
        reasons.append("The evidence is incomplete, so the %s cannot approve: %s" % (
            panel_name(stage), "; ".join(r["gaps"][:3])))
    elif triggered:
        decision = HOLD
        for v in triggered:
            reasons.append("%s veto from the evidence: %s [%s]" % (_seat_label(v["seat"]), v["why"], v["ref"]))
    elif len(answers) * 2 < n_seats:
        decision = HOLD
        reasons.append("No quorum: only %d of %d %ss gave a valid answer." % (len(answers), n_seats, who))
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
                reasons.append("%d %s(s) voted to hold; their concerns become conditions." % (len(votes["hold"]), who))
            if votes["go_with_conditions"]:
                reasons.append("%d %s(s) voted go with conditions." % (len(votes["go_with_conditions"]), who))
            if not reasons:
                reasons.append("Every %s voted go, with conditions attached." % who)
        else:
            decision = GO
            reasons.append("Every %s who answered voted go and the Proof verdict is Ship." % who)
    # The action list: blockers from the evidence first, then the seats' conditions.
    owner_for = {}
    for seat in seats:
        for sk in seat.get("veto") or []:
            owner_for.setdefault(sk, seat["id"])
    for seat in seats:
        for sk in seat["skills"]:
            owner_for.setdefault(sk, seat["id"])
    solo = stage == "solo"
    for b in r["blockers"]:
        ref = "%s/%s" % (b["skill"], b["id"]) if b["kind"] == "finding" else "%s/score:%s" % (b["skill"], b["id"])
        what = b.get("title") or ("bring the %s back to the bar" % SCORE_WORDS.get(b["id"], b["id"].replace("_", " ")))
        c = {"action": "Fix: %s" % what, "by": "before launch", "from": "evidence", "ref": ref}
        if solo:
            c["bucket"] = _evidence_bucket(b["skill"])
        else:
            c["owner"] = owner_for.get(b["skill"], seats[0]["id"] if seats else "investor")
        conditions.append(c)
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
            item = {"action": c["action"], "by": c.get("by") or "before launch", "from": "board",
                    "raised_by": [sid], "_t": toks}
            if c.get("owner") or not solo:
                item["owner"] = str(c.get("owner") or sid).lower()
            if c.get("bucket") in BUCKET_LABEL:
                item["bucket"] = c["bucket"]
            elif solo:
                item["bucket"] = "do_today"
            raised.append(item)
    for r_ in raised:
        r_.pop("_t")
    # Conditions raised by more seats come first.
    raised.sort(key=lambda c: -len(c["raised_by"]))
    conditions.extend(raised)
    return {"decision": decision, "reasons": reasons, "votes": {k: sorted(v) for k, v in votes.items()},
            "answered": sorted(answers), "missing": sorted(set(s["id"] for s in seats) - set(answers)),
            "vetoes": triggered, "conditions": conditions, "verdict": r["verdict"],
            "stage": stage, "panel_name": panel_name(stage)}


def _who(c):
    """Who does an action: its bucket for a solo founder, else its owner seat."""
    if c.get("bucket") in BUCKET_LABEL and not c.get("owner"):
        return BUCKET_LABEL[c["bucket"]]
    if c.get("bucket") in BUCKET_LABEL:
        return "%s, %s" % (BUCKET_LABEL[c["bucket"]], _seat_label(c["owner"]))
    return "owner: %s" % _seat_label(c.get("owner") or "team")


# --------------------------------------------------------------- agenda

# The meeting walks the audits area by area, in the order a board would take them.
AREAS = [
    ("market", "Market and buyers", ["proof-market", "proof-buyers"]),
    ("offer", "Offer and pricing", ["proof-pricing", "proof-offer"]),
    ("flows", "Product and user flows",
     ["proof-web-e2e", "proof-mobile-e2e", "proof-mobile-explore", "proof-explore", "proof-compat", "proof-visual",
      "proof-design-match", "proof-pwa"]),
    ("reliability", "Reliability and performance",
     ["proof-api", "proof-load", "proof-web-perf", "proof-watch", "proof-llm-device", "proof-resilience",
      "proof-voice"]),
    ("security", "Security",
     ["proof-sec-web", "proof-sec-secrets", "proof-sec-deps", "proof-sec-diff", "proof-mobile-audit",
      "proof-react-native", "proof-auth", "proof-database"]),
    ("infra", "Infrastructure and deployment", ["proof-infra"]),
    ("privacy", "Privacy and legal", ["proof-privacy", "proof-licenses"]),
    ("reach", "Accessibility, languages and reach",
     ["proof-a11y", "proof-mobile-a11y", "proof-i18n", "proof-seo", "proof-geo"]),
    ("ai", "AI features", ["proof-ai", "proof-rag"]),
    ("quality", "Code health", ["proof-restraint", "proof-slop"]),
    ("finance", "Finance", ["proof-finance", "proof-llm-cost"]),
    ("launch", "Go to market, operations and launch readiness",
     ["proof-go-to-market", "proof-ops", "proof-release", "proof-email", "proof-store-ready"]),
]
SKILL_AREA = {sk: aid for aid, _, sks in AREAS for sk in sks}
# One word per step for the meeting page's progress bar; the full title is the tooltip.
SHORT = {"product": "Product", "market": "Market", "offer": "Offer", "flows": "Flows", "reliability": "Performance", "security": "Security", "infra": "Infra",
         "privacy": "Privacy", "reach": "Reach", "ai": "AI", "quality": "Code", "finance": "Finance",
         "launch": "Launch"}
SHORT_BY_TITLE = dict((title, SHORT[aid]) for aid, title, _ in AREAS)
SHORT_BY_TITLE.update({"Other points": "Other", "The debate": "Debate", "The vote": "Vote",
                       "The product and the market": "Product"})
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
                reasons.append("Veto triggered for %s." % ", ".join(sorted(set(_seat_label(v["seat"]) for v in here))))
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
    prod = []
    for seat in seats:
        a = answers.get(seat["id"])
        if a:
            mine = [rk for rk in a.get("risks") or [] if "product" in (rk.get("evidence") or [])]
            if mine:
                prod.append({"seat": seat["id"], "risks": mine})
    if prod:
        raised = [rk for sp in prod for rk in sp["risks"]]
        sev = [rk.get("severity") for rk in raised]
        outcome = BLOCKER if "high" in sev else (CONCERNS if "medium" in sev else CLEAR)
        items.insert(0, {"id": "product", "title": "The product and the market", "skills": [], "missing": [],
                         "verified": [], "unverified": [], "ruled_out": [], "metrics": [],
                         "coverage": dict((st, 0) for st in ledger.COVERAGE_STATUSES), "notrun": [], "vetoes": [],
                         "outcome": outcome, "reasons": ["%d point(s) the directors raised about the product itself."
                                                         % len(raised)], "speakers": prod})
        product_risks = set(id(rk) for sp in prod for rk in sp["risks"])
        general = [{"seat": g["seat"], "risks": [rk for rk in g["risks"] if id(rk) not in product_risks]}
                   for g in general]
        general = [g for g in general if g["risks"]]
    untested = [title for aid, title, sks in AREAS if not any(s in present or s in r["required"] for s in sks)]
    return items, general, untested


# -------------------------------------------------------------- minutes

def _first_votes(dec, answers):
    return dict((c["seat"], c["from"]) for c in dec.get("vote_changes") or [])


def minutes_md(r, seats, answers, dec, qa, items, general, untested, debate=None):
    t = r.get("target") or {}
    debate = debate or {}
    stage = dec.get("stage") or "team"
    title_of = dict((s["id"], s["title"]) for s in seats)
    moved = _first_votes(dec, answers)
    L = ["# %s minutes: %s" % (panel_name(stage), t.get("name") or "the product"), "",
         "**Decision: %s**" % dec["decision"], ""]
    for x in dec["reasons"]:
        L.append("- %s" % x)
    L += ["", "Proof verdict: %s. Final votes: %d go, %d go with conditions, %d hold%s." % (
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
    if dec.get("debate"):
        L += ["", "## The debate", "",
              "%d challenge(s) between the seats, %d vote(s) moved. The one-page debate is in debate.md." % (
                  len(dec["debate"]), len(dec.get("vote_changes") or []))]
        for vc in dec.get("vote_changes") or []:
            L.append("- %s moved from %s to %s: %s" % (title_of.get(vc["seat"], vc["seat"]), VOTE_LABEL[vc["from"]],
                                                     VOTE_LABEL[vc["to"]], vc["why"]))
    L += ["", "## The vote", ""]
    for seat in seats:
        a = answers.get(seat["id"])
        if not a:
            L.append("- %s: did not answer." % seat["title"])
            continue
        final = (debate.get(seat["id"]) or {}).get("final_vote") or a["vote"]
        L.append("- %s: %s%s. %s" % (seat["title"], VOTE_LABEL[final],
                                     (" (first round: %s)" % VOTE_LABEL[moved[seat["id"]]]) if seat["id"] in moved
                                     else "", a["summary"]))
    L += ["", "## Actions", ""]
    for c in dec["conditions"]:
        src = ("evidence [%s]" % c["ref"]) if c["from"] == "evidence" else "raised by %s" % ", ".join(
            _seat_label(x) for x in c["raised_by"])
        L.append("- [ ] %s (%s, %s; %s)" % (c["action"], _who(c), c["by"], src))
    return "\n".join(L) + "\n"


# --------------------------------------------------------- meeting page

def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


# Each seat has its own gradient (light top, deeper bottom), clothing colour, and a
# hat and outfit, so the four seats are told apart at a glance.
AVATAR = {
    "investor": ("#c7f9d4", "#2fa36b", "#14532d"),
    "growth": ("#ffd1ea", "#e24a9b", "#7a1f52"),
    "builder": ("#b4f1ff", "#5b8cff", "#1f3b73"),
    "operator": ("#fff3b0", "#eab308", "#713f12"),
}
_HEAD = '<circle cx="50" cy="35" r="16" fill="url(#h-{id})"/>'
_BODY = '<path d="M15 96 C15 67 30 57 50 57 C70 57 85 67 85 96 Z" fill="url(#b-{id})"/>'

_BEHIND = {  # drawn before the head
    "builder": '<path d="M30 42 C30 17 70 17 70 42 L70 54 C63 60 37 60 30 54 Z" fill="{d}" opacity=".85"/>',
}
_OUTFIT = {  # drawn over the body
    "investor": ('<path d="M33 60 L50 96 L67 60 C62 58 56 57 50 57 C44 57 38 58 33 60 Z" fill="{d}"/>'
            '<path d="M44 57 L50 68 L56 57 Z" fill="#ffffff"/>'
            '<path d="M42 59 L50 63 L42 67 Z M58 59 L50 63 L58 67 Z" fill="#facc15"/>'
            '<circle cx="50" cy="63" r="1.8" fill="#ca8a04"/>'),
    "growth": ('<path d="M34 59 C42 64 58 64 66 59 L66 65 C58 70 42 70 34 65 Z" fill="{d}"/>'
            '<path d="M58 64 L62 64 L64 84 L58 86 Z" fill="{d}"/>'),
    "builder": ('<path d="M44 58 L45 70 M56 58 L55 70" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round"/>'
            '<rect x="38" y="76" width="24" height="9" rx="3" fill="{d}" opacity=".55"/>'),
    "operator": ('<path d="M32 62 L42 59 L44 96 L33 96 Z M68 62 L58 59 L56 96 L67 96 Z" fill="#f97316"/>'
            '<rect x="33" y="76" width="11" height="3.5" fill="#e5e7eb"/><rect x="56" y="76" width="11" height="3.5" fill="#e5e7eb"/>'),
}
_HAT = {  # drawn over the head
    "investor": ('<path d="M35 27 C35 9 65 9 65 27 Z" fill="{d}"/>'
            '<ellipse cx="50" cy="27" rx="21" ry="3.6" fill="{d}"/>'
            '<rect x="35.5" y="21" width="29" height="3" fill="#4ade80" opacity=".8"/>'),
    "growth": ('<ellipse cx="46" cy="22" rx="21" ry="7.5" fill="{d}" transform="rotate(-12 46 22)"/>'
            '<circle cx="45" cy="14" r="2.2" fill="{d}"/>'),
    "builder": ('<circle cx="43" cy="36" r="4.6" fill="none" stroke="{d}" stroke-width="1.8"/>'
            '<circle cx="57" cy="36" r="4.6" fill="none" stroke="{d}" stroke-width="1.8"/>'
            '<path d="M47.6 36 L52.4 36" stroke="{d}" stroke-width="1.8"/>'),
    "operator": ('<path d="M32 29 C32 11 68 11 68 29 Z" fill="#facc15" stroke="{d}" stroke-width="1"/>'
            '<rect x="27" y="27" width="46" height="5" rx="2.5" fill="#facc15" stroke="{d}" stroke-width="1"/>'
            '<path d="M50 12 L50 27" stroke="{d}" stroke-width="1.6"/>'),
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
.go-on{font:600 12px var(--display);letter-spacing:.08em;text-transform:uppercase;color:#fff;background:var(--accent);
  border:0;border-radius:999px;padding:7px 14px;cursor:pointer;margin-left:auto}
.go-on[hidden]{display:none}
body.held .segments li.now .bar i{animation-play-state:paused}
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
  var cur = -1, timer = null, reading = false, held = false, auto = false;
  var goOn = document.getElementById('go-on');
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
    auto = true;
    try { window.scrollTo(0, 0); } catch (e) {}
    setTimeout(function(){ auto = false; }, 150);
    return dur;
  }
  function next(){
    clearTimeout(timer);
    if (reading || held || cur >= steps.length - 1) return;
    var dur = show(cur + 1);
    if (cur < steps.length - 1) timer = setTimeout(next, dur);
  }
  // Scrolling, swiping or a scroll key means someone is reading: hold this step until they press Continue.
  function hold(){
    if (held || cur >= steps.length - 1) return;
    held = true; clearTimeout(timer); document.body.classList.add('held');
    if (goOn) goOn.hidden = false;
  }
  function release(){
    held = false; document.body.classList.remove('held');
    if (goOn) goOn.hidden = true;
  }
  window.addEventListener('wheel', function(ev){ if (ev.deltaY > 0) hold(); }, {passive: true});
  window.addEventListener('touchmove', hold, {passive: true});
  window.addEventListener('keydown', function(ev){
    if (['ArrowDown', 'PageDown', 'End', ' ', 'Spacebar'].indexOf(ev.key) >= 0) hold(); });
  window.addEventListener('scroll', function(){ if (!auto && window.scrollY > 30) hold(); }, {passive: true});
  if (goOn) goOn.addEventListener('click', function(){ release(); reading = false; next(); });
  segs.forEach(function(li, k){ li.querySelector('button').addEventListener('click', function(){
    clearTimeout(timer); reading = false;
    if (held) { show(k); } else { cur = k - 1; next(); } }); });
  // Opening a dropdown holds the meeting on that step until every dropdown on it is closed again.
  document.querySelectorAll('details').forEach(function(d){ d.addEventListener('toggle', function(){
    var open = steps[cur] && steps[cur].querySelector('details[open]');
    if (open) { reading = true; clearTimeout(timer); }
    else if (reading) { reading = false; if (!held) timer = setTimeout(next, 2500); } }); });
  var replay = document.getElementById('replay');
  if (replay) replay.addEventListener('click', function(){ clearTimeout(timer); reading = false; release(); cur = -1; next(); });
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


def _weighs(seat):
    """One line on what a seat weighs, from the first sentence of its section in seats.md."""
    first = _short(seat.get("lens") or "", 60)
    return re.sub(r"^You weigh", "Weighs", first).rstrip(".") or seat["title"]


def _speech(seat, risks, suffix):
    """A seat: the short form of its top points; the full points in a dropdown."""
    out = ['<div class="speaker reveal"><div class="av">%s</div><div>' % avatar_svg(seat["id"], 52, suffix),
           '<div class="name">%s</div>' % e(seat["short"])]
    rank = dict((s, k) for k, s in enumerate(ledger.SEVERITIES))
    ordered = sorted(risks, key=lambda x: rank.get(x["severity"], 9))
    for rk in ordered[:2]:
        out.append('<p class="say"><span class="sev %s">%s</span>%s</p>' % (e(rk["severity"]), e(rk["severity"]), e(_short(rk["risk"]))))
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


def meeting_html(r, seats, answers, dec, qa, fp, items, general, untested, debate=None):
    debate = debate or {}
    stage = dec.get("stage") or "team"
    pn = panel_name(stage)
    word = member_word(stage)
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
    S = ['<div class="opening"><div class="kicker">%s meeting &middot; Opening</div>' % e(pn),
         '<h2 class="hero">%s <span>presents to the %s</span></h2>' % (e(name), e(pn if stage == "solo" else "board")),
         '<div class="going-in"><span class="small">Proof verdict going in</span><b class="vword %s">%s</b></div>' % (vcls, e(r["verdict"])),
         '<div class="stats">']
    for k, (lab, n) in enumerate((("high or critical", sev["critical"] + sev["high"]), ("medium", sev["medium"]),
                                  ("low", sev["low"]))):
        S.append('<div class="stat pop" style="--i:%d"><b>%d</b><span>%s</span></div>' % (k, n, lab))
    S.append('<div class="stat pop" style="--i:3"><b>%d/%d</b><span>scores in budget</span></div></div></div>' % (passing, len(metrics)))
    S.append('<div class="label-line">At the table</div><div class="roster">')
    for k, seat in enumerate(seats):
        S.append('<div class="person" style="--i:%d"><div class="av">%s</div><div><div class="role">Seat</div>'
                 '<div class="full">%s</div><div class="from">%s</div></div></div>' % (
                     k, avatar_svg(seat["id"], 56, "o"), e(seat["title"]), e(_weighs(seat))))
    S.append('</div><div class="label-line">Agenda</div><ol class="agenda">%s</ol>' % "".join(
        '<li style="--i:%d"><span>%02d</span>%s</li>' % (k, k + 1, e(it["title"])) for k, it in enumerate(items)))
    more = []
    if untested:
        more.append("<p><b>Not tested, so not discussed:</b> %s.</p>" % e(", ".join(untested)))
    if t.get("done_means"):
        more.append("<p><b>The team's bar for done:</b> %s</p>" % e(t["done_means"]))
    more.append("<p><b>Vetoes:</b> %s.</p>" % e("; ".join("%s over %s" % (s["short"], ", ".join(x.replace("proof-", "") for x in s["veto"]))
                                                              for s in seats if s["veto"])))
    if stage == "solo":
        more.append("<p><b>Actions go in three buckets:</b> %s.</p>" % e("; ".join(lab for _, lab in BUCKETS)))
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
            S.append(_speech(seat_by[sp["seat"]], sp["risks"], "i%d%d" % (i, k)))
        if not it["speakers"]:
            S.append('<p class="small reveal">No seat raised a point here.</p>')
        S.append('<p class="outcome %s reveal"><b>%s</b> &middot; %s</p>' % (
            oclass[it["outcome"]], e(it["outcome"]), e(it["reasons"][0] if it["reasons"] else "")))
        steps.append((it["title"], _dur(len(it["speakers"])), "".join(S)))

    # Points not tied to one audit
    extra, n_extra = [], 0
    for k, seat in enumerate(seats):
        loose = next((g["risks"] for g in general if g["seat"] == seat["id"]), [])
        if loose and seat["id"] in answers:
            extra.append(_speech(seat, loose, "g%d" % k))
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
    S = ['<div class="kicker">Questions</div><h2>The %s asks</h2>' % e(pn if stage == "solo" else "board"),
         '<p class="lede reveal">%d question%s, %d answered by the team.</p><div class="thread">' % (
             len(qs), "" if len(qs) == 1 else "s", len(answered))]
    for k, (seat, q, ans) in enumerate(shown_q):
        S.append('<div class="ask" style="--i:%d"><div class="av">%s</div><div class="bubble"><b>%s</b>%s</div></div>' % (
            2 * k, avatar_svg(seat["id"], 40, "q%d" % k), e(seat["short"]), e(q)))
        S.append('<div class="reply%s" style="--i:%d"><div class="bubble"><b>The team</b>%s</div></div>' % (
            "" if ans else " none", 2 * k + 1, e(ans or "No answer recorded.")))
    S.append("</div>")
    rest = [x for x in qs if x not in shown_q]
    if rest:
        S.append('<details class="late"><summary>All %d questions</summary><div class="more">%s</div></details>' % (len(qs), "".join(
            "<p><b>%s:</b> %s<br><span class=\"small\">%s</span></p>" % (e(s["short"]), e(q), e("Team: " + a) if a else "No answer recorded")
            for s, q, a in rest)))
    steps.append(("Questions", 3500 + 2400 * len(shown_q), "".join(S)))

    # The debate: who challenged whom, what was conceded, the votes that moved
    title_of = dict((x["id"], x["short"]) for x in seats)
    moved = _first_votes(dec, answers)
    if dec.get("debate"):
        S = ['<div class="kicker">The debate</div><h2>The seats cross-examine</h2>',
             '<p class="lede reveal">%d challenge%s, %d vote%s moved. Vetoes from the ledger stand whatever the debate '
             'concludes.</p>' % (len(dec["debate"]), "" if len(dec["debate"]) == 1 else "s",
                                 len(dec.get("vote_changes") or []),
                                 "" if len(dec.get("vote_changes") or []) == 1 else "s")]
        S.append('<div class="thread">')
        k = 0
        for seat in seats:
            mine = [c for c in dec["debate"] if c["from"] == seat["id"]][:DEBATE_SHOWN]
            for c in mine:
                S.append('<div class="ask" style="--i:%d"><div class="av">%s</div><div class="bubble"><b>%s to %s, on '
                         '%s</b>%s</div></div>' % (k, avatar_svg(seat["id"], 40, "d%d" % k), e(seat["short"]),
                                                  e(title_of.get(c["to"], c["to"])),
                                                  e(_about_text(answers, c["to"], c["about"], 50)),
                                                  e(_trim(c["challenge"], 200))))
                k += 1
        S.append("</div>")
        conc = [(seat, x) for seat in seats for x in ((debate.get(seat["id"]) or {}).get("concessions") or [])[:DEBATE_SHOWN]]
        if conc:
            S.append('<div class="team reveal"><div class="label">Conceded</div><ul>%s</ul></div>' % "".join(
                "<li><b>%s:</b> %s</li>" % (e(seat["short"]), e(_trim(x, 160))) for seat, x in conc))
        changes = dec.get("vote_changes") or []
        S.append('<div class="team reveal"><div class="label">Votes that moved</div>%s</div>' % (
            "<ul>%s</ul>" % "".join("<li><b>%s:</b> %s to %s. %s</li>" % (
                e(title_of.get(vc["seat"], vc["seat"])), e(VOTE_LABEL[vc["from"]]), e(VOTE_LABEL[vc["to"]]),
                e(_trim(vc["why"], 160))) for vc in changes) if changes else
            '<p style="margin:6px 0 0">No vote moved; every seat held its first-round vote.</p>'))
        more = "".join("<p><b>%s to %s:</b> %s<br>%s</p>" % (
            e(title_of.get(c["from"], c["from"])), e(title_of.get(c["to"], c["to"])), e(c["challenge"]),
            ", ".join(_ref_link(x) for x in c["evidence"])) for c in dec["debate"])
        S.append('<details class="late"><summary>Every challenge</summary><div class="more">%s</div></details>' % more)
        steps.append(("The debate", 4000 + 1800 * min(k, 6), "".join(S)))

    # The vote
    nv = dict((k, len(v)) for k, v in dec["votes"].items())
    tot = max(1, sum(nv.values()) + len(dec["missing"]))
    # Ballots turn over one at a time; the tally fills once the last one is face up.
    S = ['<div class="kicker">The vote</div><h2>The %s votes</h2><div class="ballots">' % e(
        pn if stage == "solo" else "board")]
    for k, seat in enumerate(seats):
        a = answers.get(seat["id"])
        vote = ((debate.get(seat["id"]) or {}).get("final_vote") or a["vote"]) if a else "none"
        note = ("moved from %s" % VOTE_LABEL[moved[seat["id"]]]) if seat["id"] in moved else ""
        S.append('<div class="ballot" style="--i:%d"><div class="flip">'
                 '<div class="face back"><div class="av">%s</div><div class="who">%s</div><div class="small">votes</div></div>'
                 '<div class="face front %s"><div class="who">%s</div><div class="call">%s</div><div class="small">%s</div></div>'
                 '</div></div>' % (k, avatar_svg(seat["id"], 44, "v%d" % k), e(seat["short"]), vote, e(seat["short"]),
                                   e(VOTE_LABEL.get(vote, "No answer")), e(note)))
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
    S.append('<details class="late" style="%s"><summary>What each seat said</summary><div class="more">%s</div></details>' % (
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

    # Actions: by bucket for a solo founder, else the evidence first and then by owner seat
    if stage == "solo":
        groups = [(lab, [c for c in dec["conditions"] if c.get("bucket") == key]) for key, lab in BUCKETS]
    else:
        groups = [("From the evidence", [c for c in dec["conditions"] if c["from"] == "evidence"])]
        owners = []
        for c in dec["conditions"]:
            if c["from"] != "evidence" and c.get("owner") not in owners:
                owners.append(c.get("owner"))
        for o in owners:
            groups.append((seat_by[o]["short"] if o in seat_by else _seat_label(o),
                           [c for c in dec["conditions"] if c["from"] != "evidence" and c.get("owner") == o]))
    S = ['<div class="kicker">Actions</div><h2>What happens next</h2>',
         '<p class="lede reveal">%d action%s. The blockers from the evidence come first.</p><div class="actions reveal">' % (
             len(dec["conditions"]), "" if len(dec["conditions"]) == 1 else "s")]
    n = 0
    first = [c for c in dec["conditions"] if c["from"] == "evidence"][:6] or dec["conditions"][:3]
    for c in first:
        tag = (' <span class="small">(%s)</span>' % e(BUCKET_LABEL[c["bucket"]])) if stage == "solo" and c.get("bucket") in BUCKET_LABEL else ""
        S.append('<label><input type="checkbox" data-id="a%d"><span>%s%s</span></label>' % (n, e(_short(c["action"], 110)), tag))
        n += 1
    S.append("</div>")
    rest_html = []
    for title, group in groups:
        items_ = [c for c in group if c not in first]
        if not items_:
            continue
        rest_html.append('<div class="owner">%s</div>' % e(title))
        for c in items_:
            src = "raised by %s" % ", ".join(_seat_label(x) for x in c.get("raised_by", [])) if c["from"] != "evidence" else c.get("ref", "")
            rest_html.append('<label><input type="checkbox" data-id="a%d"><span>%s <span class="small">(%s)</span></span></label>' % (
                n, e(c["action"]), e(src)))
            n += 1
    if rest_html:
        S.append('<details class="reveal"><summary>All actions by %s</summary>' % ("bucket" if stage == "solo" else "owner") + '<div class="more actions">%s</div></details>' %
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
        "<title>%s: %s meeting</title><style>%s</style></head>" % (e(name), e(pn.lower()), MEETING_CSS),
        '<body data-fp="%s">' % e(fp),
        '<header class="top"><div class="brand"><h1>%s</h1><span class="meta">%s meeting &middot; %s</span>'
        '<button type="button" id="go-on" class="go-on" hidden>Paused while you read &middot; Continue &rsaquo;</button></div>'
        '<ol class="segments" aria-label="Meeting progress">%s</ol></header>' % (e(name), e(pn), e(ledger.now()[:10]), segs),
        "<main>%s</main>" % body,
        report_link,
        '<p class="fine">The four seats are sub-agents reading the evidence as four archetypes (money, growth, the build, '
        'running it), not people, and they quote no one. The evidence and the computed decision are the record.</p>',
        "<script>%s</script></body></html>" % MEETING_JS,
    ]) + "\n"


# ------------------------------------------------------------- transcript

def transcript(r, seats, answers, dec, qa, items, general, untested, debate=None, width=88):
    """The same meeting as plain text, for the terminal or a chat."""
    import textwrap
    debate = debate or {}
    stage = dec.get("stage") or "team"
    pn = panel_name(stage)
    t = r.get("target") or {}
    seat_by = dict((s["id"], s) for s in seats)
    moved = _first_votes(dec, answers)
    W = lambda s, ind="": textwrap.fill(s, width=width, initial_indent=ind, subsequent_indent=ind + "  ")
    L = ["=" * width, "%s MEETING: %s" % (pn.upper(), t.get("name") or "the product"), "=" * width, ""]
    L.append(W("The team presents. Proof verdict going in: %s. Verified findings: %d high or critical, %d medium." % (
        r["verdict"], sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] in ("critical", "high")),
        sum(1 for f in r["findings"] if f["status"] == "verified" and f["severity"] == "medium"))))
    L.append(W("At the table: " + "; ".join("%s (%s)" % (s["short"], _weighs(s).lower()) for s in seats)))
    L.append(W("Agenda: " + "; ".join("%d. %s" % (i, it["title"]) for i, it in enumerate(items, 1))))
    if untested:
        L.append(W("Not tested, not discussed: %s." % ", ".join(untested)))

    def speak(seat_id, risks):
        out = ["  %s:" % seat_by[seat_id]["short"]]
        for rk in risks:
            out.append(W("- %s: %s" % (rk["severity"].upper(), rk["risk"]), "    "))
            if rk.get("evidence"):
                out.append(W("evidence: %s" % ", ".join(rk["evidence"]), "      "))
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
            L.append("Seats:")
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
    if dec.get("debate"):
        L += ["", "-" * width, "THE DEBATE", "-" * width]
        for seat in seats:
            mine = [c for c in dec["debate"] if c["from"] == seat["id"]]
            for c in mine[:DEBATE_SHOWN]:
                L.append(W("%s to %s, on %s: %s" % (seat["short"], seat_by[c["to"]]["short"] if c["to"] in seat_by
                                                    else c["to"], _about_text(answers, c["to"], c["about"], 60),
                                                    _trim(c["challenge"], 220)), "  "))
            if len(mine) > DEBATE_SHOWN:
                L.append("    (and %d more from %s)" % (len(mine) - DEBATE_SHOWN, seat["short"]))
        for seat in seats:
            for x in ((debate.get(seat["id"]) or {}).get("concessions") or [])[:DEBATE_SHOWN]:
                L.append(W("%s concedes: %s" % (seat["short"], _trim(x, 180)), "  "))
        for vc in dec.get("vote_changes") or []:
            L.append(W("%s moves from %s to %s: %s" % (seat_by[vc["seat"]]["short"], VOTE_LABEL[vc["from"]].upper(),
                                                     VOTE_LABEL[vc["to"]].upper(), _trim(vc["why"], 200)), "  "))
        if not dec.get("vote_changes"):
            L.append("  No vote moved.")
    L += ["", "-" * width, "THE VOTE", "-" * width]
    for seat in seats:
        a = answers.get(seat["id"])
        if not a:
            L.append(W("%s: DID NOT ANSWER." % seat["short"], "  "))
            continue
        final = (debate.get(seat["id"]) or {}).get("final_vote") or a["vote"]
        L.append(W("%s: %s%s. %s" % (seat["short"], VOTE_LABEL[final].upper(),
                                     (" (first round %s)" % VOTE_LABEL[moved[seat["id"]]]) if seat["id"] in moved else "",
                                     a["summary"]), "  "))
    L += ["", "=" * width, "DECISION: %s" % dec["decision"].upper(), "=" * width]
    for x in dec["reasons"]:
        L.append(W("- %s" % x))
    top = dec["conditions"][:8]
    if top:
        L += ["", "Top actions:"]
        for c in top:
            L.append(W("- %s (%s)" % (c["action"], _who(c)), "  "))
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
        print("  %-9s %-10s veto: %s" % (s["id"], s["title"], ", ".join(s["veto"]) or "none"))
    return 0


def cmd_brief(a):
    if not os.path.isdir(ledger.root(a.dir)):
        raise BoardError("no proof/ folder. Run /proof-plan and some testing skills first.")
    seats = _pick(load_seats(), a.seats)
    r, refs = evidence(a.dir)
    if r.get("target") is None:
        raise BoardError("no target.json; run /proof-plan first so the board knows what it is judging.")
    stage = stage_of(r)
    pn = panel_name(stage)
    fp = fingerprint(r)
    bdir = _board_dir(a.dir)
    meta_path = os.path.join(bdir, "board.json")
    old = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            old = json.load(fh)
    old_seat_fps = old.get("seat_fingerprints") or {}
    ans_dir = os.path.join(bdir, "answers")
    triggered = vetoes(r, seats)
    _write(os.path.join(bdir, "presentation.md"), presentation(r, triggered, stage=stage))
    product = load_product(a.dir)
    if not product:
        print("Note: no proof/%s yet. Write the product dossier first (see the skill), or the seats "
              "judge from the evidence alone." % PRODUCT_FILE)
    qa = _load_qa(a.dir)
    paths, kept, cleared, seat_fps, meeting = [], [], 0, {}, []
    for seat in seats:
        p = os.path.join(bdir, "briefs", seat["id"] + ".md")
        pres_seat = seat_presentation(r, triggered, seat["skills"], product, a.dir, stage)
        text = seat_brief(seat, pres_seat, os.path.abspath(_answer_path(a.dir, seat["id"])), triggered, stage)
        _write(p, text)
        seat_fps[seat["id"]] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        ans = _answer_path(a.dir, seat["id"])
        same = old_seat_fps.get(seat["id"]) == seat_fps[seat["id"]]
        if os.path.exists(ans) and same and not a.fresh:
            kept.append(seat["id"])  # nothing this seat judges has changed: keep the answer
            continue
        if os.path.exists(ans):
            os.remove(ans)
            cleared += 1
        qa.pop(seat["id"], None)
        paths.append(p)
        meeting.append(seat)
    _write(os.path.join(bdir, "qa.json"), json.dumps(qa, indent=2, ensure_ascii=False) + "\n")
    os.makedirs(ans_dir, exist_ok=True)
    meta = {"fingerprint": fp, "seat_fingerprints": seat_fps, "seats": [s["id"] for s in seats],
            "stage": stage, "created": ledger.now()}
    if paths or a.fresh:
        _clear_debate(a.dir)  # a new first round makes the old debate stale
    else:
        for k in ("debate_fp", "debate_seats"):
            if k in old:
                meta[k] = old[k]
    _save_meta(a.dir, meta)
    if cleared:
        print("Cleared %d answer(s) whose evidence changed (the %s judges the current evidence)." % (cleared, pn))
    if kept:
        print("Kept %d answer(s) whose evidence is unchanged: %s." % (len(kept), ", ".join(kept)))
    print("%s presentation written to %s" % (ledger.OK, os.path.join(bdir, "presentation.md")))
    if triggered:
        print("The evidence already triggers %d veto(es); the %s will hold whatever the votes say." % (
            len(triggered), pn))
    if not paths:
        print("\nNo seat needs to meet again: every answer stands. Next: board.py debate-brief (or tally)")
        return 0
    if not a.separate:
        panel = os.path.join(bdir, "briefs", "panel.md")
        _write(panel, panel_brief(meeting, seat_presentation(r, triggered, ["*"], product, a.dir, stage), a.dir,
                                  triggered, stage))
        print("\nConvene %d seat(s) of the %s in ONE Agent call (subagent_type general-purpose, prompt 'Read <brief> "
              "and follow it exactly. It is your whole brief.'):\n  %s" % (len(meeting), pn, panel))
        print("\nThen: board.py check, board.py debate-brief, board.py debate-check, board.py tally")
        return 0
    print("\nConvene %s: send ONE message with one Agent call per seat below "
          "(subagent_type general-purpose, prompt 'Read <brief> and follow it exactly. It is your whole brief.'):"
          % ("the %s" % pn if not kept else "only these %d seat(s)" % len(paths)))
    for p in paths:
        print("  " + p)
    print("\nThen: board.py check, board.py debate-brief, board.py debate-check, board.py tally")
    return 0


def _context(d):
    meta = _load_meta(d)
    r, refs = evidence(d)
    stage = stage_of(r)
    good, bad = load_answers(d, meta, refs, stage)
    return meta, r, refs, stage, good, bad


def cmd_check(a):
    meta, r, refs, stage, good, bad = _context(a.dir)
    if fingerprint(r) != meta["fingerprint"]:
        print("%s the evidence changed since the brief. Run board.py brief again." % ledger.BAD)
        return 1
    print("%d of %d seats answered validly." % (len(good), len(meta["seats"])))
    for sid, probs in bad.items():
        print("  %s %s: %s" % (ledger.BAD, sid, "; ".join(probs)))
    return 0 if not bad else 1


def cmd_save(a):
    meta = _load_meta(a.dir)
    if a.seat not in meta["seats"]:
        raise BoardError("%s is not on this board" % a.seat)
    r, refs = evidence(a.dir)
    try:
        obj = json.loads(sys.stdin.read())
    except ValueError as ex:
        raise BoardError("stdin is not valid JSON: %s" % ex)
    probs = validate_answer(obj, a.seat, refs, stage_of(r), [s["id"] for s in load_seats()])
    if probs:
        raise BoardError("refusing to save %s: %s" % (a.seat, "; ".join(probs)))
    _write(_answer_path(a.dir, a.seat), json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    print("%s saved %s" % (ledger.OK, a.seat))
    return 0


def cmd_answer(a):
    meta, r, refs, stage, good, _ = _context(a.dir)
    if a.seat not in good:
        raise BoardError("%s has no valid answer to respond to" % a.seat)
    qs = good[a.seat].get("questions") or []
    if not 1 <= a.number <= len(qs):
        raise BoardError("%s asked %d question(s); pick 1 to %d" % (a.seat, len(qs), len(qs)))
    qa = _load_qa(a.dir)
    qa.setdefault(a.seat, {})[str(a.number - 1)] = a.text
    _write(os.path.join(_board_dir(a.dir), "qa.json"), json.dumps(qa, indent=2, ensure_ascii=False) + "\n")
    print("%s answered %s question %d" % (ledger.OK, _seat_label(a.seat), a.number))
    return 0


def cmd_debate_brief(a):
    meta, r, refs, stage, good, bad = _context(a.dir)
    if fingerprint(r) != meta["fingerprint"]:
        raise BoardError("the evidence changed since the brief. Run board.py brief again.")
    if len(good) < 2:
        raise BoardError("the debate needs at least two valid first-round answers (%d now). Run board.py check."
                         % len(good))
    seats = [s for s in load_seats() if s["id"] in good]
    _clear_debate(a.dir)
    os.makedirs(_debate_dir(a.dir), exist_ok=True)
    path = os.path.join(_board_dir(a.dir), "briefs", "debate.md")
    _write(path, debate_brief(seats, good, a.dir, stage))
    meta["debate_fp"] = answers_fp(good)
    meta["debate_seats"] = [s["id"] for s in seats]
    _save_meta(a.dir, meta)
    if bad:
        print("Note: no valid first-round answer from %s, so they sit out the debate." % ", ".join(sorted(bad)))
    print("%s debate brief written for %d seat(s)." % (ledger.OK, len(seats)))
    print("\nRun the debate in ONE Agent call (subagent_type general-purpose, prompt 'Read <brief> and follow it "
          "exactly. It is your whole brief.'):\n  %s" % path)
    print("\nThen: board.py debate-check, board.py tally")
    return 0


def cmd_debate_check(a):
    meta, r, refs, stage, good, _ = _context(a.dir)
    dgood, dbad, state = load_debate(a.dir, meta, good, refs)
    if state == "none":
        print("%s no debate yet. Run board.py debate-brief first." % ledger.BAD)
        return 1
    print("%d of %d seats debated validly." % (len(dgood), len(meta.get("debate_seats") or [])))
    for sid, probs in dbad.items():
        print("  %s %s: %s" % (ledger.BAD, sid, "; ".join(probs)))
    return 0 if not dbad else 1


def _build(d):
    meta = _load_meta(d)
    seats = [s for s in load_seats() if s["id"] in meta["seats"]]
    r, refs = evidence(d)
    stage = stage_of(r)
    stale = fingerprint(r) != meta["fingerprint"]
    r["report_html"] = os.path.isfile(os.path.join(ledger.root(d), "report.html"))
    good, bad = load_answers(d, meta, refs, stage)
    dgood, dbad, _ = load_debate(d, meta, good, refs)
    final = dict((sid, dict(ans, vote=dgood[sid]["final_vote"]) if sid in dgood else ans)
                 for sid, ans in good.items())
    triggered = vetoes(r, seats)
    dec = decide(r, seats, final, triggered, stage)
    challenges, changes = debate_record(seats, good, dgood)
    dec["debate"] = challenges
    dec["vote_changes"] = changes
    dec["first_votes"] = dict((sid, ans["vote"]) for sid, ans in good.items())
    qa = _load_qa(d)
    bdir = _board_dir(d)
    items, general, untested = agenda(r, seats, good, triggered)
    text = transcript(r, seats, good, dec, qa, items, general, untested, dgood)
    _write(os.path.join(bdir, "minutes.md"), minutes_md(r, seats, good, dec, qa, items, general, untested, dgood))
    _write(os.path.join(bdir, "transcript.txt"), text)
    _write(os.path.join(bdir, "meeting.html"),
           meeting_html(r, seats, good, dec, qa, meta["fingerprint"], items, general, untested, dgood))
    debate_path = os.path.join(bdir, "debate.md")
    if dgood:
        _write(debate_path, debate_md(r, seats, good, dec, dgood, stage))
    elif os.path.exists(debate_path):
        os.remove(debate_path)
    with open(os.path.join(bdir, "decision.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(dec, generated=ledger.now(), invalid=bad, debate_invalid=dbad), fh, indent=2,
                  ensure_ascii=False)
    return dec, bad, stale, bdir, text, dbad


def cmd_tally(a):
    dec, bad, stale, bdir, text, dbad = _build(a.dir)
    if not a.quiet:
        print(text)
    if stale:
        print("%s the evidence changed since the brief; the decision uses today's evidence but the seats "
              "judged the old one. Run board.py brief again for a fresh meeting." % ledger.BAD)
    if dbad:
        print("%s %d debate file(s) not counted (their first-round vote stands): %s. See board.py debate-check." % (
            ledger.BAD, len(dbad), ", ".join(sorted(dbad))))
    print("%s decision: %s" % (dec["panel_name"], dec["decision"]))
    for x in dec["reasons"]:
        print("  - %s" % x)
    print("Votes: %d go, %d go with conditions, %d hold, %d missing%s" % (
        len(dec["votes"]["go"]), len(dec["votes"]["go_with_conditions"]), len(dec["votes"]["hold"]),
        len(dec["missing"]), (" (%d moved in the debate)" % len(dec["vote_changes"])) if dec["vote_changes"] else ""))
    print("%d action(s). Minutes: %s  Meeting: %s" % (
        len(dec["conditions"]), os.path.join(bdir, "minutes.md"), os.path.join(bdir, "meeting.html")))
    if dec["debate"]:
        print("Debate: %s" % os.path.join(bdir, "debate.md"))
    print("Next: /proof-business-plan puts this decision, the numbers and every section into one plan.")
    if a.open:
        open_page(os.path.join(bdir, "meeting.html"))
    return {GO: 0, GO_COND: 0, HOLD: 1}[dec["decision"]]


def open_page(path):
    return ledger.open_page(path, "the board meeting")


def cmd_meeting(a):
    bdir = _build(a.dir)[3]
    print("%s wrote %s" % (ledger.OK, os.path.join(bdir, "meeting.html")))
    if a.open:
        open_page(os.path.join(bdir, "meeting.html"))
    return 0


def cmd_play(a):
    """Play the meeting in the terminal, line by line, with no input needed."""
    import time
    text = _build(a.dir)[4]
    for line in text.splitlines():
        print(line)
        sys.stdout.flush()
        if a.speed > 0 and line.strip():
            time.sleep(min(2.5, 0.25 + len(line) / 260.0) / a.speed)
    return 0


def cmd_status(a):
    meta, r, refs, stage, good, bad = _context(a.dir)
    for sid in meta["seats"]:
        if sid in good:
            print("  %s %-9s %s" % (ledger.OK, sid, VOTE_LABEL[good[sid]["vote"]]))
        else:
            print("  %s %-9s %s" % (ledger.BAD, sid, "; ".join(bad.get(sid, []))))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    # --dir also works after the subcommand, like every other Proof tool.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        return sub.add_parser(name, help=help_, parents=[common])

    s = add("seats", "list the board")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_seats)
    b = add("brief", "write the presentation and one brief per seat")
    b.add_argument("--seats", help="comma-separated subset, e.g. investor,builder")
    b.add_argument("--keep-answers", action="store_true",
                   help="kept for old scripts; answers are now kept by default while a seat's evidence is unchanged")
    b.add_argument("--fresh", action="store_true", help="clear every answer and convene the whole board")
    b.add_argument("--separate", action="store_true",
                   help="one agent per seat: more independent readings, about four times the fixed agent cost")
    b.set_defaults(func=cmd_brief)
    add("check", "validate the seats' answers").set_defaults(func=cmd_check)
    sv = add("save", "file one seat's answer from stdin")
    sv.add_argument("seat")
    sv.set_defaults(func=cmd_save)
    an = add("answer", "record the team's answer to a seat's question")
    an.add_argument("seat")
    an.add_argument("number", type=int, help="question number, starting at 1")
    an.add_argument("text")
    an.set_defaults(func=cmd_answer)
    add("debate-brief", "give every seat the other seats' answers for the debate round").set_defaults(
        func=cmd_debate_brief)
    add("debate-check", "validate the debate files").set_defaults(func=cmd_debate_check)
    t = add("tally", "compute the decision, print the meeting, write minutes, the debate and the meeting page")
    t.add_argument("--quiet", action="store_true", help="print only the decision, not the whole meeting")
    t.add_argument("--open", action="store_true", help="open the meeting page in the default browser")
    t.set_defaults(func=cmd_tally)
    m = add("meeting", "rebuild the meeting page")
    m.add_argument("--open", action="store_true", help="open it in the default browser")
    m.set_defaults(func=cmd_meeting)
    pl = add("play", "play the meeting in the terminal, paced, with no input needed")
    pl.add_argument("--speed", type=float, default=1.0, help="2 is twice as fast; 0 prints at once")
    pl.set_defaults(func=cmd_play)
    add("status", "who has answered").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except BoardError as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
