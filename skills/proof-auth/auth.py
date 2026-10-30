#!/usr/bin/env python3
"""Login and session safety for an app you own or are authorised to test. Standard library only.

A checklist of checks you (or the model) perform against your own app, plus offline
analysers of material you captured: a token, the response headers of a login, two
login responses, a log of repeated attempts. Every result is recorded with the
captured file as evidence. Tokens are decoded, never verified, and never printed in
full.

    python3 auth.py plan
    python3 auth.py jwt captured-token.txt
    python3 auth.py cookies login-headers.txt
    python3 auth.py enum unknown-user.txt wrong-password.txt
    python3 auth.py ratelimit attempts.txt --attempts 20
    python3 auth.py result logout-invalidates fail --evidence after-logout.txt --title "..." --verify
    python3 auth.py na mfa --note "the app offers no second factor"
    python3 auth.py status
"""

import argparse
import base64
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-auth"

# id, title, how to test, what safe looks like
CHECKS = [
    ("password-reset", "Password reset is safe",
     "Request a reset for your test account, use the link, then use it again; wait past its expiry; "
     "check the token is not in a URL that other sites see; request a reset for an address with no account.",
     "The token works once, expires, never appears in a referrer or third-party request, and the response "
     "is the same whether or not the account exists."),
    ("session-expiry", "Sessions expire",
     "Sign in, leave the session idle past the stated timeout, and call an authenticated endpoint.",
     "The old session is refused and the user is asked to sign in again."),
    ("logout-invalidates", "Logging out ends the session",
     "Capture the session token or cookie, log out, then replay a request with the old token.",
     "The replayed request is refused (401 or a redirect to sign in)."),
    ("token-storage", "Tokens are stored where scripts cannot read them",
     "On the web, look in localStorage and sessionStorage and at the cookies after sign-in. "
     "On mobile, see proof-react-native's token-storage check.",
     "Session tokens live in HttpOnly cookies (web) or the platform keychain (mobile), not in script-readable storage."),
    ("cookie-flags", "Session cookies carry the right flags",
     "Save the response headers of a sign-in and run: auth.py cookies <file>.",
     "Session cookies are HttpOnly, Secure and SameSite, with a bounded lifetime."),
    ("jwt-hygiene", "Tokens are well formed and short-lived",
     "Save an access token you were issued and run: auth.py jwt <file>.",
     "A real signing algorithm, an exp claim, a short access-token lifetime, and no secrets in the payload."),
    ("oauth-redirects", "OAuth sign-in only returns to allowed addresses",
     "Start a social sign-in, change redirect_uri to another host and drop or reuse the state parameter.",
     "The provider or the app refuses a redirect_uri that is not on the allowlist, and a missing or reused "
     "state is rejected."),
    ("rate-limiting", "Sign-in, reset and sign-up are rate-limited",
     "Send repeated failed sign-ins (and resets, sign-ups) from one client, save the status codes, and run: "
     "auth.py ratelimit <file>.",
     "Requests are slowed, answered with 429 or locked out well before an attacker could guess passwords."),
    ("enumeration", "Responses do not reveal which accounts exist",
     "Save the response to a sign-in with an unknown email and with a known email and wrong password, "
     "then run: auth.py enum <a> <b>. Repeat for sign-up and reset.",
     "Both responses have the same status, the same message and a similar size and timing."),
    ("mfa", "A second factor cannot be skipped",
     "If the app offers a second factor, sign in with it on and try to reach an authenticated page or API "
     "after the password step only.",
     "Nothing behind the second factor is reachable until it is completed."),
    ("password-policy", "Passwords meet a sensible policy",
     "Try to set a very short password and a well-known breached one.",
     "Short passwords are refused (8 characters or more; longer is better) and, ideally, known-breached ones too."),
]
CHECK_IDS = [c[0] for c in CHECKS]

SESSION_COOKIE = re.compile(r"(sess|sid|auth|token|jwt|login|remember|refresh|access)", re.I)
SECRET_CLAIM = re.compile(r"(password|passwd|secret|api[_-]?key|private[_-]?key|card|cvv|ssn)", re.I)
LOCKOUT_WORDS = re.compile(r"(too many|locked|lockout|try again later|rate limit|slow down|captcha)", re.I)


def _require_authorised(dir_):
    """Refuse active work unless the target is declared owned or authorised.

    Analysing a file you already captured is always allowed; this guard is for
    recording results of checks performed against a live target.
    """
    t = ledger.load_target(dir_)
    if t is None:
        return  # no target yet; the SKILL tells the model to run /proof-plan first
    if not t.get("authorised"):
        raise SystemExit("target.json says authorised: false. Confirm you own this target or have written "
                         "permission to test it, set authorised: true via /proof-plan, then continue.")


def _read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def mask(token):
    token = token or ""
    return token[:4] + "..." if len(token) > 4 else "..."


# ---------------------------------------------------------------- analysers

def _b64json(part):
    pad = "=" * (-len(part) % 4)
    raw = base64.urlsafe_b64decode(part + pad)
    return json.loads(raw.decode("utf-8"))


def analyse_jwt(text, max_lifetime_h=24.0):
    """Decode (never verify) the first JWT in text. Returns {masked, header, claims, problems}."""
    m = re.search(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*", text or "")
    if not m:
        raise SystemExit("no JWT (three dot-separated base64url parts) found in the file")
    token = m.group(0)
    head_b, body_b = token.split(".")[:2]
    try:
        header, claims = _b64json(head_b), _b64json(body_b)
    except (ValueError, UnicodeDecodeError):
        raise SystemExit("the token's header or payload is not base64url JSON")
    problems = []
    alg = str(header.get("alg", ""))
    if alg.lower() == "none" or not alg:
        problems.append(("high", "the token is unsigned (alg %r): anyone can forge one" % (alg or "missing")))
    elif alg.upper().startswith("HS"):
        problems.append(("low", "signed with a shared secret (%s): make sure the secret is long and random, "
                                "and that the server never accepts alg none or a switched algorithm" % alg))
    if token.split(".")[2] == "" and alg.lower() != "none":
        problems.append(("high", "the token has no signature part"))
    exp = claims.get("exp")
    iat = claims.get("iat") if claims.get("iat") is not None else claims.get("nbf")
    if exp is None:
        problems.append(("high", "no exp claim: the token never expires"))
    elif isinstance(exp, (int, float)) and isinstance(iat, (int, float)):
        hours = (exp - iat) / 3600.0
        kind = str(claims.get("typ") or claims.get("token_type") or header.get("typ") or "").lower()
        if hours > max_lifetime_h and "refresh" not in kind:
            problems.append(("medium", "lives %.1f hours, longer than the %.0f-hour budget for an access token"
                             % (hours, max_lifetime_h)))
    bad = sorted(k for k in claims if SECRET_CLAIM.search(k))
    if bad:
        problems.append(("high", "the payload carries secret-looking claims: %s (payloads are readable by anyone)"
                         % ", ".join(bad)))
    return {"masked": mask(token), "header": header, "claims": sorted(claims), "problems": problems}


def analyse_cookies(headers_text, max_age_days=30):
    """Set-Cookie flags of session-like cookies. Returns [{name, problems}]."""
    out = []
    for line in headers_text.splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() != "set-cookie":
            continue
        parts = [p.strip() for p in value.split(";")]
        cname = parts[0].split("=", 1)[0].strip()
        attrs = {}
        for p in parts[1:]:
            k, _, v = p.partition("=")
            attrs[k.strip().lower()] = v.strip()
        if not SESSION_COOKIE.search(cname):
            continue
        problems = []
        if "httponly" not in attrs:
            problems.append(("high", "not HttpOnly: page scripts can read it"))
        if "secure" not in attrs:
            problems.append(("high", "not Secure: it can travel over plain HTTP"))
        same = attrs.get("samesite", "").lower()
        if not same:
            problems.append(("medium", "no SameSite attribute"))
        elif same == "none" and "secure" in attrs:
            problems.append(("low", "SameSite=None: sent on cross-site requests"))
        try:
            age = int(attrs.get("max-age", "0") or 0)
        except ValueError:
            age = 0
        if age > max_age_days * 86400:
            problems.append(("low", "Max-Age of %d days" % (age // 86400)))
        out.append({"name": cname, "problems": problems})
    return out


def _normalise(text):
    t = re.sub(r"\s+", " ", text or "").strip().lower()
    t = re.sub(r"[0-9a-f]{8,}", "#", t)  # ids and request ids
    return re.sub(r"\d+", "#", t)


def _split_response(text):
    """(status, body) from a saved raw response or a bare body."""
    m = re.match(r"\s*HTTP/[\d.]+\s+(\d{3})", text or "")
    status = int(m.group(1)) if m else None
    body = text.split("\n\n", 1)[1] if m and "\n\n" in text else text
    return status, body


def analyse_enum(a_text, b_text, size_tolerance=0.1):
    """Compare two responses that should be identical. Returns a list of differences."""
    sa, ba = _split_response(a_text)
    sb, bb = _split_response(b_text)
    diffs = []
    if sa != sb:
        diffs.append("different status codes (%s vs %s)" % (sa, sb))
    if _normalise(ba) != _normalise(bb):
        diffs.append("different messages")
    la, lb = len(ba.strip()), len(bb.strip())
    if max(la, lb) and abs(la - lb) / float(max(la, lb)) > size_tolerance:
        diffs.append("body sizes differ by more than %d%% (%d vs %d bytes)" % (size_tolerance * 100, la, lb))
    return diffs


def analyse_ratelimit(text, attempts=20):
    """Read a capture of repeated attempts. Returns {n, first_limited, codes}."""
    rows = []
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("attempts") or data.get("results") or []
        for r in data:
            if isinstance(r, dict):
                rows.append((int(r.get("status") or 0), str(r.get("body") or r.get("message") or "")))
            else:
                rows.append((int(r), ""))
    except (ValueError, TypeError):
        for line in text.splitlines():
            m = re.search(r"\b([1-5]\d\d)\b", line)
            if m:
                rows.append((int(m.group(1)), line))
    if not rows:
        raise SystemExit("no status codes found in the capture")
    first = None
    for i, (code, body) in enumerate(rows, 1):
        if code == 429 or code == 423 or LOCKOUT_WORDS.search(body):
            first = i
            break
    return {"n": len(rows), "first_limited": first, "limited": first is not None and first <= attempts,
            "codes": sorted(set(c for c, _ in rows))}


# ---------------------------------------------------------------- recording

def _evidence(d, path):
    full = os.path.abspath(path)
    root = ledger.root(d)
    if not os.path.isfile(full):
        cand = os.path.join(root, path)
        if os.path.isfile(cand):
            full = cand
        else:
            raise SystemExit("no evidence file at %s" % path)
    try:
        inside = os.path.commonpath([root, full]) == root
    except ValueError:
        inside = False
    if not inside:
        dest = os.path.join(ledger.evidence_dir(d, SKILL), os.path.basename(full))
        shutil.copy2(full, dest)
        full = dest
    return os.path.relpath(full, root).replace(os.sep, "/")


def _ensure_plan(d):
    if ledger.load_coverage(d, SKILL) is None:
        ledger.init_coverage(d, SKILL, [(c[0], c[1]) for c in CHECKS])


def _record_problems(a, check, problems, path, what):
    """Shared by the analysers when --record is given: cover the check, one finding for the problems."""
    _require_authorised(a.dir)
    _ensure_plan(a.dir)
    rel = _evidence(a.dir, path)
    ledger.mark_coverage(a.dir, SKILL, check, "covered", evidence=[rel])
    if not problems:
        print("%s recorded %s as passing, evidence %s" % (ledger.OK, check, rel))
        return
    order = {s: i for i, s in enumerate(ledger.SEVERITIES)}
    sev = sorted((p[0] for p in problems), key=lambda s: order[s])[0]
    title = "%s: %s" % (what, problems[0][1])
    fid = ledger.slug("auth-%s-%s" % (check, os.path.basename(path)))[:60]
    f = ledger.new_finding(SKILL, title[:160], sev, id=fid, category="auth:%s" % check, evidence=[rel],
                           description="; ".join(p[1] for p in problems)[:400])
    ledger.write_finding(a.dir, f)
    ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    print("%s recorded a verified finding for %s: %s" % (ledger.BAD, check, fid))


# ---------------------------------------------------------------- commands

def cmd_plan(a):
    _ensure_plan(a.dir)
    print("%s coverage ledger with %d auth checks, all not run." % (ledger.OK, len(CHECKS)))
    for cid, title, how, safe in CHECKS:
        print("\n[%s] %s" % (cid, title))
        print("  test: %s" % how)
        print("  safe: %s" % safe)
    return 0


def _print_problems(problems):
    for sev, msg in problems:
        print("  %s [%s] %s" % (ledger.BAD, sev, msg))


def cmd_jwt(a):
    r = analyse_jwt(_read(a.path), a.max_lifetime)
    print("token %s  alg %s  claims: %s" % (r["masked"], r["header"].get("alg"), ", ".join(r["claims"])))
    if not r["problems"]:
        print("  %s no problems found in the token itself" % ledger.OK)
    _print_problems(r["problems"])
    if a.record:
        _record_problems(a, "jwt-hygiene", r["problems"], a.path, "Token")
    return 0


def cmd_cookies(a):
    cookies = analyse_cookies(_read(a.path), a.max_age_days)
    if not cookies:
        print("no session-like cookies in %s" % a.path)
        return 0
    problems = []
    for c in cookies:
        print("%s %s" % (ledger.OK if not c["problems"] else ledger.BAD, c["name"]))
        _print_problems(c["problems"])
        problems += [(s, "%s %s" % (c["name"], m)) for s, m in c["problems"]]
    if a.record:
        _record_problems(a, "cookie-flags", problems, a.path, "Session cookie")
    return 0


def cmd_enum(a):
    diffs = analyse_enum(_read(a.a), _read(a.b), a.size_tolerance)
    if not diffs:
        print("%s the two responses look the same" % ledger.OK)
    for d in diffs:
        print("  %s %s" % (ledger.BAD, d))
    if a.record:
        joined = os.path.join(ledger.evidence_dir(a.dir, SKILL), "enum-%s-vs-%s.txt" % (
            os.path.splitext(os.path.basename(a.a))[0], os.path.splitext(os.path.basename(a.b))[0]))
        with open(joined, "w", encoding="utf-8") as fh:
            fh.write("=== %s\n%s\n\n=== %s\n%s\n" % (a.a, _read(a.a), a.b, _read(a.b)))
        _record_problems(a, "enumeration", [("medium", d) for d in diffs], joined, "Responses reveal accounts")
    return 0


def cmd_ratelimit(a):
    r = analyse_ratelimit(_read(a.path), a.attempts)
    if r["limited"]:
        print("%s limited at attempt %d of %d (codes seen: %s)" % (ledger.OK, r["first_limited"], r["n"],
                                                                   ", ".join(map(str, r["codes"]))))
        problems = []
    else:
        where = "only at attempt %d" % r["first_limited"] if r["first_limited"] else "at no point"
        print("  %s %d attempts, limited %s (budget: within %d)" % (ledger.BAD, r["n"], where, a.attempts))
        problems = [("high", "no rate limit or lockout within %d attempts (%d sent, limited %s)"
                     % (a.attempts, r["n"], where))]
        if r["n"] < a.attempts and not r["first_limited"]:
            problems = [("info", "only %d attempts captured; send at least %d to judge" % (r["n"], a.attempts))]
            print("  capture more attempts before recording a result")
    if a.record and (not problems or problems[0][0] != "info"):
        _record_problems(a, "rate-limiting", problems, a.path, "Sign-in")
    return 0


def _valid(cid):
    if cid not in CHECK_IDS:
        raise SystemExit("no check %r. See: auth.py plan" % cid)


def cmd_result(a):
    _valid(a.check)
    _require_authorised(a.dir)
    _ensure_plan(a.dir)
    rel = _evidence(a.dir, a.evidence)
    if a.outcome == "pass":
        try:
            ledger.mark_coverage(a.dir, SKILL, a.check, "covered", note=a.note, evidence=[rel])
        except ledger.LedgerError as e:
            raise SystemExit("cannot record a pass: %s" % e)
        print("%s %s holds up, evidence recorded." % (ledger.OK, a.check))
        return 0
    if not a.title:
        raise SystemExit("a failing check needs --title saying what went wrong")
    f = ledger.new_finding(SKILL, a.title, a.severity or "high", category="auth:%s" % a.check,
                           evidence=[rel], repro=a.repro, fix=a.fix)
    ledger.write_finding(a.dir, f)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.check, "covered", evidence=[rel])
    except ledger.LedgerError as e:
        raise SystemExit("finding written but coverage failed: %s" % e)
    if a.verify:
        ledger.verify(a.dir, SKILL, f["id"], by="evidence-file")
        print("%s %s: finding %r written and verified." % (ledger.BAD, a.check, f["id"]))
    else:
        print("%s %s: finding %r written (unverified); re-run with --verify once the evidence shows it."
              % (ledger.BAD, a.check, f["id"]))
    return 0


def cmd_na(a):
    _valid(a.check)
    _ensure_plan(a.dir)
    try:
        ledger.mark_coverage(a.dir, SKILL, a.check, "not_applicable", note=a.note)
    except ledger.LedgerError as e:
        raise SystemExit(str(e))
    print("%s %s marked not applicable." % (ledger.OK, a.check))
    return 0


def cmd_status(a):
    cov = ledger.load_coverage(a.dir, SKILL)
    if cov is None:
        print("no auth ledger yet. Run: auth.py plan")
        return 0
    counts = {s: sum(1 for c in cov["checks"] if c["status"] == s) for s in ledger.COVERAGE_STATUSES}
    print("auth checks: %d covered, %d not applicable, %d not run"
          % (counts["covered"], counts["not_applicable"], counts["not_run"]))
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s (%s)" % (f["severity"], f["title"], f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="create the coverage ledger and print every check").set_defaults(func=cmd_plan)
    j = sub.add_parser("jwt", help="decode a captured token (never verified, never printed in full)")
    j.add_argument("path")
    j.add_argument("--max-lifetime", type=float, default=24.0, help="hours an access token may live (default 24)")
    j.add_argument("--record", action="store_true", help="record the result under jwt-hygiene")
    j.set_defaults(func=cmd_jwt)
    c = sub.add_parser("cookies", help="session cookie flags from captured response headers")
    c.add_argument("path")
    c.add_argument("--max-age-days", type=int, default=30, help="longest acceptable cookie lifetime (default 30)")
    c.add_argument("--record", action="store_true", help="record the result under cookie-flags")
    c.set_defaults(func=cmd_cookies)
    e = sub.add_parser("enum", help="compare two responses that should be identical")
    e.add_argument("a")
    e.add_argument("b")
    e.add_argument("--size-tolerance", type=float, default=0.1, help="allowed body size difference (default 0.1)")
    e.add_argument("--record", action="store_true", help="record the result under enumeration")
    e.set_defaults(func=cmd_enum)
    r = sub.add_parser("ratelimit", help="read a capture of repeated attempts")
    r.add_argument("path")
    r.add_argument("--attempts", type=int, default=20, help="limit expected within this many attempts (default 20)")
    r.add_argument("--record", action="store_true", help="record the result under rate-limiting")
    r.set_defaults(func=cmd_ratelimit)
    res = sub.add_parser("result", help="record a check you performed")
    res.add_argument("check")
    res.add_argument("outcome", choices=["pass", "fail"])
    res.add_argument("--evidence", required=True)
    res.add_argument("--note")
    res.add_argument("--title")
    res.add_argument("--severity", choices=ledger.SEVERITIES)
    res.add_argument("--repro")
    res.add_argument("--fix")
    res.add_argument("--verify", action="store_true")
    res.set_defaults(func=cmd_result)
    n = sub.add_parser("na", help="mark a check not applicable")
    n.add_argument("check")
    n.add_argument("--note", required=True)
    n.set_defaults(func=cmd_na)
    sub.add_parser("status", help="coverage and findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
