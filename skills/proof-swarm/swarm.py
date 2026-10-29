#!/usr/bin/env python3
"""The scenario-agent swarm engine. Standard library only, no network.

Other Proof skills (exploratory testing, AI red-teaming, user panels) use this to
deal a crowd of personas from a profile, fan a brief out to one sub-agent per
persona, and tally honest answers. The model orchestrates the sub-agents; this
tool does the bookkeeping and never answers for an agent.

State lives under ``proof/swarm/<run-name>/``:

    swarm.json      n, seed, profile, the exact brief text, created
    cards.json      the dealt persona cards (same seed + profile => same cards)
    briefs/<id>.md  one rendered brief per agent
    answers/<id>.json   one answer per agent (the sub-agent writes these)
    results.json / results.md   the tally

    python3 swarm.py init --profile p.json --brief b.md [--n 100 | --quick] [--seed S] [--keep-answers]
    python3 swarm.py prompts [--wave 10] [--max-attempts 2]
    python3 swarm.py check
    python3 swarm.py save <id>        # answer JSON on stdin
    python3 swarm.py tally
    python3 swarm.py status

    Global options go before the command: --dir proof, --run <name>.
"""

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

# A neutral, varied pool of short first names. No real-person reference intended.
NAMES = [
    "Ada", "Ben", "Cleo", "Dara", "Esa", "Finn", "Gwen", "Hana", "Iris", "Jude",
    "Kai", "Lena", "Milo", "Nadia", "Omar", "Priya", "Quinn", "Rosa", "Sven", "Tara",
    "Uma", "Viktor", "Wren", "Xan", "Yara", "Zane", "Arlo", "Bea", "Cato", "Dex",
    "Elsa", "Fern", "Gus", "Halle", "Ivo", "Juno", "Koa", "Liv", "Mira", "Noor",
    "Otto", "Pia", "Remy", "Soren", "Thea", "Ursa", "Vera", "Wade", "Yosef", "Zoe",
]

RUN_ROOT = "swarm"
_END_BLOCK = (
    "\n---\n\n"
    "Answer as this person would, honestly. Most people are not easily impressed:\n"
    "do not be agreeable, do not invent facts beyond this brief, and do not soften a\n"
    "'no'. If something is unclear, decide the way this person would when unsure.\n\n"
    "Create the file at:\n  {out}\n"
    "Write ONLY this JSON into it (no markdown fences, no extra text):\n\n{answer_schema}\n"
)


class SwarmError(ValueError):
    pass


def _run_dir(d, name):
    return os.path.join(ledger.root(d), RUN_ROOT, name)


# ----------------------------------------------------------------- profile

def _validate_profile(p):
    if not isinstance(p, dict):
        raise SwarmError("profile must be a JSON object")
    name = p.get("name", "")
    if not isinstance(name, str) or not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise SwarmError("profile 'name' must be a slug (lowercase letters, digits, hyphens)")
    dims = p.get("dimensions")
    if not isinstance(dims, dict) or not dims:
        raise SwarmError("profile needs at least one entry in 'dimensions'")
    for dim, options in dims.items():
        if not isinstance(options, list) or not options:
            raise SwarmError("dimension %r must be a non-empty list" % dim)
        total = 0.0
        for o in options:
            if not isinstance(o, dict) or "value" not in o or "share" not in o:
                raise SwarmError("each option in %r needs 'value' and 'share'" % dim)
            total += num.parse_number(o["share"], field="%s.share" % dim)
        if abs(total - 1.0) > 0.01:
            raise SwarmError("shares in dimension %r sum to %.3f, not 1.0" % (dim, total))
    answer = p.get("answer")
    if not isinstance(answer, dict) or "outcome" not in answer or "fields" not in answer:
        raise SwarmError("profile 'answer' needs 'outcome' and 'fields'")
    fields = answer["fields"]
    if not isinstance(fields, dict) or not fields:
        raise SwarmError("'answer.fields' must be a non-empty object")
    outcome = answer["outcome"]
    if outcome not in fields:
        raise SwarmError("answer.outcome %r is not one of the fields" % outcome)
    if fields[outcome].get("type") != "boolean":
        raise SwarmError("the outcome field %r must have type boolean" % outcome)
    for fid, spec in fields.items():
        if spec.get("type") not in ("boolean", "string", "number", "enum"):
            raise SwarmError("field %r needs a type of boolean, string, number or enum" % fid)
        if spec["type"] == "enum" and not spec.get("values"):
            raise SwarmError("enum field %r needs a 'values' list" % fid)
    return p


def _deal(profile, n, seed):
    rng = random.Random(seed)
    cards = []
    for i in range(n):
        card = {"id": "a%03d" % (i + 1), "name": NAMES[rng.randrange(len(NAMES))]}
        for dim, options in profile["dimensions"].items():
            weights = [num.parse_number(o["share"], field=dim) for o in options]
            chosen = rng.choices(options, weights=weights, k=1)[0]
            card[dim] = chosen["value"]
            if chosen.get("detail"):
                card["_detail_" + dim] = chosen["detail"]
        cards.append(card)
    return cards


def _answer_schema_text(profile):
    fields = profile["answer"]["fields"]
    lines = ["{"]
    items = list(fields.items())
    for i, (fid, spec) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        prompt = spec.get("prompt", "")
        if spec["type"] == "boolean":
            hint = "true or false"
        elif spec["type"] == "number":
            hint = "a number"
        elif spec["type"] == "enum":
            hint = "one of: " + ", ".join(map(str, spec["values"]))
        else:
            hint = "text"
        lines.append('  "%s": <%s>%s   // %s' % (fid, hint, comma, prompt) if prompt else '  "%s": <%s>%s' % (fid, hint, comma))
    lines.append("}")
    return "\n".join(lines)


# -------------------------------------------------------------------- init

def _answers_present(run):
    folder = os.path.join(run, "answers")
    return os.path.isdir(folder) and any(n.endswith(".json") for n in os.listdir(folder))


def cmd_init(a):
    with open(a.profile, encoding="utf-8") as fh:
        profile = _validate_profile(json.load(fh))
    with open(a.brief, encoding="utf-8") as fh:
        brief = fh.read()
    if not brief.strip():
        raise SwarmError("the brief is empty")
    n = 20 if a.quick else a.n
    if n < 1:
        raise SwarmError("n must be at least 1")
    seed = a.seed if a.seed is not None else random.SystemRandom().randrange(1, 10 ** 9)
    run = _run_dir(a.dir, profile["name"])

    if _answers_present(run):
        old = None
        swarm_path = os.path.join(run, "swarm.json")
        if os.path.exists(swarm_path):
            with open(swarm_path, encoding="utf-8") as fh:
                old = json.load(fh).get("brief")
        if a.keep_answers:
            if old != brief:
                raise SwarmError("refusing --keep-answers: the brief changed since the last run, so old "
                                 "answers would not match it. Re-run without --keep-answers to start fresh.")
        else:
            _wipe(os.path.join(run, "answers"))
            _wipe(os.path.join(run, "briefs"))
            print("Cleared answers and briefs from a previous run (a new brief must not inherit old answers).")
    attempts = os.path.join(run, "attempts.json")
    if os.path.exists(attempts) and not (a.keep_answers and _answers_present(run)):
        os.remove(attempts)

    cards = _deal(profile, n, seed)
    os.makedirs(os.path.join(run, "briefs"), exist_ok=True)
    os.makedirs(os.path.join(run, "answers"), exist_ok=True)
    _write(os.path.join(run, "swarm.json"), {"name": profile["name"], "n": n, "seed": seed,
                                             "profile": profile, "brief": brief, "created": ledger.now()})
    _write(os.path.join(run, "cards.json"), cards)
    print("Dealt %d cards for run %r (seed %d). Next: prompts" % (n, profile["name"], seed))
    return 0


def _wipe(folder):
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.endswith((".json", ".md")):
                os.remove(os.path.join(folder, name))


# ----------------------------------------------------------------- prompts

def _load_run(d, name=None):
    base = os.path.join(ledger.root(d), RUN_ROOT)
    if name is None:
        runs = sorted(os.listdir(base)) if os.path.isdir(base) else []
        if not runs:
            raise SwarmError("no swarm run under %s. Run init first." % base)
        if len(runs) > 1:
            raise SwarmError("more than one run (%s); pass --run <name>" % ", ".join(runs))
        name = runs[0]
    run = _run_dir(d, name)
    with open(os.path.join(run, "swarm.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    with open(os.path.join(run, "cards.json"), encoding="utf-8") as fh:
        cards = json.load(fh)
    return run, meta, cards


def _render_brief(template, card, meta, out_path):
    # The honesty block and the answer instructions always end the brief, whatever
    # the author wrote above them.
    template = template.rstrip() + "\n" + _END_BLOCK
    profile = meta["profile"]
    values = {"id": card["id"], "name": card["name"], "out": os.path.abspath(out_path),
              "purpose": profile.get("purpose", ""), "answer_schema": _answer_schema_text(profile)}
    for dim in profile["dimensions"]:
        values[dim] = card.get(dim, "")
        values[dim + ".detail"] = card.get("_detail_" + dim, "")
    # Resolve {card.<dim>} style too.
    text = template
    for key, val in values.items():
        text = text.replace("{card.%s}" % key, str(val)).replace("{%s}" % key, str(val))
    unresolved = set()
    i = 0
    while True:
        j = text.find("{", i)
        if j < 0:
            break
        k = text.find("}", j)
        if k < 0:
            break
        token = text[j + 1:k]
        if token and all(c.isalnum() or c in "._-" for c in token):
            unresolved.add(token)
        i = k + 1
    if unresolved:
        raise SwarmError("brief uses placeholders with no value: %s" % ", ".join(sorted(unresolved)))
    return text


def _answer_path(run, cid):
    return os.path.join(run, "answers", cid + ".json")


def _load_attempts(run):
    path = os.path.join(run, "attempts.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def cmd_prompts(a):
    run, meta, cards = _load_run(a.dir, a.run)
    if a.max_attempts < 1:
        raise SwarmError("--max-attempts must be at least 1")
    attempts = _load_attempts(run)
    todo, given_up = [], []
    for c in cards:
        out = _answer_path(run, c["id"])
        if _validate_answer_file(out, c["id"], meta["profile"])[0]:
            continue
        if attempts.get(c["id"], 0) >= a.max_attempts:
            given_up.append(c["id"])
            continue
        brief = _render_brief(meta["brief"], c, meta, out)
        bpath = os.path.join(run, "briefs", c["id"] + ".md")
        with open(bpath, "w", encoding="utf-8") as fh:
            fh.write(brief)
        attempts[c["id"]] = attempts.get(c["id"], 0) + 1
        todo.append(bpath)
    _write(os.path.join(run, "attempts.json"), attempts)
    if given_up:
        print("%d agent(s) failed %d time(s) and are excluded: %s" % (
            len(given_up), a.max_attempts, ", ".join(given_up)))
    if not todo:
        print("No briefs left to run. Next: tally")
        return 0
    print("%d brief(s) to run, in waves of %d. For each wave send ONE message with one" % (len(todo), a.wave))
    print("Agent/Task call per brief (subagent_type general-purpose), then wait for the wave:\n")
    for i in range(0, len(todo), a.wave):
        print("  wave %d:" % (i // a.wave + 1))
        for p in todo[i:i + a.wave]:
            print("    " + p)
    return 0


# ------------------------------------------------------------ check / save

def _validate_answer_file(path, cid, profile):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False, "no answer"
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except ValueError as e:
        return False, "not valid JSON (%s)" % e
    return _validate_answer_obj(obj, cid, profile)


def _validate_answer_obj(obj, cid, profile):
    if not isinstance(obj, dict):
        return False, "answer must be a JSON object"
    if "id" in obj and obj["id"] != cid:
        return False, "answer says id %r but belongs to %s" % (obj["id"], cid)
    fields = profile["answer"]["fields"]
    for fid, spec in fields.items():
        if fid not in obj:
            return False, "missing field %r" % fid
        v = obj[fid]
        t = spec["type"]
        if t == "boolean" and not isinstance(v, bool):
            return False, "field %r must be true or false" % fid
        if t == "number":
            try:
                num.parse_number(v, field=fid)
            except (num.BadNumber, num.MissingInput):
                return False, "field %r must be a number" % fid
        if t == "enum" and v not in spec["values"]:
            return False, "field %r must be one of %s" % (fid, ", ".join(map(str, spec["values"])))
        if t == "string" and not isinstance(v, str):
            return False, "field %r must be text" % fid
    return True, None


def cmd_check(a):
    run, meta, cards = _load_run(a.dir, a.run)
    bad = []
    for c in cards:
        ok, why = _validate_answer_file(_answer_path(run, c["id"]), c["id"], meta["profile"])
        if not ok:
            bad.append((c["id"], why))
    if not bad:
        print("%s all %d answers present and valid." % (ledger.OK, len(cards)))
        return 0
    print("%s %d of %d need attention:" % (ledger.BAD, len(bad), len(cards)))
    for cid, why in bad:
        print("  %s: %s" % (cid, why))
    return 1


def cmd_save(a):
    run, meta, cards = _load_run(a.dir, a.run)
    if a.id not in {c["id"] for c in cards}:
        raise SwarmError("no agent %r in this run" % a.id)
    raw = sys.stdin.read()
    try:
        obj = json.loads(raw)
    except ValueError as e:
        raise SwarmError("stdin is not valid JSON: %s" % e)
    ok, why = _validate_answer_obj(obj, a.id, meta["profile"])
    if not ok:
        raise SwarmError("refusing to save %s: %s" % (a.id, why))
    with open(_answer_path(run, a.id), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("%s saved %s" % (ledger.OK, a.id))
    return 0


# ------------------------------------------------------------------ tally

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def cmd_tally(a):
    run, meta, cards = _load_run(a.dir, a.run)
    profile = meta["profile"]
    outcome = profile["answer"]["outcome"]
    fields = profile["answer"]["fields"]
    answers, excluded = {}, []
    for c in cards:
        ok, _ = _validate_answer_file(_answer_path(run, c["id"]), c["id"], profile)
        if ok:
            with open(_answer_path(run, c["id"]), encoding="utf-8") as fh:
                answers[c["id"]] = json.load(fh)
        else:
            excluded.append(c["id"])

    answered = len(answers)
    yes = sum(1 for v in answers.values() if v.get(outcome) is True)
    res = {"name": meta["name"], "seed": meta["seed"], "n": meta["n"], "answered": answered,
           "excluded": len(excluded), "outcome_field": outcome,
           "outcome_rate": (yes / answered) if answered else None, "outcome_yes": yes,
           "by_dimension": {}, "fields": {}}

    cards_by_id = {c["id"]: c for c in cards}
    for dim in profile["dimensions"]:
        rows = {}
        for cid, ans in answers.items():
            val = cards_by_id[cid].get(dim)
            r = rows.setdefault(val, {"count": 0, "yes": 0})
            r["count"] += 1
            r["yes"] += 1 if ans.get(outcome) is True else 0
        for r in rows.values():
            r["rate"] = r["yes"] / r["count"] if r["count"] else None
        res["by_dimension"][dim] = {k: rows[k] for k in sorted(rows, key=str)}

    for fid, spec in fields.items():
        if fid == outcome:
            continue
        vals = [ans[fid] for ans in answers.values() if fid in ans]
        if spec["type"] == "enum":
            dist = {v: 0 for v in spec["values"]}
            for v in vals:
                dist[v] = dist.get(v, 0) + 1
            res["fields"][fid] = {"type": "enum", "distribution": dist}
        elif spec["type"] == "number":
            nums = [num.parse_number(v, field=fid) for v in vals]
            res["fields"][fid] = {"type": "number", "min": min(nums) if nums else None,
                                  "median": _median(nums), "max": max(nums) if nums else None}
        elif spec["type"] == "string":
            freq = {}
            for v in vals:
                freq[v] = freq.get(v, 0) + 1
            top = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
            res["fields"][fid] = {"type": "string", "top": [{"text": t, "count": c} for t, c in top]}

    _write(os.path.join(run, "results.json"), res)
    with open(os.path.join(run, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(_render_results(res, profile))
    rate = "%.0f%%" % (res["outcome_rate"] * 100) if res["outcome_rate"] is not None else "n/a"
    print("%s %d answered, %d excluded. %s=%s. Wrote results.json and results.md" % (
        ledger.OK, answered, len(excluded), outcome, rate))
    return 0


def _render_results(res, profile):
    L = ["# Swarm results: %s" % res["name"], "",
         "- Agents: %d dealt, %d answered, %d excluded (seed %d)" % (res["n"], res["answered"], res["excluded"], res["seed"])]
    rate = res["outcome_rate"]
    L.append("- %s: %s (%d of %d)" % (res["outcome_field"], "%.0f%%" % (rate * 100) if rate is not None else "n/a",
                                      res["outcome_yes"], res["answered"]))
    L.append("")
    for dim, rows in res["by_dimension"].items():
        L.append("## by %s" % dim)
        L.append("")
        L.append("| %s | agents | %s |" % (dim, res["outcome_field"]))
        L.append("| --- | ---: | ---: |")
        for val, r in rows.items():
            L.append("| %s | %d | %s |" % (val, r["count"], "%.0f%%" % (r["rate"] * 100) if r["rate"] is not None else "n/a"))
        L.append("")
    for fid, info in res["fields"].items():
        L.append("## %s" % fid)
        L.append("")
        if info["type"] == "enum":
            for v, c in info["distribution"].items():
                L.append("- %s: %d" % (v, c))
        elif info["type"] == "number":
            L.append("- min %s, median %s, max %s" % (info["min"], info["median"], info["max"]))
        else:
            for item in info["top"]:
                L.append("- (%d) %s" % (item["count"], item["text"]))
        L.append("")
    L.append("_Simulated agents share one model's leanings; read an agreeable outcome as an upper bound. "
             "Good for surfacing cases and wording, not for predicting real rates. Confirm with real users._")
    return "\n".join(L) + "\n"


def cmd_status(a):
    run, meta, cards = _load_run(a.dir, a.run)
    done = sum(1 for c in cards if _validate_answer_file(_answer_path(run, c["id"]), c["id"], meta["profile"])[0])
    print("run %r: %d agents, %d answered, %d to go (seed %d)" % (meta["name"], meta["n"], done, meta["n"] - done, meta["seed"]))
    return 0


# ------------------------------------------------------------------- util

def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    ap.add_argument("--run", help="run name (needed only when more than one run exists)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="deal the persona cards")
    i.add_argument("--profile", required=True)
    i.add_argument("--brief", required=True)
    g = i.add_mutually_exclusive_group()
    g.add_argument("--n", type=int, default=100)
    g.add_argument("--quick", action="store_true", help="20 agents for a first look")
    i.add_argument("--seed", type=int)
    i.add_argument("--keep-answers", action="store_true")
    i.set_defaults(func=cmd_init)

    p = sub.add_parser("prompts", help="write briefs and list them in waves")
    p.add_argument("--wave", type=int, default=10)
    p.add_argument("--max-attempts", type=int, default=2,
                   help="stop re-briefing an agent after this many tries without a valid answer")
    p.set_defaults(func=cmd_prompts)

    sub.add_parser("check", help="list missing or invalid answers").set_defaults(func=cmd_check)

    s = sub.add_parser("save", help="save one answer from stdin")
    s.add_argument("id")
    s.set_defaults(func=cmd_save)

    sub.add_parser("tally", help="write results.json and results.md").set_defaults(func=cmd_tally)

    sub.add_parser("status", help="one-line progress").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except SwarmError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
