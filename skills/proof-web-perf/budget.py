#!/usr/bin/env python3
"""Score a web page's load performance against a written budget, into the ledger.

Standard library only, no network. The budget is a decision written down once; the
measured numbers come from a Lighthouse JSON result when Lighthouse is installed,
or from a small file you fill in by hand. Every metric in the budget becomes a
score (value against budget) and a coverage check. A metric nobody measured is
never a pass: it is an error, or a gap the report shows.

    python3 budget.py template --out budget.json
    python3 budget.py record --budget budget.json --measured measured.json --source "devtools, 3rd reload"
    python3 budget.py from-lighthouse --json lhr.json --budget budget.json
    python3 budget.py skip tbt --note "no long-task timing in this browser" --budget budget.json
    python3 budget.py status
"""

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402
import num  # noqa: E402

SKILL = "proof-web-perf"
OPS = ("<=", ">=", "<", ">", "==")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# The budget the template writes. Units: s = seconds, ms = milliseconds,
# kb = 1024 bytes (the way Lighthouse shows weight), score = unitless, count = how many.
DEFAULT_METRICS = [
    {"id": "lcp", "label": "Largest Contentful Paint", "unit": "s", "budget": 2.5, "op": "<="},
    {"id": "cls", "label": "Cumulative Layout Shift", "unit": "score", "budget": 0.1, "op": "<="},
    {"id": "tbt", "label": "Total Blocking Time", "unit": "ms", "budget": 200, "op": "<="},
    {"id": "fcp", "label": "First Contentful Paint", "unit": "s", "budget": 1.8, "op": "<="},
    {"id": "ttfb", "label": "Time to first byte", "unit": "ms", "budget": 800, "op": "<="},
    {"id": "total_js", "label": "JavaScript transferred", "unit": "kb", "budget": 300, "op": "<="},
    {"id": "total_transfer", "label": "Total bytes transferred", "unit": "kb", "budget": 1600, "op": "<="},
    {"id": "requests", "label": "Network requests", "unit": "count", "budget": 80, "op": "<="},
]

ABOUT = ("A web performance budget. Edit the numbers to the line your team decided on; "
         "remove a metric you do not care about rather than setting it loose. Units: s = seconds, "
         "ms = milliseconds, kb = 1024 bytes, score = unitless, count = how many.")

# budget id -> (Lighthouse audit id, the unit Lighthouse reports it in, divisor into the budget's unit)
LH_AUDITS = {
    "lcp": ("largest-contentful-paint", "millisecond", 1000.0),
    "fcp": ("first-contentful-paint", "millisecond", 1000.0),
    "cls": ("cumulative-layout-shift", "unitless", 1.0),
    "tbt": ("total-blocking-time", "millisecond", 1.0),
    "ttfb": ("server-response-time", "millisecond", 1.0),
    "total_transfer": ("total-byte-weight", "byte", 1024.0),
}
LH_NETWORK = "network-requests"  # requests = its row count; total_js = its Script rows' transferSize
NO_MAPPING = "no Lighthouse audit maps to this id; measure it by hand and pass it with --measured, or skip it"


class BudgetError(ValueError):
    """The budget or the measured file is not usable as given."""


class NotAnLhr(ValueError):
    """The file is not a Lighthouse JSON result."""


# ---------------------------------------------------------------- helpers

def _ascii(text):
    """Console output stays ASCII whatever the input files contain."""
    return str(text).encode("ascii", "replace").decode("ascii")


def say(text=""):
    print(_ascii(text))


def _fmt(x):
    x = float(x)
    if x.is_integer():
        return "%d" % x
    return ("%.3f" % x).rstrip("0").rstrip(".")


def _tidy(x):
    """Store 80 rather than 80.0 so the ledger reads naturally."""
    x = float(x)
    return int(x) if x.is_integer() else x


def _short(path):
    """A path relative to the working directory when that is possible (not across drives)."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def _read_json(path, what):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return json.loads(_decode(raw))
    except OSError as e:
        raise BudgetError("cannot read %s %s: %s" % (what, path, e.strerror or e))
    except ValueError as e:
        raise BudgetError("%s %s is not valid JSON: %s" % (what, path, e))


def _decode(raw):
    """UTF-8 by default; honour a byte-order mark, since a PowerShell redirect writes UTF-16."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8")


def _under_proof(d, path):
    """The ledger-relative path of ``path`` when it already lives under proof/, else None."""
    base = ledger.root(d)
    full = os.path.abspath(path)
    try:
        inside = os.path.commonpath([full, base]) == base
    except ValueError:  # different drives on Windows
        inside = False
    return os.path.relpath(full, base).replace(os.sep, "/") if inside else None


def _check_evidence(d, rel):
    """``rel`` must name an existing, non-empty file under proof/. Returns it with forward slashes."""
    full = rel if os.path.isabs(rel) else os.path.join(ledger.root(d), rel)
    if not os.path.isfile(full):
        raise BudgetError("evidence file does not exist under %s: %s" % (ledger.root(d), rel))
    if os.path.getsize(full) == 0:
        raise BudgetError("evidence file is empty: %s" % rel)
    inside = _under_proof(d, full)
    if inside is None:
        raise BudgetError("evidence must live under %s: %s" % (ledger.root(d), rel))
    return inside


# ----------------------------------------------------------------- budget

def load_budget(path):
    """Read a budget file and return its metrics, in file order, with numeric budgets."""
    data = _read_json(path, "budget")
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(metrics, list) or not metrics:
        raise BudgetError("%s needs a non-empty 'metrics' list (write one with: budget.py template)" % path)
    out, seen = [], set()
    for i, m in enumerate(metrics):
        where = "metric %d in %s" % (i + 1, path)
        if not isinstance(m, dict):
            raise BudgetError("%s is not an object" % where)
        mid = m.get("id")
        if not isinstance(mid, str) or not _ID.match(mid):
            raise BudgetError("%s needs an 'id' of lowercase letters, digits, '-' or '_'" % where)
        if mid in seen:
            raise BudgetError("duplicate metric id %r in %s" % (mid, path))
        seen.add(mid)
        unit = m.get("unit")
        if not isinstance(unit, str) or not unit.strip():
            raise BudgetError("metric %r needs a 'unit' (s, ms, kb, count, score, ...)" % mid)
        if m.get("op") not in OPS:
            raise BudgetError("metric %r needs 'op' as one of %s" % (mid, ", ".join(OPS)))
        try:
            budget = num.require(m, "budget")
        except (num.MissingInput, num.BadNumber) as e:
            raise BudgetError("metric %r: %s" % (mid, e))
        label = m.get("label")
        if not isinstance(label, str) or not label.strip():
            label = mid
        out.append({"id": mid, "label": label.strip(), "unit": unit.strip(), "budget": budget, "op": m["op"]})
    return out


def load_measured(path):
    """Read a measured file, a JSON object of metric id to value, and return {id: float}.

    Values may be numbers or strings; strings parse locale-safe, so "2,5" is two and
    a half. An empty or non-numeric value is an error, never a zero.
    """
    data = _read_json(path, "measured file")
    if not isinstance(data, dict) or not data:
        raise BudgetError("%s must be a non-empty JSON object of metric id to measured value" % path)
    out = {}
    for mid, raw in data.items():
        try:
            out[mid] = num.parse_number(raw, field=mid)
        except (num.MissingInput, num.BadNumber) as e:
            raise BudgetError("%s: %s" % (path, e))
    return out


def match(budget, measured, allow_missing=False):
    """Pair every budget metric with its measured value.

    Returns (scores, missing): ledger score records in budget order, and the budget
    ids that have no value. A measured id the budget does not define is always an
    error. A budget id with no value is an error unless ``allow_missing``; the caller
    then has to account for every id in ``missing``.
    """
    ids = [m["id"] for m in budget]
    unknown = sorted(k for k in measured if k not in ids)
    if unknown:
        raise BudgetError("measured value(s) for %s, which the budget does not define; add them to the "
                          "budget or take them out of the measured file" % ", ".join(repr(u) for u in unknown))
    missing = [i for i in ids if i not in measured]
    if missing and not allow_missing:
        raise BudgetError("no measured value for %s; a metric nobody measured is never a pass"
                          % ", ".join(repr(i) for i in missing))
    scores = [{"id": m["id"], "value": _tidy(measured[m["id"]]), "unit": m["unit"],
               "budget": _tidy(m["budget"]), "op": m["op"]} for m in budget if m["id"] in measured]
    return scores, missing


def not_applicable_marks(d):
    """{metric id: note} for every check already marked not applicable in this skill's coverage."""
    cov = ledger.load_coverage(d, SKILL)
    if cov is None:
        return {}
    return {c["id"]: c.get("note", "") for c in cov["checks"] if c["status"] == "not_applicable"}


# ---------------------------------------------------------------- recording

def record(d, budget, scores, source=None, evidence=None, gaps=None):
    """Write this run's scores and a fresh coverage ledger.

    ``gaps`` maps a budget id with no score to ("not_run", why) or
    ("not_applicable", note). The coverage ledger is rebuilt from the budget each
    run, so nothing from an earlier run against other inputs survives unnoticed.
    """
    gaps = gaps or {}
    metrics = []
    for s in scores:
        m = dict(s)
        if source:
            m["source"] = source
        if evidence:
            m["evidence"] = list(evidence)
        metrics.append(m)
    by_id = {s["id"]: s for s in scores}
    ledger.write_scores(d, SKILL, metrics)
    ledger.init_coverage(d, SKILL, [(m["id"], m["label"]) for m in budget], keep_existing=False)
    for m in budget:
        mid = m["id"]
        if mid in by_id:
            s = by_id[mid]
            note = "measured %s %s against %s %s %s" % (_fmt(s["value"]), s["unit"], s["op"], _fmt(s["budget"]), s["unit"])
            if source:
                note += " (%s)" % source
            ledger.mark_coverage(d, SKILL, mid, "covered", note=note, evidence=evidence)
        else:
            status, note = gaps.get(mid, ("not_run", "not measured in this run"))
            ledger.mark_coverage(d, SKILL, mid, status, note=note or "no reason given")
    return metrics


MARK = {"not_run": "..", "not_applicable": "--"}


def print_table(budget, scores, gaps):
    """One row per budget metric. Returns how many measured metrics are within budget."""
    by_id = {s["id"]: s for s in scores}
    w_id = max(len(m["id"]) for m in budget)
    w_label = max(len(m["label"]) for m in budget)
    passed = 0
    for m in budget:
        limit = ("%s %s %s" % (m["op"], _fmt(m["budget"]), m["unit"])) if m["op"] else ""
        if m["id"] in by_id:
            s = by_id[m["id"]]
            ok = ledger.metric_passes(s)
            passed += 1 if ok else 0
            mark, value, tail = (ledger.OK if ok else ledger.BAD), "%s %s" % (_fmt(s["value"]), s["unit"]), ""
        else:
            status, note = gaps.get(m["id"], ("not_run", "not measured"))
            mark, value = MARK[status], ("not applicable" if status == "not_applicable" else "not measured")
            tail = "  (%s)" % note
        say("  %s  %-*s  %-*s  %16s  %s%s" % (mark, w_id, m["id"], w_label, m["label"], value, limit, tail))
    return passed


def summary(d, budget, scores, gaps):
    passed = print_table(budget, scores, gaps)
    over = len(scores) - passed
    bits = ["%d of %d within budget" % (passed, len(scores))]
    if over:
        bits.append("%d over" % over)
    n_na = sum(1 for g in gaps.values() if g[0] == "not_applicable")
    n_nr = len(gaps) - n_na
    if n_nr:
        bits.append("%d not measured" % n_nr)
    if n_na:
        bits.append("%d not applicable" % n_na)
    say("%s. Wrote %s and coverage." % (", ".join(bits), _short(ledger.scores_path(d, SKILL))))
    if n_nr:
        say("A metric not measured keeps the verdict Incomplete: measure it and record again, "
            "or skip it with a reason.")
    say("Next: /proof-report")


def _target_line(d):
    try:
        target = ledger.load_target(d)
    except ledger.LedgerError:
        return None
    if not target:
        return None
    urls = ", ".join(target.get("urls") or []) or "no URL listed"
    return "target: %s (%s)%s" % (target["name"], urls,
                                  "" if target.get("authorised") else "; not marked authorised, measure local builds only")


# --------------------------------------------------------------- lighthouse

def read_lhr(path):
    """Return the Lighthouse result in ``path``, or raise NotAnLhr."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        raise NotAnLhr("cannot read %s: %s" % (path, e.strerror or e))
    try:
        data = json.loads(_decode(raw))
    except ValueError:
        raise NotAnLhr("%s is not JSON. Expected a Lighthouse result, written with: "
                       "lighthouse <url> --output json --output-path %s" % (path, path))
    if isinstance(data, dict) and isinstance(data.get("lhr"), dict):
        data = data["lhr"]  # the programmatic API wraps the result as {lhr, report}
    if not isinstance(data, dict) or not isinstance(data.get("audits"), dict):
        raise NotAnLhr("%s does not look like a Lighthouse result: no 'audits' object in it" % path)
    return data


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _network_items(audits):
    audit = audits.get(LH_NETWORK)
    if not isinstance(audit, dict):
        return None
    details = audit.get("details")
    items = details.get("items") if isinstance(details, dict) else None
    return items if isinstance(items, list) else None


def lighthouse_values(lhr):
    """Pull the budget metrics out of a Lighthouse result.

    Returns (values, notes): ``values`` maps a budget id to a number in the budget's
    unit; ``notes`` maps a budget id that could not be read to the reason. Nothing is
    guessed: an audit that is absent, errored or in an unexpected unit becomes a note.
    """
    audits = lhr["audits"]
    values, notes = {}, {}
    for mid, (audit_id, unit, divisor) in LH_AUDITS.items():
        audit = audits.get(audit_id)
        if not isinstance(audit, dict):
            notes[mid] = "no %s audit in this result" % audit_id
            continue
        raw = audit.get("numericValue")
        if not _number(raw):
            why = audit.get("errorMessage") or audit.get("scoreDisplayMode") or "not measured"
            notes[mid] = "%s has no numericValue (%s)" % (audit_id, why)
            continue
        got = audit.get("numericUnit")
        if got and got != unit:
            notes[mid] = "%s is reported in %s, expected %s" % (audit_id, got, unit)
            continue
        values[mid] = raw / divisor
    items = _network_items(audits)
    if items is None:
        notes["requests"] = notes["total_js"] = "no %s audit with a details table in this result" % LH_NETWORK
        return values, notes
    values["requests"] = float(len(items))
    rows = [i for i in items if isinstance(i, dict) and "resourceType" in i]
    if not rows:
        notes["total_js"] = "%s rows carry no resourceType, so script bytes cannot be summed" % LH_NETWORK
        return values, notes
    total = 0.0
    for row in rows:
        if str(row.get("resourceType")).lower() != "script":
            continue
        size = row.get("transferSize")
        if not _number(size):
            notes["total_js"] = "a Script row in %s has no transferSize" % LH_NETWORK
            return values, notes
        total += size
    values["total_js"] = total / 1024.0
    return values, notes


def lighthouse_source(lhr):
    bits = ["Lighthouse %s" % lhr.get("lighthouseVersion", "(version unknown)")]
    settings = lhr.get("configSettings") if isinstance(lhr.get("configSettings"), dict) else {}
    if settings.get("formFactor"):
        bits.append("%s profile" % settings["formFactor"])
    url = lhr.get("finalDisplayedUrl") or lhr.get("finalUrl") or lhr.get("requestedUrl")
    if url:
        bits.append("on %s" % url)
    if lhr.get("fetchTime"):
        bits.append("at %s" % lhr["fetchTime"])
    return _ascii(" ".join(bits))


def stash_evidence(d, path):
    """Keep the result next to the ledger so the scores point at a file under proof/."""
    rel = _under_proof(d, path)
    if rel is not None:
        return rel
    name = os.path.basename(path)
    dest = os.path.join(ledger.evidence_dir(d, SKILL), name)
    shutil.copyfile(path, dest)
    return "evidence/%s/%s" % (SKILL, name)


# --------------------------------------------------------------- commands

def cmd_template(a):
    if os.path.exists(a.out) and not a.force:
        raise SystemExit("%s exists; edit it, or pass --force to replace it with the example budget" % a.out)
    out_dir = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump({"about": ABOUT, "metrics": DEFAULT_METRICS}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    say("%s wrote %s with %d metrics. Edit the numbers: a budget is a decision, not a law."
        % (ledger.OK, a.out, len(DEFAULT_METRICS)))
    return 0


def cmd_record(a):
    try:
        budget = load_budget(a.budget)
        measured = load_measured(a.measured)
        na = not_applicable_marks(a.dir)
        scores, missing = match(budget, measured, allow_missing=True)
        unmarked = [i for i in missing if i not in na]
        if unmarked:
            raise BudgetError("no measured value for %s; a metric nobody measured is never a pass. "
                              "Measure it, drop it from the budget, or skip it with a reason."
                              % ", ".join(repr(i) for i in unmarked))
        if not scores:
            raise BudgetError("every metric in the budget is marked not applicable; nothing to score")
        evidence = [_check_evidence(a.dir, a.evidence)] if a.evidence else None
        gaps = {i: ("not_applicable", na[i]) for i in missing}
        record(a.dir, budget, scores, source=a.source, evidence=evidence, gaps=gaps)
    except (BudgetError, ledger.LedgerError) as e:
        raise SystemExit(str(e))
    say("%s: %d metric(s) from %s against %s" % (SKILL, len(scores), a.measured, a.budget))
    line = _target_line(a.dir)
    if line:
        say(line)
    if not a.source:
        say("(no --source given; next time say what measured these numbers and which run this was)")
    summary(a.dir, budget, scores, gaps)
    return 0


def cmd_from_lighthouse(a):
    try:
        lhr = read_lhr(a.lhr)
    except NotAnLhr as e:
        print(_ascii("%s %s" % (ledger.BAD, e)), file=sys.stderr)
        return 2
    try:
        budget = load_budget(a.budget)
        values, notes = lighthouse_values(lhr)
        measured = {m["id"]: values[m["id"]] for m in budget if m["id"] in values}
        if a.measured:
            extra = load_measured(a.measured)
            both = sorted(k for k in extra if k in measured)
            if both:
                raise BudgetError("%s in %s is also in the Lighthouse result; keep one source per metric"
                                  % (", ".join(repr(k) for k in both), a.measured))
            measured.update(extra)
        na = not_applicable_marks(a.dir)
        scores, missing = match(budget, measured, allow_missing=True)
        if not scores:
            raise BudgetError("none of the budget's metrics could be read from %s (%s); nothing recorded"
                              % (a.lhr, "; ".join("%s: %s" % (i, notes.get(i, NO_MAPPING)) for i in missing)))
        gaps = {}
        for i in missing:
            if i in na:
                gaps[i] = ("not_applicable", na[i])
            else:
                gaps[i] = ("not_run", notes.get(i, NO_MAPPING))
        evidence = [stash_evidence(a.dir, a.lhr)]
        source = a.source or lighthouse_source(lhr)
        record(a.dir, budget, scores, source=source, evidence=evidence, gaps=gaps)
    except (BudgetError, ledger.LedgerError, OSError) as e:
        raise SystemExit(str(e))
    say("%s: %d metric(s) from %s against %s" % (SKILL, len(scores), a.lhr, a.budget))
    say("source: %s" % source)
    say("evidence: %s" % evidence[0])
    line = _target_line(a.dir)
    if line:
        say(line)
    summary(a.dir, budget, scores, gaps)
    return 0


def cmd_skip(a):
    if a.budget:
        try:
            budget = load_budget(a.budget)
        except BudgetError as e:
            raise SystemExit(str(e))
        ledger.init_coverage(a.dir, SKILL, [(m["id"], m["label"]) for m in budget])
    elif ledger.load_coverage(a.dir, SKILL) is None:
        raise SystemExit("nothing recorded yet for %s; pass --budget budget.json so the checks exist to mark" % SKILL)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.metric_id, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    removed = _drop_score(a.dir, a.metric_id)
    say("%s %s marked not applicable: %s%s" % (MARK["not_applicable"], a.metric_id, a.note,
                                              " (its recorded value was removed from the scores)" if removed else ""))
    return 0


def _drop_score(d, metric_id):
    """Take one metric out of the scores file; a skipped metric must not keep counting."""
    current = next((s for s in ledger.load_all_scores(d) if s["skill"] == SKILL), None)
    if current is None or not any(m["id"] == metric_id for m in current["metrics"]):
        return False
    kept = [m for m in current["metrics"] if m["id"] != metric_id]
    if kept:
        ledger.write_scores(d, SKILL, kept)
    else:
        os.remove(ledger.scores_path(d, SKILL))
    return True


def cmd_status(a):
    current = next((s for s in ledger.load_all_scores(a.dir) if s["skill"] == SKILL), None)
    cov = ledger.load_coverage(a.dir, SKILL)
    if current is None and cov is None:
        say("no scores recorded yet. Run: budget.py record --budget budget.json --measured measured.json "
            "(or: budget.py from-lighthouse --json lhr.json --budget budget.json)")
        return 0
    scores = current["metrics"] if current else []
    by_id = {m["id"]: m for m in scores}
    # Rebuild the rows from the coverage ledger (every budget id) plus the scores.
    budget, gaps = [], {}
    for c in (cov["checks"] if cov else []):
        m = by_id.get(c["id"])
        if m:
            budget.append({"id": m["id"], "label": c["title"], "unit": m["unit"], "budget": m["budget"], "op": m["op"]})
        else:
            budget.append({"id": c["id"], "label": c["title"], "unit": "", "budget": 0, "op": ""})
            status = c["status"] if c["status"] != "covered" else "not_run"
            gaps[c["id"]] = (status, c.get("note") or "no value recorded")
    for m in scores:  # a score with no coverage row still shows
        if not any(b["id"] == m["id"] for b in budget):
            budget.append({"id": m["id"], "label": m["id"], "unit": m["unit"], "budget": m["budget"], "op": m["op"]})
    say("%s: %d scored metric(s)%s" % (SKILL, len(scores), (", updated %s" % current["updated"]) if current and current.get("updated") else ""))
    sources = sorted({m["source"] for m in scores if m.get("source")})
    for s in sources:
        say("source: %s" % s)
    passed = print_table(budget, scores, gaps)
    over = len(scores) - passed
    say("%d of %d within budget%s%s" % (passed, len(scores), (", %d over" % over) if over else "",
                                        (", %d without a value" % len(gaps)) if gaps else ""))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof", help="the proof/ folder (default: proof)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", help="write an example budget file to edit")
    t.add_argument("--out", default="budget.json")
    t.add_argument("--force", action="store_true", help="replace the file if it exists")
    t.set_defaults(func=cmd_template)

    r = sub.add_parser("record", help="score hand-measured numbers against the budget")
    r.add_argument("--budget", required=True, help="the budget file (write one with: template)")
    r.add_argument("--measured", required=True, help="JSON object of metric id to measured value")
    r.add_argument("--source", help="what measured it, e.g. 'devtools, 3rd reload, cache off'")
    r.add_argument("--evidence", help="path under proof/ to a saved capture that backs the numbers")
    r.set_defaults(func=cmd_record)

    lh = sub.add_parser("from-lighthouse", help="score a Lighthouse JSON result against the budget")
    lh.add_argument("--json", dest="lhr", required=True,
                    help="the result file: lighthouse <url> --output json --output-path lhr.json")
    lh.add_argument("--budget", required=True, help="the budget file (write one with: template)")
    lh.add_argument("--measured", help="hand-measured values for metrics the result does not carry")
    lh.add_argument("--source", help="override the default 'Lighthouse <version> ... on <url> at <time>'")
    lh.set_defaults(func=cmd_from_lighthouse)

    s = sub.add_parser("skip", help="mark a budget metric not applicable, with a reason")
    s.add_argument("metric_id")
    s.add_argument("--note", required=True, help="why it cannot or need not be measured here")
    s.add_argument("--budget", help="the budget file; needed when nothing has been recorded yet")
    s.set_defaults(func=cmd_skip)

    sub.add_parser("status", help="the recorded scores and whether each passes").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
