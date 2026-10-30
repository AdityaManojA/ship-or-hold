#!/usr/bin/env python3
"""Check that a web app works as an installable Progressive Web App. Standard library only, no network.

Reads files the user saved from the running site: the page HTML as served, the web
app manifest as served, the service worker script, and optionally the service
worker's response headers and the folder the icons are served from. Reports
whether the manifest is linked and complete, the icons exist at the sizes they
claim, a service worker is registered and handles fetches, the app has an offline
fallback, it is served over HTTPS, and the iOS home-screen and theme meta are set.

    python3 pwa.py check --html saved/home.html --manifest saved/manifest.json --sw saved/sw.js
    python3 pwa.py check --html saved/home.html --manifest saved/manifest.json --icons-dir frontend/public --json
    python3 pwa.py record --html saved/home.html --manifest saved/manifest.json --sw saved/sw.js --url https://app.example
    python3 pwa.py status
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-pwa"

# (id, title, severity, fix)
CHECKS = [
    ("manifest-linked", "The page links a web app manifest", "high",
     "Add <link rel=\"manifest\" href=\"/manifest.webmanifest\"> to every page's head."),
    ("manifest-fields", "The manifest has the fields an install needs", "medium",
     "Set name or short_name, id, start_url, scope, display (standalone), theme_color and background_color."),
    ("icons", "Icons at 192 and 512 px, one maskable, at their declared sizes", "medium",
     "Ship PNG icons at 192x192 and 512x512, add one with purpose \"maskable\", and make each file match its sizes."),
    ("service-worker", "A service worker is registered and handles fetches", "high",
     "Register a service worker from the page and give it a fetch handler, served as JavaScript."),
    ("offline", "The app has an offline fallback", "medium",
     "Precache an offline page and answer failed navigations from the cache (caches.match)."),
    ("https", "The app is served over HTTPS", "high",
     "Serve the app over HTTPS; service workers and install only work on a secure origin."),
    ("ios", "iOS home-screen icon and meta", "low",
     "Add <link rel=\"apple-touch-icon\" sizes=\"180x180\" href=\"...\"> and the mobile-web-app-capable meta."),
    ("meta", "Viewport and theme-color meta", "low",
     "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> and <meta name=\"theme-color\">."),
]
CHECK_BY_ID = dict((c[0], c) for c in CHECKS)
WEIGHT = {"high": 20, "medium": 10, "low": 5}
DISPLAY_OK = ("standalone", "fullscreen", "minimal-ui")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")
REGISTER = re.compile(r"serviceWorker\s*\.\s*register\s*\(")
FETCH_HANDLER = re.compile(r"addEventListener\s*\(\s*['\"]fetch['\"]|\bonfetch\s*=|registerRoute\s*\(|"
                          r"workbox\.routing|precacheAndRoute\s*\(")
OFFLINE = re.compile(r"caches\s*\.\s*match\s*\(|cache\s*\.\s*match\s*\(|precacheAndRoute\s*\(|"
                     r"setCatchHandler\s*\(|offlineFallback\s*\(|\boffline(?:\.html|-page|Page|_page)\b", re.I)
CACHE_ADD = re.compile(r"\.addAll\s*\(|\.add\s*\(|\bprecache", re.I)


class _Page(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.links = []    # attribute dicts of <link>
        self.metas = []    # attribute dicts of <meta>
        self.scripts = []  # inline script text
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict((k.lower(), (v or "")) for k, v in attrs)
        if tag == "link":
            self.links.append(a)
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "script":
            self._in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and self.scripts:
            self.scripts[-1] += data


def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def png_size(path):
    """(width, height) of a PNG from its IHDR chunk, or None when it is not a PNG."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _sizes(text):
    out = set()
    for part in (text or "").lower().split():
        m = re.match(r"^(\d+)x(\d+)$", part)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


def _rels(link):
    return set(link.get("rel", "").lower().split())


def check_html(html):
    """Return {check_id: [problems]} for the checks the page alone decides, plus the parsed page."""
    p = _Page()
    p.feed(html)
    p.close()
    out = {"manifest-linked": [], "ios": [], "meta": []}
    if not any("manifest" in _rels(l) and l.get("href") for l in p.links):
        out["manifest-linked"].append("no <link rel=\"manifest\" href=...> in the page")
    touch = [l for l in p.links if _rels(l) & {"apple-touch-icon", "apple-touch-icon-precomposed"} and l.get("href")]
    if not touch:
        out["ios"].append("no apple-touch-icon link, so iOS uses a screenshot as the home-screen icon")
    elif not any((180, 180) in _sizes(l.get("sizes")) for l in touch) and not any(not l.get("sizes") for l in touch):
        out["ios"].append("no 180x180 apple-touch-icon")
    names = dict((m.get("name", "").lower(), m.get("content", "")) for m in p.metas if m.get("name"))
    if "apple-mobile-web-app-capable" not in names and "mobile-web-app-capable" not in names:
        out["ios"].append("no mobile-web-app-capable meta, so iOS opens the app in a browser frame")
    if "viewport" not in names:
        out["meta"].append("no viewport meta")
    elif "width=device-width" not in names["viewport"].replace(" ", "").lower():
        out["meta"].append("the viewport meta does not set width=device-width")
    if "theme-color" not in names:
        out["meta"].append("no theme-color meta")
    return out, p


def check_manifest(manifest, icons_dir=None):
    """Return ({check_id: [problems]}, notes) for the manifest fields and icons."""
    out = {"manifest-fields": [], "icons": []}
    if not isinstance(manifest, dict):
        return {"manifest-fields": ["the manifest is not a JSON object"], "icons": ["no icons (manifest unreadable)"]}
    if not (manifest.get("name") or manifest.get("short_name")):
        out["manifest-fields"].append("neither name nor short_name is set")
    for key in ("start_url", "id", "scope", "theme_color", "background_color"):
        if not manifest.get(key):
            out["manifest-fields"].append("%s is missing" % key)
    display = manifest.get("display")
    if display not in DISPLAY_OK:
        out["manifest-fields"].append("display is %r; it must be standalone, fullscreen or minimal-ui to install"
                                      % (display or "missing"))
    icons = manifest.get("icons") if isinstance(manifest.get("icons"), list) else []
    if not icons:
        out["icons"].append("the manifest lists no icons")
        return out
    declared = set()
    for ic in icons:
        if isinstance(ic, dict):
            declared |= _sizes(ic.get("sizes"))
    for want in ((192, 192), (512, 512)):
        if want not in declared and not any("any" in str(ic.get("sizes", "")) for ic in icons if isinstance(ic, dict)):
            out["icons"].append("no %dx%d icon" % want)
    if not any("maskable" in str(ic.get("purpose", "")).lower() for ic in icons if isinstance(ic, dict)):
        out["icons"].append("no icon with purpose \"maskable\", so Android crops the icon into a circle")
    if icons_dir:
        for ic in icons:
            if not isinstance(ic, dict) or not ic.get("src"):
                continue
            src = ic["src"]
            if urlparse(src).scheme in ("http", "https", "data"):
                continue
            rel = urlparse(src).path.lstrip("/")
            path = os.path.join(icons_dir, *rel.split("/"))
            if not os.path.isfile(path):
                out["icons"].append("%s is listed but not found under %s" % (src, icons_dir))
                continue
            real = png_size(path)
            want = _sizes(ic.get("sizes"))
            if real and want and real not in want:
                out["icons"].append("%s says %s but the file is %dx%d" % (src, ic.get("sizes"), real[0], real[1]))
    return out


def check_sw(sw_text, page_scripts=(), headers_text=None):
    """Return {check_id: [problems]} for the service worker and the offline fallback."""
    out = {"service-worker": [], "offline": []}
    if sw_text is not None:
        if not FETCH_HANDLER.search(sw_text):
            out["service-worker"].append("the service worker has no fetch handler, so it cannot serve anything offline")
        if not OFFLINE.search(sw_text):
            if CACHE_ADD.search(sw_text):
                out["offline"].append("the service worker caches files but never answers from the cache "
                                      "(no caches.match or offline fallback)")
            else:
                out["offline"].append("the service worker has no offline fallback: no precache and no caches.match")
    if page_scripts is not None and not any(REGISTER.search(s or "") for s in page_scripts):
        out["service-worker"].append("no navigator.serviceWorker.register(...) call found in the page or the scripts "
                                     "given (pass the script that registers it with --script)")
    if headers_text:
        ctype, cache = None, None
        for line in headers_text.splitlines():
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-type":
                ctype = value.strip().lower()
            elif name.strip().lower() == "cache-control":
                cache = value.strip().lower()
        if ctype and "javascript" not in ctype:
            out["service-worker"].append("the service worker is served as %s; browsers only register JavaScript" % ctype)
        m = re.search(r"max-age\s*=\s*(\d+)", cache or "")
        if m and int(m.group(1)) > 86400 and "no-cache" not in (cache or ""):
            out["service-worker"].append("the service worker is cached for %s seconds, which delays updates"
                                         % m.group(1))
    return out


def check_url(url):
    """Problems for the https check, or [] when the origin is secure."""
    u = urlparse(url if "//" in url else "//" + url)
    host = (u.hostname or "").lower()
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        return []
    if u.scheme != "https":
        return ["%s is not served over HTTPS" % url]
    return []


def _target_url(d):
    t = ledger.load_target(d)
    urls = (t or {}).get("urls") or []
    return urls[0] if urls else None


def analyse(a):
    """Run every check the inputs allow. Returns {check_id: problems list or a str reason it was not run}."""
    res = {}
    page_scripts = None
    if a.html:
        html_res, page = check_html(_read(a.html))
        res.update(html_res)
        page_scripts = list(page.scripts)
    else:
        for cid in ("manifest-linked", "ios", "meta"):
            res[cid] = "no page HTML given (--html)"
    if a.script:
        page_scripts = (page_scripts or []) + [_read(s) for s in a.script]
    if a.manifest:
        try:
            manifest = json.loads(_read(a.manifest))
        except ValueError as e:
            manifest = None
            res["manifest-fields"] = ["the manifest is not valid JSON (%s)" % e]
            res["icons"] = ["no icons (manifest unreadable)"]
        if manifest is not None:
            res.update(check_manifest(manifest, a.icons_dir))
    else:
        res["manifest-fields"] = res["icons"] = "no manifest given (--manifest)"
    if a.sw or page_scripts is not None:
        sw = check_sw(_read(a.sw) if a.sw else None, page_scripts, _read(a.headers) if a.headers else None)
        res["service-worker"] = sw["service-worker"]
        res["offline"] = sw["offline"] if a.sw else "no service worker script given (--sw)"
    else:
        res["service-worker"] = res["offline"] = "no page HTML or service worker given (--html, --sw)"
    url = a.url or _target_url(a.dir)
    res["https"] = check_url(url) if url else "no URL given (--url) and none in proof/target.json"
    return res


def score(res):
    """Weighted share of applicable checks that pass, 0..100; None when nothing could be checked."""
    total = got = 0
    for cid, _, sev, _ in CHECKS:
        r = res.get(cid)
        if isinstance(r, list):
            total += WEIGHT[sev]
            got += 0 if r else WEIGHT[sev]
    return int(round(100.0 * got / total)) if total else None


# ---------------------------------------------------------------- commands

def cmd_check(a):
    res = analyse(a)
    s = score(res)
    if a.json:
        print(json.dumps({"score": s, "checks": res}, indent=2))
        return 0
    print("PWA check%s" % ((": score %d/100" % s) if s is not None else ""))
    for cid, title, sev, _ in CHECKS:
        r = res[cid]
        if isinstance(r, str):
            print("  -- %s: not checked (%s)" % (title, r))
            continue
        print("  %s %s" % (ledger.OK if not r else ledger.BAD, title))
        for prob in r:
            print("      [%s] %s" % (sev, prob))
    return 0


def _evidence(d, path, name=None):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), name or os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


def cmd_record(a):
    res = analyse(a)
    ledger.init_coverage(a.dir, SKILL, [(c[0], c[1]) for c in CHECKS])
    ev = {}
    for key, path, name in (("html", a.html, "page.html"), ("manifest", a.manifest, "manifest.json"),
                            ("sw", a.sw, "sw.js"), ("headers", a.headers, "sw-headers.txt")):
        if path:
            ev[key] = _evidence(a.dir, path, name)
    scripts = [_evidence(a.dir, s, "script-%d-%s" % (i, os.path.basename(s))) for i, s in enumerate(a.script or [])]
    url = a.url or _target_url(a.dir)
    if url:
        note_path = os.path.join(ledger.evidence_dir(a.dir, SKILL), "url.txt")
        with open(note_path, "w", encoding="utf-8") as fh:
            fh.write("Checked origin: %s\n" % url)
        ev["url"] = os.path.relpath(note_path, ledger.root(a.dir)).replace(os.sep, "/")
    sources = {"manifest-linked": ["html"], "ios": ["html"], "meta": ["html"], "manifest-fields": ["manifest"],
               "icons": ["manifest"], "service-worker": ["sw", "html", "headers"], "offline": ["sw"], "https": ["url"]}
    n = 0
    for cid, title, sev, fix in CHECKS:
        r = res[cid]
        fid = "pwa-%s" % cid
        path = ledger.finding_path(a.dir, SKILL, fid)
        existing = ledger.load_finding(a.dir, SKILL, fid) if os.path.exists(path) else None
        if isinstance(r, str):
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note=r)
            continue
        files = [ev[k] for k in sources[cid] if k in ev] + (scripts if cid == "service-worker" else [])
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=files,
                             note="%d problem(s)" % len(r))
        if existing and existing["status"] == "false_positive":
            continue
        if not r:
            if existing:
                os.remove(path)
            continue
        f = ledger.new_finding(SKILL, ("%s: %s" % (title, r[0]))[:160], sev, id=fid, category="pwa:%s" % cid,
                               evidence=files, fix=fix, description="; ".join(r)[:400])
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
        n += 1
    s = score(res)
    if s is not None:
        ledger.write_scores(a.dir, SKILL, [{"id": "pwa-score", "value": s, "unit": "points", "budget": a.budget,
                                            "op": ">="}])
    print("%s recorded a PWA check: %d finding(s)%s." % (
        ledger.OK if not n else ledger.BAD, n, (", score %d/100 (budget %d)" % (s, a.budget)) if s is not None else ""))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no PWA check recorded yet. Run: pwa.py record ...")
        return 0
    for c in cov["checks"]:
        print("  %-16s %s" % (c["id"], c["status"]))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def _inputs(p):
    p.add_argument("--html", help="the page HTML as served")
    p.add_argument("--manifest", help="the web app manifest as served (manifest.webmanifest or manifest.json)")
    p.add_argument("--sw", help="the service worker script as served")
    p.add_argument("--script", action="append", help="a page script that may register the service worker (repeatable)")
    p.add_argument("--headers", help="response headers of the service worker request")
    p.add_argument("--icons-dir", help="the folder icons are served from, e.g. frontend/public")
    p.add_argument("--url", help="the app's URL (default: the first URL in proof/target.json)")


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="print the PWA checks")
    _inputs(c)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_check)
    r = sub.add_parser("record", help="write the PWA checks to the ledger")
    _inputs(r)
    r.add_argument("--budget", type=int, default=80, help="lowest passing pwa-score (default 80)")
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded checks and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    if a.cmd != "status":
        for path in [a.html, a.manifest, a.sw, a.headers] + list(a.script or []):
            if path and not os.path.isfile(path):
                raise SystemExit("no file at %s" % path)
        if a.icons_dir and not os.path.isdir(a.icons_dir):
            raise SystemExit("no folder at %s" % a.icons_dir)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
