#!/usr/bin/env python3
"""Measure the answer quality of a retrieval-augmented (RAG) feature. Standard library only.

This tool never calls a model. You write a set of cases (questions, who asks them,
which documents should answer them), run them against your own app, save what came
back, and this tool scores the results: were the right documents retrieved, do the
citations point at documents that were retrieved and relevant, does the app abstain
when nothing relevant exists, are the answers faithful, and did any user ever see
another user's documents.

    python3 rag.py template --out proof/rag/cases.json
    python3 rag.py score cases.json results.json --k 5
    python3 rag.py record cases.json results.json --owners docs.json
    python3 rag.py status
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-rag"
TEMPLATE_MARK = "_proof_rag_template"
DEFAULT_ABSTAIN = ["i don't know", "i do not know", "not in your documents", "couldn't find", "could not find",
                   "no relevant", "don't have enough information", "do not have enough information"]

# id, label, unit, default budget, op, severity when it fails, budget flag (None = fixed)
METRICS = [
    ("retrieval_hit_rate", "Retrieval finds an expected document in the top k", "fraction", 0.85, ">=", "medium",
     "min_hit"),
    ("citation_precision", "Cited documents are relevant", "fraction", 0.9, ">=", "medium", "min_precision"),
    ("citation_support", "Every cited document was actually retrieved", "fraction", 1.0, ">=", "high", None),
    ("abstain_rate", "Abstains when nothing relevant exists", "fraction", 0.9, ">=", "medium", "min_abstain"),
    ("answer_rate", "Answers when the documents hold the answer", "fraction", 0.9, ">=", "medium", "min_answer"),
    ("faithfulness", "Answers are faithful to the sources (judged)", "fraction", 0.9, ">=", "high", "min_faithful"),
    ("must_mention_rate", "Answers mention the required facts", "fraction", 0.9, ">=", "low", "min_mention"),
    ("cross_tenant_leaks", "No user sees another user's documents", "count", 0, "<=", "critical", None),
]

TEMPLATE = {
    TEMPLATE_MARK: "Cases for rag.py. Replace these examples with 30 to 100 of your own.",
    "cases": [
        {"id": "refund-policy", "question": "What is our refund window?", "user": "alice",
         "expected_docs": ["alice/policies.pdf"], "must_mention": ["30 days"]},
        {"id": "two-docs", "question": "Compare the 2025 and 2026 budgets.", "user": "alice",
         "expected_docs": ["alice/budget-2025.xlsx", "alice/budget-2026.xlsx"]},
        {"id": "not-in-docs", "question": "What is the CEO's home address?", "user": "alice",
         "expected_docs": []},
        {"id": "other-tenant-bait", "question": "Summarise Bob's contract.", "user": "alice",
         "expected_docs": []},
    ],
}


# ---------------------------------------------------------------- reading

def _load(path, key):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit("cannot read %s: %s" % (path, e))
    rows = data.get(key) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise SystemExit("%s should be a list, or an object with a %r list" % (path, key))
    return rows


def load_cases(path):
    cases, seen = [], set()
    for n, c in enumerate(_load(path, "cases"), 1):
        if not isinstance(c, dict) or not c.get("id"):
            raise SystemExit("case %d in %s needs an id" % (n, path))
        if not isinstance(c.get("expected_docs"), list):
            raise SystemExit("case %r needs expected_docs (an empty list means the documents hold no answer)" % c["id"])
        if c["id"] in seen:
            raise SystemExit("duplicate case id %r" % c["id"])
        seen.add(c["id"])
        cases.append(c)
    if not cases:
        raise SystemExit("no cases in %s" % path)
    return cases


def load_results(path):
    out = {}
    for n, r in enumerate(_load(path, "results"), 1):
        if not isinstance(r, dict) or not r.get("id"):
            raise SystemExit("result %d in %s needs the id of its case" % (n, path))
        out[r["id"]] = r
    return out


def load_owners(path):
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit("cannot read %s: %s" % (path, e))
    if not isinstance(data, dict):
        raise SystemExit("%s should map each document id to its user or tenant" % path)
    return dict((str(k), str(v)) for k, v in data.items())


def _who(row):
    v = row.get("user", row.get("tenant"))
    return None if v is None else str(v)


def _retrieved(result):
    """[(doc, owner or None)] in rank order."""
    out = []
    for item in result.get("retrieved") or []:
        if isinstance(item, dict):
            doc = item.get("doc", item.get("id"))
            if doc is not None:
                out.append((str(doc), _who(item)))
        elif item is not None:
            out.append((str(item), None))
    return out


def abstained(result, phrases):
    if isinstance(result.get("abstained"), bool):
        return result["abstained"]
    text = (result.get("answer") or "").lower().replace("\u2019", "'")
    return any(p in text for p in phrases)


# ---------------------------------------------------------------- scoring

def compute(cases, results, k=5, phrases=None, owners=None):
    """Return {"metrics": {id: {value, n, failing: [case ids], detail}}, "missing": [case ids], "leaks": [...]}."""
    phrases = [p.lower() for p in (phrases or DEFAULT_ABSTAIN)]
    owners = owners or {}
    acc = dict((m[0], {"num": 0, "den": 0, "failing": []}) for m in METRICS)
    leaks, missing, tenant_info = [], [], False

    def tally(mid, ok, cid):
        acc[mid]["den"] += 1
        if ok:
            acc[mid]["num"] += 1
        else:
            acc[mid]["failing"].append(cid)

    for c in cases:
        cid = c["id"]
        r = results.get(cid)
        if r is None:
            missing.append(cid)
            continue
        expected = set(str(d) for d in c["expected_docs"])
        retrieved = _retrieved(r)
        retrieved_docs = [d for d, _ in retrieved]
        cited = [str(d) for d in (r.get("cited") or [])]
        absent = abstained(r, phrases)
        if expected:
            if "retrieved" in r:
                tally("retrieval_hit_rate", bool(expected & set(retrieved_docs[:k])), cid)
            tally("answer_rate", not absent, cid)
            if cited:
                good = sum(1 for d in cited if d in expected)
                acc["citation_precision"]["num"] += good
                acc["citation_precision"]["den"] += len(cited)
                if good < len(cited):
                    acc["citation_precision"]["failing"].append(cid)
        else:
            tally("abstain_rate", absent, cid)
        if cited and "retrieved" in r:
            tally("citation_support", all(d in retrieved_docs for d in cited), cid)
        if isinstance(r.get("faithful"), bool):
            tally("faithfulness", r["faithful"], cid)
        needed = [str(p) for p in c.get("must_mention") or []]
        if needed and not absent:
            text = (r.get("answer") or "").lower()
            tally("must_mention_rate", all(p.lower() in text for p in needed), cid)
        user = _who(c)
        if user is not None:
            seen = dict(retrieved)
            for d in cited:
                seen.setdefault(d, None)
            bad = []
            for doc, owner in seen.items():
                owner = owner if owner is not None else owners.get(doc)
                if owner is None:
                    continue
                tenant_info = True
                if owner != user:
                    bad.append("%s (belongs to %s)" % (doc, owner))
            if bad:
                leaks.append({"case": cid, "user": user, "docs": bad})

    metrics = {}
    for mid, label, unit, _, _, _, _ in METRICS:
        a = acc[mid]
        if mid == "cross_tenant_leaks":
            if tenant_info:
                metrics[mid] = {"value": len(leaks), "n": len(leaks), "failing": [x["case"] for x in leaks]}
            continue
        if a["den"]:
            metrics[mid] = {"value": a["num"] / float(a["den"]), "n": a["den"], "failing": a["failing"]}
    return {"metrics": metrics, "missing": missing, "leaks": leaks, "cases": len(cases)}


def budgets(a):
    out = {}
    for mid, _, _, default, op, _, flag in METRICS:
        v = getattr(a, flag, None) if flag else None
        out[mid] = (default if v is None else v, op)
    return out


def _passes(value, budget, op):
    return value >= budget if op == ">=" else value <= budget


def _fmt(mid, value):
    return "%d" % value if mid == "cross_tenant_leaks" else "%.0f%%" % (value * 100)


NA_REASON = {
    "retrieval_hit_rate": "no answerable case reported what it retrieved",
    "citation_precision": "no answerable case cited a document",
    "citation_support": "no case reported both citations and what it retrieved",
    "abstain_rate": "no case expects an abstention (expected_docs empty)",
    "answer_rate": "no answerable cases",
    "faithfulness": "no result carries a faithful judgement",
    "must_mention_rate": "no case lists must_mention facts, or every such case abstained",
    "cross_tenant_leaks": "no user on the cases or owner on the documents, so leaks cannot be judged",
}


# ---------------------------------------------------------------- commands

def cmd_template(a):
    out = a.out or os.path.join(ledger.root(a.dir), "rag", "cases.json")
    ledger.guard_out(out, a.dir, a.force, ours=TEMPLATE_MARK)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(TEMPLATE, fh, indent=2)
    print("%s wrote a cases template to %s. Replace the examples with 30 to 100 of your own." % (ledger.OK, out))
    return 0


def _compute(a):
    phrases = DEFAULT_ABSTAIN + [p.lower() for p in (a.abstain_phrase or [])]
    return compute(load_cases(a.cases), load_results(a.results), a.k, phrases, load_owners(a.owners))


def cmd_score(a):
    r = _compute(a)
    b = budgets(a)
    if a.json:
        print(json.dumps({"budgets": dict((k, {"budget": v[0], "op": v[1]}) for k, v in b.items()), **r}, indent=2))
        return 0
    print("RAG quality over %d case(s)%s:" % (r["cases"], (", %d with no result" % len(r["missing"])) if r["missing"] else ""))
    for mid, label, _, _, _, _, _ in METRICS:
        m = r["metrics"].get(mid)
        if m is None:
            print("  -- %-22s not applicable: %s" % (mid, NA_REASON[mid]))
            continue
        budget, op = b[mid]
        ok = _passes(m["value"], budget, op)
        print("  %s %-22s %s (budget %s %s)%s" % (ledger.OK if ok else ledger.BAD, mid, _fmt(mid, m["value"]), op,
                                                 _fmt(mid, budget),
                                                 "" if ok else "  failing: %s" % ", ".join(m["failing"][:8])))
    for leak in r["leaks"]:
        print("  !! %s (asked by %s) saw: %s" % (leak["case"], leak["user"], "; ".join(leak["docs"][:5])))
    return 0


def _copy(d, path, name):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), name)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


def cmd_record(a):
    r = _compute(a)
    b = budgets(a)
    ev = [_copy(a.dir, a.cases, "cases.json"), _copy(a.dir, a.results, "results.json")]
    if a.owners:
        ev.append(_copy(a.dir, a.owners, "owners.json"))
    ledger.init_coverage(a.dir, SKILL, [(m[0], m[1]) for m in METRICS])
    before = set(f["id"] for f in ledger.load_findings(a.dir, SKILL))
    written, scores, failing = set(), [], []

    def write(fid, title, sev, mid, desc, fix):
        path = ledger.finding_path(a.dir, SKILL, fid)
        if os.path.exists(path) and ledger.load_finding(a.dir, SKILL, fid)["status"] == "false_positive":
            written.add(fid)
            return
        f = ledger.new_finding(SKILL, title[:160], sev, id=fid, category="rag:%s" % mid, evidence=ev,
                               description=desc[:400], fix=fix)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
        written.add(fid)

    for mid, label, unit, _, _, sev, _ in METRICS:
        m = r["metrics"].get(mid)
        if m is None:
            ledger.mark_coverage(a.dir, SKILL, mid, "not_applicable", note=NA_REASON[mid])
            continue
        budget, op = b[mid]
        ok = _passes(m["value"], budget, op)
        ledger.mark_coverage(a.dir, SKILL, mid, "covered", evidence=ev,
                             note="%s over %d %s" % (_fmt(mid, m["value"]), m["n"],
                                                     "leak(s)" if mid == "cross_tenant_leaks" else "check(s)"))
        scores.append({"id": mid, "value": round(m["value"], 4), "unit": unit, "budget": budget, "op": op,
                       "source": "rag.py", "evidence": ev})
        if ok:
            continue
        failing.append(mid)
        if mid == "cross_tenant_leaks":
            for leak in r["leaks"]:
                write(ledger.slug("rag-leak-%s" % leak["case"])[:60],
                      "Case %s: %s was shown another user's documents" % (leak["case"], leak["user"]), "critical",
                      mid, "Documents: %s" % "; ".join(leak["docs"]),
                      "Filter retrieval by the signed-in user or tenant on the server, before ranking, and test it.")
            continue
        fix = {
            "citation_support": "Only let the answer cite documents the retriever returned; drop or flag any other citation.",
            "abstain_rate": "When nothing relevant is retrieved, say so instead of answering from the model's own knowledge.",
            "answer_rate": "Check retrieval and the prompt: the documents hold the answer but the app refused or missed it.",
            "faithfulness": "Ground answers in the retrieved text, and check claims against it before showing them.",
        }.get(mid, "Look at the failing cases, fix retrieval or the prompt, then run the cases again.")
        title = "%s: %s against a budget of %s %s" % (label, _fmt(mid, m["value"]), op, _fmt(mid, budget))
        write("rag-" + mid.replace("_", "-"), title, sev, mid,
              "Failing cases: %s" % ", ".join(m["failing"]), fix)
    for fid in before - written:
        f = ledger.load_finding(a.dir, SKILL, fid)
        if f["status"] != "false_positive":
            os.remove(ledger.finding_path(a.dir, SKILL, fid))
    if scores:
        ledger.write_scores(a.dir, SKILL, scores)
    note = (" %d case(s) had no result: %s." % (len(r["missing"]), ", ".join(r["missing"][:10]))) if r["missing"] else ""
    print("%s recorded %d metric(s) over %d case(s); %d failing%s.%s" % (
        ledger.OK if not failing else ledger.BAD, len(scores), r["cases"], len(failing),
        (": " + ", ".join(failing)) if failing else "", note))
    return 0


def cmd_status(a):
    scores = [s for s in ledger.load_all_scores(a.dir) if s["skill"] == SKILL]
    if not scores:
        print("no RAG run recorded yet.")
        return 0
    for m in scores[0]["metrics"]:
        mark = ledger.OK if ledger.metric_passes(m) else ledger.BAD
        print("  %s %-22s %s (budget %s %s)" % (mark, m["id"], _fmt(m["id"], m["value"]), m["op"],
                                                _fmt(m["id"], m["budget"])))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template", help="write a cases file to start from")
    t.add_argument("--out", help="default: <dir>/rag/cases.json")
    t.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    t.set_defaults(func=cmd_template)
    for name, func, hlp in (("score", cmd_score, "print the metrics"), ("record", cmd_record, "write them to the ledger")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("cases")
        p.add_argument("results")
        p.add_argument("--k", type=int, default=5, help="how many retrieved documents count as a hit (default 5)")
        p.add_argument("--owners", help="JSON mapping each document id to its user or tenant")
        p.add_argument("--abstain-phrase", action="append", help="extra phrase that means the app abstained")
        p.add_argument("--min-hit", type=float, help="retrieval hit rate budget (default 0.85)")
        p.add_argument("--min-precision", type=float, help="citation precision budget (default 0.9)")
        p.add_argument("--min-abstain", type=float, help="abstain rate budget on unanswerable cases (default 0.9)")
        p.add_argument("--min-answer", type=float, help="answer rate budget on answerable cases (default 0.9)")
        p.add_argument("--min-faithful", type=float, help="faithfulness budget (default 0.9)")
        p.add_argument("--min-mention", type=float, help="must-mention budget (default 0.9)")
        if name == "score":
            p.add_argument("--json", action="store_true")
        p.set_defaults(func=func)
    sub.add_parser("status", help="recorded metrics and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
