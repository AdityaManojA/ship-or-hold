#!/usr/bin/env python3
"""Score what an app's LLM calls cost and how fast they answer, against a budget, into the ledger.

Standard library only, no network, and it never calls a model provider: it reads
usage records you exported and prices you supplied. Records are JSON (a list, or
{"records": [...]}, {"data": [...]}), JSON lines or CSV, one row per model call.
Recognised keys (any subset):

  model                         model, model_name
  input tokens                  input_tokens, prompt_tokens, tokens_in
  output tokens                 output_tokens, completion_tokens, tokens_out
  cached input tokens           cached_tokens, cached_input_tokens, cache_read_input_tokens
  latency                       latency_ms, duration_ms (ms); latency_s, duration_s (seconds)
  user                          user, user_id, userId
  conversation                  conversation, conversation_id, session, session_id, trace_id, thread_id
  time                          timestamp, time, created_at, start_time (ISO 8601 or epoch s/ms)
  outcome                       status, error, ok
  prompt fingerprint            prompt_hash

Prices come only from your prices file: {"model": {"input_per_1m": .., "output_per_1m": ..,
"cached_input_per_1m": ..}} in USD per million tokens. A model with no price is an
error, never a guess. A record model matches a price key exactly, or starts with
the key followed by "-" (so "gpt-x-2026-01-01" uses "gpt-x"); the longest key wins.

    python3 llmcost.py prices --out proof/llm-cost/prices.json
    python3 llmcost.py analyze usage.jsonl --prices prices.json
    python3 llmcost.py record usage.jsonl --prices prices.json --budget-latency-p95-ms 6000
    python3 llmcost.py scan-code backend/app
    python3 llmcost.py status
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-llm-cost"

# (id, label, unit, op, default budget). Defaults are starting points, not standards.
METRICS = [
    ("cost_per_request_p50", "Cost per model call, median", "USD", "<=", 0.02),
    ("cost_per_request_p95", "Cost per model call, 95th percentile", "USD", "<=", 0.05),
    ("cost_per_conversation_p50", "Cost per conversation, median", "USD", "<=", 0.10),
    ("cost_per_conversation_p95", "Cost per conversation, 95th percentile", "USD", "<=", 0.50),
    ("cost_per_user_day", "Cost per active user per day, median", "USD", "<=", 0.25),
    ("heavy_user_month", "Projected monthly cost of a heavy (90th percentile) user", "USD", "<=", 10.0),
    ("tokens_per_request_p95", "Tokens per call, 95th percentile", "tokens", "<=", 16000),
    ("latency_p50_ms", "Response time, median", "ms", "<=", 3000),
    ("latency_p95_ms", "Response time, 95th percentile", "ms", "<=", 8000),
    ("latency_p99_ms", "Response time, 99th percentile", "ms", "<=", 15000),
    ("error_rate", "Share of calls that failed", "fraction", "<=", 0.02),
    ("cache_hit_share", "Share of input tokens served from the prompt cache", "fraction", ">=", 0.0),
]
METRIC = dict((m[0], m) for m in METRICS)
EXTRA_CHECKS = [("runaway", "No runaway conversations"), ("duplicates", "No duplicate calls within 5 seconds")]
CODE_CHECK = ("token-caps", "Every model call sets an output-token cap")
DUP_WINDOW_S = 5
MAX_LISTED = 10

KEYS = {
    "model": ("model", "model_name"),
    "input": ("input_tokens", "prompt_tokens", "tokens_in"),
    "output": ("output_tokens", "completion_tokens", "tokens_out"),
    "cached": ("cached_tokens", "cached_input_tokens", "cache_read_input_tokens"),
    "latency_ms": ("latency_ms", "duration_ms", "latency", "duration"),
    "latency_s": ("latency_s", "duration_s"),
    "user": ("user", "user_id", "userId"),
    "conversation": ("conversation", "conversation_id", "session", "session_id", "trace_id", "thread_id"),
    "time": ("timestamp", "time", "created_at", "start_time"),
    "prompt_hash": ("prompt_hash",),
}

PRICES_TEMPLATE = {
    "_note": "PLACEHOLDER prices. Replace every number with the provider's current price in USD per "
             "1 million tokens, and use the model names exactly as they appear in your usage records.",
    "example-model-large": {"input_per_1m": 0.0, "output_per_1m": 0.0, "cached_input_per_1m": 0.0},
    "example-model-small": {"input_per_1m": 0.0, "output_per_1m": 0.0, "cached_input_per_1m": 0.0},
}
PRICES_MARK = '"input_per_1m"'


# ---------------------------------------------------------------- reading

def _first(row, names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _num(value, field):
    try:
        return num.parse_number(value, field=field)
    except (num.MissingInput, num.BadNumber) as e:
        raise SystemExit("bad number: %s" % e)


def _time(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or re.fullmatch(r"\d{9,13}(\.\d+)?", str(value).strip()):
        t = float(value)
        if t > 1e11:  # milliseconds
            t /= 1000.0
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).replace(tzinfo=None)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise SystemExit("cannot read the time %r (use ISO 8601 or epoch seconds)" % value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def _failed(row):
    if "ok" in row and row["ok"] not in (None, ""):
        return str(row["ok"]).strip().lower() in ("false", "0", "no")
    err = row.get("error")
    if err not in (None, "", False, 0, "0", "false", "False", "null", "None"):
        return True
    status = row.get("status")
    if status in (None, ""):
        return False
    s = str(status).strip().lower()
    if re.fullmatch(r"\d{3}", s):
        return int(s) >= 400
    return s in ("error", "failed", "failure", "timeout", "cancelled", "canceled")


def normalise(row):
    out = {"model": None, "input": None, "output": None, "cached": None, "latency_ms": None,
           "user": None, "conversation": None, "time": None, "prompt_hash": None, "failed": _failed(row)}
    m = _first(row, KEYS["model"])
    out["model"] = str(m) if m is not None else None
    for k in ("input", "output", "cached"):
        v = _first(row, KEYS[k])
        if v is not None:
            out[k] = _num(v, k + " tokens")
    v = _first(row, KEYS["latency_ms"])
    if v is not None:
        out["latency_ms"] = _num(v, "latency_ms")
    else:
        v = _first(row, KEYS["latency_s"])
        if v is not None:
            out["latency_ms"] = _num(v, "latency_s") * 1000.0
    for k in ("user", "conversation", "prompt_hash"):
        v = _first(row, KEYS[k])
        out[k] = str(v) if v is not None else None
    out["time"] = _time(_first(row, KEYS["time"]))
    return out


def read_records(path):
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    stripped = text.lstrip()
    rows = None
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("records") or data.get("data") or data.get("results")
            rows = data
        except ValueError:
            rows = None
        if rows is None:
            rows = []
            for n, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        raise SystemExit("line %d of %s is not JSON" % (n, path))
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    if not isinstance(rows, list) or not rows:
        raise SystemExit("no usage records in %s" % path)
    out = []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise SystemExit("record %d of %s is not an object" % (n, path))
        out.append(normalise(row))
    return out


def load_prices(path):
    with open(path, encoding="utf-8-sig") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            raise SystemExit("%s is not JSON" % path)
    prices = {}
    for model, p in data.items():
        if model.startswith("_") or not isinstance(p, dict):
            continue
        if "input_per_1m" not in p or "output_per_1m" not in p:
            raise SystemExit("price for %r needs input_per_1m and output_per_1m" % model)
        inp = _num(p["input_per_1m"], "%s input_per_1m" % model)
        prices[model] = {"input": inp, "output": _num(p["output_per_1m"], "%s output_per_1m" % model),
                         "cached": _num(p.get("cached_input_per_1m", inp), "%s cached_input_per_1m" % model)}
    if not prices:
        raise SystemExit("no model prices in %s" % path)
    return prices


def price_for(model, prices):
    if model in prices:
        return prices[model]
    keys = [k for k in prices if model.startswith(k + "-")]
    return prices[max(keys, key=len)] if keys else None


# ---------------------------------------------------------------- analysis

def _pct(values, p):
    vals = sorted(values)
    if not vals:
        return None
    k = (len(vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def analyse(records, prices, max_calls=25, max_conversation_cost=None):
    """Return {"values": {metric: value}, "why_na": {metric: reason}, "runaway": [...], "duplicates": [...], ...}."""
    priced = [r for r in records if r["input"] is not None or r["output"] is not None]
    unpriced = sorted(set(r["model"] or "(no model)" for r in priced if not r["model"] or not price_for(r["model"], prices)))
    if unpriced:
        raise SystemExit("no price for: %s. Add them to the prices file (current provider prices, per 1M "
                         "tokens); Proof never guesses a price." % ", ".join(unpriced))
    for r in records:
        r["cost"] = None
        if r["input"] is not None or r["output"] is not None:
            p = price_for(r["model"], prices)
            inp, out, cached = r["input"] or 0.0, r["output"] or 0.0, min(r["cached"] or 0.0, r["input"] or 0.0)
            r["cost"] = ((inp - cached) * p["input"] + cached * p["cached"] + out * p["output"]) / 1e6
    values, why = {}, {}
    costs = [r["cost"] for r in records if r["cost"] is not None]
    if costs:
        values["cost_per_request_p50"] = _pct(costs, 50)
        values["cost_per_request_p95"] = _pct(costs, 95)
    else:
        for mid in ("cost_per_request_p50", "cost_per_request_p95"):
            why[mid] = "no record has token counts"
    conv = {}
    for r in records:
        if r["conversation"] is not None:
            c = conv.setdefault(r["conversation"], {"calls": 0, "cost": 0.0})
            c["calls"] += 1
            c["cost"] += r["cost"] or 0.0
    if conv and costs:
        cc = [c["cost"] for c in conv.values()]
        values["cost_per_conversation_p50"] = _pct(cc, 50)
        values["cost_per_conversation_p95"] = _pct(cc, 95)
    else:
        for mid in ("cost_per_conversation_p50", "cost_per_conversation_p95"):
            why[mid] = "records carry no conversation id" if not conv else "no record has token counts"
    user_days, user_total, user_dates = {}, {}, {}
    for r in records:
        if r["user"] is None or r["time"] is None or r["cost"] is None:
            continue
        key = (r["user"], r["time"].date())
        user_days[key] = user_days.get(key, 0.0) + r["cost"]
        user_total[r["user"]] = user_total.get(r["user"], 0.0) + r["cost"]
        user_dates.setdefault(r["user"], set()).add(r["time"].date())
    if user_days:
        values["cost_per_user_day"] = _pct(list(user_days.values()), 50)
        monthly = [user_total[u] / len(user_dates[u]) * 30.0 for u in user_total]
        values["heavy_user_month"] = _pct(monthly, 90)
        values["_active_users"] = len(user_total)
        values["_typical_user_month"] = _pct(monthly, 50)
    else:
        for mid in ("cost_per_user_day", "heavy_user_month"):
            why[mid] = "records need a user id, a time and token counts"
    toks = [(r["input"] or 0.0) + (r["output"] or 0.0) for r in priced]
    if toks:
        values["tokens_per_request_p95"] = _pct(toks, 95)
    else:
        why["tokens_per_request_p95"] = "no record has token counts"
    lat = [r["latency_ms"] for r in records if r["latency_ms"] is not None]
    for mid, p in (("latency_p50_ms", 50), ("latency_p95_ms", 95), ("latency_p99_ms", 99)):
        if lat:
            values[mid] = _pct(lat, p)
        else:
            why[mid] = "records carry no latency"
    values["error_rate"] = sum(1 for r in records if r["failed"]) / float(len(records))
    if any(r["cached"] is not None for r in records):
        total_in = sum(r["input"] or 0.0 for r in records)
        values["cache_hit_share"] = (sum(min(r["cached"] or 0.0, r["input"] or 0.0) for r in records) / total_in
                                     if total_in else 0.0)
    else:
        why["cache_hit_share"] = "records carry no cached-token counts"
    runaway = []
    if conv:
        for cid, c in sorted(conv.items()):
            over_calls = c["calls"] > max_calls
            over_cost = max_conversation_cost is not None and c["cost"] > max_conversation_cost
            if over_calls or over_cost:
                runaway.append({"conversation": cid, "calls": c["calls"], "cost": round(c["cost"], 6)})
    dups = []
    hashed = sorted((r for r in records if r["prompt_hash"] and r["time"]), key=lambda r: (r["prompt_hash"], r["time"]))
    for a, b in zip(hashed, hashed[1:]):
        if a["prompt_hash"] == b["prompt_hash"] and (b["time"] - a["time"]).total_seconds() <= DUP_WINDOW_S:
            dups.append({"prompt_hash": b["prompt_hash"], "time": b["time"].isoformat(),
                         "conversation": b["conversation"]})
    return {"values": values, "why_na": why, "runaway": runaway, "duplicates": dups,
            "conversations": len(conv), "calls": len(records), "has_conv": bool(conv),
            "has_dup_inputs": bool(hashed), "total_cost": sum(costs) if costs else None}


# ---------------------------------------------------------------- code scan

CALL = re.compile(r"(chat\.completions\.create|responses\.create|messages\.create|completions\.create|"
                  r"generateContent|\.invoke\(|ChatCompletion\.create)")
CAP = re.compile(r"(max_tokens|max_output_tokens|max_completion_tokens|maxTokens|maxOutputTokens|max_new_tokens)")
CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs")
SKIP = {"node_modules", ".git", "dist", "build", ".next", "__pycache__", ".venv", "venv", "proof", ".expo"}


def scan_code(paths):
    """Model calls with no output-token cap within the call (the next 15 lines)."""
    hits, files = [], 0
    for root in paths:
        walk = [(os.path.dirname(root), [], [os.path.basename(root)])] if os.path.isfile(root) else os.walk(root)
        for base, dirs, names in walk:
            dirs[:] = [d for d in dirs if d not in SKIP]
            for name in names:
                if not name.endswith(CODE_EXT) or name.startswith("test_") or ".test." in name or ".spec." in name:
                    continue
                path = os.path.join(base, name)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        lines = fh.read().splitlines()
                except OSError:
                    continue
                files += 1
                for i, line in enumerate(lines):
                    if CALL.search(line) and not line.lstrip().startswith(("#", "//", "*")):
                        end = i + 1
                        while end < min(len(lines), i + 15) and not CALL.search(lines[end]):
                            end += 1
                        window = "\n".join(lines[max(0, i - 3):end])
                        if not CAP.search(window):
                            hits.append({"file": path.replace(os.sep, "/"), "line": i + 1, "code": line.strip()[:120]})
    return files, hits


# ---------------------------------------------------------------- commands

def _fmt(mid, v):
    unit = METRIC[mid][2]
    if unit == "USD":
        return "$%.4f" % v
    if unit == "fraction":
        return "%.1f%%" % (v * 100)
    return "%d %s" % (round(v), unit)


def _budgets(a):
    out = {}
    for mid, _, _, _, default in METRICS:
        v = getattr(a, "budget_" + mid, None)
        out[mid] = default if v is None else v
    return out


def _read_all(a):
    prices = load_prices(a.prices)
    records = []
    for p in a.records:
        records += read_records(p)
    return records, prices


def cmd_prices(a):
    ledger.guard_out(a.out, a.dir, a.force, ours=PRICES_MARK)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(PRICES_TEMPLATE, fh, indent=2)
        fh.write("\n")
    print("%s wrote %s. Every price in it is a placeholder: fill in your provider's current prices." % (ledger.OK, a.out))
    return 0


def cmd_analyze(a):
    records, prices = _read_all(a)
    r = analyse(records, prices, a.max_calls, a.max_conversation_cost)
    budgets = _budgets(a)
    if a.json:
        print(json.dumps({"values": {k: v for k, v in r["values"].items() if not k.startswith("_")},
                          "not_applicable": r["why_na"], "budgets": budgets, "runaway": r["runaway"],
                          "duplicates": r["duplicates"], "calls": r["calls"], "total_cost": r["total_cost"]},
                         indent=2))
        return 0
    print("%d call(s), %d conversation(s)%s" % (r["calls"], r["conversations"],
                                                 ", total $%.4f" % r["total_cost"] if r["total_cost"] is not None else ""))
    for mid, label, unit, op, _ in METRICS:
        if mid in r["values"]:
            v = r["values"][mid]
            ok = v <= budgets[mid] if op == "<=" else v >= budgets[mid]
            print("  %s %-58s %s (budget %s %s)" % (ledger.OK if ok else ledger.BAD, label, _fmt(mid, v), op,
                                                    _fmt(mid, budgets[mid])))
        else:
            print("  -- %-58s not measured: %s" % (label, r["why_na"][mid]))
    if r["runaway"]:
        print("  %s %d runaway conversation(s): %s" % (ledger.BAD, len(r["runaway"]),
                                                      ", ".join(x["conversation"] for x in r["runaway"][:MAX_LISTED])))
    if r["duplicates"]:
        print("  %s %d duplicate call(s) within %ds" % (ledger.BAD, len(r["duplicates"]), DUP_WINDOW_S))
    return 0


def _existing(d, fid):
    path = ledger.finding_path(d, SKILL, fid)
    return ledger.load_finding(d, SKILL, fid) if os.path.exists(path) else None


def _drop(d, fid):
    f = _existing(d, fid)
    if f and f["status"] != "false_positive":
        os.remove(ledger.finding_path(d, SKILL, fid))


def _finding(d, fid, title, severity, ev, description, fix, location=None):
    f0 = _existing(d, fid)
    if f0 and f0["status"] == "false_positive":
        return False
    f = ledger.new_finding(SKILL, title[:160], severity, id=fid, category="llm-cost:%s" % fid.replace("llm-cost-", ""),
                           evidence=ev, description=description[:400], fix=fix, location=location)
    ledger.write_finding(d, f)
    ledger.verify(d, SKILL, fid, by="evidence-file")
    return True


def _evidence(d, paths):
    out = []
    ev_dir = ledger.evidence_dir(d, SKILL)
    for p in paths:
        dest = os.path.join(ev_dir, os.path.basename(p))
        if os.path.abspath(p) != os.path.abspath(dest):
            shutil.copy2(p, dest)
        out.append(os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/"))
    return out


def _checks_with_existing(d, checks):
    cov = ledger.load_coverage(d, SKILL)
    ids = set(c for c, _ in checks)
    extra = [(c["id"], c["title"]) for c in (cov["checks"] if cov else []) if c["id"] not in ids]
    return checks + extra


def cmd_record(a):
    records, prices = _read_all(a)
    r = analyse(records, prices, a.max_calls, a.max_conversation_cost)
    budgets = _budgets(a)
    ev_inputs = _evidence(a.dir, list(a.records) + [a.prices])
    summary_path = os.path.join(ledger.evidence_dir(a.dir, SKILL), "cost-summary.json")
    v = r["values"]
    summary = {"calls": r["calls"], "conversations": r["conversations"], "total_cost_usd": r["total_cost"],
               "active_users": v.get("_active_users"),
               "llm_cost_per_active_user_per_month_usd": v.get("_typical_user_month"),
               "llm_cost_per_heavy_user_per_month_usd": v.get("heavy_user_month"),
               "note": "Projections assume each user's average active-day spend over 30 days. Copy the "
                       "per-user figure into the variable costs of your proof-finance numbers."}
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    ev = ev_inputs + [os.path.relpath(summary_path, ledger.root(a.dir)).replace(os.sep, "/")]
    checks = [(mid, label) for mid, label, _, _, _ in METRICS] + EXTRA_CHECKS
    ledger.init_coverage(a.dir, SKILL, _checks_with_existing(a.dir, checks))
    scores, failing = [], []
    for mid, label, unit, op, _ in METRICS:
        fid = "llm-cost-" + mid.replace("_", "-")
        if mid not in v:
            ledger.mark_coverage(a.dir, SKILL, mid, "not_applicable", note=r["why_na"][mid])
            _drop(a.dir, fid)
            continue
        val, budget = v[mid], budgets[mid]
        ok = val <= budget if op == "<=" else val >= budget
        ledger.mark_coverage(a.dir, SKILL, mid, "covered", evidence=ev,
                             note="%s over %d call(s)" % (_fmt(mid, val), r["calls"]))
        scores.append({"id": mid, "value": round(val, 6), "unit": unit, "budget": budget, "op": op,
                       "source": "llmcost.py", "evidence": ev})
        if ok:
            _drop(a.dir, fid)
            continue
        failing.append(mid)
        _finding(a.dir, fid, "%s is %s against a budget of %s %s" % (label, _fmt(mid, val), op, _fmt(mid, budget)),
                 "medium", ev, "%s over %d call(s) in %s" % (_fmt(mid, val), r["calls"], ", ".join(a.records)),
                 "Cap output tokens, trim the prompt and retrieved context, cache repeated prefixes, or route "
                 "simple requests to a smaller model; then export usage again.")
    if r["has_conv"]:
        ledger.mark_coverage(a.dir, SKILL, "runaway", "covered", evidence=ev,
                             note="%d of %d conversation(s) over the cap" % (len(r["runaway"]), r["conversations"]))
        if r["runaway"]:
            ids = ", ".join("%s (%d calls, $%.4f)" % (x["conversation"], x["calls"], x["cost"])
                            for x in r["runaway"][:MAX_LISTED])
            _finding(a.dir, "llm-cost-runaway", "%d runaway conversation(s): over %d calls%s" % (
                len(r["runaway"]), a.max_calls,
                (" or $%.2f" % a.max_conversation_cost) if a.max_conversation_cost is not None else ""),
                "medium", ev, ids, "Stop agent or retry loops after a fixed number of calls and a spend cap per "
                                   "conversation, and alert when one is hit.")
        else:
            _drop(a.dir, "llm-cost-runaway")
    else:
        ledger.mark_coverage(a.dir, SKILL, "runaway", "not_applicable", note="records carry no conversation id")
        _drop(a.dir, "llm-cost-runaway")
    if r["has_dup_inputs"]:
        ledger.mark_coverage(a.dir, SKILL, "duplicates", "covered", evidence=ev,
                             note="%d duplicate call(s) within %ds" % (len(r["duplicates"]), DUP_WINDOW_S))
        if r["duplicates"]:
            ex = ", ".join("%s at %s" % (x["prompt_hash"], x["time"]) for x in r["duplicates"][:MAX_LISTED])
            _finding(a.dir, "llm-cost-duplicates", "%d duplicate model call(s) within %d seconds" % (
                len(r["duplicates"]), DUP_WINDOW_S), "low", ev, ex,
                "Disable the send button while a request is in flight and de-duplicate retries by request id.")
        else:
            _drop(a.dir, "llm-cost-duplicates")
    else:
        ledger.mark_coverage(a.dir, SKILL, "duplicates", "not_applicable",
                             note="records need a prompt_hash and a time")
        _drop(a.dir, "llm-cost-duplicates")
    if scores:
        ledger.write_scores(a.dir, SKILL, scores)
    print("%s recorded %d metric(s) over %d call(s); %d over budget%s. Summary for proof-finance: %s" % (
        ledger.OK if not failing else ledger.BAD, len(scores), r["calls"], len(failing),
        (": " + ", ".join(failing)) if failing else "",
        os.path.relpath(summary_path, ledger.root(a.dir)).replace(os.sep, "/")))
    return 0


def cmd_scan_code(a):
    files, hits = scan_code(a.paths)
    if a.json:
        print(json.dumps({"files": files, "uncapped": hits}, indent=2))
        return 0
    print("%s scanned %d file(s): %d model call(s) with no output-token cap nearby." % (
        ledger.OK if not hits else ledger.BAD, files, len(hits)))
    for h in hits[:MAX_LISTED * 3]:
        print("  %s:%d  %s" % (h["file"], h["line"], h["code"]))
    if not a.record:
        return 0
    ev_path = os.path.join(ledger.evidence_dir(a.dir, SKILL), "code-scan.json")
    with open(ev_path, "w", encoding="utf-8") as fh:
        json.dump({"files": files, "uncapped": hits}, fh, indent=2)
    ev = [os.path.relpath(ev_path, ledger.root(a.dir)).replace(os.sep, "/")]
    ledger.init_coverage(a.dir, SKILL, _checks_with_existing(a.dir, [CODE_CHECK]))
    ledger.mark_coverage(a.dir, SKILL, CODE_CHECK[0], "covered", evidence=ev,
                         note="%d uncapped call(s) in %d file(s)" % (len(hits), files))
    for f in ledger.load_findings(a.dir, SKILL):
        if f["id"].startswith("llm-cost-uncapped-") and f["status"] != "false_positive":
            os.remove(ledger.finding_path(a.dir, SKILL, f["id"]))
    for h in hits:
        fid = ledger.slug("llm-cost-uncapped-%s-%d" % (os.path.basename(h["file"]), h["line"]))[:60]
        _finding(a.dir, fid, "Model call with no output-token cap at %s:%d" % (os.path.basename(h["file"]), h["line"]),
                 "low", ev, h["code"], "Pass max_tokens (or max_output_tokens) so one reply cannot run up the bill.",
                 location="%s:%d" % (h["file"], h["line"]))
    print("%s recorded the code scan." % ledger.OK)
    return 0


def cmd_status(a):
    scores = [s for s in ledger.load_all_scores(a.dir) if s["skill"] == SKILL]
    if not scores:
        print("no LLM cost measurements recorded yet.")
    for m in (scores[0]["metrics"] if scores else []):
        mark = ledger.OK if ledger.metric_passes(m) else ledger.BAD
        print("  %s %-26s %s (budget %s %s)" % (mark, m["id"], _fmt(m["id"], m["value"]), m["op"],
                                                _fmt(m["id"], m["budget"])))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def _add_record_args(p):
    p.add_argument("records", nargs="+", help="usage records: JSON, JSON lines or CSV")
    p.add_argument("--prices", required=True, help="your prices file (USD per 1M tokens per model)")
    p.add_argument("--max-calls", type=int, default=25, help="calls in one conversation that count as runaway")
    p.add_argument("--max-conversation-cost", type=float, help="USD in one conversation that counts as runaway")
    for mid, label, unit, op, default in METRICS:
        p.add_argument("--budget-" + mid.replace("_", "-"), dest="budget_" + mid, type=float,
                       help="%s budget, %s %s (default %s)" % (label.lower(), op, unit, default))


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prices", help="write a prices template to fill in")
    pr.add_argument("--out", default=os.path.join("proof", "llm-cost", "prices.json"))
    pr.add_argument("--force", action="store_true", help="overwrite a file Proof did not write")
    pr.set_defaults(func=cmd_prices)
    an = sub.add_parser("analyze", help="print cost and latency figures")
    _add_record_args(an)
    an.add_argument("--json", action="store_true")
    an.set_defaults(func=cmd_analyze)
    rc = sub.add_parser("record", help="write scores, coverage and findings to the ledger")
    _add_record_args(rc)
    rc.set_defaults(func=cmd_record)
    sc = sub.add_parser("scan-code", help="find model calls with no output-token cap")
    sc.add_argument("paths", nargs="+", help="source files or folders")
    sc.add_argument("--json", action="store_true")
    sc.add_argument("--record", action="store_true", help="also write the result to the ledger")
    sc.set_defaults(func=cmd_scan_code)
    sub.add_parser("status", help="recorded scores and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
