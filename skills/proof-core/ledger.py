#!/usr/bin/env python3
"""The evidence ledger every Proof skill writes to and the report reads from.

Standard library only, no network. State lives under ``proof/`` in the user's
project:

    proof/target.json              what is under test (proof-plan writes it)
    proof/findings/<skill>/<id>.json   one finding per file
    proof/coverage/<skill>.json    which checks the skill looked at
    proof/scores/<skill>.json      measured numbers against budgets
    proof/evidence/<skill>/...     screenshots, logs, captures the findings point to

Three rules the ledger enforces:

* A finding is born ``unverified``. Only ``verify`` (with an evidence file that
  exists, or a repro that was run) moves it to ``verified``. Only verified
  findings count toward the verdict.
* Every file is validated against evidence-schema.json on write; a tool cannot
  write a malformed record.
* Nothing is ever silently created with a default that changes the verdict.

Import from a tool in another skill folder with::

    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "proof-core"))
    import ledger
"""

import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "evidence-schema.json")

SEVERITIES = ["critical", "high", "medium", "low", "info"]
STATUSES = ["unverified", "verified", "false_positive"]
COVERAGE_STATUSES = ["not_run", "covered", "not_applicable"]
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SKILL = re.compile(r"^proof-[a-z0-9-]+$")


class LedgerError(ValueError):
    """A record is malformed or an operation is not allowed."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def console():
    """Stop a tool crashing on a console that cannot show every character.

    Windows consoles often default to cp1252; printing a check mark there raises
    UnicodeEncodeError. Files are always written as UTF-8 regardless. Call this
    at the top of every tool's main().
    """
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, AttributeError):
                pass


OK, BAD = "ok", "!!"  # console marks; the markdown report uses real check marks


def slug(text):
    """Turn a title into an id: lowercase, hyphens, nothing else."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        raise LedgerError("cannot make an id from %r" % text)
    return s[:60]


# ----------------------------------------------------------------- schema

_schema_cache = None


def schema():
    global _schema_cache
    if _schema_cache is None:
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            _schema_cache = json.load(fh)
    return _schema_cache


def check(obj, kind):
    """Return a list of problems with ``obj`` against the schema for ``kind``.

    ``kind`` is one of target, finding, coverage, scores. The checker covers the
    subset of JSON Schema the evidence schema uses: type, required, enum,
    pattern, minLength, items, properties. Unknown properties are allowed.
    """
    definition = schema()["$defs"].get(kind)
    if definition is None:
        raise LedgerError("no schema for %r" % kind)
    problems = []
    _check(obj, definition, kind, problems)
    return problems


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "number": (int, float), "integer": int}


def _check(value, spec, path, problems):
    typ = spec.get("type")
    if typ:
        expected = _TYPES[typ]
        ok = isinstance(value, expected) and not (typ in ("number", "integer") and isinstance(value, bool))
        if not ok:
            problems.append("%s: expected %s" % (path, typ))
            return
    if "enum" in spec and value not in spec["enum"]:
        problems.append("%s: %r is not one of %s" % (path, value, ", ".join(map(str, spec["enum"]))))
    if "pattern" in spec and isinstance(value, str) and not re.search(spec["pattern"], value):
        problems.append("%s: %r does not match %s" % (path, value, spec["pattern"]))
    if "minLength" in spec and isinstance(value, str) and len(value) < spec["minLength"]:
        problems.append("%s: must not be empty" % path)
    if isinstance(value, dict):
        for key in spec.get("required", []):
            if key not in value:
                problems.append("%s: '%s' is required" % (path, key))
        for key, sub in spec.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, "%s.%s" % (path, key), problems)
    if isinstance(value, list) and "items" in spec:
        for i, item in enumerate(value):
            _check(item, spec["items"], "%s[%d]" % (path, i), problems)


def _validated(obj, kind):
    problems = check(obj, kind)
    if problems:
        raise LedgerError("invalid %s: %s" % (kind, "; ".join(problems)))
    return obj


# ------------------------------------------------------------------ paths

def root(d="proof"):
    return os.path.abspath(d)


def guard_out(path, d="proof", force=False, ours=None):
    """Stop a tool overwriting a file the user owns.

    Writing is fine when the file does not exist, sits under the proof folder, carries
    the marker ``ours`` (a file an earlier Proof run wrote), or ``force`` is set.
    Otherwise the tool exits with a message instead of replacing the file.
    """
    if force or not os.path.isfile(path):
        return
    base, full = root(d), os.path.abspath(path)
    try:
        if os.path.commonpath([base, full]) == base:
            return
    except ValueError:  # another drive
        pass
    if ours:
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                if ours in fh.read(65536):
                    return
        except OSError:
            pass
    raise SystemExit("%s already exists and was not written by Proof; pass --force to overwrite it" % path)


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def _skill_name(skill):
    if not _SKILL.match(skill or ""):
        raise LedgerError("skill must look like proof-something, got %r" % skill)
    return skill


# ----------------------------------------------------------------- target

def target_path(d="proof"):
    return os.path.join(root(d), "target.json")


def write_target(d, target):
    target = dict(target)
    target.setdefault("created", now())
    target.setdefault("urls", [])
    return _write_json(target_path(d), _validated(target, "target"))


def load_target(d="proof"):
    """Return the target, or None when proof-plan has not run."""
    path = target_path(d)
    if not os.path.exists(path):
        return None
    return _validated(_read_json(path), "target")


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _url_host(url):
    from urllib.parse import urlparse
    return (urlparse(url if "//" in url else "//" + url).hostname or "").lower()


def authorised_for(d, url):
    """Return None when traffic may be sent to url, else the reason it may not.

    A local address is always allowed. Anything else must be one of the hosts in
    proof/target.json, and the target must be marked as owned or authorised.
    """
    host = _url_host(url)
    if host in LOCAL_HOSTS or host.endswith(".localhost") or host.endswith(".test"):
        return None
    t = load_target(d)
    if not t:
        return "%s is not a local address and there is no proof/target.json; run /proof-plan first" % host
    if not t.get("authorised"):
        return "proof/target.json does not say you own or are authorised to test this target"
    hosts = set(_url_host(u) for u in t.get("urls") or [])
    if host not in hosts:
        return "%s is not one of the target's hosts (%s)" % (host, ", ".join(sorted(hosts)) or "none listed")
    return None


# --------------------------------------------------------------- findings

def finding_path(d, skill, fid):
    return os.path.join(root(d), "findings", _skill_name(skill), fid + ".json")


def new_finding(skill, title, severity, **fields):
    """Build an ``unverified`` finding. Does not write it; see ``write_finding``."""
    if severity not in SEVERITIES:
        raise LedgerError("severity must be one of %s" % ", ".join(SEVERITIES))
    f = {"id": fields.pop("id", None) or slug(title), "skill": _skill_name(skill),
         "severity": severity, "title": title, "status": "unverified", "created": now()}
    if not _ID.match(f["id"]):
        raise LedgerError("id %r must be lowercase letters, digits and hyphens" % f["id"])
    if "status" in fields and fields["status"] != "unverified":
        raise LedgerError("a new finding is always unverified; use verify() or reject()")
    fields.pop("status", None)
    f.update({k: v for k, v in fields.items() if v is not None})
    f.setdefault("evidence", [])
    return _validated(f, "finding")


def write_finding(d, finding):
    _validated(finding, "finding")
    return _write_json(finding_path(d, finding["skill"], finding["id"]), finding)


def load_finding(d, skill, fid):
    return _validated(_read_json(finding_path(d, skill, fid)), "finding")


def load_findings(d="proof", skill=None):
    """All findings, newest last, optionally for one skill."""
    base = os.path.join(root(d), "findings")
    out = []
    if not os.path.isdir(base):
        return out
    skills = [skill] if skill else sorted(os.listdir(base))
    for s in skills:
        folder = os.path.join(base, s)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                out.append(_validated(_read_json(os.path.join(folder, name)), "finding"))
    out.sort(key=lambda f: (f.get("created", ""), f["id"]))
    return out


def _evidence_ok(d, paths):
    """Every evidence path must exist under proof/ and be non-empty."""
    if not paths:
        return "no evidence file given"
    base = root(d)
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(base, p)
        if not os.path.isfile(full):
            return "evidence file does not exist: %s" % p
        if os.path.getsize(full) == 0:
            return "evidence file is empty: %s" % p
        if os.path.commonpath([os.path.abspath(full), base]) != base:
            return "evidence must live under %s: %s" % (base, p)
    return None


def verify(d, skill, fid, by, note=None, evidence=None):
    """Promote a finding to ``verified``.

    ``by`` is how: "evidence-file" (the listed evidence exists and is non-empty),
    "repro-run" (the caller ran the repro and it reproduced; the output must be
    saved as evidence), or "manual" (a human confirmed it; ``note`` is required).
    """
    f = load_finding(d, skill, fid)
    if f["status"] == "false_positive":
        raise LedgerError("%s was rejected as a false positive; reopen it first" % fid)
    if evidence:
        f["evidence"] = sorted(set(f.get("evidence", [])) | set(evidence))
    if by in ("evidence-file", "repro-run"):
        problem = _evidence_ok(d, f.get("evidence"))
        if problem:
            raise LedgerError("cannot verify %s: %s" % (fid, problem))
        if by == "repro-run" and not f.get("repro"):
            raise LedgerError("cannot verify %s by repro-run: the finding has no repro" % fid)
    elif by == "manual":
        if not note:
            raise LedgerError("manual verification needs a note saying who checked and how")
    else:
        raise LedgerError("by must be evidence-file, repro-run or manual")
    f.update({"status": "verified", "verified_by": by, "updated": now()})
    if note:
        f["verified_note"] = note
    return write_finding(d, f)


def reject(d, skill, fid, reason):
    """Mark a finding a false positive. It stays in the ledger, listed as ruled out."""
    if not reason:
        raise LedgerError("a rejection needs a reason")
    f = load_finding(d, skill, fid)
    f.update({"status": "false_positive", "verified_note": reason, "updated": now()})
    f.pop("verified_by", None)
    return write_finding(d, f)


def reopen(d, skill, fid):
    f = load_finding(d, skill, fid)
    f.update({"status": "unverified", "updated": now()})
    f.pop("verified_by", None)
    f.pop("verified_note", None)
    return write_finding(d, f)


# --------------------------------------------------------------- coverage

def coverage_path(d, skill):
    return os.path.join(root(d), "coverage", _skill_name(skill) + ".json")


def init_coverage(d, skill, checks, keep_existing=True):
    """Create the coverage ledger for a skill with every check ``not_run``.

    ``checks`` is a list of (id, title). With ``keep_existing`` the statuses of
    checks already recorded are preserved, so re-running a skill never loses
    what it has already looked at.
    """
    previous = {}
    if keep_existing and os.path.exists(coverage_path(d, skill)):
        previous = {c["id"]: c for c in load_coverage(d, skill)["checks"]}
    out = {"skill": _skill_name(skill), "checks": [], "updated": now()}
    for cid, title in checks:
        entry = previous.get(cid, {"id": cid, "title": title, "status": "not_run"})
        entry["title"] = title
        out["checks"].append(entry)
    return _write_json(coverage_path(d, skill), _validated(out, "coverage"))


def load_coverage(d, skill):
    path = coverage_path(d, skill)
    if not os.path.exists(path):
        return None
    return _validated(_read_json(path), "coverage")


def load_all_coverage(d="proof"):
    base = os.path.join(root(d), "coverage")
    if not os.path.isdir(base):
        return []
    return [_validated(_read_json(os.path.join(base, n)), "coverage")
            for n in sorted(os.listdir(base)) if n.endswith(".json")]


def mark_coverage(d, skill, check_id, status, note=None, evidence=None):
    if status not in COVERAGE_STATUSES:
        raise LedgerError("status must be one of %s" % ", ".join(COVERAGE_STATUSES))
    cov = load_coverage(d, skill)
    if cov is None:
        raise LedgerError("no coverage ledger for %s; call init_coverage first" % skill)
    for c in cov["checks"]:
        if c["id"] == check_id:
            if status == "not_applicable" and not note:
                raise LedgerError("marking %s not applicable needs a note saying why" % check_id)
            if status == "covered" and evidence:
                problem = _evidence_ok(d, evidence)
                if problem:
                    raise LedgerError("cannot mark %s covered: %s" % (check_id, problem))
            c["status"] = status
            if note:
                c["note"] = note
            if evidence:
                c["evidence"] = sorted(set(c.get("evidence", [])) | set(evidence))
            cov["updated"] = now()
            return _write_json(coverage_path(d, skill), _validated(cov, "coverage"))
    raise LedgerError("no check %r in %s coverage" % (check_id, skill))


# ----------------------------------------------------------------- scores

def scores_path(d, skill):
    return os.path.join(root(d), "scores", _skill_name(skill) + ".json")


def write_scores(d, skill, metrics):
    """Record measured numbers against budgets. Each metric: id, value, unit, budget, op."""
    out = {"skill": _skill_name(skill), "metrics": list(metrics), "updated": now()}
    return _write_json(scores_path(d, skill), _validated(out, "scores"))


def load_all_scores(d="proof"):
    base = os.path.join(root(d), "scores")
    if not os.path.isdir(base):
        return []
    return [_validated(_read_json(os.path.join(base, n)), "scores")
            for n in sorted(os.listdir(base)) if n.endswith(".json")]


def metric_passes(metric):
    v, b, op = metric["value"], metric["budget"], metric["op"]
    return {"<=": v <= b, ">=": v >= b, "<": v < b, ">": v > b, "==": v == b}[op]


# ---------------------------------------------------------------- summary

def skills_reported(d="proof"):
    """Every skill that has written at least one finding, coverage or score file."""
    seen = set()
    for f in load_findings(d):
        seen.add(f["skill"])
    for c in load_all_coverage(d):
        seen.add(c["skill"])
    for s in load_all_scores(d):
        seen.add(s["skill"])
    return sorted(seen)


def evidence_dir(d, skill):
    path = os.path.join(root(d), "evidence", _skill_name(skill))
    os.makedirs(path, exist_ok=True)
    return path
