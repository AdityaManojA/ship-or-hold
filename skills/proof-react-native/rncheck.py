#!/usr/bin/env python3
"""Audit a React Native / Expo project's own source and config for problems that ship. Offline.

Standard library only, no network, read-only: it never builds, installs or runs the
app. It reads package.json, app.json (or app.config.js / .ts, or the resolved config
saved from `npx expo config --json`), eas.json, the native android/ and ios/ folders
when they exist, .env files and the JS/TS source, and reports secrets that end up in
the app bundle, tokens kept in plain AsyncStorage, cleartext traffic, permissions
that do not match the code, debug leftovers, risky deep-link handling, update
settings and Android backups. Every hit names a file and line. Secrets are masked.

    python3 rncheck.py scan .
    python3 rncheck.py scan . --expo-config expo-config.json --doctor doctor.txt --json
    python3 rncheck.py record . --expo-config expo-config.json
    python3 rncheck.py status
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-react-native"

CHECKS = [
    ("secrets-in-bundle", "No secrets in the app bundle"),
    ("token-storage", "Tokens and passwords kept in secure storage"),
    ("cleartext", "No cleartext traffic"),
    ("permissions", "Permissions match what the code uses"),
    ("debug-in-release", "No debug leftovers in release builds"),
    ("deep-links", "Deep links cannot drive the app blindly"),
    ("updates", "Over-the-air updates are pinned and secure"),
    ("android-backup", "Android backups do not copy local data"),
    ("versions", "expo-doctor checks pass"),
    ("tests", "The project has tests"),
]

SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
SKIP_DIRS = {"node_modules", ".expo", "dist", "build", "android", "ios", ".git", "coverage", "web-build",
             ".next", "Pods", "proof"}
LOCAL_HOSTS = ("localhost", "127.0.0.1", "10.0.2.2", "0.0.0.0", "192.168.", "10.0.3.2")

SECRET_NAME = re.compile(r"(SECRET|PRIVATE_KEY|PRIVATEKEY|SERVICE_ROLE|PASSWORD|PASSWD|CLIENT_SECRET|API_SECRET)",
                         re.I)
SECRET_VALUE = [
    (re.compile(r"\bsk_live_[0-9A-Za-z]{8,}"), "a live Stripe secret key"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"), "an OpenAI-style secret key"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "an Anthropic API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "a GitHub token"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "a private key"),
]
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.(eyJ[A-Za-z0-9_-]{10,})\.[A-Za-z0-9_-]{10,}")
PUBLIC_ENV = re.compile(r"process\.env\.(EXPO_PUBLIC_\w+)")
ASYNC_SET = re.compile(r"AsyncStorage\s*\.\s*(?:setItem|multiSet|mergeItem)\s*\(\s*([^\n]{0,120})")
TOKEN_KEY = re.compile(r"token|auth|session|password|passwd|jwt|refresh|credential|secret", re.I)
HTTP_URL = re.compile(r"""['"`](http://[^'"`\s]+)""")
CONSOLE_LOG = re.compile(r"\bconsole\.(?:log|debug)\s*\(")
LINK_IN = re.compile(r"Linking\.(?:getInitialURL|addEventListener|parse)|\buseURL\s*\(|\buseLinkingURL\s*\(")
LINK_OUT = re.compile(r"(?:Linking\.openURL|WebBrowser\.openBrowserAsync|WebBrowser\.openAuthSessionAsync)\s*\(\s*([^)'\"`\s][^)]*)\)")
GENERIC_DESC = re.compile(r"^\s*(?:this )?(?:app|application)?\s*(?:needs|requires|wants|uses)\s+(?:access|permission)"
                          r"(?:\s+to\s+(?:your\s+)?(?:camera|microphone|photos?|location|contacts))?\.?\s*$|^allow\b.{0,20}$",
                          re.I)
DOCTOR_FAIL = re.compile(u"^\\s*(?:\u2716|\u2717|\u00d7|x)\\s+(.+)$")

# capability: (code patterns, android permissions, iOS usage-description keys, expo plugin option keys)
CAPABILITIES = {
    "camera": ([r"from ['\"]expo-camera['\"]", r"launchCameraAsync\s*\(", r"requestCameraPermissionsAsync\s*\(",
                r"react-native-vision-camera"],
               {"CAMERA"}, {"NSCameraUsageDescription"}, {"cameraPermission"}),
    "microphone": ([r"useAudioRecorder\s*\(", r"requestRecordingPermissionsAsync\s*\(", r"Audio\.Recording",
                    r"getUserMedia\s*\(", r"from ['\"]react-native-webrtc['\"]", r"expo-speech-recognition"],
                   {"RECORD_AUDIO"}, {"NSMicrophoneUsageDescription"}, {"microphonePermission", "recordAudioAndroid"}),
    "photos": ([r"launchImageLibraryAsync\s*\(", r"from ['\"]expo-media-library['\"]"],
               set(), {"NSPhotoLibraryUsageDescription", "NSPhotoLibraryAddUsageDescription"},
               {"photosPermission", "savePhotosPermission"}),
    "location": ([r"from ['\"]expo-location['\"]", r"getCurrentPositionAsync\s*\(", r"watchPositionAsync\s*\("],
                 {"ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION"},
                 {"NSLocationWhenInUseUsageDescription", "NSLocationAlwaysAndWhenInUseUsageDescription"},
                 {"locationWhenInUsePermission", "locationAlwaysAndWhenInUsePermission", "locationAlwaysPermission"}),
    "contacts": ([r"from ['\"]expo-contacts['\"]"], {"READ_CONTACTS", "WRITE_CONTACTS"}, {"NSContactsUsageDescription"},
                 {"contactsPermission"}),
    "calendar": ([r"from ['\"]expo-calendar['\"]"], {"READ_CALENDAR", "WRITE_CALENDAR"},
                 {"NSCalendarsUsageDescription", "NSCalendarsFullAccessUsageDescription"}, {"calendarPermission"}),
    "faceid": ([r"from ['\"]expo-local-authentication['\"]"], {"USE_BIOMETRIC", "USE_FINGERPRINT"},
               {"NSFaceIDUsageDescription"}, {"faceIDPermission"}),
}

FIX = {
    "secrets-in-bundle": "Anything in app config extra, EXPO_PUBLIC_* variables or the source ships inside the app and "
                         "can be read by anyone. Move the secret to your backend and call it from there; rotate it.",
    "token-storage": "Keep tokens and passwords in expo-secure-store (Keychain / Keystore), not AsyncStorage, which is "
                     "plain text on the device.",
    "cleartext": "Use https for every API and media URL, and leave cleartext traffic and arbitrary loads off.",
    "permissions": "Ask only for permissions the app uses, and give each iOS permission a specific usage description "
                   "that says why the app needs it.",
    "debug-in-release": "Strip debug logging and development settings from release builds.",
    "deep-links": "Validate a deep link's host, path and parameters against an allow list before navigating or "
                  "opening anything it contains.",
    "updates": "Set a runtimeVersion policy and serve updates over https so an update only reaches a compatible build.",
    "android-backup": "Set android.allowBackup to false (or exclude app data) so local storage is not copied into "
                      "device backups.",
    "versions": "Fix what expo-doctor reports, usually by running npx expo install --check.",
}


def mask(secret):
    """At most the first 4 characters, then stars. Never the whole value."""
    secret = secret.strip().strip("'\"`")
    if len(secret) <= 6:
        return "*" * len(secret)
    return secret[:4] + "*" * min(len(secret) - 4, 12)


def _read(path):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _line_of(text, needle):
    if text is None or not needle:
        return 1
    i = text.find(needle)
    return text.count("\n", 0, i) + 1 if i >= 0 else 1


def _jwt_role(token_payload):
    import base64
    try:
        pad = token_payload + "=" * (-len(token_payload) % 4)
        return json.loads(base64.urlsafe_b64decode(pad.encode("ascii")).decode("utf-8", "replace")).get("role")
    except (ValueError, TypeError, AttributeError):
        return None


def secret_in(text):
    """Return (what, masked) for the first secret-looking value in text, or None."""
    for rx, what in SECRET_VALUE:
        m = rx.search(text)
        if m:
            return what, mask(m.group(0))
    m = JWT.search(text)
    if m and _jwt_role(m.group(1)) == "service_role":
        return "a service_role JWT (full database access)", mask(m.group(0))
    return None


# ------------------------------------------------------------------ config

def _empty_view():
    return {"source": None, "file": None, "text": None, "android_permissions": set(), "ios_descriptions": {},
            "plugin_permissions": {}, "cleartext": False, "arbitrary_loads": False, "scheme": None,
            "runtime_version": False, "updates_url": None, "updates_enabled": None, "allow_backup": None,
            "extra": {}, "platforms": None, "dynamic": False}


def _view_from_json(cfg, path, text):
    v = _empty_view()
    if isinstance(cfg, dict) and isinstance(cfg.get("expo"), dict):
        cfg = cfg["expo"]
    elif isinstance(cfg, dict) and isinstance(cfg.get("exp"), dict):
        cfg = cfg["exp"]
    if not isinstance(cfg, dict):
        return v
    v.update(source="json", file=path, text=text)
    android = cfg.get("android") or {}
    ios = cfg.get("ios") or {}
    for p in android.get("permissions") or []:
        v["android_permissions"].add(str(p).split(".")[-1].upper())
    plist = ios.get("infoPlist") or {}
    for k, val in plist.items():
        if k.endswith("UsageDescription"):
            v["ios_descriptions"][k] = str(val)
    ats = plist.get("NSAppTransportSecurity") or {}
    v["arbitrary_loads"] = bool(ats.get("NSAllowsArbitraryLoads"))
    v["cleartext"] = bool(android.get("usesCleartextTraffic"))
    for plugin in cfg.get("plugins") or []:
        if isinstance(plugin, list) and len(plugin) > 1 and isinstance(plugin[1], dict):
            opts = plugin[1]
            for k, val in opts.items():
                if k.endswith("Permission") or k == "recordAudioAndroid":
                    if val is not False:
                        v["plugin_permissions"][k] = str(val)
            if plugin[0] == "expo-build-properties":
                if (opts.get("android") or {}).get("usesCleartextTraffic"):
                    v["cleartext"] = True
    scheme = cfg.get("scheme")
    v["scheme"] = scheme if isinstance(scheme, str) else (scheme[0] if isinstance(scheme, list) and scheme else None)
    v["runtime_version"] = cfg.get("runtimeVersion") is not None
    updates = cfg.get("updates") or {}
    v["updates_url"] = updates.get("url")
    v["updates_enabled"] = updates.get("enabled")
    if "allowBackup" in android:
        v["allow_backup"] = bool(android.get("allowBackup"))
    v["extra"] = cfg.get("extra") or {}
    v["platforms"] = cfg.get("platforms")
    return v


def _view_from_js(path, text):
    """Pull the obvious literals out of app.config.js / .ts. Dynamic values are not evaluated."""
    v = _empty_view()
    v.update(source="js", file=path, text=text, dynamic=True)
    for m in re.finditer(r"permissions\s*:\s*\[([^\]]*)\]", text):
        for p in re.findall(r"['\"]([\w.]+)['\"]", m.group(1)):
            v["android_permissions"].add(p.split(".")[-1].upper())
    for k, val in re.findall(r"(NS\w+UsageDescription)['\"]?\s*:\s*['\"`]([^'\"`]*)", text):
        v["ios_descriptions"][k] = val
    for k, val in re.findall(r"(\w+Permission|recordAudioAndroid)['\"]?\s*:\s*['\"`]([^'\"`]*)", text):
        v["plugin_permissions"][k] = val
    v["cleartext"] = bool(re.search(r"usesCleartextTraffic['\"]?\s*:\s*true", text))
    v["arbitrary_loads"] = bool(re.search(r"NSAllowsArbitraryLoads['\"]?\s*:\s*true", text))
    m = re.search(r"\bscheme['\"]?\s*:\s*['\"`]([\w.+-]+)", text)
    v["scheme"] = m.group(1) if m else None
    v["runtime_version"] = bool(re.search(r"\bruntimeVersion\b", text))
    m = re.search(r"\burl['\"]?\s*:\s*['\"`](https?://[^'\"`]*u\.expo\.dev[^'\"`]*|http://[^'\"`]*)", text)
    v["updates_url"] = m.group(1) if m else None
    m = re.search(r"allowBackup['\"]?\s*:\s*(true|false)", text)
    v["allow_backup"] = (m.group(1) == "true") if m else None
    return v


def load_view(root, expo_config=None):
    if expo_config:
        text = _read(expo_config)
        try:
            return _view_from_json(json.loads(text or ""), expo_config, text)
        except ValueError:
            raise SystemExit("%s is not JSON; save it with: npx expo config --json > %s" % (expo_config, expo_config))
    path = os.path.join(root, "app.json")
    text = _read(path)
    if text is not None:
        try:
            v = _view_from_json(json.loads(text), path, text)
        except ValueError:
            v = _empty_view()
        for name in ("app.config.js", "app.config.ts"):
            js = os.path.join(root, name)
            if os.path.isfile(js):
                v["dynamic"] = True  # app.config.* can change what app.json says
        return v
    for name in ("app.config.js", "app.config.ts", "app.config.mjs"):
        js = os.path.join(root, name)
        jtext = _read(js)
        if jtext is not None:
            return _view_from_js(js, jtext)
    return _empty_view()


# ------------------------------------------------------------------ scan

def _source_files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            if os.path.splitext(n)[1].lower() in SOURCE_EXT:
                yield os.path.join(base, n)


def _is_test(rel):
    return "__tests__/" in rel or re.search(r"\.(test|spec)\.[jt]sx?$", rel) or rel.startswith(("e2e/", "test/", "tests/"))


def scan(root, expo_config=None, doctor=None):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise SystemExit("no project folder at %s" % root)
    pkg_text = _read(os.path.join(root, "package.json"))
    try:
        pkg = json.loads(pkg_text) if pkg_text else {}
    except ValueError:
        pkg = {}
    deps = dict(pkg.get("dependencies") or {}, **(pkg.get("devDependencies") or {}))
    view = load_view(root, expo_config)
    cfg_rel = _rel(root, view["file"]) if view["file"] and os.path.abspath(view["file"]).startswith(root) else (
        os.path.basename(view["file"]) if view["file"] else None)
    hits = []
    na = {}

    def hit(check, sev, title, rel, line, text=""):
        hits.append({"check": check, "severity": sev, "title": title, "file": rel, "line": line,
                     "text": " ".join(str(text).split())[:140]})

    # Source
    sources, tests, logs = [], [], []
    link_in_files, uses_storage = [], False
    caps_used = {}
    for path in _source_files(root):
        rel = _rel(root, path)
        text = _read(path) or ""
        if _is_test(rel):
            tests.append(rel)
            continue
        sources.append(rel)
        lines = text.splitlines()
        for n, line in enumerate(lines, 1):
            m = PUBLIC_ENV.search(line)
            if m and SECRET_NAME.search(m.group(1)):
                hit("secrets-in-bundle", "high", "Secret-named public env var %s is compiled into the app" % m.group(1),
                    rel, n, m.group(1))
            s = secret_in(line)
            if s:
                hit("secrets-in-bundle", "high", "Source contains %s" % s[0], rel, n, s[1])
            m = ASYNC_SET.search(line)
            if m:
                uses_storage = True
                if TOKEN_KEY.search(m.group(1)):
                    hit("token-storage", "high", "A token or password is written to AsyncStorage", rel, n,
                        "AsyncStorage." + m.group(0).split(".", 1)[1][:80])
            if "SecureStore." in line or "AsyncStorage." in line:
                uses_storage = True
            for url in HTTP_URL.findall(line):
                host = url[7:]
                if not host.startswith(LOCAL_HOSTS) and "www.w3.org" not in host and "schemas." not in host:
                    hit("cleartext", "high" if re.search(r"api|backend|server", url, re.I) else "medium",
                        "Plain http URL in the app: %s" % url[:60], rel, n, url)
            if CONSOLE_LOG.search(line):
                logs.append((rel, n))
        if LINK_IN.search(text):
            link_in_files.append(rel)
            m = LINK_OUT.search(text)
            if m:
                hit("deep-links", "medium", "Deep-link handler opens a URL taken from its input", rel,
                    _line_of(text, m.group(0)), m.group(0)[:100])
        for cap, (pats, _, _, _) in CAPABILITIES.items():
            for p in pats:
                m = re.search(p, text)
                if m:
                    caps_used.setdefault(cap, (rel, _line_of(text, m.group(0))))
                    break

    # .env files: EXPO_PUBLIC_* values are bundled
    for name in sorted(os.listdir(root)):
        if not name.startswith(".env"):
            continue
        text = _read(os.path.join(root, name)) or ""
        for n, line in enumerate(text.splitlines(), 1):
            m = re.match(r"\s*(EXPO_PUBLIC_\w+)\s*=\s*(.*)$", line)
            if not m:
                continue
            s = secret_in(m.group(2))
            if SECRET_NAME.search(m.group(1)) or s:
                hit("secrets-in-bundle", "high", "%s in %s is bundled into the app%s" % (
                    m.group(1), name, (" and looks like " + s[0]) if s else ""), name, n,
                    "%s=%s" % (m.group(1), mask(m.group(2))))

    # Config
    if view["file"]:
        ctext = view["text"]

        def walk(obj, path=""):
            if isinstance(obj, dict):
                for k, val in obj.items():
                    walk(val, "%s.%s" % (path, k) if path else k)
            elif isinstance(obj, list):
                for i, val in enumerate(obj):
                    walk(val, "%s[%d]" % (path, i))
            elif isinstance(obj, str):
                s = secret_in(obj)
                key = path.split(".")[-1]
                if s or (SECRET_NAME.search(key) and len(obj) >= 8):
                    hit("secrets-in-bundle", "high", "App config extra.%s is shipped in the bundle%s" % (
                        path, (" and looks like " + s[0]) if s else ""), cfg_rel, _line_of(ctext, key), mask(obj))
        walk(view["extra"])
        if view["source"] == "js":
            for n, line in enumerate((ctext or "").splitlines(), 1):
                s = secret_in(line)
                if s:
                    hit("secrets-in-bundle", "high", "App config contains %s" % s[0], cfg_rel, n, s[1])
        if view["cleartext"]:
            hit("cleartext", "medium", "Android cleartext traffic is allowed", cfg_rel,
                _line_of(ctext, "usesCleartextTraffic"), "usesCleartextTraffic: true")
        if view["arbitrary_loads"]:
            hit("cleartext", "medium", "iOS App Transport Security is off (NSAllowsArbitraryLoads)", cfg_rel,
                _line_of(ctext, "NSAllowsArbitraryLoads"), "NSAllowsArbitraryLoads: true")

    # Native folders
    manifest = os.path.join(root, "android", "app", "src", "main", "AndroidManifest.xml")
    mtext = _read(manifest)
    if mtext:
        mrel = _rel(root, manifest)
        for m in re.finditer(r'android:name="android\.permission\.(\w+)"', mtext):
            view["android_permissions"].add(m.group(1))
        if 'android:usesCleartextTraffic="true"' in mtext:
            hit("cleartext", "medium", "Android cleartext traffic is allowed", mrel,
                _line_of(mtext, "usesCleartextTraffic"), 'android:usesCleartextTraffic="true"')
        if 'android:debuggable="true"' in mtext:
            hit("debug-in-release", "medium", "The Android manifest sets debuggable", mrel,
                _line_of(mtext, "debuggable"), 'android:debuggable="true"')
        m = re.search(r'android:allowBackup="(true|false)"', mtext)
        if m:
            view["allow_backup"] = m.group(1) == "true"
    for base, dirs, names in os.walk(os.path.join(root, "ios")):
        dirs[:] = [d for d in dirs if d not in ("Pods", "build")]
        if "Info.plist" in names:
            ptext = _read(os.path.join(base, "Info.plist")) or ""
            prel = _rel(root, os.path.join(base, "Info.plist"))
            for k, val in re.findall(r"<key>(NS\w+UsageDescription)</key>\s*<string>([^<]*)</string>", ptext):
                view["ios_descriptions"].setdefault(k, val)
            if re.search(r"<key>NSAllowsArbitraryLoads</key>\s*<true/>", ptext):
                hit("cleartext", "medium", "iOS App Transport Security is off (NSAllowsArbitraryLoads)", prel,
                    _line_of(ptext, "NSAllowsArbitraryLoads"), "NSAllowsArbitraryLoads: true")

    # Permissions against the code
    platforms = view["platforms"]
    targets_ios = platforms is None or "ios" in platforms
    descs = dict(view["plugin_permissions"])
    descs.update(view["ios_descriptions"])
    if view["file"] or mtext:
        for cap, (_, andro, ios_keys, plugin_keys) in CAPABILITIES.items():
            used = caps_used.get(cap)
            have_ios = [k for k in ios_keys | plugin_keys if k in descs]
            declared_android = sorted(andro & view["android_permissions"])
            if used and targets_ios and ios_keys and not have_ios:
                hit("permissions", "high", "Uses the %s with no iOS usage description (App Review rejects it, iOS "
                    "stops the app)" % cap, used[0], used[1], ", ".join(sorted(ios_keys)))
            if not used and (declared_android or have_ios):
                where = declared_android or have_ios
                hit("permissions", "low", "Declares %s access the code never uses: %s" % (cap, ", ".join(where)),
                    cfg_rel or (mtext and _rel(root, manifest)) or "app.json",
                    _line_of(view["text"], where[0]), ", ".join(where))
        for k, text in sorted(descs.items()):
            if text and GENERIC_DESC.search(text):
                hit("permissions", "low", "Usage description %s does not say why: \"%s\"" % (k, text[:60]),
                    cfg_rel or "app.json", _line_of(view["text"], k), text)
    else:
        na["permissions"] = "no app.json, app.config or native manifest found"

    # Debug leftovers
    if logs:
        hit("debug-in-release", "low", "%d console.log call(s) in app code ship to users" % len(logs), logs[0][0],
            logs[0][1], ", ".join("%s:%d" % x for x in logs[:5]))
    eas_text = _read(os.path.join(root, "eas.json"))
    if eas_text:
        try:
            eas = json.loads(eas_text)
        except ValueError:
            eas = {}
        for name, prof in (eas.get("build") or {}).items():
            if isinstance(prof, dict) and prof.get("developmentClient") and prof.get("distribution") == "store":
                hit("debug-in-release", "medium", "EAS profile %s ships a development client to the store" % name,
                    "eas.json", _line_of(eas_text, '"%s"' % name), name)

    # Deep links
    if not view["scheme"] and not link_in_files:
        na["deep-links"] = "no URL scheme and no deep-link handler in the code"

    # Updates
    if "expo-updates" in deps or view["updates_url"]:
        if view["updates_url"] and str(view["updates_url"]).startswith("http://"):
            hit("updates", "medium", "Updates are fetched over plain http", cfg_rel or "app.json",
                _line_of(view["text"], "url"), view["updates_url"])
        if view["updates_enabled"] is not False and not view["runtime_version"]:
            hit("updates", "medium", "expo-updates is on with no runtimeVersion, so an update can reach an "
                "incompatible build", cfg_rel or "package.json", _line_of(view["text"], "updates"), "runtimeVersion")
    else:
        na["updates"] = "expo-updates is not used"

    # Android backup
    has_android = (platforms is None or "android" in platforms)
    if not has_android:
        na["android-backup"] = "the app does not target Android"
    elif not uses_storage:
        na["android-backup"] = "the code keeps nothing in local storage"
    elif view["allow_backup"] is not False:
        hit("android-backup", "low", "Android backup is not turned off while the app stores data on the device",
            cfg_rel or "app.json", _line_of(view["text"], "android"), "allowBackup")

    # expo-doctor
    doctor_rel = None
    if doctor:
        dtext = _read(doctor)
        if dtext is None:
            raise SystemExit("cannot read %s" % doctor)
        doctor_rel = doctor
        for n, line in enumerate(dtext.splitlines(), 1):
            m = DOCTOR_FAIL.match(line)
            if m:
                hit("versions", "medium", "expo-doctor: %s" % m.group(1).strip()[:120], os.path.basename(doctor), n,
                    m.group(1))
    else:
        na["versions"] = "no expo-doctor output given (save it with: npx expo-doctor > doctor.txt)"

    facts = {"source_files": len(sources), "test_files": len(tests),
             "jest": "jest" in deps or "jest-expo" in deps, "config": cfg_rel,
             "config_dynamic": view["dynamic"], "expo": deps.get("expo"), "react-native": deps.get("react-native"),
             "capabilities_used": sorted(caps_used), "scheme": view["scheme"]}
    return {"root": root, "hits": hits, "not_applicable": na, "facts": facts, "doctor": doctor_rel}


# ---------------------------------------------------------------- commands

SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def cmd_scan(a):
    r = scan(a.path, a.expo_config, a.doctor)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    f = r["facts"]
    print("React Native project: %d source file(s), %d test file(s), config %s%s" % (
        f["source_files"], f["test_files"], f["config"] or "not found",
        " (app.config is code; dynamic values were not evaluated, pass --expo-config)" if f["config_dynamic"] else ""))
    for cid, title in CHECKS:
        found = [h for h in r["hits"] if h["check"] == cid]
        if cid in r["not_applicable"]:
            print("  -- %s: not applicable (%s)" % (title, r["not_applicable"][cid]))
            continue
        if cid == "tests":
            print("  %s %s: %d test file(s)%s" % (ledger.OK if f["test_files"] else ledger.BAD, title, f["test_files"],
                                                  ", jest configured" if f["jest"] else ""))
            continue
        print("  %s %s%s" % (ledger.BAD if found else ledger.OK, title, (": %d" % len(found)) if found else ""))
        for h in sorted(found, key=lambda h: SEV_ORDER[h["severity"]])[:a.show]:
            print("      [%s] %s  %s:%d" % (h["severity"], h["title"], h["file"], h["line"]))
    return 0


def cmd_record(a):
    r = scan(a.path, a.expo_config, a.doctor)
    ledger.init_coverage(a.dir, SKILL, CHECKS)
    ev_path = os.path.join(ledger.evidence_dir(a.dir, SKILL), "rn-scan.json")
    with open(ev_path, "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=2)
    ev = os.path.relpath(ev_path, ledger.root(a.dir)).replace(os.sep, "/")
    for cid, title in CHECKS:
        if cid in r["not_applicable"]:
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note=r["not_applicable"][cid])
            continue
        if cid == "tests":
            ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[ev],
                                 note="%d test file(s)%s" % (r["facts"]["test_files"],
                                                             ", jest configured" if r["facts"]["jest"] else ""))
            continue
        n = sum(1 for h in r["hits"] if h["check"] == cid)
        ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[ev], note="%d hit(s)" % n)
    for h in r["hits"]:
        fid = ledger.slug("rn-%s-%s-%d-%s" % (h["check"], h["file"], h["line"], h["title"]))[:60]
        f = ledger.new_finding(SKILL, h["title"][:160], h["severity"], id=fid, category="react-native:%s" % h["check"],
                               location="%s:%d" % (h["file"], h["line"]), evidence=[ev], fix=FIX.get(h["check"]),
                               description=h["text"] or None)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    high = sum(1 for h in r["hits"] if h["severity"] == "high")
    print("%s recorded a React Native audit: %d finding(s), %d high. Evidence: %s" % (
        ledger.OK if not r["hits"] else ledger.BAD, len(r["hits"]), high, ev))
    return 0


def cmd_status(a):
    for f in ledger.load_findings(a.dir, SKILL):
        print("  [%s] %s  %s (%s)" % (f["severity"], f["title"], f.get("location", ""), f["status"]))
    return 0


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="proof")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, func, hlp in (("scan", cmd_scan, "print what was found"), ("record", cmd_record, "write it to the ledger")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("path", help="the React Native / Expo project folder")
        p.add_argument("--expo-config", help="the resolved config saved with: npx expo config --json > expo-config.json")
        p.add_argument("--doctor", help="output saved with: npx expo-doctor > doctor.txt")
        if name == "scan":
            p.add_argument("--json", action="store_true")
            p.add_argument("--show", type=int, default=5, help="hits to print per check")
        p.set_defaults(func=func)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
