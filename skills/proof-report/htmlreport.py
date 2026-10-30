#!/usr/bin/env python3
"""Write the Proof report as one self-contained HTML page. Standard library only.

The same verdict and evidence as report.md, as a page you can open in a browser or
send to someone: the verdict at the top, every required skill with its coverage,
verified findings with their screenshots inline, scores against budgets, and the
ruled-out and unverified findings kept separate. Images are embedded, so the file
works on its own with no folder beside it.

    python3 htmlreport.py                    # writes proof/report.html
    python3 htmlreport.py --out report.html --fail-on medium
"""

import argparse
import base64
import html
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

_spec = importlib.util.spec_from_file_location("proof_verdict", os.path.join(HERE, "verdict.py"))
verdict = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verdict)

MAX_IMAGE_BYTES = 1500000
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
COLOR = {"Ship": "#1a7f37", "Not yet": "#cf222e", "Incomplete": "#9a6700"}
SEV_COLOR = {"critical": "#8b0000", "high": "#cf222e", "medium": "#bc4c00", "low": "#6e7781", "info": "#6e7781"}
TICK, CROSS, DASH, DOT = "&#10003;", "&#10007;", "&ndash;", "&#9675;"

CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1f2328;background:#fff;
max-width:960px;margin:0 auto;padding:24px 16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px;border-bottom:1px solid #d0d7de;padding-bottom:4px}
.verdict{display:inline-block;color:#fff;font-weight:600;padding:6px 14px;border-radius:6px;font-size:18px;margin:12px 0}
.muted{color:#656d76}ul.checks{list-style:none;padding:0}ul.checks li{margin:4px 0}
.ok{color:#1a7f37}.bad{color:#cf222e}
.finding{border:1px solid #d0d7de;border-radius:6px;padding:10px 12px;margin:10px 0}
.sev{display:inline-block;color:#fff;border-radius:4px;padding:0 6px;font-size:12px;font-weight:600;text-transform:uppercase}
.finding img{max-width:100%;border:1px solid #d0d7de;border-radius:4px;margin-top:8px}
code{background:#f6f8fa;padding:1px 4px;border-radius:4px;font-size:13px}
table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border:1px solid #d0d7de;padding:4px 8px;text-align:left}
th{background:#f6f8fa}.cov li{margin:2px 0}
@media (prefers-color-scheme: dark){body{background:#0d1117;color:#e6edf3}code,th{background:#161b22}
.finding,td,th,h2{border-color:#30363d}.muted{color:#8d96a0}}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _embed(d, rel):
    """Return a data: URI for an image evidence file under proof/, or None."""
    ext = os.path.splitext(rel)[1].lower()
    if ext not in IMAGE_TYPES:
        return None
    full = rel if os.path.isabs(rel) else os.path.join(ledger.root(d), rel)
    try:
        if not os.path.isfile(full) or os.path.getsize(full) > MAX_IMAGE_BYTES:
            return None
        with open(full, "rb") as fh:
            return "data:%s;base64,%s" % (IMAGE_TYPES[ext], base64.b64encode(fh.read()).decode("ascii"))
    except OSError:
        return None


def _finding(d, f):
    L = ['<div class="finding">',
         '<span class="sev" style="background:%s">%s</span> <strong>%s</strong>' % (
             SEV_COLOR[f["severity"]], esc(f["severity"]), esc(f["title"]))]
    meta = []
    if f.get("location") or f.get("category"):
        meta.append(esc(f.get("location") or f.get("category")))
    if f.get("verified_by"):
        meta.append("verified by %s" % esc(f["verified_by"]))
    if meta:
        L.append('<div class="muted">%s</div>' % " &middot; ".join(meta))
    if f.get("verified_note"):
        L.append("<div>%s</div>" % esc(f["verified_note"]))
    if f.get("repro"):
        L.append("<div>Repro: <code>%s</code></div>" % esc(f["repro"]))
    if f.get("fix"):
        L.append("<div>Fix: %s</div>" % esc(f["fix"]))
    for ev in f.get("evidence", []):
        uri = _embed(d, ev)
        if uri:
            L.append('<img alt="%s" src="%s">' % (esc(ev), uri))
        else:
            L.append('<div class="muted">Evidence: <code>%s</code></div>' % esc(ev))
    L.append("</div>")
    return "\n".join(L)


def render_html(d, r):
    t = r.get("target") or {}
    name = t.get("name") or "Target"
    L = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         "<title>%s - Proof report</title><style>%s</style></head><body>" % (esc(name), CSS),
         "<h1>%s</h1>" % esc(name),
         '<div class="muted">Proof report, generated %s</div>' % esc(r.get("generated", "")),
         '<div class="verdict" style="background:%s">Verdict: %s</div>' % (COLOR[r["verdict"]], esc(r["verdict"]))]
    if os.path.isfile(os.path.join(d, "board", "meeting.html")):
        L.append('<p><a href="board/meeting.html">Watch the board meeting</a></p>')
    if r.get("fail_on"):
        L.append('<div class="muted">Blocks Ship: a verified finding at %s or above, or a score over budget.</div>'
                 % esc(r["fail_on"]))
    L.append('<ul class="checks">')
    for c in r["checks"]:
        L.append('<li><span class="%s">%s</span> %s</li>' % ("ok" if c["ok"] else "bad",
                                                            TICK if c["ok"] else CROSS, esc(c["text"])))
    L.append("</ul>")

    if t:
        L.append("<h2>Target</h2><ul>")
        L.append("<li>Kind: %s</li>" % esc(t.get("kind")))
        if t.get("urls"):
            L.append("<li>URLs: %s</li>" % esc(", ".join(t["urls"])))
        if t.get("done_means"):
            L.append("<li>Done means: %s</li>" % esc(t["done_means"]))
        L.append("<li>Required skills: %s</li></ul>" % esc(", ".join(r["required"]) or "none"))

    by_skill = {}
    for f in r["findings"]:
        by_skill.setdefault(f["skill"], []).append(f)
    skills = sorted(set(list(by_skill) + [c["skill"] for c in r["coverage"]] + [s["skill"] for s in r["scores"]]))
    rank = verdict.RANK
    for skill in skills:
        tag = "required" if skill in r["required"] else "optional, run anyway"
        L.append("<h2>%s <span class=\"muted\">(%s)</span></h2>" % (esc(skill), tag))
        fs = by_skill.get(skill, [])
        for status, heading in (("verified", "Verified findings"), ("unverified", "Unverified (not counted)"),
                                ("false_positive", "Ruled out")):
            group = sorted([f for f in fs if f["status"] == status], key=lambda f: rank[f["severity"]])
            if group:
                L.append("<h3>%s</h3>" % heading)
                L.extend(_finding(d, f) for f in group)
        sc = next((s for s in r["scores"] if s["skill"] == skill), None)
        if sc and sc["metrics"]:
            L.append("<table><tr><th>metric</th><th>value</th><th>budget</th><th></th></tr>")
            for m in sc["metrics"]:
                ok = ledger.metric_passes(m)
                L.append('<tr><td>%s</td><td>%s %s</td><td>%s %s</td><td class="%s">%s</td></tr>' % (
                    esc(m["id"]), esc(verdict._n(m["value"])), esc(m["unit"]), esc(m["op"]),
                    esc(verdict._n(m["budget"])), "ok" if ok else "bad", TICK if ok else CROSS))
            L.append("</table>")
        cov = next((c for c in r["coverage"] if c["skill"] == skill), None)
        if cov:
            L.append('<details><summary>Coverage (%d checks)</summary><ul class="cov">' % len(cov["checks"]))
            for c in cov["checks"]:
                mark = {"covered": TICK, "not_applicable": DASH, "not_run": DOT}[c["status"]]
                note = (" - %s" % esc(c["note"])) if c.get("note") else ""
                L.append("<li>%s %s: %s%s</li>" % (mark, esc(c["id"]), esc(c["title"]), note))
            L.append("</ul></details>")

    L.append('<p class="muted">Verified findings rest on the evidence they list. A screenshot shows one moment '
             'and a repro shows one machine. Not legal, security-assurance or financial advice.</p>')
    L.append("</body></html>")
    return "\n".join(L) + "\n"


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    ap.add_argument("--out", help="default: <dir>/report.html")
    ap.add_argument("--force", action="store_true", help="overwrite the file even if it is not one Proof wrote")
    ap.add_argument("--fail-on", choices=ledger.SEVERITIES[:-1])
    ap.add_argument("--open", action="store_true", help="open the page in the default browser")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.dir):
        print("error: no %s folder. Run /proof-plan first." % a.dir, file=sys.stderr)
        return 2
    r = verdict.compute(a.dir, a.fail_on)
    out = a.out or os.path.join(a.dir, "report.html")
    ledger.guard_out(out, a.dir, a.force, ours="Proof report</title>")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render_html(a.dir, r))
    print("Verdict: %s. Wrote %s" % (r["verdict"], out))
    if a.open:
        ledger.open_page(out, "the report")
    return verdict.EXIT[r["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
