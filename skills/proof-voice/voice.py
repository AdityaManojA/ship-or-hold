#!/usr/bin/env python3
"""Test voice and real-time audio features with evidence. Standard library only, no network.

A voice assistant, a WebRTC call or speech in and out of an app is judged on things
a unit test never sees: how long the reply takes after the user stops talking,
whether the user can interrupt it, what happens when the screen locks or the
network drops, and whether the microphone is really released at the end. This
tool keeps a checklist of those behaviours, records each result you run on your
own app with its evidence, and measures what can be measured from files you
captured: reply latency per turn, word error rate of a transcript, and packet loss,
jitter and round-trip time from a WebRTC stats dump.

    python3 voice.py plan
    python3 voice.py latency turns.csv
    python3 voice.py wer --ref ref.txt --hyp hyp.txt
    python3 voice.py webrtc webrtc_internals_dump.json
    python3 voice.py record --latency turns.csv --wer-ref ref.txt --wer-hyp hyp.txt --webrtc dump.json
    python3 voice.py result barge-in pass --evidence evidence/proof-voice/barge-in.mp4
    python3 voice.py result teardown fail --evidence <path> --title "..." --verify
    python3 voice.py na audio-route --note "web only, no audio routing"
    python3 voice.py status

A measurement nobody captured is never a pass: its check stays not run, and the
optional transcript check is marked not applicable until a transcript is given.
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

SKILL = "proof-voice"

# id, title, default severity of a failure, what to do, what a pass looks like
CHECKS = [
    ("mic-permission", "Microphone permission asked in context", "medium",
     "Fresh install: start the voice feature for the first time.",
     "The app asks for the microphone only when the user starts talking, with a clear reason; never at launch."),
    ("permission-denied", "Recovers when the microphone is denied", "medium",
     "Deny the permission (and later turn it off in system settings), then start the voice feature.",
     "The app explains the microphone is off and how to turn it on; no crash, no silent dead button."),
    ("response-latency", "Reply latency after the user stops talking", "medium",
     "Time end of user speech to the first reply audio over 10 or more turns (see the latency command).",
     "p95 within the budget (default 1500 ms) and no turn that hangs."),
    ("barge-in", "User can interrupt the reply", "medium",
     "Start talking while the reply is playing.",
     "The reply stops promptly (well under a second) and the app listens to the new speech."),
    ("background-and-lock", "Background and screen lock mid-session", "medium",
     "Send the app to the background and lock the screen during a session, then return.",
     "Behaviour is deliberate (session continues or ends cleanly); the mic indicator matches what the app is doing."),
    ("audio-route", "Audio route changes mid-session", "low",
     "Switch speaker, earpiece, Bluetooth and wired headphones during a session.",
     "Audio follows the new route without dropping the session or blasting the speaker."),
    ("poor-network", "Degrades gracefully on a poor network", "medium",
     "Throttle the network or add packet loss (emulator network settings, a network link conditioner) mid-session.",
     "The app shows the connection is poor, recovers or reconnects, and never leaves a half-dead session."),
    ("echo", "No echo or feedback loop on speaker", "medium",
     "Use the loudspeaker in a quiet room for several turns.",
     "The assistant never hears and answers itself; no audible echo for the other party."),
    ("teardown", "Microphone and camera released at the end", "high",
     "End the session (button, navigation away, app close) and watch the system mic and camera indicators.",
     "The indicators turn off within a second or two; nothing keeps recording."),
    ("transcript-accuracy", "Transcript accuracy (optional)", "low",
     "Read a known script and save the app's transcript (see the wer command).",
     "Word error rate within the budget (default 0.15)."),
]
CHECK_IDS = [c[0] for c in CHECKS]


def _rel(d, path):
    return os.path.relpath(path, ledger.root(d)).replace(os.sep, "/")


def _copy(d, path, prefix):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), "%s-%s" % (prefix, os.path.basename(path)))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return _rel(d, dest)


def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _rows(path):
    """Rows of dicts from JSON (a list, or an object holding one list) or CSV."""
    text = _read(path)
    try:
        data = json.loads(text)
    except ValueError:
        reader = csv.DictReader(io.StringIO(text))
        return [dict((k.strip().lower(), v) for k, v in r.items() if k) for r in reader]
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if lists else [data]
    if not isinstance(data, list):
        raise SystemExit("%s holds no list of rows" % path)
    return [dict((str(k).lower(), v) for k, v in r.items()) for r in data if isinstance(r, dict)]


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def percentile(values, p):
    """Nearest-rank percentile of a non-empty list."""
    s = sorted(values)
    k = max(1, int(-(-p * len(s) // 100)))
    return s[min(k, len(s)) - 1]


# ---------------------------------------------------------------- latency

def parse_latency(path):
    out = []
    for i, r in enumerate(_rows(path), 1):
        if not _blank(r.get("latency_ms")):
            out.append(num.parse_number(r["latency_ms"], field="latency_ms (row %d)" % i))
        elif not _blank(r.get("speech_end_ms")) and not _blank(r.get("first_audio_ms")):
            end = num.parse_number(r["speech_end_ms"], field="speech_end_ms (row %d)" % i)
            first = num.parse_number(r["first_audio_ms"], field="first_audio_ms (row %d)" % i)
            if first < end:
                raise SystemExit("row %d: first_audio_ms is before speech_end_ms" % i)
            out.append(first - end)
    if not out:
        raise SystemExit("no turns in %s: need latency_ms, or speech_end_ms and first_audio_ms" % path)
    return out


def latency_summary(values):
    return {"turns": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95), "max": max(values)}


# ---------------------------------------------------------------- word error rate

def _words(text):
    text = text.lower().replace("\u2019", "'")
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text)


def edit_distance(ref, hyp):
    prev = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, hw in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1]


def wer_pairs(pairs):
    """Corpus word error rate over (reference, hypothesis) pairs: total edits / total reference words."""
    edits = words = 0
    for ref, hyp in pairs:
        r, h = _words(ref), _words(hyp)
        edits += edit_distance(r, h)
        words += len(r)
    if not words:
        raise SystemExit("the reference transcript has no words")
    return {"pairs": len(pairs), "words": words, "edits": edits, "wer": edits / float(words)}


def read_pairs(a):
    if a.pairs:
        rows = _rows(a.pairs)
        pairs = []
        for i, r in enumerate(rows, 1):
            ref = r.get("ref", r.get("reference"))
            hyp = r.get("hyp", r.get("hypothesis", r.get("transcript")))
            if _blank(ref) or hyp is None:
                raise SystemExit("row %d of %s needs ref and hyp" % (i, a.pairs))
            pairs.append((str(ref), str(hyp)))
        return pairs, [a.pairs]
    if a.ref and a.hyp:
        return [(_read(a.ref), _read(a.hyp))], [a.ref, a.hyp]
    raise SystemExit("give --ref and --hyp, or --pairs")


# ---------------------------------------------------------------- WebRTC stats

def _series_last(values):
    """chrome://webrtc-internals keeps each stat as a JSON-encoded list of samples; take the last."""
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            return values
    if isinstance(values, list):
        return values[-1] if values else None
    return values


def _series_mean(values):
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            return None
    if isinstance(values, list):
        nums = [float(v) for v in values if isinstance(v, (int, float))]
        return sum(nums) / len(nums) if nums else None
    return float(values) if isinstance(values, (int, float)) else None


def _reports(data):
    """Normalise a webrtc-internals dump or a getStats() export into a list of report dicts."""
    out = []
    if isinstance(data, dict) and "PeerConnections" in data:
        for pc_id, pc in (data.get("PeerConnections") or {}).items():
            grouped = {}
            for key, entry in (pc.get("stats") or {}).items():
                if "-" not in key or not isinstance(entry, dict):
                    continue
                sid, attr = key.rsplit("-", 1)
                rep = grouped.setdefault(sid, {"id": "%s/%s" % (pc_id, sid), "type": entry.get("statsType")})
                rep[attr] = entry.get("values")
                rep["_mean_" + attr] = entry.get("values")
            out.extend(grouped.values())
        return out
    if isinstance(data, dict):
        data = list(data.values()) if all(isinstance(v, dict) for v in data.values()) else [data]
    if isinstance(data, list):
        out = [r for r in data if isinstance(r, dict)]
    return out


def parse_webrtc(path):
    try:
        data = json.loads(_read(path))
    except ValueError:
        raise SystemExit("%s is not JSON; save chrome://webrtc-internals with Create dump, or getStats() as JSON" % path)
    streams, rtts = [], []
    for r in _reports(data):
        kind_type = r.get("type")
        if kind_type == "inbound-rtp":
            lost = _series_last(r.get("packetsLost"))
            got = _series_last(r.get("packetsReceived"))
            jitter = _series_last(r.get("jitter"))
            kind = _series_last(r.get("kind")) or r.get("mediaType") or "media"
            s = {"id": r.get("id", "?"), "kind": str(kind).strip('"')}
            if isinstance(lost, (int, float)) and isinstance(got, (int, float)) and lost + got > 0:
                s["loss_pct"] = 100.0 * max(lost, 0) / (max(lost, 0) + got)
            if isinstance(jitter, (int, float)):
                s["jitter_ms"] = jitter * 1000.0
            streams.append(s)
        elif kind_type in ("candidate-pair", "remote-inbound-rtp"):
            field = "currentRoundTripTime" if kind_type == "candidate-pair" else "roundTripTime"
            if kind_type == "candidate-pair" and _series_last(r.get("state")) not in (None, "succeeded", '"succeeded"'):
                continue
            rtt = _series_mean(r.get("_mean_" + field, r.get(field)))
            if rtt is not None and rtt > 0:
                rtts.append(rtt * 1000.0)
    if not streams and not rtts:
        raise SystemExit("no inbound-rtp or candidate-pair stats in %s" % path)
    return {"streams": streams, "rtt_ms": max(rtts) if rtts else None}


def webrtc_worst(stats):
    loss = [s["loss_pct"] for s in stats["streams"] if "loss_pct" in s]
    jit = [s["jitter_ms"] for s in stats["streams"] if "jitter_ms" in s]
    return {"loss_pct": max(loss) if loss else None, "jitter_ms": max(jit) if jit else None, "rtt_ms": stats["rtt_ms"]}


# ---------------------------------------------------------------- printing

def _n(v):
    return ("%.3f" % v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def cmd_plan(a):
    ledger.init_coverage(a.dir, SKILL, [(c[0], c[1]) for c in CHECKS])
    print("%s coverage ledger created with %d checks." % (ledger.OK, len(CHECKS)))
    for cid, title, _, how, ok in CHECKS:
        print("\n[%s] %s" % (cid, title))
        print("   test: %s" % how)
        print("   pass: %s" % ok)
    print("\nRun these on your own app and device. Save a screen recording, screenshot or log for each,")
    print("then: voice.py result <check> pass|fail --evidence <path>  (paths under proof/)")
    return 0


def cmd_latency(a):
    s = latency_summary(parse_latency(a.path))
    mark = ledger.OK if s["p95"] <= a.p95_ms else ledger.BAD
    print("%s %d turn(s): p50 %s ms, p95 %s ms (budget %s), max %s ms"
          % (mark, s["turns"], _n(s["p50"]), _n(s["p95"]), _n(a.p95_ms), _n(s["max"])))
    return 0


def cmd_wer(a):
    pairs, _ = read_pairs(a)
    r = wer_pairs(pairs)
    mark = ledger.OK if r["wer"] <= a.max_wer else ledger.BAD
    print("%s word error rate %.3f (%d edits over %d reference words, %d pair(s)); budget %s"
          % (mark, r["wer"], r["edits"], r["words"], r["pairs"], _n(a.max_wer)))
    return 0


def cmd_webrtc(a):
    stats = parse_webrtc(a.path)
    for s in stats["streams"]:
        print("  %-6s %-40s loss %s%%  jitter %s ms" % (s["kind"], str(s["id"])[:40],
                                                     _n(s.get("loss_pct", "-")) if "loss_pct" in s else "-",
                                                     _n(s["jitter_ms"]) if "jitter_ms" in s else "-"))
    w = webrtc_worst(stats)
    print("worst: loss %s%% (budget %s), jitter %s ms (budget %s), RTT %s ms (budget %s)"
          % (_n(w["loss_pct"]) if w["loss_pct"] is not None else "-", _n(a.max_loss),
             _n(w["jitter_ms"]) if w["jitter_ms"] is not None else "-", _n(a.max_jitter),
             _n(w["rtt_ms"]) if w["rtt_ms"] is not None else "-", _n(a.max_rtt)))
    return 0


# ---------------------------------------------------------------- recording

def _sync_finding(d, fid, failing, build):
    """Write and verify a finding while the check fails; drop it once it passes. False positives stay."""
    path = ledger.finding_path(d, SKILL, fid)
    existing = ledger.load_finding(d, SKILL, fid) if os.path.exists(path) else None
    if existing and existing["status"] == "false_positive":
        return
    if not failing:
        if existing:
            os.remove(path)
        return
    f = build()
    ledger.write_finding(d, f)
    ledger.verify(d, SKILL, fid, by="evidence-file")


def _metric(mid, value, unit, budget, ev):
    return {"id": mid, "value": round(float(value), 4), "unit": unit, "budget": float(budget), "op": "<=",
            "source": "voice.py", "evidence": [ev]}


def cmd_record(a):
    if bool(a.wer_ref) != bool(a.wer_hyp):
        raise SystemExit("--wer-ref and --wer-hyp go together")
    if not (a.latency or a.webrtc or a.pairs or a.wer_ref):
        raise SystemExit("nothing to record: give --latency, --webrtc, or --wer-ref with --wer-hyp (or --pairs)")
    ledger.init_coverage(a.dir, SKILL, [(c[0], c[1]) for c in CHECKS])
    existing = [s for s in ledger.load_all_scores(a.dir) if s["skill"] == SKILL]
    metrics = dict((m["id"], m) for m in (existing[0]["metrics"] if existing else []))
    lines = []

    if a.latency:
        s = latency_summary(parse_latency(a.latency))
        ev = _copy(a.dir, a.latency, "latency")
        metrics["latency_p95_ms"] = _metric("latency_p95_ms", s["p95"], "ms", a.p95_ms, ev)
        metrics["latency_p50_ms"] = _metric("latency_p50_ms", s["p50"], "ms", a.p50_ms, ev)
        bad = s["p95"] > a.p95_ms or s["p50"] > a.p50_ms
        ledger.mark_coverage(a.dir, SKILL, "response-latency", "covered", evidence=[ev],
                             note="%d turns, p50 %s ms, p95 %s ms, max %s ms" % (s["turns"], _n(s["p50"]),
                                                                                _n(s["p95"]), _n(s["max"])))
        _sync_finding(a.dir, "voice-latency-over-budget", bad, lambda: ledger.new_finding(
            SKILL, "Voice replies are slow: p95 %s ms, p50 %s ms (budget %s / %s ms)"
            % (_n(s["p95"]), _n(s["p50"]), _n(a.p95_ms), _n(a.p50_ms)), "medium",
            id="voice-latency-over-budget", category="voice:response-latency", evidence=[ev],
            fix="Stream the reply as soon as the first audio is ready, shorten the pipeline, and end speech "
                "detection faster."))
        lines.append("latency p95 %s ms" % _n(s["p95"]))

    if a.pairs or a.wer_ref:
        if a.pairs:
            pairs = [(str(r.get("ref", r.get("reference", ""))), str(r.get("hyp", r.get("hypothesis", ""))))
                     for r in _rows(a.pairs)]
            evs = [_copy(a.dir, a.pairs, "wer")]
        else:
            pairs = [(_read(a.wer_ref), _read(a.wer_hyp))]
            evs = [_copy(a.dir, a.wer_ref, "wer-ref"), _copy(a.dir, a.wer_hyp, "wer-hyp")]
        r = wer_pairs(pairs)
        metrics["wer"] = _metric("wer", r["wer"], "fraction", a.max_wer, evs[0])
        ledger.mark_coverage(a.dir, SKILL, "transcript-accuracy", "covered", evidence=evs,
                             note="word error rate %.3f over %d words" % (r["wer"], r["words"]))
        _sync_finding(a.dir, "voice-wer-over-budget", r["wer"] > a.max_wer, lambda: ledger.new_finding(
            SKILL, "Transcripts are inaccurate: word error rate %.3f (budget %s)" % (r["wer"], _n(a.max_wer)),
            "medium", id="voice-wer-over-budget", category="voice:transcript-accuracy", evidence=evs,
            fix="Check the speech model, audio level and noise handling; test with the accents of real users."))
        lines.append("WER %.3f" % r["wer"])
    else:
        cov = ledger.load_coverage(a.dir, SKILL)
        if next(c for c in cov["checks"] if c["id"] == "transcript-accuracy")["status"] == "not_run":
            ledger.mark_coverage(a.dir, SKILL, "transcript-accuracy", "not_applicable",
                                 note="no reference and app transcript were given")

    if a.webrtc:
        w = webrtc_worst(parse_webrtc(a.webrtc))
        ev = _copy(a.dir, a.webrtc, "webrtc")
        over = []
        for mid, key, unit, budget, label in (("webrtc_loss_pct", "loss_pct", "percent", a.max_loss, "packet loss"),
                                              ("webrtc_jitter_ms", "jitter_ms", "ms", a.max_jitter, "jitter"),
                                              ("webrtc_rtt_ms", "rtt_ms", "ms", a.max_rtt, "round-trip time")):
            if w[key] is None:
                continue
            metrics[mid] = _metric(mid, w[key], unit, budget, ev)
            if w[key] > budget:
                over.append("%s %s %s (budget %s)" % (label, _n(w[key]), "%" if unit == "percent" else unit,
                                                        _n(budget)))
        ledger.mark_coverage(a.dir, SKILL, "poor-network", "covered", evidence=[ev],
                             note="stats: " + ", ".join("%s %s" % (k, _n(v)) for k, v in w.items() if v is not None))
        _sync_finding(a.dir, "voice-network-over-budget", bool(over), lambda: ledger.new_finding(
            SKILL, "Call quality over budget: %s" % "; ".join(over), "medium", id="voice-network-over-budget",
            category="voice:poor-network", evidence=[ev],
            fix="Check the TURN setup and codec settings, and add jitter buffering and reconnect handling."))
        lines.append("WebRTC " + (", ".join(over) if over else "within budget"))

    ledger.write_scores(a.dir, SKILL, list(metrics.values()))
    failing = [m for m in metrics.values() if not ledger.metric_passes(m)]
    print("%s recorded: %s." % (ledger.BAD if failing else ledger.OK, "; ".join(lines)))
    return 0


def _valid(cid):
    if cid not in CHECK_IDS:
        raise SystemExit("no check %r. See: voice.py plan" % cid)


def cmd_result(a):
    _valid(a.check)
    if ledger.load_coverage(a.dir, SKILL) is None:
        ledger.init_coverage(a.dir, SKILL, [(c[0], c[1]) for c in CHECKS])
    check = next(c for c in CHECKS if c[0] == a.check)
    if a.outcome == "pass":
        try:
            ledger.mark_coverage(a.dir, SKILL, a.check, "covered", note=a.note, evidence=[a.evidence])
        except ledger.LedgerError as e:
            raise SystemExit("cannot record a pass: %s" % e)
        print("%s %s passes, evidence recorded." % (ledger.OK, a.check))
        return 0
    if not a.title:
        raise SystemExit("a failing check needs --title saying what the app did")
    f = ledger.new_finding(SKILL, a.title, a.severity or check[2], category="voice:%s" % a.check,
                           evidence=[a.evidence], repro=a.repro or check[3], fix=a.fix or check[4])
    ledger.write_finding(a.dir, f)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.check, "covered", evidence=[a.evidence])
    except ledger.LedgerError as e:
        raise SystemExit("finding written but coverage failed: %s" % e)
    if a.verify:
        ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
        print("%s %s: finding %r written and verified." % (ledger.BAD, a.check, f["id"]))
    else:
        print("%s %s: finding %r written (unverified); re-run with --verify once the evidence is saved."
              % (ledger.BAD, a.check, f["id"]))
    return 0


def cmd_na(a):
    _valid(a.check)
    if ledger.load_coverage(a.dir, SKILL) is None:
        ledger.init_coverage(a.dir, SKILL, [(c[0], c[1]) for c in CHECKS])
    try:
        ledger.mark_coverage(a.dir, SKILL, a.check, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked not applicable." % (ledger.OK, a.check))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no voice test ledger yet. Run: voice.py plan")
        return 0
    for c in cov["checks"]:
        print("  %-20s %s" % (c["id"], c["status"]))
    for s in ledger.load_all_scores(a.dir):
        if s["skill"] == SKILL:
            for m in s["metrics"]:
                print("  %s %s = %s %s (budget %s %s)" % (ledger.OK if ledger.metric_passes(m) else ledger.BAD,
                                                         m["id"], _n(m["value"]), m["unit"], m["op"], _n(m["budget"])))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def _budgets(p, latency=False, wer=False, webrtc=False):
    if latency:
        p.add_argument("--p95-ms", type=float, default=1500.0, help="p95 reply latency budget in ms (default 1500)")
        p.add_argument("--p50-ms", type=float, default=800.0, help="median reply latency budget in ms (default 800)")
    if wer:
        p.add_argument("--max-wer", type=float, default=0.15, help="word error rate budget (default 0.15)")
    if webrtc:
        p.add_argument("--max-loss", type=float, default=3.0, help="packet loss budget in percent (default 3)")
        p.add_argument("--max-jitter", type=float, default=30.0, help="jitter budget in ms (default 30)")
        p.add_argument("--max-rtt", type=float, default=300.0, help="round-trip time budget in ms (default 300)")


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="create the checklist and print how to test each item").set_defaults(func=cmd_plan)
    lt = sub.add_parser("latency", help="reply latency from a turns JSON or CSV")
    lt.add_argument("path")
    _budgets(lt, latency=True)
    lt.set_defaults(func=cmd_latency)
    w = sub.add_parser("wer", help="word error rate of a transcript")
    w.add_argument("--ref", help="the reference text that was spoken")
    w.add_argument("--hyp", help="the app's transcript of it")
    w.add_argument("--pairs", help="a JSON or CSV of rows with ref and hyp")
    _budgets(w, wer=True)
    w.set_defaults(func=cmd_wer)
    wr = sub.add_parser("webrtc", help="packet loss, jitter and RTT from a WebRTC stats dump")
    wr.add_argument("path")
    _budgets(wr, webrtc=True)
    wr.set_defaults(func=cmd_webrtc)
    r = sub.add_parser("record", help="write the measurements to the ledger")
    r.add_argument("--latency", help="turns JSON or CSV")
    r.add_argument("--wer-ref", help="reference text for the transcript check")
    r.add_argument("--wer-hyp", help="the app's transcript")
    r.add_argument("--pairs", help="a JSON or CSV of ref and hyp rows, instead of --wer-ref and --wer-hyp")
    r.add_argument("--webrtc", help="a chrome://webrtc-internals dump or getStats() JSON")
    _budgets(r, latency=True, wer=True, webrtc=True)
    r.set_defaults(func=cmd_record)
    res = sub.add_parser("result", help="record a checklist item pass or fail")
    res.add_argument("check")
    res.add_argument("outcome", choices=["pass", "fail"])
    res.add_argument("--evidence", required=True, help="path under proof/, e.g. evidence/proof-voice/x.mp4")
    res.add_argument("--note")
    res.add_argument("--title")
    res.add_argument("--severity", choices=ledger.SEVERITIES)
    res.add_argument("--repro")
    res.add_argument("--fix")
    res.add_argument("--verify", action="store_true")
    res.set_defaults(func=cmd_result)
    n = sub.add_parser("na", help="mark a checklist item not applicable")
    n.add_argument("check")
    n.add_argument("--note", required=True)
    n.set_defaults(func=cmd_na)
    sub.add_parser("status", help="checks, scores and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (num.MissingInput, num.BadNumber) as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    sys.exit(main())
