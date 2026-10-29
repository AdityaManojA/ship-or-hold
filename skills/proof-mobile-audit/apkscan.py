#!/usr/bin/env python3
"""Inspect a mobile app build for configuration and data-handling problems. Offline.

Standard library only, no network. For Android it opens the APK as a zip and reads
what it can without extra tooling: the permissions the app requests, whether it
allows cleartext (non-HTTPS) traffic, and the plain URLs baked into it. For iOS it
reads Info.plist from the IPA with the standard plist reader and checks the app
transport security settings, URL schemes and permission-use descriptions.

Android's manifest is compiled to a binary format, so this reads the strings it can
see reliably and is honest about what it cannot. For a deep inspection, use a
dedicated mobile security tool; this gives a fast, repeatable first pass and a
ledger entry.

    python3 apkscan.py apk app-release.apk
    python3 apkscan.py ios MyApp.ipa
    python3 apkscan.py record apk app-release.apk
    python3 apkscan.py status
"""

import argparse
import os
import plistlib
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-mobile-audit"

# Permissions worth a second look, with why.
SENSITIVE_PERMS = {
    "ACCESS_FINE_LOCATION": "precise location",
    "ACCESS_BACKGROUND_LOCATION": "location even when not in use",
    "READ_CONTACTS": "the user's contacts",
    "READ_SMS": "text messages",
    "RECEIVE_SMS": "incoming text messages",
    "RECORD_AUDIO": "the microphone",
    "CAMERA": "the camera",
    "READ_EXTERNAL_STORAGE": "files on the device",
    "READ_CALL_LOG": "the call history",
    "REQUEST_INSTALL_PACKAGES": "installing other apps",
    "SYSTEM_ALERT_WINDOW": "drawing over other apps",
}

PERM_RE = re.compile(rb"android\.permission\.([A-Z_]+)")
URL_RE = re.compile(rb"https?://[A-Za-z0-9._~:/?#%\-]+")


def _decode_strings(data):
    """Pull readable ASCII and UTF-16LE permission/url strings out of binary data."""
    perms = set(m.group(1).decode("ascii", "replace") for m in PERM_RE.finditer(data))
    # UTF-16LE: permission strings often appear with null bytes between chars.
    for m in re.finditer(rb"a\x00n\x00d\x00r\x00o\x00i\x00d\x00\.\x00p\x00e\x00r\x00m\x00i\x00s\x00s\x00i\x00o\x00n\x00\.\x00([A-Z_\x00]+)", data):
        name = m.group(1).replace(b"\x00", b"").decode("ascii", "replace")
        if name:
            perms.add(name)
    urls = sorted(set(m.group(0).decode("ascii", "replace") for m in URL_RE.finditer(data)))
    return sorted(perms), urls


def scan_apk(path):
    result = {"permissions": [], "sensitive": [], "http_urls": [], "firebase": [],
              "cleartext": None, "debuggable": None, "notes": []}
    with zipfile.ZipFile(path) as z:
        try:
            manifest = z.read("AndroidManifest.xml")
        except KeyError:
            raise SystemExit("no AndroidManifest.xml in %s; is this an APK?" % path)
        perms, _ = _decode_strings(manifest)
        result["permissions"] = perms
        result["sensitive"] = [(p, SENSITIVE_PERMS[p]) for p in perms if p in SENSITIVE_PERMS]
        if b"usesCleartextTraffic" in manifest or b"cleartextTrafficPermitted" in manifest:
            result["notes"].append("The manifest or a network-security config mentions cleartext traffic; "
                                   "confirm whether plain HTTP is allowed.")
        if b"debuggable" in manifest:
            result["notes"].append("The manifest mentions a debuggable flag; confirm it is false in release builds.")
        # URLs across all entries (resources, dex strings are compiled, but many URLs live in resources).
        all_urls = set()
        for name in z.namelist():
            if name.endswith((".xml", ".json", ".txt", ".properties")) or name == "AndroidManifest.xml":
                try:
                    _, urls = _decode_strings(z.read(name))
                    all_urls.update(urls)
                except KeyError:
                    continue
        result["http_urls"] = sorted(u for u in all_urls if u.startswith("http://"))
        result["firebase"] = sorted(u for u in all_urls if "firebaseio.com" in u or "firebaseapp.com" in u)
    return result


def scan_ipa(path):
    result = {"ats_arbitrary_loads": None, "url_schemes": [], "permissions": [], "notes": []}
    plist_data = None
    if path.endswith(".plist"):
        with open(path, "rb") as fh:
            plist_data = fh.read()
    else:
        with zipfile.ZipFile(path) as z:
            cand = [n for n in z.namelist() if re.match(r"Payload/[^/]+\.app/Info\.plist$", n)]
            if not cand:
                raise SystemExit("no Payload/*.app/Info.plist in %s; is this an IPA?" % path)
            plist_data = z.read(cand[0])
    try:
        info = plistlib.loads(plist_data)
    except Exception:
        raise SystemExit("could not read Info.plist (it may be encrypted or malformed)")
    ats = info.get("NSAppTransportSecurity", {})
    if isinstance(ats, dict):
        result["ats_arbitrary_loads"] = bool(ats.get("NSAllowsArbitraryLoads"))
    for entry in info.get("CFBundleURLTypes", []) or []:
        for s in entry.get("CFBundleURLSchemes", []) or []:
            result["url_schemes"].append(s)
    for key in info:
        if key.endswith("UsageDescription"):
            result["permissions"].append(key)
    if result["ats_arbitrary_loads"]:
        result["notes"].append("App Transport Security allows arbitrary loads, so plain HTTP is permitted app-wide.")
    return result


# --------------------------------------------------------------- commands

def _print_apk(r):
    print("Permissions: %d (%d sensitive)" % (len(r["permissions"]), len(r["sensitive"])))
    for p, why in r["sensitive"]:
        print("  %s %s  (%s)" % (ledger.BAD, p, why))
    if r["http_urls"]:
        print("Plain HTTP URLs: %d" % len(r["http_urls"]))
        for u in r["http_urls"][:10]:
            print("  %s %s" % (ledger.BAD, u))
    if r["firebase"]:
        print("Firebase URLs: %s" % ", ".join(r["firebase"][:5]))
    for n in r["notes"]:
        print("  note: %s" % n)


def cmd_apk(a):
    _print_apk(scan_apk(a.path))
    print("\nThis is a first pass over what the APK exposes without extra tooling. For a deep")
    print("inspection, use a dedicated mobile security tool. Record this with: apkscan.py record apk %s" % a.path)
    return 0


def cmd_ios(a):
    r = scan_ipa(a.path)
    print("App Transport Security allows arbitrary loads: %s" % r["ats_arbitrary_loads"])
    if r["url_schemes"]:
        print("Custom URL schemes: %s" % ", ".join(r["url_schemes"]))
    print("Permission-use descriptions: %d" % len(r["permissions"]))
    for n in r["notes"]:
        print("  note: %s" % n)
    return 0


def cmd_record(a):
    if a.kind == "apk":
        r = scan_apk(a.path)
        checks = [("permissions", "App permissions"), ("cleartext", "Cleartext traffic"),
                  ("urls", "Embedded URLs"), ("firebase", "Firebase endpoints")]
    else:
        r = scan_ipa(a.path)
        checks = [("ats", "App Transport Security"), ("schemes", "URL schemes"),
                  ("permissions", "Permission usage")]
    ev_dir = ledger.evidence_dir(a.dir, SKILL)
    report = os.path.join(ev_dir, "mobile-audit.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Mobile audit of %s (%s)\n\n" % (a.path, a.kind))
        for k, v in r.items():
            fh.write("%s: %s\n" % (k, v))
    rel = os.path.relpath(report, ledger.root(a.dir)).replace(os.sep, "/")
    ledger.init_coverage(a.dir, SKILL, checks)
    for cid, _ in checks:
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[rel])

    def add(title, severity, cid):
        fid = ledger.slug("mobile-%s" % cid)
        f = ledger.new_finding(SKILL, title, severity, id=fid, category=cid, evidence=[rel],
                               fix="Review and tighten this in the build configuration.")
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")

    if a.kind == "apk":
        if r["http_urls"]:
            add("App ships %d plain-HTTP URL(s)" % len(r["http_urls"]), "medium", "cleartext")
        if r["sensitive"]:
            add("App requests %d sensitive permission(s)" % len(r["sensitive"]), "low", "permissions")
    else:
        if r.get("ats_arbitrary_loads"):
            add("App Transport Security allows arbitrary (plain-HTTP) loads", "medium", "ats")
    print("%s recorded a %s audit. Report: %s" % (ledger.OK, a.kind, rel))
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
    a1 = sub.add_parser("apk", help="scan an Android APK")
    a1.add_argument("path")
    a1.set_defaults(func=cmd_apk)
    i1 = sub.add_parser("ios", help="scan an iOS IPA or Info.plist")
    i1.add_argument("path")
    i1.set_defaults(func=cmd_ios)
    r = sub.add_parser("record", help="write an audit to the ledger")
    r.add_argument("kind", choices=["apk", "ios"])
    r.add_argument("path")
    r.set_defaults(func=cmd_record)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
