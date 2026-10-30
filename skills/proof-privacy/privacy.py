#!/usr/bin/env python3
"""Check a site's privacy basics from saved files. Standard library only, no network.

Reads files the user saved: the server HTML of a page, the response headers of a
first visit, and a list of URLs (from logs or a browser export). Reports third-party
trackers that load before any consent, cookies set on the first visit, whether a
consent banner and a privacy policy link exist, forms that send personal data over
plain HTTP, and personal data leaking into URLs.

    python3 privacy.py page saved/home.html
    python3 privacy.py cookies saved/home-headers.txt
    python3 privacy.py urls saved/urls.txt
    python3 privacy.py record saved/home.html --headers saved/home-headers.txt --urls saved/urls.txt
    python3 privacy.py status
"""

import argparse
import json
import os
import re
import shutil
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-privacy"

# Domains of common analytics, advertising and session-recording services.
TRACKERS = {
    "google-analytics.com": "analytics", "googletagmanager.com": "tag manager",
    "doubleclick.net": "advertising", "googlesyndication.com": "advertising",
    "googleadservices.com": "advertising", "facebook.net": "advertising pixel",
    "connect.facebook.net": "advertising pixel", "facebook.com/tr": "advertising pixel",
    "hotjar.com": "session recording", "clarity.ms": "session recording",
    "fullstory.com": "session recording", "mouseflow.com": "session recording",
    "segment.com": "analytics", "segment.io": "analytics", "mixpanel.com": "analytics",
    "amplitude.com": "analytics", "heap.io": "analytics", "heapanalytics.com": "analytics",
    "tiktok.com": "advertising pixel", "analytics.tiktok.com": "advertising pixel",
    "snap.licdn.com": "advertising pixel", "px.ads.linkedin.com": "advertising pixel",
    "bat.bing.com": "advertising pixel", "ads-twitter.com": "advertising pixel",
    "static.ads-twitter.com": "advertising pixel", "criteo.com": "advertising",
    "taboola.com": "advertising", "outbrain.com": "advertising", "quantserve.com": "advertising",
    "scorecardresearch.com": "advertising", "newrelic.com": "monitoring",
    "nr-data.net": "monitoring", "sentry.io": "error tracking", "intercom.io": "chat widget",
    "hs-scripts.com": "marketing", "hubspot.com": "marketing", "matomo.cloud": "analytics",
    "plausible.io": "analytics (cookieless)", "usefathom.com": "analytics (cookieless)",
}
# Analytics that does not set cookies; still third-party, reported as low.
COOKIELESS = {"plausible.io", "usefathom.com"}
# Cookie names that are almost never strictly necessary.
NONESSENTIAL_COOKIES = re.compile(r"^(_ga|_gid|_gat|_gcl_|_fbp|_fbc|_hj|_clck|_clsk|ajs_|mp_|amplitude_id|"
                                  r"_uet|_pin_|_tt_|li_|IDE$|NID$|__hs|hubspot|_scid|muid|_rdt)", re.I)
CONSENT_WORDS = re.compile(r"\b(cookie|cookies|consent)\b", re.I)
ACCEPT_WORDS = re.compile(r"\b(accept|agree|allow|reject|decline|manage|preferences|settings)\b", re.I)
POLICY_WORDS = re.compile(r"privacy|data protection|datenschutz|politique de confidentialit", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"^\+?\d[\d\s().-]{7,}\d$")
SENSITIVE_PARAMS = re.compile(r"^(email|e-mail|mail|phone|tel|mobile|password|passwd|pwd|ssn|dob|birth|"
                              r"birthday|address|zip|postcode|first_?name|last_?name|full_?name|name|"
                              r"card|cc|cvv|iban|token|access_token|session|auth)$", re.I)


class _Page(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.resources = []   # (tag, url, gated)
        self.links = []       # (href, text)
        self.forms = []       # {"action","method","fields":[types/names]}
        self.text = []
        self._a = None
        self._form = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "script" and a.get("src"):
            gated = a.get("type", "").lower() in ("text/plain", "text/x-consent") or any(
                k.startswith("data-consent") or k in ("data-cookieconsent", "data-category") for k in a)
            self.resources.append(("script", a["src"], gated))
        elif tag in ("iframe", "img") and a.get("src"):
            self.resources.append((tag, a["src"], False))
        elif tag == "link" and a.get("href") and "preconnect" not in a.get("rel", ""):
            if "stylesheet" in a.get("rel", ""):
                self.resources.append(("link", a["href"], False))
        if tag == "a":
            self._a = [a.get("href", ""), []]
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "get").lower(), "fields": []}
        if tag in ("input", "textarea", "select") and self._form is not None:
            self._form["fields"].append((a.get("type", "text").lower(), a.get("name", "").lower(),
                                         a.get("autocomplete", "").lower()))

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag == "a" and self._a is not None:
            self.links.append((self._a[0], " ".join(self._a[1]).strip()))
            self._a = None
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None

    def handle_data(self, data):
        if self._skip:
            return
        if data.strip():
            self.text.append(data.strip())
            if self._a is not None:
                self._a[1].append(data.strip())


def _host(url):
    if url.startswith("//"):
        url = "https:" + url
    return (urlparse(url).netloc or "").lower().split(":")[0]


def _tracker_for(url):
    low = url.lower()
    host = _host(url)
    # Longest domain first, so a specific entry wins over its parent domain.
    for dom, kind in sorted(TRACKERS.items(), key=lambda kv: -len(kv[0])):
        if "/" in dom:
            if dom in low:
                return dom, kind
        elif host == dom or host.endswith("." + dom):
            return dom, kind
    return None, None


def analyse_page(html_text, own_host=None):
    p = _Page()
    p.feed(html_text)
    p.close()
    text = " ".join(p.text)
    trackers, third_party = [], set()
    for tag, url, gated in p.resources:
        host = _host(url)
        if host and own_host and not (host == own_host or host.endswith("." + own_host)):
            third_party.add(host)
        elif host and not own_host:
            third_party.add(host)
        dom, kind = _tracker_for(url)
        if dom:
            trackers.append({"domain": dom, "kind": kind, "tag": tag, "gated": gated, "url": url[:120]})
    banner = bool(CONSENT_WORDS.search(text) and ACCEPT_WORDS.search(text)) or any(
        re.search(r"consent|cookiebot|onetrust|cookieyes|iubenda|usercentrics|klaro|osano|termly", u, re.I)
        for _, u, _ in p.resources)
    policy = any(POLICY_WORDS.search(href) or POLICY_WORDS.search(t) for href, t in p.links)
    insecure_forms = []
    for f in p.forms:
        personal = [n or t for t, n, ac in f["fields"]
                    if t in ("email", "tel", "password") or SENSITIVE_PARAMS.match(n or "")]
        if not personal:
            continue
        if f["action"].lower().startswith("http://"):
            insecure_forms.append({"action": f["action"], "fields": personal, "why": "posts over plain HTTP"})
        elif f["method"] == "get" and any(t == "password" or n in ("password", "card", "cvv") for t, n, _ in f["fields"]):
            insecure_forms.append({"action": f["action"] or "(same page)", "fields": personal,
                                   "why": "sends sensitive fields with GET, so they land in the URL"})
    ungated = [t for t in trackers if not t["gated"] and t["domain"] not in COOKIELESS]
    return {"trackers": trackers, "ungated_trackers": ungated, "third_party_hosts": sorted(third_party),
            "consent_banner": banner, "privacy_policy_link": policy, "insecure_forms": insecure_forms}


def analyse_cookies(headers_text):
    cookies = []
    for line in headers_text.splitlines():
        if not line.strip():
            if cookies:
                break
            continue
        name, _, value = line.partition(":")
        if name.strip().lower() != "set-cookie":
            continue
        cname = value.strip().split("=", 1)[0].strip()
        cookies.append({"name": cname, "nonessential": bool(NONESSENTIAL_COOKIES.match(cname))})
    return cookies


def analyse_urls(lines):
    leaks = []
    for raw in lines:
        url = raw.strip().split()[0] if raw.strip() else ""
        if "?" not in url:
            continue
        for k, v in parse_qsl(urlparse(url).query, keep_blank_values=True):
            why = None
            if SENSITIVE_PARAMS.match(k) and v:
                why = "parameter '%s'" % k
            elif EMAIL.search(v):
                why = "an email address in '%s'" % k
            elif PHONE.match(v):
                why = "a phone number in '%s'" % k
            if why:
                path = urlparse(url).path or "/"
                leaks.append({"path": path, "param": k, "why": why})
    # One entry per path + parameter.
    seen, out = set(), []
    for l in leaks:
        key = (l["path"], l["param"])
        if key not in seen:
            seen.add(key)
            out.append(l)
    return out


def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _own_host(d, given):
    if given:
        return given.lower()
    t = ledger.load_target(d)
    if t and t.get("urls"):
        return _host(t["urls"][0])
    return None


# ---------------------------------------------------------------- commands

def cmd_page(a):
    r = analyse_page(_read(a.path), _own_host(a.dir, a.host))
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    print("Privacy check of %s" % a.path)
    print("  %s consent banner %s" % (ledger.OK if r["consent_banner"] else ledger.BAD,
                                       "found" if r["consent_banner"] else "not found in the HTML"))
    print("  %s privacy policy link %s" % (ledger.OK if r["privacy_policy_link"] else ledger.BAD,
                                            "found" if r["privacy_policy_link"] else "not found"))
    print("  third-party hosts: %d" % len(r["third_party_hosts"]))
    for t in r["trackers"]:
        mark = ledger.OK if t["gated"] or t["domain"] in COOKIELESS else ledger.BAD
        state = "waits for consent" if t["gated"] else ("cookieless" if t["domain"] in COOKIELESS else "loads before consent")
        print("  %s %s (%s): %s" % (mark, t["domain"], t["kind"], state))
    for f in r["insecure_forms"]:
        print("  %s form to %s %s (%s)" % (ledger.BAD, f["action"], f["why"], ", ".join(f["fields"])))
    return 0


def cmd_cookies(a):
    cookies = analyse_cookies(_read(a.path))
    if not cookies:
        print("%s no cookies set by this response." % ledger.OK)
    for c in cookies:
        print("  %s %s%s" % (ledger.BAD if c["nonessential"] else ledger.OK, c["name"],
                             "  (usually not strictly necessary)" if c["nonessential"] else ""))
    return 0


def cmd_urls(a):
    leaks = analyse_urls(_read(a.path).splitlines())
    if not leaks:
        print("%s no personal data found in the URLs." % ledger.OK)
    for l in leaks:
        print("  %s %s: %s" % (ledger.BAD, l["path"], l["why"]))
    return 0


def _evidence(d, path):
    dest = os.path.join(ledger.evidence_dir(d, SKILL), os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)
    return os.path.relpath(dest, ledger.root(d)).replace(os.sep, "/")


def cmd_record(a):
    checks = [("trackers-before-consent", "Trackers wait for consent"),
              ("consent-banner", "A consent banner is shown"),
              ("privacy-policy", "A privacy policy is linked"),
              ("insecure-forms", "Personal data is not sent insecurely"),
              ("first-load-cookies", "No non-essential cookies on the first visit"),
              ("pii-in-urls", "No personal data in URLs")]
    ledger.init_coverage(a.dir, SKILL, checks)
    findings = []
    page_rel = _evidence(a.dir, a.path)
    r = analyse_page(_read(a.path), _own_host(a.dir, a.host))
    for cid in ("trackers-before-consent", "consent-banner", "privacy-policy", "insecure-forms"):
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[page_rel])
    if r["ungated_trackers"]:
        names = sorted({t["domain"] for t in r["ungated_trackers"]})
        findings.append(("trackers-before-consent", "%d tracker(s) load before consent: %s" % (len(names), ", ".join(names)),
                         "high", page_rel, "Load these only after the visitor opts in (gate the tags behind the consent choice)."))
    if r["trackers"] and not r["consent_banner"]:
        findings.append(("consent-banner", "Trackers are present but no consent banner was found", "high", page_rel,
                         "Show a consent banner with a real reject option before any non-essential tag loads."))
    if not r["privacy_policy_link"]:
        findings.append(("privacy-policy", "No link to a privacy policy on this page", "medium", page_rel,
                         "Link the privacy policy from every page, usually in the footer."))
    for f in r["insecure_forms"]:
        findings.append(("insecure-forms", "Form to %s %s" % (f["action"], f["why"]), "high", page_rel,
                         "Post personal data over HTTPS with POST."))
    if a.headers:
        h_rel = _evidence(a.dir, a.headers)
        cookies = analyse_cookies(_read(a.headers))
        ledger.mark_coverage(a.dir, SKILL, "first-load-cookies", "covered", evidence=[h_rel],
                             note="%d cookie(s) on first visit" % len(cookies))
        bad = [c["name"] for c in cookies if c["nonessential"]]
        if bad:
            findings.append(("first-load-cookies", "Non-essential cookies set on the first visit: %s" % ", ".join(bad),
                             "medium", h_rel, "Set analytics and advertising cookies only after consent."))
    else:
        ledger.mark_coverage(a.dir, SKILL, "first-load-cookies", "not_applicable",
                             note="no first-visit response headers provided")
    if a.urls:
        u_rel = _evidence(a.dir, a.urls)
        leaks = analyse_urls(_read(a.urls).splitlines())
        ledger.mark_coverage(a.dir, SKILL, "pii-in-urls", "covered", evidence=[u_rel],
                             note="%d leak(s)" % len(leaks))
        for l in leaks:
            findings.append(("pii-in-urls", "Personal data in the URL at %s: %s" % (l["path"], l["why"]), "medium", u_rel,
                             "Send personal data in the request body, never the query string; URLs end up in logs and referrers."))
    else:
        ledger.mark_coverage(a.dir, SKILL, "pii-in-urls", "not_applicable", note="no URL list provided")
    for cid, title, sev, ev, fix in findings:
        fid = ledger.slug("privacy-%s-%s" % (cid, title))[:60]
        f = ledger.new_finding(SKILL, title[:160], sev, id=fid, category="privacy:%s" % cid, evidence=[ev], fix=fix)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    print("%s recorded a privacy check: %d finding(s)." % (ledger.OK if not findings else ledger.BAD, len(findings)))
    for _, title, sev, _, _ in findings:
        print("  [%s] %s" % (sev, title))
    return 0


def cmd_status(a):
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("page", help="check one saved HTML page")
    p.add_argument("path")
    p.add_argument("--host", help="the site's own host, to tell first-party from third-party")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_page)
    c = sub.add_parser("cookies", help="cookies set by a captured first-visit response")
    c.add_argument("path")
    c.set_defaults(func=cmd_cookies)
    u = sub.add_parser("urls", help="personal data in a list of URLs")
    u.add_argument("path")
    u.set_defaults(func=cmd_urls)
    r = sub.add_parser("record", help="write the privacy check to the ledger")
    r.add_argument("path", help="saved HTML of a page")
    r.add_argument("--host")
    r.add_argument("--headers", help="captured first-visit response headers")
    r.add_argument("--urls", help="a file of URLs, one per line")
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
