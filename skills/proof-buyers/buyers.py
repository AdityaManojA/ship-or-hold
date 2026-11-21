#!/usr/bin/env python3
"""Run a seeded panel of simulated buyers and tally what they say. Standard library only, no network.

Each buyer is a card dealt from the target customer (segment, behaviour, a habit, a
doubt, an age, an income). The model moderates: it hands each buyer's brief to one
sub-agent and files the reply. This tool deals the cards, writes the briefs, refuses
answers that are not valid, counts, and writes the "Buyer panel" section of the
business plan plus coverage, a score and computed findings in the Proof ledger. It
never answers for a buyer.

State lives under ``proof/business/panel/``:

    panel.json   n, seed, run number, the customer profile, the pitch word for word
    cards.json   the dealt buyers (same seed + customer + n => same buyers)
    briefs/<id>.md, answers/<id>.json, results.json
    quotes.md    every buyer's words, for the owner only (never in the client report)
    runs/<n>/    an earlier run archived by ``retest``

    python3 buyers.py template --out DIR
    python3 buyers.py init --customer customer.json --pitch pitch.md --n 20 [--seed N] [--free]
    python3 buyers.py prompts [--wave 10]
    python3 buyers.py save ID            # the buyer's JSON reply on stdin
    python3 buyers.py check
    python3 buyers.py tally [--min-buy-rate 25]
    python3 buyers.py retest --pitch new-pitch.md
    python3 buyers.py status

    Options: --dir proof (the proof folder), --force.
"""

import argparse
import importlib.util
import json
import os
import random
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-buyers"
TITLE = "# Buyer panel"
MARKER = "simulated buyers"
CODES = ["habit", "trust", "need", "price", "convenience", "other"]
PRICE_FIELDS = [
    ("price_too_cheap", "so cheap you would doubt the quality"),
    ("price_bargain", "a good deal that you would be glad to pay"),
    ("price_getting_expensive", "starting to feel expensive, so you would think twice"),
    ("price_too_expensive", "too expensive to consider at all"),
]
THIN_GROUP = 5
SMALL_PANEL = 20
CHECKS = [
    ("panel-run", "A seeded panel of simulated buyers answered the pitch"),
    ("objections-ranked", "The reasons buyers pass are ranked, with counts (their words are kept for the owner)"),
    ("flip-conditions", "What would change a no is recorded"),
    ("retested", "A revised pitch was put to the same buyers and compared"),
]
FINDING_PREFIX = "buyers-"
GENDERS = ("female", "male", "any")

# Neutral, varied, international first names. No real-person reference intended.
FEMALE_NAMES = [
    "Ada", "Amara", "Anika", "Bea", "Carmen", "Chiara", "Dara", "Elif", "Elsa", "Fatima",
    "Fern", "Gwen", "Hana", "Ines", "Iris", "Jana", "Kavya", "Leila", "Lena", "Liv",
    "Maya", "Mei", "Mira", "Nadia", "Noor", "Olga", "Pia", "Priya", "Rosa", "Sana",
    "Sofia", "Tara", "Thea", "Uma", "Vera", "Wren", "Yara", "Yuki", "Zara", "Zoe",
]
MALE_NAMES = [
    "Aarav", "Arlo", "Ben", "Carlos", "Dev", "Emil", "Farid", "Finn", "Gus", "Hiro",
    "Ivo", "Jonas", "Jude", "Kai", "Kofi", "Luca", "Mateo", "Milo", "Nikhil", "Omar",
    "Otto", "Pavel", "Rafael", "Ravi", "Remy", "Sami", "Soren", "Sven", "Tomas", "Tariq",
    "Umar", "Viktor", "Wade", "Yosef", "Yusuf", "Zane", "Arjun", "Diego", "Lars", "Kenji",
]
# An age range stated in a segment name: "18 to 22", "18-22", "aged 25 to 40".
NAMED_AGE = re.compile(r"(?<![\d,.])(\d{1,3})\s*(?:-|to)\s*(\d{1,3})(?![\d,.%])", re.I)


class BuyersError(ValueError):
    pass


def _swarm():
    """The persona dealer lives in proof-swarm; load it by path and reuse it as it is."""
    path = os.path.join(HERE, "..", "proof-swarm", "swarm.py")
    if not os.path.isfile(path):
        raise BuyersError("the persona dealer was not found at %s (proof-swarm ships with the pack)" % path)
    spec = importlib.util.spec_from_file_location("proof_swarm_dealer", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _panel_dir(d):
    return os.path.join(ledger.root(d), "business", "panel")


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- customer

def _text(c, key):
    if not isinstance(c.get(key), str) or not c[key].strip():
        raise BuyersError("'%s' is missing from customer.json" % key)
    return c[key].strip()


def _pair(value, field):
    if not isinstance(value, list) or len(value) != 2:
        raise BuyersError("'%s' must be a pair [lowest, highest]" % field)
    try:
        lo, hi = (num.parse_number(v, field=field) for v in value)
    except (num.BadNumber, num.MissingInput) as e:
        raise BuyersError(str(e))
    if lo < 0 or hi < lo:
        raise BuyersError("'%s' must run from a lower number to a higher one" % field)
    return [lo, hi]


def _group(c, key, extra):
    items = c.get(key)
    if not isinstance(items, list) or not items:
        raise BuyersError("'%s' is missing or empty in customer.json" % key)
    total, seen, out = 0.0, set(), []
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not it["name"].strip():
            raise BuyersError("each entry in '%s' needs a 'name'" % key)
        name = it["name"].strip()
        if name in seen:
            raise BuyersError("'%s' lists %r twice" % (key, name))
        seen.add(name)
        if "share" not in it:
            raise BuyersError("%s %r needs a 'share'" % (key, name))
        try:
            share = num.parse_number(it["share"], field="%s.share" % key)
        except (num.BadNumber, num.MissingInput) as e:
            raise BuyersError(str(e))
        if share <= 0:
            raise BuyersError("%s %r has a share of %s; drop it or give it a share above 0" % (key, name, share))
        total += share
        out.append(dict(extra(it, name), name=name, share=share))
    if abs(total - 1.0) > 0.01:
        raise BuyersError("shares in '%s' sum to %.2f, not 1" % (key, total))
    return out


def _lines(c, key):
    items = c.get(key)
    if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
        raise BuyersError("'%s' must be a non-empty list of text lines in customer.json" % key)
    return [x.strip() for x in items]


def name_pool(gender):
    """The first names a buyer of this gender is drawn from; "any" mixes both pools."""
    if gender == "female":
        return FEMALE_NAMES
    if gender == "male":
        return MALE_NAMES
    if gender == "any":
        return FEMALE_NAMES + MALE_NAMES
    raise BuyersError("'gender' must be \"female\", \"male\" or \"any\" (got %r). A panel dealt before "
                      "gender was required needs init --force with a customer.json that states it." % (gender,))


def _named_age(name):
    """The age range a segment's name states, or None."""
    for m in NAMED_AGE.finditer(name):
        lo, hi = int(m.group(1)), int(m.group(2))
        if 12 <= lo < hi <= 120:
            return [lo, hi]
    return None


def validate_customer(c, free_flag=False):
    """Return a cleaned customer profile, or raise BuyersError. Nothing is defaulted."""
    if not isinstance(c, dict):
        raise BuyersError("customer.json must be a JSON object")
    out = {"business": _text(c, "business"), "target": _text(c, "target"), "where": _text(c, "where")}
    if not isinstance(c.get("currency"), str) or not c["currency"].strip():
        raise BuyersError("'currency' is missing from customer.json. Give the prefix incomes and prices "
                          "are in, such as \"Rs \" or \"$\". There is no default currency.")
    out["currency"] = c["currency"]
    age = _pair(c.get("age"), "age")
    out["age"] = [int(age[0]), int(age[1])]
    if c.get("gender") not in GENDERS:
        raise BuyersError("'gender' is missing or not valid in customer.json: give \"female\", \"male\" or "
                          "\"any\" so each buyer gets a name that fits the customer")
    out["gender"] = c["gender"]

    def seg_extra(it, name):
        extra = {"income": _pair(it.get("income"), "segments[%s].income" % name)}
        if "age" in it:
            lo, hi = _pair(it["age"], "segments[%s].age" % name)
            seg_age = [int(lo), int(hi)]
            if seg_age[0] < out["age"][0] or seg_age[1] > out["age"][1]:
                raise BuyersError("segment %r has age %d to %d, outside the top-level age %d to %d. The top-level "
                                  "age must contain every segment's age." % (
                                      name, seg_age[0], seg_age[1], out["age"][0], out["age"][1]))
            extra["age"] = seg_age
        span = extra.get("age", out["age"])
        stated = _named_age(name)
        if stated and (stated[0] < span[0] or stated[1] > span[1]):
            raise BuyersError("segment %r says ages %d to %d in its name, but its buyers would be aged %d to %d. "
                              "Give the segment an \"age\": [%d, %d] (inside the top-level age) or rename it." % (
                                  name, stated[0], stated[1], span[0], span[1], stated[0], stated[1]))
        return extra

    def beh_extra(it, name):
        if not isinstance(it.get("how"), str) or not it["how"].strip():
            raise BuyersError("behaviour %r needs a 'how' line saying what this buyer does today" % name)
        return {"how": it["how"].strip()}

    out["segments"] = _group(c, "segments", seg_extra)
    out["behaviours"] = _group(c, "behaviours", beh_extra)
    out["objections"] = _lines(c, "objections")
    out["habits"] = _lines(c, "habits")
    if "free" in c and not isinstance(c["free"], bool):
        raise BuyersError("'free' must be true or false")
    out["free"] = bool(free_flag or c.get("free"))
    if isinstance(c.get("income_period"), str) and c["income_period"].strip():
        out["income_period"] = c["income_period"].strip()
    if not out["free"]:
        if not isinstance(c.get("price_for"), str) or not c["price_for"].strip():
            raise BuyersError("'price_for' is missing from customer.json: say what one price buys (for example "
                              "\"one lunch delivered\") so every buyer answers in the same unit. "
                              "If the product is free, pass --free.")
        out["price_for"] = c["price_for"].strip()
    return out


def read_pitch(path):
    if not os.path.isfile(path):
        raise BuyersError("pitch file not found: %s" % path)
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read().strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not 3 <= len(lines) <= 6:
        raise BuyersError("the pitch has %d line(s); write 3 to 6 plain lines, the way a buyer would first "
                          "hear it" % len(lines))
    return text


# ------------------------------------------------------------------ dealing

def _round_income(x, lo, hi):
    step = 10 ** max(0, len(str(int(hi))) - 2)
    return min(hi, max(lo, round(x / step) * step))


def deal(customer, n, seed):
    """Deal n buyers. The same customer, n and seed always give the same buyers."""
    profile = {"dimensions": {
        "segment": [{"value": s["name"], "share": s["share"]} for s in customer["segments"]],
        "behaviour": [{"value": b["name"], "share": b["share"], "detail": b["how"]} for b in customer["behaviours"]],
        "habit": [{"value": h, "share": 1.0} for h in customer["habits"]],
        "doubt": [{"value": o, "share": 1.0} for o in customer["objections"]],
    }}
    segs = {s["name"]: s for s in customer["segments"]}
    pool = name_pool(customer.get("gender"))
    cards = []
    for i, raw in enumerate(_swarm()._deal(profile, n, seed)):
        cid = "b%03d" % (i + 1)
        rng = random.Random("%d:%s" % (seed, cid))
        seg = segs[raw["segment"]]
        lo, hi = seg["income"]
        age_lo, age_hi = seg.get("age", customer["age"])
        cards.append({
            "id": cid, "name": raw["name"],
            "age": rng.randint(age_lo, age_hi),
            "segment": raw["segment"], "income": _round_income(rng.uniform(lo, hi), lo, hi),
            "behaviour": raw["behaviour"], "how": raw.get("_detail_behaviour", ""),
            "habit": raw["habit"], "doubt": raw["doubt"],
        })
    # The shared dealer draws from its own name pool. The name kept is drawn afterwards from the
    # gender-matched pool with its own seeded draw, so the rest of every card is unchanged.
    for card in cards:
        card["name"] = random.Random("%d:%s:name" % (seed, card["id"])).choice(pool)
    validate_cards(customer, cards)
    return cards


def validate_cards(customer, cards):
    """Every dealt card must fit the profile. A failure here is a bug in the dealer, not bad input."""
    segs = {s["name"]: s for s in customer["segments"]}
    pool = set(name_pool(customer.get("gender")))
    for card in cards:
        seg = segs.get(card["segment"])
        if seg is None:
            raise RuntimeError("card %s has segment %r, which is not in the profile" % (card["id"], card["segment"]))
        lo, hi = seg.get("age", customer["age"])
        if not lo <= card["age"] <= hi:
            raise RuntimeError("card %s is aged %d, outside segment %r (%d to %d)" % (
                card["id"], card["age"], card["segment"], lo, hi))
        if card["name"] not in pool:
            raise RuntimeError("card %s is named %r, which is not in the %s name pool" % (
                card["id"], card["name"], customer.get("gender")))


def _money(currency, x):
    s = "{:,.2f}".format(x)
    if s.endswith(".00"):
        s = s[:-3]
    return currency + s


# -------------------------------------------------------------------- state

def _load_panel(d):
    base = _panel_dir(d)
    if not os.path.isfile(os.path.join(base, "panel.json")):
        raise BuyersError("no panel under %s. Run init first." % base)
    return base, _read_json(os.path.join(base, "panel.json")), _read_json(os.path.join(base, "cards.json"))


def _answer_path(base, cid):
    return os.path.join(base, "answers", cid + ".json")


def _wipe(folder):
    if os.path.isdir(folder):
        shutil.rmtree(folder)


def _has_answers(base):
    folder = os.path.join(base, "answers")
    return os.path.isdir(folder) and any(n.endswith(".json") for n in os.listdir(folder))


# ------------------------------------------------------------------ answers

def validate_answer(obj, cid, free):
    """(True, None) when obj is a complete answer for buyer cid, else (False, why)."""
    if not isinstance(obj, dict):
        return False, "the answer must be a JSON object"
    if "id" in obj and obj["id"] != cid:
        return False, "the answer says id %r but belongs to %s" % (obj["id"], cid)
    if not isinstance(obj.get("buys"), bool):
        return False, "'buys' must be true or false"
    t = obj.get("times_first_month")
    if isinstance(t, bool) or not isinstance(t, int) or t < 0:
        return False, "'times_first_month' must be a whole number, 0 or more"
    if obj.get("reason_code") not in CODES:
        return False, "'reason_code' must be one of %s" % ", ".join(CODES)
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        return False, "'reason' must be the buyer's own words"
    for key in ("objection", "would_change_mind"):
        if not isinstance(obj.get(key), str):
            return False, "'%s' must be text" % key
    if not free:
        last = 0.0
        for key, _ in PRICE_FIELDS:
            v = obj.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                return False, "'%s' must be a plain number, 0 or more" % key
            if v < last:
                return False, "the four price points must not go down: '%s' is below the one before it" % key
            last = v
    return True, None


def _load_answer(base, cid, free):
    path = _answer_path(base, cid)
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None, "no answer"
    try:
        with open(path, encoding="utf-8-sig") as fh:
            obj = json.load(fh)
    except ValueError as e:
        return None, "not valid JSON (%s)" % e
    ok, why = validate_answer(obj, cid, free)
    return (obj, None) if ok else (None, why)


# ------------------------------------------------------------------- briefs

def _answer_shape(free):
    verb = "would use it" if free else "would buy it"
    times = "times you would use it in the first month" if free else "times you would buy in the first month"
    rows = [
        ('"buys": true or false', "whether you %s" % verb),
        ('"times_first_month": 0', "a whole number: %s (0 if you would not)" % times),
        ('"reason_code": "habit"', "the ONE main reason for your answer: %s" % ", ".join(CODES)),
        ('"reason": "..."', "why, in your own voice, one or two sentences"),
        ('"objection": "..."', "your biggest doubt, even if you said yes"),
        ('"would_change_mind": "..."', "the one thing that would change your answer"),
    ]
    if not free:
        rows += [('"%s": 0' % key, "a plain number: the price that is %s" % text) for key, text in PRICE_FIELDS]
    out = ["{"]
    for i, (field, hint) in enumerate(rows):
        out.append("  %s%s   <- %s" % (field, "," if i < len(rows) - 1 else "", hint))
    out.append("}")
    return "\n".join(out)


def render_brief(panel, card, out_path):
    c = panel["customer"]
    free = panel["free"]
    income = _money(c["currency"], card["income"])
    if c.get("income_period"):
        income += " " + c["income_period"]
    L = ["# Buyer brief %s" % card["id"], "",
         "You are one buyer in a panel for %s. You are not an assistant here. Be this person for the "
         "whole task and answer only for yourself." % c["business"], "",
         "## Who you are", "",
         "- Name: %s" % card["name"],
         "- Age: %d" % card["age"],
         "- Where: %s" % c["where"],
         "- You are one of: %s (%s)" % (c["target"], card["segment"]),
         "- Income: %s" % income,
         "- What you do today: %s. %s" % (card["behaviour"], card["how"]),
         "- A habit of yours: %s" % card["habit"],
         "- The doubt on your mind: %s" % card["doubt"], "",
         "## The pitch, word for word", ""]
    L += ["> " + ln if ln.strip() else ">" for ln in panel["pitch"].splitlines()]
    L += ["", "That is all you know about it. You have not tried it and nobody you know has.", "",
          "## How to answer", "",
          "Answer as this person would on an ordinary day. Do not be agreeable. Most people say no to most "
          "new things, and a polite yes is worth nothing here. Do not invent facts about the product, the "
          "seller or other people that are not in this brief. If something is unclear, decide the way this "
          "person would when unsure, and say so in your reason.", ""]
    if free:
        L += ["This product costs you nothing. \"buys\" means you would really start using it. "
              "There are no price questions.", ""]
    else:
        L += ["## Price", "",
              "Every price you give is in %s and is for %s. Give four plain numbers, no symbols, each at "
              "least as high as the one before:" % (c["currency"].strip(), c["price_for"]), ""]
        L += ["- %s: the price that is %s" % (key, text) for key, text in PRICE_FIELDS]
        L.append("")
    L += ["## Your reply", "",
          "Create this file:", "", "    %s" % os.path.abspath(out_path), "",
          "Write ONLY this JSON into it, with your own values. No markdown fences, no notes, no extra keys:", "",
          _answer_shape(free), "",
          "Then reply with one line saying the file is written. Do nothing else."]
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- commands

def cmd_template(a):
    os.makedirs(a.out, exist_ok=True)
    for src, name in (("customer.example.json", "customer.json"), ("pitch.example.md", "pitch.md")):
        dest = os.path.join(a.out, name)
        ledger.guard_out(dest, a.dir, a.force)
        shutil.copyfile(os.path.join(HERE, src), dest)
        print("%s wrote %s" % (ledger.OK, dest))
    print("This customer is made up. Replace every line with what you know about your own buyers.")
    return 0


def _profile_lines(c, n, seed):
    L = ["%s: %s, %s, aged %d to %d" % (c["business"], c["target"], c["where"], c["age"][0], c["age"][1])]
    L.append("  names drawn for gender: %s" % c.get("gender"))
    L.append("  segments: " + "; ".join("%s %.0f%% (%sincome %s to %s)" % (
        s["name"], s["share"] * 100, "aged %d to %d, " % tuple(s["age"]) if s.get("age") else "",
        _money(c["currency"], s["income"][0]), _money(c["currency"], s["income"][1]))
        for s in c["segments"]))
    L.append("  behaviours: " + "; ".join("%s %.0f%%" % (b["name"], b["share"] * 100) for b in c["behaviours"]))
    L.append("  %d doubts and %d habits to draw from" % (len(c["objections"]), len(c["habits"])))
    L.append("  " + ("free product: no price questions" if c["free"] else
                     "prices asked in %s for %s" % (c["currency"].strip(), c["price_for"])))
    L.append("  %d buyers, seed %d" % (n, seed))
    return L


def cmd_init(a):
    if not os.path.isfile(a.customer):
        raise BuyersError("customer file not found: %s" % a.customer)
    try:
        raw = _read_json(a.customer)
    except ValueError as e:
        raise BuyersError("customer.json is not valid JSON: %s" % e)
    customer = validate_customer(raw, a.free)
    pitch = read_pitch(a.pitch)
    if a.n < 1:
        raise BuyersError("--n must be at least 1")
    seed = a.seed if a.seed is not None else random.SystemRandom().randrange(1, 10 ** 9)
    base = _panel_dir(a.dir)
    if _has_answers(base) or os.path.isdir(os.path.join(base, "runs")):
        if not a.force:
            raise BuyersError("a panel with answers already exists under %s. To test a new pitch on the same "
                              "buyers use 'retest'. To throw the panel away and start again, pass --force." % base)
        print("Cleared the previous panel, its answers and its archived runs.")
    for name in ("answers", "briefs", "runs"):
        _wipe(os.path.join(base, name))
    if os.path.isfile(os.path.join(base, "results.json")):
        os.remove(os.path.join(base, "results.json"))
    os.makedirs(os.path.join(base, "answers"))
    os.makedirs(os.path.join(base, "briefs"))
    _write_json(os.path.join(base, "panel.json"), {
        "business": customer["business"], "n": a.n, "seed": seed, "free": customer["free"], "run": 1,
        "customer": customer, "pitch": pitch, "created": ledger.now()})
    _write_json(os.path.join(base, "cards.json"), deal(customer, a.n, seed))
    print("\n".join(_profile_lines(customer, a.n, seed)))
    print("%s dealt %d buyers. Next: prompts" % (ledger.OK, a.n))
    return 0


def cmd_prompts(a):
    base, panel, cards = _load_panel(a.dir)
    if a.wave < 1:
        raise BuyersError("--wave must be at least 1")
    os.makedirs(os.path.join(base, "briefs"), exist_ok=True)
    os.makedirs(os.path.join(base, "answers"), exist_ok=True)
    todo = []
    for c in cards:
        if _load_answer(base, c["id"], panel["free"])[0] is not None:
            continue
        bpath = os.path.abspath(os.path.join(base, "briefs", c["id"] + ".md"))
        with open(bpath, "w", encoding="utf-8") as fh:
            fh.write(render_brief(panel, c, _answer_path(base, c["id"])))
        todo.append((c["id"], bpath))
    if not todo:
        print("Every buyer has answered. Next: tally")
        return 0
    print("%d buyer(s) to brief, in waves of %d. For each wave send ONE message with one Agent call per" % (
        len(todo), a.wave))
    print("buyer (subagent_type general-purpose), each with exactly the prompt shown, then wait for the wave.")
    for i in range(0, len(todo), a.wave):
        print("\nwave %d:" % (i // a.wave + 1))
        for cid, bpath in todo[i:i + a.wave]:
            print("  %s: Read %s and follow it exactly. It is your whole brief." % (cid, bpath))
    return 0


def cmd_save(a):
    base, panel, cards = _load_panel(a.dir)
    if a.id not in {c["id"] for c in cards}:
        raise BuyersError("no buyer %r in this panel" % a.id)
    try:
        obj = json.loads(sys.stdin.read())
    except ValueError as e:
        raise BuyersError("refusing to save %s: stdin is not valid JSON (%s)" % (a.id, e))
    ok, why = validate_answer(obj, a.id, panel["free"])
    if not ok:
        raise BuyersError("refusing to save %s: %s" % (a.id, why))
    _write_json(_answer_path(base, a.id), obj)
    print("%s saved %s" % (ledger.OK, a.id))
    return 0


def cmd_check(a):
    base, panel, cards = _load_panel(a.dir)
    bad = [(c["id"], why) for c in cards for obj, why in [_load_answer(base, c["id"], panel["free"])] if obj is None]
    if not bad:
        print("%s all %d answers present and valid." % (ledger.OK, len(cards)))
        return 0
    print("%s %d of %d need attention:" % (ledger.BAD, len(bad), len(cards)))
    for cid, why in bad:
        print("  %s: %s" % (cid, why))
    return 1


def cmd_status(a):
    base, panel, cards = _load_panel(a.dir)
    done = sum(1 for c in cards if _load_answer(base, c["id"], panel["free"])[0] is not None)
    print("%s: run %d, %d buyers, %d answered, %d to go (seed %d%s)" % (
        panel["business"], panel["run"], len(cards), done, len(cards) - done, panel["seed"],
        ", free product" if panel["free"] else ""))
    return 0


# -------------------------------------------------------------------- tally

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _by(cards, answers, key, order):
    rows = []
    for name in order:
        ids = [c["id"] for c in cards if c[key] == name and c["id"] in answers]
        if not ids:
            continue
        yes = sum(1 for i in ids if answers[i]["buys"])
        rows.append({"name": name, "buyers": len(ids), "buys": yes,
                     "rate": round(100.0 * yes / len(ids), 1), "thin": len(ids) < THIN_GROUP})
    return rows


def _reasons(cards, answers, buys):
    groups = {}
    for c in cards:
        ans = answers.get(c["id"])
        if ans is None or ans["buys"] is not buys:
            continue
        g = groups.setdefault(ans["reason_code"], {"code": ans["reason_code"], "count": 0, "quotes": []})
        g["count"] += 1
        if len(g["quotes"]) < 2:
            g["quotes"].append({"id": c["id"], "name": c["name"], "age": c["age"], "segment": c["segment"],
                                "text": ans["reason"].strip()})
    return sorted(groups.values(), key=lambda g: (-g["count"], CODES.index(g["code"])))


def build_results(base, panel, cards, min_rate):
    answers, excluded = {}, []
    for c in cards:
        obj, _ = _load_answer(base, c["id"], panel["free"])
        if obj is None:
            excluded.append(c["id"])
        else:
            answers[c["id"]] = obj
    if not answers:
        raise BuyersError("no valid answers to tally. Run 'prompts', brief the buyers, then 'check'.")
    cust = panel["customer"]
    answered = len(answers)
    buys = sum(1 for v in answers.values() if v["buys"])
    res = {
        "business": panel["business"], "run": panel["run"], "seed": panel["seed"], "n": panel["n"],
        "free": panel["free"], "currency": cust["currency"], "price_for": cust.get("price_for"),
        "answered": answered, "excluded": excluded, "buys": buys, "passes": answered - buys,
        "buy_rate": round(100.0 * buys / answered, 1), "min_buy_rate": min_rate,
        "by_segment": _by(cards, answers, "segment", [s["name"] for s in cust["segments"]]),
        "by_behaviour": _by(cards, answers, "behaviour", [b["name"] for b in cust["behaviours"]]),
        "pass_reasons": _reasons(cards, answers, False), "buy_reasons": _reasons(cards, answers, True),
        "flip": [{"id": c["id"], "name": c["name"], "reason_code": answers[c["id"]]["reason_code"],
                  "text": answers[c["id"]]["would_change_mind"].strip()}
                 for c in cards if c["id"] in answers and not answers[c["id"]]["buys"]
                 and answers[c["id"]]["would_change_mind"].strip()],
        "words": [{"id": c["id"], "name": c["name"], "age": c["age"], "segment": c["segment"],
                   "buys": answers[c["id"]]["buys"], "reason_code": answers[c["id"]]["reason_code"],
                   "reason": answers[c["id"]]["reason"].strip(), "objection": answers[c["id"]]["objection"].strip(),
                   "would_change_mind": answers[c["id"]]["would_change_mind"].strip()}
                  for c in cards if c["id"] in answers],
        "times_first_month_median": _median([v["times_first_month"] for v in answers.values() if v["buys"]]) if buys else None,
        "prices": None, "previous": None, "moved": [], "pitch": panel["pitch"], "created": ledger.now(),
    }
    if not panel["free"]:
        res["prices"] = {key: _median([v[key] for v in answers.values()]) for key, _ in PRICE_FIELDS}
    prev_path = os.path.join(base, "runs", str(panel["run"] - 1), "results.json")
    if panel["run"] > 1 and os.path.isfile(prev_path):
        prev = _read_json(prev_path)
        res["previous"] = {k: prev[k] for k in ("run", "answered", "buys", "passes", "buy_rate", "pitch")}
        res["previous"]["pass_reasons"] = [{"code": g["code"], "count": g["count"]} for g in prev["pass_reasons"]]
        before = {g["code"]: g["count"] for g in prev["pass_reasons"]}
        after = {g["code"]: g["count"] for g in res["pass_reasons"]}
        res["moved"] = [{"code": code, "before": before.get(code, 0), "after": after.get(code, 0),
                         "change": after.get(code, 0) - before.get(code, 0)}
                        for code in CODES if before.get(code, 0) or after.get(code, 0)]
    res["flip_themes"] = flip_themes(res["flip"])
    res["findings"] = _findings(res)
    return res


STOPWORDS = set("""a an the and or but if of to in on at for from by with without about into than then
    so as is are was were be been being am it its this that these those there here i me my mine we us our
    you your he she they them their his her would could should will can may might must do does did done
    not no yes just only more most less very really much many some any all also too get got make made
    one when what which who how why where up out over under again need want like""".split())


def _keywords(text):
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if len(w) >= 3 and w not in STOPWORDS and w not in out:
            out.append(w)
    return out


def flip_themes(flips):
    """Group what would change a no by simple keyword overlap: themes with counts, no quotes.

    An answer joins a theme when at least half the keywords of the shorter of the two appear in
    the theme's first answer. Each theme is labelled by its most frequent keywords."""
    groups = []
    for f in flips:
        kws = _keywords(f["text"])
        home = None
        for g in groups:
            first = g["first"]
            if (kws and first and len(set(kws) & set(first)) * 2 >= min(len(kws), len(first))) \
                    or (not kws and not first):
                home = g
                break
        if home is None:
            home = {"first": kws, "members": []}
            groups.append(home)
        home["members"].append((kws, f["reason_code"]))
    out = []
    for g in groups:
        counts, order = {}, []
        for kws, _ in g["members"]:
            for w in kws:
                if w not in counts:
                    order.append(w)
                counts[w] = counts.get(w, 0) + 1
        top = sorted(order, key=lambda w: (-counts[w], order.index(w)))[:3]
        codes = {}
        for _, code in g["members"]:
            codes[code] = codes.get(code, 0) + 1
        out.append({"theme": ", ".join(top) if top else "other", "count": len(g["members"]),
                    "reason_codes": [{"code": c, "count": codes[c]} for c in CODES if c in codes]})
    return sorted(out, key=lambda t: -t["count"])


def _findings(res):
    out = []
    n = res["answered"]
    for g in res["pass_reasons"]:
        if g["count"] * 4 >= n:
            out.append({"id": "buyers-pass-%s" % g["code"], "severity": "medium", "category": "objection:%s" % g["code"],
                        "title": "%d of %d pass because of %s" % (g["count"], n, g["code"]),
                        "description": "At least a quarter of the simulated panel gave '%s' as the main reason "
                                       "for saying no. In their words: \"%s\"" % (g["code"], g["quotes"][0]["text"]),
                        "fix": "Answer this objection in the offer or the pitch, then retest on the same buyers."})
    prev = res["previous"]
    if prev and prev["pass_reasons"]:
        top = prev["pass_reasons"][0]
        now = next((m["after"] for m in res["moved"] if m["code"] == top["code"]), 0)
        if now >= top["count"]:
            out.append({"id": "buyers-retest-no-move", "severity": "medium", "category": "retest",
                        "title": "A revised pitch did not move the main objection",
                        "description": "Before the retest %d buyer(s) passed because of %s; after it %d did."
                                       % (top["count"], top["code"], now),
                        "fix": "Words alone did not shift it. Change the offer itself (price, proof, risk to "
                               "the buyer), not the wording."})
    if n < SMALL_PANEL:
        out.append({"id": "buyers-panel-small", "severity": "low", "category": "panel",
                    "title": "Panel too small to read by group",
                    "description": "%d buyer(s) answered. Under %d, the segment and behaviour tables are "
                                   "mostly single voices." % (n, SMALL_PANEL),
                    "fix": "Run at least %d buyers before reading the tables by group." % SMALL_PANEL})
    return out


def _pct(x):
    return ("%.1f" % x).rstrip("0").rstrip(".") + "%"


def _one_line(text):
    return " ".join(text.split())


def render(res):
    free = res["free"]
    verb = "use it" if free else "buy"
    L = [TITLE, "",
         "**%d would %s, %d pass (%s)**" % (res["buys"], verb, res["passes"], _pct(res["buy_rate"])), "",
         "%d of %d %s answered for %s. Bar: %s or more. Run %d, seed %d." % (
             res["answered"], res["n"], MARKER, res["business"], _pct(res["min_buy_rate"]), res["run"], res["seed"])]
    if res["excluded"]:
        L.append("No valid answer from: %s." % ", ".join(res["excluded"]))
    if res["times_first_month_median"] is not None:
        L.append("Those who said yes expect to %s a median of %s time(s) in the first month." % (
            verb, ("%.1f" % res["times_first_month_median"]).rstrip("0").rstrip(".")))
    L += ["", "## The pitch they heard", ""]
    L += ["> " + ln if ln.strip() else ">" for ln in res["pitch"].splitlines()]
    L.append("")

    prev = res["previous"]
    if prev:
        L += ["## Before and after the revised pitch", "",
              "| | Before (run %d) | After (run %d) |" % (prev["run"], res["run"]), "| --- | ---: | ---: |",
              "| Answered | %d | %d |" % (prev["answered"], res["answered"]),
              "| Would %s | %d | %d |" % (verb, prev["buys"], res["buys"]),
              "| Pass | %d | %d |" % (prev["passes"], res["passes"]),
              "| Rate | %s | %s |" % (_pct(prev["buy_rate"]), _pct(res["buy_rate"])), "",
              "The same buyers, dealt from the same seed, heard both pitches.", ""]
        if res["moved"]:
            L += ["| Reason for passing | Before | After | Change |", "| --- | ---: | ---: | ---: |"]
            L += ["| %s | %d | %d | %+d |" % (m["code"], m["before"], m["after"], m["change"]) for m in res["moved"]]
            L.append("")

    for title, key, head in (("By segment", "by_segment", "Segment"), ("By behaviour", "by_behaviour", "What they do today")):
        L += ["## %s" % title, "", "| %s | Buyers | Would %s | Rate | |" % (head, verb), "| --- | ---: | ---: | ---: | --- |"]
        for r in res[key]:
            L.append("| %s | %d | %d | %s | %s |" % (r["name"].replace("|", "\\|"), r["buyers"], r["buys"], _pct(r["rate"]),
                                                    "thin: under %d buyers" % THIN_GROUP if r["thin"] else ""))
        L.append("")

    L += ["## Why they pass", ""]
    if not res["pass_reasons"]:
        L.append("Nobody passed. With %s that is a warning, not good news: see the note at the end." % MARKER)
    for i, g in enumerate(res["pass_reasons"], start=1):
        L.append("%d. **%s**: %d of %d" % (i, g["code"], g["count"], res["answered"]))
    L += ["", "## Why they %s" % verb, ""]
    if not res["buy_reasons"]:
        L.append("Nobody said yes.")
    for i, g in enumerate(res["buy_reasons"], start=1):
        L.append("%d. **%s**: %d of %d" % (i, g["code"], g["count"], res["answered"]))
    L += ["", "## What would change a no", ""]
    themes = res.get("flip_themes") or []
    if not themes:
        L.append("Nothing recorded.")
    else:
        L += ["Grouped by the words the buyers used. These are hypotheses to test with real buyers.", ""]
    for t in themes:
        L.append("- **%s**: %d of %d who passed (%s)" % (
            t["theme"], t["count"], res["passes"],
            ", ".join("%s %d" % (r["code"], r["count"]) for r in t["reason_codes"])))
    L.append("")

    if res["prices"]:
        cur = res["currency"]
        p = res["prices"]
        L += ["## Price points", "",
              "Medians across the panel, in %s, for %s: doubt the quality below %s, a good deal at %s, "
              "think twice at %s, would not consider above %s. A range to test with real buyers, not a price." % (
                  cur.strip(), res["price_for"], _money(cur, p["price_too_cheap"]), _money(cur, p["price_bargain"]),
                  _money(cur, p["price_getting_expensive"]), _money(cur, p["price_too_expensive"])), ""]

    L += ["## Read this before you use it", "",
          "These are %s, not real people. Each one is a language model playing a card dealt from the "
          "customer profile (seed %d). Language models lean agreeable, so the rate above is an upper bound: "
          "real buyers will say yes less often. Use the panel to find objections and wording, then check "
          "them with real people. What each buyer said is kept for the owner only and is never a customer "
          "testimonial." % (MARKER, res["seed"])]
    return "\n".join(L) + "\n"


def render_quotes(res):
    """Every buyer's own words, for the owner only. Never part of the client report."""
    L = ["# Buyer panel quotes", "",
         "**For the owner only. These words never go in the client report or the PDF.** They come from "
         "%s, not real people (run %d, seed %d), and are never customer testimonials." % (
             MARKER, res["run"], res["seed"]), ""]
    for title, buys in (("Passed", False), ("Would %s" % ("use it" if res["free"] else "buy"), True)):
        rows = [w for w in res.get("words", []) if w["buys"] is buys]
        L += ["## %s (%d)" % (title, len(rows)), ""]
        if not rows:
            L += ["Nobody.", ""]
        for w in rows:
            L.append("- **%s** (%s, %d, %s), %s" % (w["id"], w["name"], w["age"], w["segment"], w["reason_code"]))
            L.append("  - Reason: \"%s\"" % _one_line(w["reason"]))
            if w["objection"]:
                L.append("  - Biggest doubt: \"%s\"" % _one_line(w["objection"]))
            if w["would_change_mind"]:
                L.append("  - Would change their mind: \"%s\"" % _one_line(w["would_change_mind"]))
        L.append("")
    return "\n".join(L)


def _clear_findings(d):
    folder = os.path.dirname(ledger.finding_path(d, SKILL, "x"))
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith(FINDING_PREFIX) and name.endswith(".json"):
                os.remove(os.path.join(folder, name))


def cmd_tally(a):
    base, panel, cards = _load_panel(a.dir)
    if not 0 <= a.min_buy_rate <= 100:
        raise BuyersError("--min-buy-rate is a percentage from 0 to 100")
    res = build_results(base, panel, cards, a.min_buy_rate)
    root = ledger.root(a.dir)
    out = os.path.join(root, "business", "buyers.md")
    ledger.guard_out(out, a.dir, a.force, ours=MARKER)
    results_path = os.path.join(base, "results.json")
    _write_json(results_path, res)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(res))
    quotes_path = os.path.join(base, "quotes.md")
    with open(quotes_path, "w", encoding="utf-8") as fh:
        fh.write(render_quotes(res))
    rel = os.path.relpath(results_path, root).replace(os.sep, "/")

    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    _write_json(os.path.join(ev_dir, "customer.json"), panel["customer"])
    with open(os.path.join(ev_dir, "pitch.md"), "w", encoding="utf-8") as fh:
        fh.write(panel["pitch"] + "\n")

    ledger.write_scores(a.dir, SKILL, [
        {"id": "buy_rate", "value": res["buy_rate"], "unit": "percent", "budget": a.min_buy_rate, "op": ">=",
         "source": "buyers.py, %d simulated buyers, seed %d" % (res["answered"], res["seed"]), "evidence": [rel]},
    ])
    ledger.init_coverage(a.dir, SKILL, CHECKS, keep_existing=False)
    ledger.mark_coverage(a.dir, SKILL, "panel-run", "covered", evidence=[rel],
                         note="%d of %d buyers answered" % (res["answered"], res["n"]))
    if res["pass_reasons"]:
        ledger.mark_coverage(a.dir, SKILL, "objections-ranked", "covered", evidence=[rel])
        if res["flip"]:
            ledger.mark_coverage(a.dir, SKILL, "flip-conditions", "covered", evidence=[rel])
    else:
        for cid in ("objections-ranked", "flip-conditions"):
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note="no buyer on the panel passed")
    if res["previous"]:
        ledger.mark_coverage(a.dir, SKILL, "retested", "covered", evidence=[rel],
                             note="run %d compared with run %d" % (res["run"], res["previous"]["run"]))
    else:
        # A re-test is a second step some runs never need. Leaving it not run would
        # hold every first-pass verdict at Incomplete, so it is recorded as optional.
        ledger.mark_coverage(a.dir, SKILL, "retested", "not_applicable",
                             note="no revised pitch has been put to this panel yet; a re-test is optional "
                                  "(buyers.py retest --pitch)")

    _clear_findings(a.dir)
    for f in res["findings"]:
        ledger.write_finding(a.dir, ledger.new_finding(
            SKILL, f["title"], f["severity"], id=f["id"], category=f["category"],
            description=f["description"], fix=f["fix"], evidence=[rel]))
        ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")

    verb = "use it" if res["free"] else "buy"
    print("%d would %s, %d pass (%s). Bar %s: %s" % (
        res["buys"], verb, res["passes"], _pct(res["buy_rate"]), _pct(a.min_buy_rate),
        ledger.OK if res["buy_rate"] >= a.min_buy_rate else ledger.BAD))
    for f in res["findings"]:
        print("%s [%s] %s" % (ledger.BAD, f["severity"], f["title"]))
    if res["excluded"]:
        print("%s %d buyer(s) had no valid answer and are left out: %s" % (
            ledger.BAD, len(res["excluded"]), ", ".join(res["excluded"])))
    print("%s wrote %s and %s; recorded coverage, 1 score and %d finding(s)." % (
        ledger.OK, out, results_path, len(res["findings"])))
    print("Buyer quotes are in %s, for the owner only; they never go in the client report." % quotes_path)
    return 0


def cmd_retest(a):
    base, panel, cards = _load_panel(a.dir)
    results_path = os.path.join(base, "results.json")
    if not os.path.isfile(results_path) or _read_json(results_path).get("run") != panel["run"]:
        raise BuyersError("tally the current run before a retest, so there is a 'before' to compare with")
    pitch = read_pitch(a.pitch)
    if pitch == panel["pitch"]:
        raise BuyersError("the new pitch is the same as the old one; a retest needs a revised pitch")
    dest = os.path.join(base, "runs", str(panel["run"]))
    _wipe(dest)
    os.makedirs(dest)
    # Copied, not moved: the ledger's evidence points at results.json until the next tally.
    shutil.copyfile(results_path, os.path.join(dest, "results.json"))
    if os.path.isfile(os.path.join(base, "quotes.md")):
        shutil.copyfile(os.path.join(base, "quotes.md"), os.path.join(dest, "quotes.md"))
    shutil.move(os.path.join(base, "answers"), os.path.join(dest, "answers"))
    shutil.copyfile(os.path.join(base, "panel.json"), os.path.join(dest, "panel.json"))
    shutil.copyfile(os.path.join(base, "cards.json"), os.path.join(dest, "cards.json"))
    with open(os.path.join(dest, "pitch.md"), "w", encoding="utf-8") as fh:
        fh.write(panel["pitch"] + "\n")
    _wipe(os.path.join(base, "briefs"))
    os.makedirs(os.path.join(base, "briefs"))
    os.makedirs(os.path.join(base, "answers"))
    old_run = panel["run"]
    panel.update({"run": old_run + 1, "pitch": pitch, "created": ledger.now()})
    _write_json(os.path.join(base, "panel.json"), panel)
    _write_json(os.path.join(base, "cards.json"), deal(panel["customer"], panel["n"], panel["seed"]))
    print("%s archived run %d under %s" % (ledger.OK, old_run, dest))
    print("Dealt the same %d buyers again (seed %d) with the new pitch and cleared their answers." % (
        panel["n"], panel["seed"]))
    print("The section file and the ledger still show run %d until you tally. Next: prompts" % old_run)
    return 0


def main(argv=None):
    ledger.console()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=argparse.SUPPRESS, help="the proof folder (default: proof)")
    common.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                        help="overwrite a file Proof did not write, or start a new panel over an old one")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof", help="the proof folder (default: proof)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a file Proof did not write, or start a new panel over an old one")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", parents=[common], help="write an example customer.json and pitch.md")
    t.add_argument("--out", required=True, help="folder to write the examples into")
    t.set_defaults(func=cmd_template)

    i = sub.add_parser("init", parents=[common], help="deal the panel from the customer profile")
    i.add_argument("--customer", required=True, help="customer.json")
    i.add_argument("--pitch", required=True, help="pitch.md, 3 to 6 plain lines")
    i.add_argument("--n", type=int, default=20, help="how many buyers (default 20)")
    i.add_argument("--seed", type=int, help="deal the same buyers again by giving the same seed")
    i.add_argument("--free", action="store_true", help="the product is free: ask about use, not price")
    i.set_defaults(func=cmd_init)

    p = sub.add_parser("prompts", parents=[common], help="write the briefs and print one prompt per buyer")
    p.add_argument("--wave", type=int, default=10, help="buyers per wave (default 10)")
    p.set_defaults(func=cmd_prompts)

    s = sub.add_parser("save", parents=[common], help="file one buyer's JSON reply from stdin")
    s.add_argument("id", help="the buyer id, for example b007")
    s.set_defaults(func=cmd_save)

    sub.add_parser("check", parents=[common], help="list missing or broken answers").set_defaults(func=cmd_check)

    y = sub.add_parser("tally", parents=[common], help="count the answers, write the section and the ledger")
    y.add_argument("--min-buy-rate", type=float, default=25.0,
                   help="the bar the buy rate is scored against, in percent (default 25)")
    y.set_defaults(func=cmd_tally)

    r = sub.add_parser("retest", parents=[common], help="archive this run and put a new pitch to the same buyers")
    r.add_argument("--pitch", required=True, help="the revised pitch.md")
    r.set_defaults(func=cmd_retest)

    sub.add_parser("status", parents=[common], help="one-line progress").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (BuyersError, ledger.LedgerError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
