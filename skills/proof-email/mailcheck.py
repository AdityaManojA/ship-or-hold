#!/usr/bin/env python3
"""Check the transactional emails an app sends, from saved .eml files and a saved DNS TXT capture.

Standard library only, no network: no DNS lookups, no SMTP. Capture the messages the
app sends (signup, password reset, receipt) as .eml files from a local mail catcher
or the framework's dev mailbox, and paste the domain's TXT records into a text file
yourself. This tool reads those files and nothing else.

    python3 mailcheck.py message reset.eml [--expect-link-host example.com] [--json]
    python3 mailcheck.py dns txt-records.txt --domain example.com [--json]
    python3 mailcheck.py record reset.eml receipt.eml [--dns txt-records.txt --domain example.com] [--expect-link-host H]
    python3 mailcheck.py status
"""

import argparse
import difflib
import email
import email.policy
import json
import os
import re
import shutil
import sys
import urllib.parse
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-email"
WARN = "~~"   # console mark for a warning (worth a look, not a failure)
NA = "--"     # console mark for a check that does not apply

# Thresholds. They live here and nowhere else.
MAX_SUBJECT = 78            # characters; longer subjects are cut off in most inbox lists
MAX_BYTES = 100 * 1024      # some clients clip a message past roughly 100 KB
MIN_TOKEN = 16              # a URL value at least this long, with letters and digits, counts as a token
REQUIRED_HEADERS = ("From", "To", "Subject", "Date", "Message-ID")

TRANSACTIONAL = re.compile(
    r"\b(reset|password|receipt|invoice|verify|verification|confirm|confirmation|order|"
    r"sign[- ]?in|log[- ]?in|login|magic link|one[- ]time|security code|2fa|two[- ]factor|"
    r"activate|activation|payment|refund|shipped|shipping)\b", re.I)
DEV_WORDS = {"staging", "stage", "stg", "dev", "develop", "development", "test", "testing",
             "qa", "uat", "preview", "sandbox", "local", "internal"}
DEV_SUFFIXES = (".localhost", ".local", ".internal", ".test", ".lan", ".home.arpa")
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
SECRET_PATTERNS = [
    (re.compile(r"\bpassword(?:[ \t]+is[ \t]+|[ \t]*[:=][ \t]*)(?!https?://)(?=\S*[^\sA-Za-z])(\S{4,})", re.I),
     "a password printed in the body"),
    (re.compile(r"\b[sprk]k_live_[A-Za-z0-9]{8,}"), "a live API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "a cloud access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "a code-hosting access token"),
]


def _ascii(text):
    """Console output stays ASCII; files are written as UTF-8 regardless."""
    return str(text).encode("ascii", "replace").decode("ascii")


def _short(text, n=60):
    text = str(text)
    return text if len(text) <= n else text[:n - 3] + "..."


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


# ------------------------------------------------------------------ parsing

class _Html(HTMLParser):
    """Collect links, images, other resource URLs and visible text from an HTML part."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.links, self.imgs, self.resources, self.text = [], [], [], []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict((k, "" if v is None else v) for k, v in attrs)
        if tag == "a" and "href" in a:
            self.links.append(a["href"].strip())
        if tag == "img":
            self.imgs.append(a)
        for key in ("src", "background"):
            if a.get(key):
                self.resources.append(a[key].strip())
        for m in re.finditer(r"url\(\s*['\"]?([^'\")]+)", a.get("style", ""), re.I):
            self.resources.append(m.group(1).strip())
        if tag in ("style", "script", "head", "title"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def parse_html(text):
    p = _Html()
    p.feed(text)
    p.close()
    p.visible = _clean(" ".join(p.text))
    return p


def _part_text(part):
    try:
        return part.get_content()
    except (LookupError, KeyError, ValueError, AssertionError):
        raw = part.get_payload(decode=True) or b""
        return raw.decode("utf-8", "replace")


def read_message(path):
    """Parse one .eml file into the pieces the checks look at."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        raise SystemExit("cannot read %s: %s" % (path, e))
    if not raw.strip():
        raise SystemExit("%s is empty; save the whole message source (.eml), headers included" % path)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    if not any(msg.get(h) for h in ("From", "To", "Subject", "Content-Type")):
        raise SystemExit("%s does not look like an email message (no From, To, Subject or Content-Type header)" % path)
    texts, htmls = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            texts.append(_part_text(part))
        elif ctype == "text/html":
            htmls.append(_part_text(part))
    headers = {}
    for h in REQUIRED_HEADERS + ("List-Unsubscribe", "List-Unsubscribe-Post"):
        try:
            v = msg.get(h)
        except (ValueError, IndexError, AttributeError):
            v = None
        headers[h] = None if v is None else _clean(str(v))
    return {"file": path, "name": os.path.basename(path), "bytes": len(raw),
            "content_type": msg.get_content_type(), "headers": headers,
            "subject": headers["Subject"] or "", "text": "\n".join(texts),
            "html": "\n".join(htmls), "has_text": bool(texts), "has_html": bool(htmls)}


# -------------------------------------------------------------------- hosts

def _host_of(url):
    """(host, port) of an absolute http(s) URL, lowercased, no trailing dot."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError:
        return "", None
    return host, port


def _norm_host(h):
    h = (h or "").strip().lower().rstrip(".")
    if "//" in h:
        h = _host_of(h)[0]
    h = h.split("/")[0]
    return h[4:] if h.startswith("www.") else h


def dev_host_reason(host, port=None):
    """Why a host looks like a development or internal host, or None."""
    if not host:
        return None
    if host in ("localhost", "0.0.0.0", "::1") or host.startswith("127."):
        return "loopback host"
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)\d", host):
        return "private network address"
    if host.endswith(DEV_SUFFIXES):
        return "internal or test domain"
    labels = host.split(".")
    for label in labels[:-2]:
        if set(label.split("-")) & DEV_WORDS:
            return "'%s' in the host name" % label
    if port and port not in (80, 443):
        return "non-standard port %d" % port
    return None


def host_matches(host, expected):
    return host == expected or host.endswith("." + expected)


def lookalike(host, expected):
    """True when ``host`` is not ``expected`` but is close enough to fool a reader."""
    if host_matches(host, expected):
        return False
    base = expected.split(".")[0]
    if len(base) >= 3 and base in host:
        return True
    first = host.split(".")[0] if host.count(".") else host
    return difflib.SequenceMatcher(None, first, base).ratio() >= 0.8 or \
        difflib.SequenceMatcher(None, host, expected).ratio() >= 0.85


# ------------------------------------------------------------------- checks
# Each check returns (status, found, problem): status is pass, fail or na.

def _urls(m, p):
    """Every absolute URL in the message: HTML links and resources, and URLs in the text part."""
    out = []
    if p is not None:
        out += [u for u in p.links + p.resources if u.lower().startswith(("http://", "https://"))]
    out += URL_IN_TEXT.findall(m["text"])
    seen, uniq = set(), []
    for u in out:
        u = u.rstrip(".,;:!?")
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def chk_headers(m, p, ctx):
    missing = [h for h in REQUIRED_HEADERS if not m["headers"].get(h)]
    if missing:
        return "fail", None, "missing header(s): %s" % ", ".join(missing)
    return "pass", "From, To, Subject, Date, Message-ID present", None


def chk_subject(m, p, ctx):
    s = m["subject"].strip()
    if not s:
        return "fail", None, "subject is empty"
    found = '"%s" (%d chars)' % (_short(s), len(s))
    if len(s) > MAX_SUBJECT:
        return "fail", found, "subject is %d chars (keep it under %d)" % (len(s), MAX_SUBJECT)
    return "pass", found, None


def chk_text_part(m, p, ctx):
    if m["has_text"] and m["has_html"]:
        return "pass", "text/plain and text/html parts (%s)" % m["content_type"], None
    if m["has_text"]:
        return "pass", "plain text only (no HTML to fall back from)", None
    if m["has_html"]:
        return "fail", "HTML only (%s)" % m["content_type"], "no plain-text part, only HTML"
    return "fail", None, "no text/plain or text/html body part"


def chk_mixed_content(m, p, ctx):
    if p is None:
        return "na", "no HTML part", None
    insecure = [u for u in p.resources if u.lower().startswith("http://")]
    if insecure:
        return "fail", "first: %s" % _short(insecure[0], 80), \
            "%d image or resource URL(s) load over http://" % len(insecure)
    return "pass", "%d resource URL(s), none over http://" % len(p.resources), None


def chk_link_https(m, p, ctx):
    if p is None:
        return "na", "no HTML part", None
    bad, counted = [], 0
    for href in p.links:
        low = href.lower()
        if not href or low.startswith(("mailto:", "tel:", "#")):
            continue
        counted += 1
        if not low.startswith("https://"):
            bad.append(href)
    if bad:
        what = "not https" if bad[0].lower().startswith("http://") else "not an absolute https link"
        return "fail", "first: %s" % _short(bad[0], 80), \
            "%d of %d link(s) are not https (first is %s)" % (len(bad), counted, what)
    return "pass", "%d link(s), all https (mailto and tel skipped)" % counted, None


def chk_dev_hosts(m, p, ctx):
    leaks = []
    for u in _urls(m, p):
        host, port = _host_of(u)
        why = dev_host_reason(host, port)
        if why:
            leaks.append((u, why))
    if leaks:
        return "fail", "first: %s (%s)" % (_short(leaks[0][0], 80), leaks[0][1]), \
            "%d link(s) point at a development or internal host" % len(leaks)
    return "pass", "no localhost, private or staging hosts in %d URL(s)" % len(_urls(m, p)), None


def chk_link_host(m, p, ctx):
    expected = ctx.get("expect_host")
    if not expected:
        return "na", "no --expect-link-host given", None
    wrong, looks = [], []
    for u in _urls(m, p):
        host = _norm_host(_host_of(u)[0])
        if host and not host_matches(host, expected):
            wrong.append(host)
            if lookalike(host, expected):
                looks.append(host)
    if wrong:
        hosts = sorted(set(wrong))
        extra = ("; looks like %s: %s" % (expected, ", ".join(sorted(set(looks))[:3]))) if looks else ""
        return "fail", "hosts: %s%s" % (", ".join(hosts[:5]), extra), \
            "%d URL(s) do not point at %s" % (len(wrong), expected)
    return "pass", "every URL points at %s" % expected, None


def chk_unsubscribe(m, p, ctx):
    lu = m["headers"].get("List-Unsubscribe")
    if lu:
        one_click = " with one-click List-Unsubscribe-Post" if m["headers"].get("List-Unsubscribe-Post") else ""
        return "pass", "List-Unsubscribe present%s" % one_click, None
    sample = m["subject"] + " " + m["text"][:2000] + " " + (p.visible[:2000] if p is not None else "")
    hit = TRANSACTIONAL.search(sample)
    if hit:
        return "pass", "no List-Unsubscribe; optional for transactional mail ('%s')" % hit.group(0).lower(), None
    return "fail", None, "looks like marketing mail but has no List-Unsubscribe header"


def _tokens(urls):
    out = set()
    for u in urls:
        try:
            parsed = urllib.parse.urlsplit(u)
        except ValueError:
            continue
        values = [v for _, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)]
        values += [s for s in parsed.path.split("/") if s]
        values += [parsed.fragment] if parsed.fragment else []
        for v in values:
            if len(v) >= MIN_TOKEN and re.search(r"[A-Za-z]", v) and re.search(r"\d", v) \
                    and re.match(r"^[A-Za-z0-9._~+=-]+$", v):
                out.add(v)
    return sorted(out)


def chk_secrets(m, p, ctx):
    visible = p.visible if p is not None else ""
    problems = []
    for tok in _tokens(_urls(m, p)):
        n_text, n_html = m["text"].count(tok), visible.count(tok)
        if n_text > 1 or n_html > 1:
            problems.append("token %s... printed %d time(s) in the text part, %d in the visible HTML"
                            % (tok[:6], n_text, n_html))
    # Search line by line (text part) and text node by text node (HTML), so a label
    # such as "Reset your password:" is never read together with the next line.
    chunks = m["text"].splitlines() + (p.text if p is not None else [])
    for rx, label in SECRET_PATTERNS:
        if any(rx.search(chunk) for chunk in chunks):
            problems.append(label)
    if problems:
        return "fail", "; ".join(problems[:3]), "secret or token exposed: %s" % problems[0]
    return "pass", "no repeated link tokens or obvious secrets in the body", None


def chk_size(m, p, ctx):
    found = "%d bytes (%.1f KB)" % (m["bytes"], m["bytes"] / 1024.0)
    if m["bytes"] > MAX_BYTES:
        return "fail", found, "message is %.1f KB (keep it under %d KB or clients may clip it)" % (m["bytes"] / 1024.0, MAX_BYTES // 1024)
    return "pass", found, None


def chk_img_alt(m, p, ctx):
    if p is None or not p.imgs:
        return "na", "no img elements", None
    missing = [i for i in p.imgs if "alt" not in i]
    found = "%d of %d images have alt" % (len(p.imgs) - len(missing), len(p.imgs))
    if missing:
        return "fail", "%s; first without: %s" % (found, _short(missing[0].get("src", "") or "(no src)", 80)), \
            "%d of %d images have no alt text" % (len(missing), len(p.imgs))
    return "pass", found, None


# id, title, function, severity when it fails, fix
CHECKS = [
    ("headers", "From, To, Subject, Date and Message-ID present", chk_headers, "low",
     "Send every message with From, To, Subject, Date and Message-ID; most mail libraries add the last two when asked."),
    ("subject", "Subject present and at most %d chars" % MAX_SUBJECT, chk_subject, "low",
     "Write a short subject that says what the message is (\"Reset your password\")."),
    ("text-part", "A plain-text part alongside the HTML", chk_text_part, "low",
     "Send multipart/alternative with a text/plain part, so text-only clients and filters can read it."),
    ("mixed-content", "No images or resources over http://", chk_mixed_content, "medium",
     "Serve every image and resource in the email over https://."),
    ("link-https", "Every link is https (mailto and tel excepted)", chk_link_https, "medium",
     "Make every link an absolute https:// URL; relative links do not resolve in a mail client."),
    ("dev-hosts", "No localhost, private or staging hosts in links", chk_dev_hosts, "high",
     "Build email links from the production base URL setting, not the request host or a dev default."),
    ("link-host", "Every URL points at the expected host", chk_link_host, "medium",
     "Point every link at your own domain so readers can trust it; check the base URL and any link rewriting."),
    ("unsubscribe", "List-Unsubscribe on marketing-type mail", chk_unsubscribe, "low",
     "Add List-Unsubscribe (and List-Unsubscribe-Post for one-click) to anything that is not strictly transactional."),
    ("secrets", "No repeated tokens or printed secrets", chk_secrets, "medium",
     "Show a reset or sign-in token once, inside one link; never print passwords or keys in an email."),
    ("size", "Message under %d KB" % (MAX_BYTES // 1024), chk_size, "low",
     "Trim inline CSS and embedded images; host images instead of attaching them inline."),
    ("img-alt", "Every img has alt text", chk_img_alt, "low",
     "Give every image alt text; many clients block images by default and show the alt instead."),
]


def analyse_message(path, expect_host=None):
    m = read_message(path)
    p = parse_html(m["html"]) if m["has_html"] else None
    ctx = {"expect_host": _norm_host(expect_host) if expect_host else None}
    checks = []
    for cid, title, fn, severity, fix in CHECKS:
        status, found, problem = fn(m, p, ctx)
        checks.append({"id": cid, "title": title, "status": status, "found": found, "problem": problem,
                       "severity": severity if status == "fail" else None,
                       "fix": fix if status == "fail" else None})
    return {"file": path, "name": m["name"], "subject": m["subject"], "bytes": m["bytes"],
            "content_type": m["content_type"], "expect_link_host": ctx["expect_host"],
            "checks": checks, "failed": sum(1 for c in checks if c["status"] == "fail")}


# ---------------------------------------------------------------------- dns

_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_DIG = re.compile(r"^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?TXT\s+(.*)$", re.I)
_NSLOOKUP = re.compile(r"^(\S+)\s+text\s*=\s*(.*)$", re.I)
_BARE = re.compile(r"^(?:(\S+\.\S+?)\s*[:=]?\s+)?(v=(?:spf1|dmarc1|dkim1)\b.*)$", re.I)


def _unquote(s):
    parts = _QUOTED.findall(s)
    if not parts:
        return s.strip()
    return "".join(re.sub(r"\\(.)", r"\1", x) for x in parts)


def parse_txt_records(text):
    """Pull (owner, record) pairs out of pasted dig or nslookup output, or bare record lines.

    ``owner`` is lowercased with no trailing dot, or None when the line did not say.
    Multi-string records ("v=spf1 " "-all") are joined.
    """
    out, pending = [], None   # pending: [owner, [strings]] for an nslookup block
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            if pending and pending[1] and not line:
                out.append((pending[0], "".join(pending[1])))
                pending = None
            continue
        m = _DIG.match(line)
        if m and '"' in m.group(2):
            if pending and pending[1]:
                out.append((pending[0], "".join(pending[1])))
            pending = None
            out.append((m.group(1).rstrip(".").lower(), _unquote(m.group(2))))
            continue
        m = _NSLOOKUP.match(line)
        if m:
            if pending and pending[1]:
                out.append((pending[0], "".join(pending[1])))
            pending = [m.group(1).rstrip(".").lower(), []]
            if '"' in m.group(2):
                pending[1].append(_unquote(m.group(2)))
            continue
        if line.startswith('"'):
            if pending is not None:
                pending[1].append(_unquote(line))
            else:
                out.append((None, _unquote(line)))
            continue
        m = _BARE.match(line)
        if m:
            if pending and pending[1]:
                out.append((pending[0], "".join(pending[1])))
            pending = None
            owner = m.group(1).rstrip(".:").lower() if m.group(1) else None
            out.append((owner, _unquote(m.group(2))))
    if pending and pending[1]:
        out.append((pending[0], "".join(pending[1])))
    return out


def _tags(record):
    tags = {}
    for piece in record.split(";"):
        if "=" in piece:
            k, _, v = piece.partition("=")
            tags[k.strip().lower()] = v.strip()
    return tags


def check_dns(text, domain):
    """SPF, DMARC and DKIM checks over the captured records. Returns a list of check dicts."""
    domain = _norm_host(domain)
    records = parse_txt_records(text)
    spf = [r for o, r in records if r.lower().startswith("v=spf1") and o in (None, domain)]
    dmarc = [r for o, r in records if r.lower().startswith("v=dmarc1") and o in (None, "_dmarc." + domain)]
    dkim = [(o, r) for o, r in records
            if (o and o.endswith("._domainkey." + domain)) or r.lower().startswith("v=dkim1")]
    out = []

    def add(cid, title, status, found, problem=None, severity=None, fix=None):
        out.append({"id": cid, "title": title, "status": status, "found": found, "problem": problem,
                    "severity": severity if status in ("fail", "warn") else None,
                    "fix": fix if status in ("fail", "warn") else None})

    spf_title = "SPF record present and ending in -all or ~all"
    spf_fix = "Publish one TXT record at %s: v=spf1 <your senders> -all (or ~all while testing)." % domain
    if not spf:
        add("spf", spf_title, "fail", None, "no SPF record (v=spf1) for %s in the file" % domain, "medium", spf_fix)
    elif len(spf) > 1:
        add("spf", spf_title, "fail", "%d SPF records" % len(spf),
            "%d SPF records for %s (receivers treat more than one as an error)" % (len(spf), domain), "medium", spf_fix)
    else:
        terms = spf[0].split()
        alls = [t for t in terms if re.match(r"^[+~?-]?all$", t, re.I)]
        found = _short(spf[0], 90)
        if not alls:
            if any(t.lower().startswith("redirect=") for t in terms):
                add("spf", spf_title, "pass", found + " (policy comes from the redirect target)")
            else:
                add("spf", spf_title, "warn", found, "SPF record has no 'all' term, so unlisted senders are not refused",
                    "low", spf_fix)
        else:
            q = alls[-1][0] if alls[-1][0] in "+~?-" else "+"
            if q in "-~":
                add("spf", spf_title, "pass", found)
            elif q == "?":
                add("spf", spf_title, "warn", found, "SPF ends in ?all (neutral: says nothing about other senders)",
                    "low", spf_fix)
            else:
                add("spf", spf_title, "fail", found, "SPF ends in +all, which lets any server send as %s" % domain,
                    "medium", spf_fix)

    dm_title = "DMARC record at _dmarc.%s with p=quarantine or p=reject" % domain
    dm_fix = "Publish TXT at _dmarc.%s: v=DMARC1; p=quarantine; rua=mailto:<reports address>, then move to p=reject." % domain
    if not dmarc:
        add("dmarc", dm_title, "fail", None, "no DMARC record at _dmarc.%s in the file" % domain, "medium", dm_fix)
    else:
        tags = _tags(dmarc[0])
        policy = tags.get("p", "").lower()
        found = _short(dmarc[0], 90)
        if policy in ("quarantine", "reject"):
            pct = tags.get("pct")
            note = (" (pct=%s: applied to part of the mail only)" % pct) if pct and pct != "100" else ""
            add("dmarc", dm_title, "pass", found + note)
        elif policy == "none":
            add("dmarc", dm_title, "warn", found, "DMARC policy is p=none (monitoring only; spoofed mail is still delivered)",
                "low", dm_fix)
        else:
            add("dmarc", dm_title, "fail", found, "DMARC record has no valid p= policy", "medium", dm_fix)

    dk_title = "DKIM selector records (when the file includes them)"
    if not dkim:
        add("dkim", dk_title, "na", "no <selector>._domainkey.%s records in the file; selectors cannot be listed "
            "from the domain, so capture the one your mail provider gave you" % domain)
    else:
        empty = [o or "(no name)" for o, r in dkim if not _tags(r).get("p")]
        names = ", ".join(sorted(set((o or "(no name)") for o, _ in dkim)))
        if empty:
            add("dkim", dk_title, "warn", names, "DKIM record with an empty p= (key revoked): %s" % ", ".join(empty),
                "low", "Publish the current public key at the selector your provider signs with.")
        else:
            add("dkim", dk_title, "pass", "%d selector record(s) with a key: %s" % (len(dkim), names))
    return {"domain": domain, "records": len(records), "checks": out}


def read_dns(path, domain):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        raise SystemExit("cannot read %s: %s" % (path, e))
    r = check_dns(text, domain)
    r["file"] = path
    return r


# ------------------------------------------------------------------ printing

def _print_checks(checks, indent="  "):
    for c in checks:
        if c["status"] == "na":
            mark, text = NA, "not applicable: %s" % c["found"]
        elif c["status"] == "pass":
            mark, text = ledger.OK, c["found"]
        else:
            mark = ledger.BAD if c["status"] == "fail" else WARN
            text = c["problem"] + (" [%s]" % c["found"] if c["found"] else "")
        print("%s%s  %-14s %s" % (indent, mark, c["id"], _ascii(text)))


def _print_message(r):
    print("Message: %s (%s)" % (_ascii(_short(r["subject"] or "(no subject)", 70)), _ascii(r["name"])))
    _print_checks(r["checks"])
    print("%s  %d check(s) failed" % (ledger.OK if not r["failed"] else ledger.BAD, r["failed"]))


def _print_dns(r):
    print("DNS (from %s, %d TXT record(s) read; captured by you, not queried) for %s:"
          % (_ascii(os.path.basename(r["file"])), r["records"], r["domain"]))
    _print_checks(r["checks"])


# ----------------------------------------------------------------- commands

def cmd_message(a):
    r = analyse_message(a.eml, a.expect_link_host)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    _print_message(r)
    return 0


def cmd_dns(a):
    r = read_dns(a.records, a.domain)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    _print_dns(r)
    if not r["records"]:
        print("No TXT records found in the file. Paste the output of 'dig TXT %s', 'dig TXT _dmarc.%s' "
              "(or nslookup -type=TXT) into it." % (r["domain"], r["domain"]))
    return 0


def _into_evidence(d, path):
    """Copy ``path`` under proof/evidence/proof-email/ unless it is already under proof/; return the ledger path."""
    base, full = ledger.root(d), os.path.abspath(path)
    try:
        inside = os.path.commonpath([full, base]) == base
    except ValueError:
        inside = False
    if inside:
        return os.path.relpath(full, base).replace(os.sep, "/")
    dest = os.path.join(ledger.evidence_dir(d, SKILL), os.path.basename(full))
    shutil.copyfile(full, dest)
    rel = "evidence/%s/%s" % (SKILL, os.path.basename(full))
    print("  copied %s to %s" % (_ascii(path), rel))
    return rel


def _sync_finding(d, fid, failing, build):
    """Write and verify a finding when the check fails; drop a stale one when it passes now.
    A finding the user rejected as a false positive is left alone either way."""
    path = ledger.finding_path(d, SKILL, fid)
    existing = ledger.load_finding(d, SKILL, fid) if os.path.exists(path) else None
    if existing and existing["status"] == "false_positive":
        print("  kept %s as the false positive you ruled out (not rewritten)" % fid)
        return None
    if not failing:
        if existing:
            os.remove(path)
            print("  removed stale finding %s (the check passes now)" % fid)
        return None
    f = build()
    ledger.write_finding(d, f)
    ledger.verify(d, SKILL, fid, by="evidence-file")
    print("  %s  finding %s written and verified (%s)" % (ledger.BAD, fid, f["severity"]))
    return f


def _message_slug(r, used):
    base = r["subject"].strip() or os.path.splitext(r["name"])[0]
    try:
        s = ledger.slug(base)[:40].strip("-")
    except ledger.LedgerError:
        s = ledger.slug(os.path.splitext(r["name"])[0] + "-message")[:40].strip("-")
    out, n = s, 2
    while out in used:
        out, n = "%s-%d" % (s, n), n + 1
    used.add(out)
    return out


def cmd_record(a):
    if bool(a.dns) != bool(a.domain):
        raise SystemExit("--dns and --domain go together: pass both or neither")
    if a.dns and not os.path.isfile(a.dns):
        raise SystemExit("%s does not exist" % a.dns)
    for path in a.eml:
        if not os.path.isfile(path):
            raise SystemExit("%s does not exist" % path)
    results = [analyse_message(p, a.expect_link_host) for p in a.eml]
    dns = read_dns(a.dns, a.domain) if a.dns else None
    for r in results:
        _print_message(r)
    if dns:
        _print_dns(dns)

    print("Ledger:")
    used, rows = set(), []
    for r in results:
        rows.append((r, _message_slug(r, used), _into_evidence(a.dir, r["file"])))
    dns_rel = _into_evidence(a.dir, a.dns) if dns else None

    # Coverage: one check per message (kept across runs) plus dns.
    previous = ledger.load_coverage(a.dir, SKILL)
    checks = [(c["id"], c["title"]) for c in (previous["checks"] if previous else []) if c["id"] != "dns"]
    for r, mslug, _ in rows:
        cid, title = "msg-" + mslug, "Message: %s" % (_short(r["subject"], 70) or r["name"])
        checks = [c for c in checks if c[0] != cid] + [(cid, title)]
    checks.append(("dns", "SPF, DMARC and DKIM in the captured TXT records"))
    ledger.init_coverage(a.dir, SKILL, checks)
    for r, mslug, rel in rows:
        failed = [c["id"] for c in r["checks"] if c["status"] == "fail"]
        note = "%s: %d check(s), %s" % (r["name"], len(r["checks"]),
                                         ("failed: " + ", ".join(failed)) if failed else "all pass or not applicable")
        ledger.mark_coverage(a.dir, SKILL, "msg-" + mslug, "covered", note=note, evidence=[rel])
    if dns:
        bad = ["%s %s" % (c["id"], c["status"]) for c in dns["checks"] if c["status"] in ("fail", "warn")]
        ledger.mark_coverage(a.dir, SKILL, "dns", "covered", evidence=[dns_rel],
                             note="%s for %s: %s" % (os.path.basename(a.dns), dns["domain"],
                                                    ", ".join(bad) if bad else "SPF and DMARC pass"))
    else:
        prior = next((c for c in (previous["checks"] if previous else []) if c["id"] == "dns"), None)
        if prior and prior["status"] == "covered":
            print("  dns: kept the earlier record (no --dns this run)")
        else:
            ledger.mark_coverage(a.dir, SKILL, "dns", "not_applicable",
                                 note="no DNS capture passed (--dns FILE --domain D)")

    # Findings: one per failed check per message, and per failing or warning DNS check.
    written = 0
    for r, mslug, rel in rows:
        label = _short(r["subject"], 50) or r["name"]
        for c in r["checks"]:
            fid = "email-%s-%s" % (mslug, c["id"])

            def build(c=c, rel=rel, label=label, fid=fid):
                return ledger.new_finding(
                    SKILL, "%s in '%s'" % (c["problem"], label), c["severity"], id=fid, category="email",
                    location=rel, evidence=[rel], fix=c["fix"],
                    description="Check '%s' failed on %s. Found: %s. Re-run 'mailcheck.py message %s' to see it."
                    % (c["title"], r["name"], c["found"] or c["problem"], r["name"]))
            if _sync_finding(a.dir, fid, c["status"] == "fail", build):
                written += 1
    if dns:
        for c in dns["checks"]:
            fid = "email-dns-%s" % c["id"]
            if c["status"] == "na":
                continue

            def build(c=c, fid=fid):
                return ledger.new_finding(
                    SKILL, "%s" % c["problem"], c["severity"], id=fid, category="email", location=dns_rel,
                    evidence=[dns_rel], fix=c["fix"],
                    description="Check '%s' on the captured TXT records for %s. Found: %s. The file was captured "
                                "by the user; re-capture it if DNS has changed." % (c["title"], dns["domain"], c["found"] or "nothing"))
            if _sync_finding(a.dir, fid, c["status"] in ("fail", "warn"), build):
                written += 1
    print("%s  %d message(s) recorded%s; %d finding(s) this run."
          % (ledger.OK if not written else ledger.BAD, len(rows), " plus DNS" if dns else "", written))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no email record yet. Run: mailcheck.py record <message.eml> [--dns records.txt --domain example.com]")
        return 0
    counts = {s: sum(1 for c in cov["checks"] if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
    print("checks: %d covered, %d not applicable, %d not run" % (counts["covered"], counts["not_applicable"], counts["not_run"]))
    marks = {"covered": ledger.OK, "not_applicable": NA, "not_run": "..."}
    for c in cov["checks"]:
        note = (" (%s)" % c["note"]) if c.get("note") else ""
        print("  %s %s: %s%s" % (marks[c["status"]], c["id"], _ascii(c["title"]), _ascii(note)))
    findings = ledger.load_findings(a.dir, SKILL)
    if findings:
        print("findings:")
        for f in findings:
            print("  [%s] %s (%s) %s" % (f["severity"], _ascii(f["title"]), f["status"], f["id"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("message", help="check one saved .eml message")
    m.add_argument("eml", help="a saved .eml file (the full message source)")
    m.add_argument("--expect-link-host", help="the domain every link should point at, e.g. example.com")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_message)

    dn = sub.add_parser("dns", help="check SPF, DMARC and DKIM in a saved TXT record capture (no lookup is made)")
    dn.add_argument("records", help="a text file of pasted dig or nslookup TXT output")
    dn.add_argument("--domain", required=True, help="the sending domain, e.g. example.com")
    dn.add_argument("--json", action="store_true")
    dn.set_defaults(func=cmd_dns)

    rc = sub.add_parser("record", help="check messages (and DNS) and write coverage and findings to the ledger")
    rc.add_argument("eml", nargs="+", help="one or more saved .eml files")
    rc.add_argument("--dns", help="a saved TXT record capture")
    rc.add_argument("--domain", help="the sending domain (with --dns)")
    rc.add_argument("--expect-link-host", help="the domain every link should point at")
    rc.set_defaults(func=cmd_record)

    sub.add_parser("status", help="recorded coverage and findings").set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
