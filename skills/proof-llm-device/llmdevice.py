#!/usr/bin/env python3
"""Score an app's on-device language-model performance against a budget, into the ledger.

Standard library only, no network, and it never touches the device: it reads files
you captured. Supported inputs, detected by content:

  - llama.cpp `llama-bench -o json` output (prefill and decode tokens per second,
    model size);
  - Android `adb shell dumpsys meminfo <package>` text (the app's total PSS, taken
    as peak memory when several captures are given);
  - Android `adb shell dumpsys battery` text, two captures (before and after a
    sustained run) with --prompts, for battery drain per 100 prompts;
  - a measurements file you fill in, JSON or CSV, one row per run (see SCHEMA).

Every metric in the budget becomes a coverage check and, when measured, a score.
A metric with no input is not applicable with a reason, never a silent pass.

    python3 llmdevice.py budget --out proof/llm-device/budget.json --class mid
    python3 llmdevice.py measure bench.json meminfo.txt runs.csv
    python3 llmdevice.py record bench.json meminfo.txt runs.csv --device "Pixel 8" --model "gemma-2b-q4"
    python3 llmdevice.py record before.txt after.txt --prompts 100
    python3 llmdevice.py status
"""

import argparse
import csv
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

SKILL = "proof-llm-device"

# (id, label, unit, op). ">=" metrics are better when higher.
METRICS = [
    ("ttft_ms", "Time to first token", "ms", "<="),
    ("prefill_tps", "Prefill speed", "tokens/s", ">="),
    ("decode_tps", "Decode speed", "tokens/s", ">="),
    ("peak_mem_mb", "Peak memory", "MB", "<="),
    ("throttle_pct", "Slowdown over a sustained run (thermal throttling)", "%", "<="),
    ("battery_pct_per_100", "Battery drain per 100 prompts", "%", "<="),
    ("model_mb", "Model file size", "MB", "<="),
]
METRIC = dict((m[0], m) for m in METRICS)

# Default budgets by device class. Starting points, not standards: edit them to the
# line your team decided on.
CLASSES = {
    "high": {"ttft_ms": 800, "prefill_tps": 100, "decode_tps": 15, "peak_mem_mb": 3000, "throttle_pct": 25,
             "battery_pct_per_100": 5, "model_mb": 2500},
    "mid": {"ttft_ms": 1500, "prefill_tps": 50, "decode_tps": 8, "peak_mem_mb": 2000, "throttle_pct": 30,
            "battery_pct_per_100": 8, "model_mb": 1500},
    "low": {"ttft_ms": 3000, "prefill_tps": 20, "decode_tps": 4, "peak_mem_mb": 1200, "throttle_pct": 40,
            "battery_pct_per_100": 12, "model_mb": 800},
}

SCHEMA = ("Measurements file: JSON (a list of runs, or {\"runs\": [...]}) or CSV with a header row. "
          "Each run may carry any of: " + ", ".join(m[0] for m in METRICS) +
          ". Several runs give a median and a worst-10% figure. A JSON run may also carry "
          "battery_start_pct, battery_end_pct and prompts, and a sustained run of 20 or more decode_tps "
          "values in order gives throttle_pct when it is not stated.")

BUDGET_MARK = '"decode_tps"'


class InputError(ValueError):
    """A captured file could not be read as any supported input."""


# ---------------------------------------------------------------- statistics

def _percentile(values, p):
    vals = sorted(values)
    if not vals:
        return None
    k = (len(vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def summarise(mid, values):
    """Median and the worst-10% value (the high tail for <= metrics, the low tail for >=)."""
    op = METRIC[mid][3]
    return {"median": _percentile(values, 50), "worst": _percentile(values, 10 if op == ">=" else 90),
            "runs": len(values)}


# ---------------------------------------------------------------- parsers

def parse_llama_bench(data):
    """llama-bench -o json: a list of runs. Prompt runs give prefill, generation runs give decode."""
    out = {}
    for run in data:
        n_prompt = run.get("n_prompt") or 0
        n_gen = run.get("n_gen") or 0
        ts = num.parse_number(run.get("avg_ts"), field="avg_ts")
        if n_gen > 0:
            out.setdefault("decode_tps", []).append(ts)
        elif n_prompt > 0:
            out.setdefault("prefill_tps", []).append(ts)
        if run.get("model_size"):
            out["model_mb"] = [num.parse_number(run["model_size"], field="model_size") / (1024.0 * 1024.0)]
    return out


def parse_meminfo(text):
    """Total PSS in MB from dumpsys meminfo (the App Summary line or the TOTAL row, in KB)."""
    m = re.search(r"TOTAL PSS:\s*([\d,]+)", text)
    if not m:
        m = re.search(r"^\s*TOTAL\s+([\d,]+)", text, re.M)
    if not m:
        raise InputError("no TOTAL PSS row found in the meminfo capture")
    return int(m.group(1).replace(",", "")) / 1024.0


def parse_battery(text):
    m = re.search(r"^\s*level:\s*(\d+)", text, re.M)
    if not m:
        raise InputError("no 'level:' line found in the battery capture")
    return int(m.group(1))


def _runs_from_rows(rows):
    out = {}
    for i, row in enumerate(rows, 1):
        for mid in METRIC:
            v = row.get(mid)
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            out.setdefault(mid, []).append(num.parse_number(v, field="run %d %s" % (i, mid)))
        if all(row.get(k) not in (None, "") for k in ("battery_start_pct", "battery_end_pct", "prompts")):
            drop = num.parse_number(row["battery_start_pct"]) - num.parse_number(row["battery_end_pct"])
            out.setdefault("battery_pct_per_100", []).append(drop / num.parse_number(row["prompts"]) * 100.0)
    decode = out.get("decode_tps", [])
    if "throttle_pct" not in out and len(decode) >= 20:
        first, last = _percentile(decode[:5], 50), _percentile(decode[-5:], 50)
        if first:
            out["throttle_pct"] = [max(0.0, (1.0 - last / first) * 100.0)]
    return out


def parse_generic(data):
    rows = data.get("runs") if isinstance(data, dict) and "runs" in data else data
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise InputError("a measurements JSON must be a list of runs or {\"runs\": [...]}")
    return _runs_from_rows(rows)


def classify(path, text):
    """Return (kind, parsed) for one captured file."""
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise InputError("%s is not valid JSON: %s" % (path, e))
        if isinstance(data, list) and data and all(isinstance(r, dict) and "avg_ts" in r for r in data):
            return "llama-bench", parse_llama_bench(data)
        return "measurements", parse_generic(data)
    if "MEMINFO" in text or "TOTAL PSS" in text or re.search(r"^\s*TOTAL\s+\d", text, re.M):
        return "meminfo", parse_meminfo(text)
    if re.search(r"^\s*level:\s*\d+", text, re.M) and "battery" in text.lower():
        return "battery", parse_battery(text)
    first = text.splitlines()[0] if text.strip() else ""
    if any(mid in first for mid in METRIC):
        return "measurements", _runs_from_rows(list(csv.DictReader(io.StringIO(text))))
    raise InputError("%s is not a llama-bench JSON, meminfo, battery or measurements file" % path)


def gather(paths, prompts=None):
    """Read every file. Returns ({metric: [values]}, {metric: [source paths]}, [(path, kind)])."""
    values, sources, kinds, mem, battery = {}, {}, [], [], []
    for p in paths:
        with open(p, encoding="utf-8-sig", errors="replace") as fh:
            kind, parsed = classify(p, fh.read())
        kinds.append((p, kind))
        if kind == "meminfo":
            mem.append(parsed)
            sources.setdefault("peak_mem_mb", []).append(p)
        elif kind == "battery":
            battery.append((p, parsed))
        else:
            for mid, vals in parsed.items():
                values.setdefault(mid, []).extend(vals)
                sources.setdefault(mid, []).append(p)
    if mem:
        values["peak_mem_mb"] = values.get("peak_mem_mb", []) + [max(mem)]
    if battery:
        if len(battery) != 2:
            raise InputError("battery drain needs exactly two dumpsys battery captures (before, after); got %d"
                             % len(battery))
        if not prompts:
            raise InputError("battery drain from two captures needs --prompts (how many prompts ran between them)")
        drop = battery[0][1] - battery[1][1]
        values.setdefault("battery_pct_per_100", []).append(drop / float(prompts) * 100.0)
        sources.setdefault("battery_pct_per_100", []).extend(p for p, _ in battery)
    return values, sources, kinds


# ---------------------------------------------------------------- budget

def default_budget(cls="mid"):
    return {"about": "An on-device LLM performance budget for %s-class devices. Edit the numbers to the line "
                     "your team decided on; remove a metric you do not care about. Units: ms, tokens/s, MB, %%."
                     % cls,
            "device_class": cls,
            "metrics": [{"id": mid, "label": label, "unit": unit, "budget": CLASSES[cls][mid], "op": op}
                        for mid, label, unit, op in METRICS]}


def load_budget(path):
    if not path:
        return default_budget("mid")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for m in data.get("metrics", []):
        if m.get("id") not in METRIC:
            raise SystemExit("budget metric %r is not one of: %s" % (m.get("id"), ", ".join(METRIC)))
        out.append({"id": m["id"], "label": m.get("label") or METRIC[m["id"]][1], "unit": METRIC[m["id"]][2],
                    "budget": num.parse_number(m.get("budget"), field="%s budget" % m["id"]),
                    "op": m.get("op") or METRIC[m["id"]][3]})
    data["metrics"] = out
    return data


def _passes(value, budget, op):
    return ledger.metric_passes({"value": value, "budget": budget, "op": op})


def _n(x):
    return ("%.1f" % x).rstrip("0").rstrip(".") if x is not None else "-"


# ---------------------------------------------------------------- commands

def cmd_budget(a):
    ledger.guard_out(a.out, a.dir, a.force, ours=BUDGET_MARK)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(default_budget(a.cls), fh, indent=2)
        fh.write("\n")
    print("%s wrote a %s-class budget to %s. Edit it to your own line." % (ledger.OK, a.cls, a.out))
    return 0


def _read_all(a):
    try:
        return gather(a.files, getattr(a, "prompts", None))
    except (InputError, num.BadNumber, num.MissingInput) as e:
        raise SystemExit("error: %s" % e)


def cmd_measure(a):
    values, sources, kinds = _read_all(a)
    for p, kind in kinds:
        print("  %s: %s" % (kind, p))
    for mid, label, unit, _ in METRICS:
        if mid in values:
            s = summarise(mid, values[mid])
            print("  %-20s median %s %s, worst 10%% %s (%d run(s))" % (mid, _n(s["median"]), unit, _n(s["worst"]),
                                                                      s["runs"]))
        else:
            print("  %-20s not measured" % mid)
    return 0


def _evidence(d, paths):
    out = []
    ev_dir = ledger.evidence_dir(d, SKILL)
    for i, p in enumerate(paths, 1):
        dest = os.path.join(ev_dir, "%02d-%s" % (i, os.path.basename(p)))
        shutil.copy2(p, dest)
        out.append(os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/"))
    return out


def cmd_record(a):
    values, sources, kinds = _read_all(a)
    budget = load_budget(a.budget)
    copied = dict(zip(a.files, _evidence(a.dir, a.files)))
    if a.budget:
        copied[a.budget] = _evidence(a.dir, [a.budget])[0]
    ledger.init_coverage(a.dir, SKILL, [(m["id"], m["label"]) for m in budget["metrics"]])
    where = ", ".join(x for x in (a.device, a.model) if x)
    scores, failing = [], []
    for m in budget["metrics"]:
        mid = m["id"]
        if mid not in values:
            ledger.mark_coverage(a.dir, SKILL, mid, "not_applicable",
                                 note="no captured input measured %s" % m["label"].lower())
            _drop_stale(a.dir, mid)
            continue
        s = summarise(mid, values[mid])
        ev = sorted(set(copied[p] for p in sources[mid]))
        ok = _passes(s["median"], m["budget"], m["op"])
        note = "median %s %s, worst 10%% %s over %d run(s)%s" % (_n(s["median"]), m["unit"], _n(s["worst"]),
                                                                s["runs"], (" on " + where) if where else "")
        ledger.mark_coverage(a.dir, SKILL, mid, "covered", evidence=ev, note=note)
        scores.append({"id": mid, "value": round(s["median"], 3), "unit": m["unit"], "budget": m["budget"],
                       "op": m["op"], "source": "llmdevice.py" + (" (%s)" % where if where else ""),
                       "evidence": ev})
        if ok:
            _drop_stale(a.dir, mid)
            continue
        failing.append(mid)
        fid = "llm-device-" + mid.replace("_", "-")
        title = "%s is %s %s against a budget of %s %s %s%s" % (
            m["label"], _n(s["median"]), m["unit"], m["op"], _n(m["budget"]), m["unit"],
            (" (%s)" % where) if where else "")
        existing = _existing(a.dir, fid)
        if existing and existing["status"] == "false_positive":
            continue
        f = ledger.new_finding(SKILL, title[:160], "medium", id=fid, category="llm-device:%s" % mid,
                               evidence=ev, description=note,
                               fix="Use a smaller or more quantised model, cap the context, or move the work off "
                                   "the device; then measure again.")
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    if scores:
        ledger.write_scores(a.dir, SKILL, scores)
    print("%s recorded %d of %d metric(s); %d over budget%s." % (
        ledger.OK if not failing else ledger.BAD, len(scores), len(budget["metrics"]), len(failing),
        (": " + ", ".join(failing)) if failing else ""))
    return 0


def _existing(d, fid):
    path = ledger.finding_path(d, SKILL, fid)
    return ledger.load_finding(d, SKILL, fid) if os.path.exists(path) else None


def _drop_stale(d, mid):
    fid = "llm-device-" + mid.replace("_", "-")
    f = _existing(d, fid)
    if f and f["status"] != "false_positive":
        os.remove(ledger.finding_path(d, SKILL, fid))


def cmd_status(a):
    scores = [s for s in ledger.load_all_scores(a.dir) if s["skill"] == SKILL]
    if not scores:
        print("no on-device LLM measurements recorded yet.")
        return 0
    for m in scores[0]["metrics"]:
        mark = ledger.OK if ledger.metric_passes(m) else ledger.BAD
        print("  %s %-20s %s %s (budget %s %s)" % (mark, m["id"], _n(m["value"]), m["unit"], m["op"], _n(m["budget"])))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("budget", help="write an example budget")
    b.add_argument("--out", default=os.path.join("proof", "llm-device", "budget.json"))
    b.add_argument("--class", dest="cls", choices=sorted(CLASSES), default="mid",
                   help="device class the defaults are for (default mid)")
    b.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    b.set_defaults(func=cmd_budget)
    for name, func, hlp in (("measure", cmd_measure, "print what the captured files measure"),
                            ("record", cmd_record, "score the captured files against the budget")):
        p = sub.add_parser(name, help=hlp, epilog=SCHEMA)
        p.add_argument("files", nargs="+", help="llama-bench JSON, meminfo or battery text, measurements JSON/CSV")
        p.add_argument("--prompts", type=int, help="prompts run between two battery captures")
        if name == "record":
            p.add_argument("--budget", help="budget file (default: the mid-class defaults)")
            p.add_argument("--device", help="the device measured, e.g. \"Pixel 8\"")
            p.add_argument("--model", help="the model measured, e.g. \"gemma-2b-q4\"")
        p.set_defaults(func=func)
    sub.add_parser("status", help="recorded scores").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
