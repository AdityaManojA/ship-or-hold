#!/usr/bin/env python3
"""Catch App Store and Google Play rejection risks before submitting. Offline.

Standard library only, no network, read-only. Reads an Expo / React Native project
(app.json, app.config.* literals, or the resolved config saved from
`npx expo config --json`, eas.json, package.json, the source) or a native project
(AndroidManifest.xml, build.gradle(.kts), Info.plist, PrivacyInfo.xcprivacy,
Podfile), plus optional saved files: a privacy manifest, a Play Data Safety CSV
export and a folder of store screenshots. Reports the things review teams reject
builds for: no in-app account deletion, third-party login without Sign in with
Apple, a target SDK below Play's floor, a missing or incomplete privacy manifest,
tracking SDKs that the privacy declarations do not mention, version numbers,
export compliance, icons and screenshot sizes. Every hit names a file and line.

    python3 store.py scan .
    python3 store.py scan . --expo-config expo-config.json --data-safety data-safety.csv --assets store/
    python3 store.py record . --expo-config expo-config.json --assets store/
    python3 store.py status
"""

import argparse
import csv
import io
import json
import os
import plistlib
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-store-ready"

# The lowest targetSdkVersion Google Play accepts for new apps and updates. Play raises
# it every year (API 35 from 31 August 2025); pass --min-target-sdk when it moves.
MIN_TARGET_SDK = 35

CHECKS = [
    ("account-deletion", "Accounts can be deleted inside the app"),
    ("sign-in-with-apple", "Third-party login on iOS also offers Sign in with Apple"),
    ("target-sdk", "Android targets the API level Google Play requires"),
    ("privacy-manifest", "iOS privacy manifest declares the required-reason APIs used"),
    ("tracking-sdks", "Analytics, crash and ads SDKs are in the privacy declarations"),
    ("versioning", "Version and build numbers are set"),
    ("export-compliance", "Export compliance (encryption) is answered"),
    ("icons", "App icons meet the store rules"),
    ("screenshots", "Store screenshots come in the required sizes"),
    ("permissions", "Permissions match the code and carry usage text"),
]

REFS = {
    "account-deletion": "App Store Review Guideline 5.1.1(v) (account deletion); Google Play User Data policy "
                        "(account deletion requirement)",
    "sign-in-with-apple": "App Store Review Guideline 4.8 Login Services",
    "target-sdk": "Google Play target API level requirement",
    "privacy-manifest": "Apple privacy manifest and required reason APIs (ITMS-91053)",
    "tracking-sdks": "App Store Review Guideline 5.1.2 and App Privacy details; Google Play Data safety section",
    "versioning": "App Store Connect build numbers; Google Play versionCode",
    "export-compliance": "App Store Connect export compliance (ITSAppUsesNonExemptEncryption)",
    "icons": "App Store app icon requirements (1024 x 1024, no alpha); Android adaptive icons",
    "screenshots": "App Store Connect screenshot specifications; Google Play screenshot requirements",
}

FIX = {
    "account-deletion": "Add a Delete account action in the app (settings or profile) that deletes the account "
                        "and its data, not only signs out, and link a web deletion page in Play Console.",
    "sign-in-with-apple": "Offer Sign in with Apple (expo-apple-authentication) next to the other login buttons "
                          "on iOS, with equal prominence.",
    "target-sdk": "Raise targetSdkVersion (expo-build-properties android.targetSdkVersion, or the Gradle "
                  "config) and test the behaviour changes of that Android version.",
    "privacy-manifest": "Add a privacy manifest (ios.privacyManifests in app.json, or PrivacyInfo.xcprivacy) "
                        "that lists every required-reason API the app and its libraries use, with a reason.",
    "tracking-sdks": "Declare what each SDK collects in the privacy manifest (NSPrivacyCollectedDataTypes), "
                     "App Store Connect App Privacy and the Play Data safety form, or remove the SDK.",
    "versioning": "Set version, ios.buildNumber and android.versionCode as plain numbers, or let EAS manage "
                  "them (appVersionSource remote with autoIncrement).",
    "export-compliance": "Set ios.config.usesNonExemptEncryption (or ITSAppUsesNonExemptEncryption) to false "
                         "if the app only uses standard HTTPS, so each upload does not stop for the question.",
    "icons": "Export the App Store icon at 1024 x 1024 without an alpha channel, and set an Android adaptive "
             "icon (android.adaptiveIcon foreground and background).",
    "screenshots": "Export screenshots at the sizes App Store Connect and Play Console ask for.",
}

SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".swift", ".m", ".mm", ".kt", ".java", ".dart"}
SKIP_DIRS = {"node_modules", ".expo", "dist", "build", ".git", "coverage", "web-build", ".next", "Pods",
             "proof", ".gradle", "DerivedData"}

AUTH_DEPS = ("expo-auth-session", "@react-native-google-signin/google-signin", "expo-apple-authentication",
             "@react-native-firebase/auth", "firebase", "@supabase/supabase-js", "aws-amplify", "@clerk/clerk-expo",
             "react-native-auth0", "@invertase/react-native-apple-authentication", "react-native-fbsdk-next")
AUTH_CODE = re.compile(r"\bsign[\s_-]?up\b|\bsignUp\w*\s*\(|createUserWithEmailAndPassword|auth\.signUp|"
                       r"/(?:auth/)?register\b|\bcreate (?:an )?account\b", re.I)
DELETE_CODE = re.compile(r"delete[\s_-]?(?:my |your |the )?account|deleteAccount|delete_account|deleteUser\s*\(|"
                         r"account/delete|/(?:me|account|user|users/me)['\"`]?\s*,\s*\{[^}]*method\s*:\s*['\"]DELETE|"
                         r"remove[\s_-]?account|close[\s_-]?(?:my )?account", re.I)
THIRD_PARTY_DEPS = {"@react-native-google-signin/google-signin": "Google",
                    "react-native-fbsdk-next": "Facebook", "react-native-fbsdk": "Facebook",
                    "@react-native-seoul/kakao-login": "Kakao", "react-native-twitter-signin": "Twitter"}
THIRD_PARTY_CODE = re.compile(r"expo-auth-session/providers/(google|facebook)|GoogleSignin\.|"
                              r"signInWithOAuth\s*\(\s*\{\s*provider\s*:\s*['\"](google|github|facebook|twitter|discord)",
                              re.I)
APPLE_DEPS = ("expo-apple-authentication", "@invertase/react-native-apple-authentication")
APPLE_CODE = re.compile(r"AuthenticationServices|ASAuthorizationAppleIDProvider|AppleAuthentication\.|"
                        r"provider\s*:\s*['\"]apple['\"]")

# Required-reason API categories and what in an app's code or dependencies uses them.
REASON_APIS = {
    "NSPrivacyAccessedAPICategoryUserDefaults": (
        ("@react-native-async-storage/async-storage", "react-native-mmkv", "react-native-default-preference"),
        re.compile(r"\bAsyncStorage\b|\bUserDefaults\b|NSUserDefaults")),
    "NSPrivacyAccessedAPICategoryFileTimestamp": (
        ("react-native-fs", "react-native-blob-util"),
        re.compile(r"modificationTime|\.stat\s*\(|creationDate|getInfoAsync\s*\(")),
    "NSPrivacyAccessedAPICategorySystemBootTime": (
        (), re.compile(r"systemUptime|mach_absolute_time|getStartupTime|SystemClock\.elapsedRealtime")),
    "NSPrivacyAccessedAPICategoryDiskSpace": (
        (), re.compile(r"getFreeDiskStorage\w*\s*\(|getTotalDiskCapacity\w*\s*\(|volumeAvailableCapacity")),
}

# SDK: (label, Apple collected data types it implies, Play Data safety words it implies)
SDKS = {
    "posthog": ("PostHog analytics", ["NSPrivacyCollectedDataTypeProductInteraction"], ["app interactions"]),
    "@sentry": ("Sentry crash reporting", ["NSPrivacyCollectedDataTypeCrashData"], ["crash logs"]),
    "sentry": ("Sentry crash reporting", ["NSPrivacyCollectedDataTypeCrashData"], ["crash logs"]),
    "@react-native-firebase/analytics": ("Firebase Analytics", ["NSPrivacyCollectedDataTypeProductInteraction"],
                                         ["app interactions"]),
    "firebase-analytics": ("Firebase Analytics", ["NSPrivacyCollectedDataTypeProductInteraction"], ["app interactions"]),
    "@react-native-firebase/crashlytics": ("Crashlytics", ["NSPrivacyCollectedDataTypeCrashData"], ["crash logs"]),
    "amplitude": ("Amplitude analytics", ["NSPrivacyCollectedDataTypeProductInteraction"], ["app interactions"]),
    "mixpanel": ("Mixpanel analytics", ["NSPrivacyCollectedDataTypeProductInteraction"], ["app interactions"]),
    "@segment": ("Segment analytics", ["NSPrivacyCollectedDataTypeProductInteraction"], ["app interactions"]),
    "appsflyer": ("AppsFlyer attribution", ["NSPrivacyCollectedDataTypeAdvertisingData",
                                            "NSPrivacyCollectedDataTypeDeviceID"], ["device or other ids"]),
    "google-mobile-ads": ("AdMob ads", ["NSPrivacyCollectedDataTypeAdvertisingData",
                                        "NSPrivacyCollectedDataTypeDeviceID"], ["device or other ids"]),
    "play-services-ads": ("AdMob ads", ["NSPrivacyCollectedDataTypeAdvertisingData",
                                        "NSPrivacyCollectedDataTypeDeviceID"], ["device or other ids"]),
    "fbsdk": ("Facebook SDK", ["NSPrivacyCollectedDataTypeAdvertisingData", "NSPrivacyCollectedDataTypeDeviceID"],
              ["device or other ids"]),
    "facebook-android-sdk": ("Facebook SDK", ["NSPrivacyCollectedDataTypeAdvertisingData",
                                              "NSPrivacyCollectedDataTypeDeviceID"], ["device or other ids"]),
}

IPHONE_SIZES = {(1320, 2868), (1290, 2796), (1284, 2778), (1242, 2688)}
IPAD_SIZES = {(2064, 2752), (2048, 2732)}


# ------------------------------------------------------------------ files

def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _rel(root, path):
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def _line_of(text, needle):
    if not text or not needle:
        return 1
    i = text.find(needle)
    return text.count("\n", 0, i) + 1 if i >= 0 else 1


def image_size(path):
    """(width, height, has_alpha) for a PNG or JPEG, or None."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 26:
        w, h = struct.unpack(">II", data[16:24])
        color = data[25]
        alpha = color in (4, 6) or b"tRNS" in data[:max(64, data.find(b"IDAT"))]
        return w, h, alpha
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h, False
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
    return None


def _walk(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            yield os.path.join(base, n)


def _find(root, name):
    return [p for p in _walk(root) if os.path.basename(p) == name]


def _plist(path):
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except Exception:  # an unreadable plist is treated as absent
        return None


# ------------------------------------------------------------------ project view

def load_project(root, expo_config=None, privacy=None):
    """Everything the checks need, gathered once."""
    p = {"root": root, "config": {}, "config_file": None, "config_text": None, "dynamic": False,
         "deps": {}, "eas": {}, "sources": [], "gradle": [], "manifests": [], "infoplists": [],
         "privacy_files": [], "podfile": None, "expo": False}
    if expo_config:
        text = _read(expo_config)
        try:
            cfg = json.loads(text or "")
        except ValueError:
            raise SystemExit("%s is not JSON; save it with: npx expo config --json > %s" % (expo_config, expo_config))
        p.update(config_file=expo_config, config_text=text)
        p["config"] = cfg.get("expo") or cfg.get("exp") or cfg if isinstance(cfg, dict) else {}
    else:
        path = os.path.join(root, "app.json")
        text = _read(path)
        if text is not None:
            try:
                cfg = json.loads(text)
                p["config"] = cfg.get("expo") or cfg if isinstance(cfg, dict) else {}
                p.update(config_file=path, config_text=text)
            except ValueError:
                pass
        for name in ("app.config.js", "app.config.ts", "app.config.mjs"):
            js = os.path.join(root, name)
            if os.path.isfile(js):
                p["dynamic"] = True
                if p["config_file"] is None:
                    p.update(config_file=js, config_text=_read(js))
    pkg = _read(os.path.join(root, "package.json"))
    if pkg:
        try:
            data = json.loads(pkg)
            p["deps"] = dict(data.get("dependencies") or {}, **(data.get("devDependencies") or {}))
        except ValueError:
            pass
    p["expo"] = "expo" in p["deps"] or bool(p["config"])
    eas = _read(os.path.join(root, "eas.json"))
    if eas:
        try:
            p["eas"] = json.loads(eas)
        except ValueError:
            pass
    for path in _walk(root):
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        if name in ("build.gradle", "build.gradle.kts") and os.sep + "app" + os.sep in path:
            p["gradle"].append(path)
        elif name == "AndroidManifest.xml":
            p["manifests"].append(path)
        elif name == "Info.plist":
            p["infoplists"].append(path)
        elif name == "PrivacyInfo.xcprivacy":
            p["privacy_files"].append(path)
        elif name == "Podfile":
            p["podfile"] = path
        elif ext in SOURCE_EXT and not re.search(r"(^|[/\\])(__tests__|tests?)[/\\]|\.(test|spec)\.", path):
            p["sources"].append(path)
    if privacy:
        p["privacy_files"].insert(0, privacy)
    return p


def _platforms(p):
    cfg = p["config"]
    plats = cfg.get("platforms")
    ios = bool(cfg.get("ios")) or os.path.isdir(os.path.join(p["root"], "ios")) or bool(p["infoplists"])
    android = bool(cfg.get("android")) or os.path.isdir(os.path.join(p["root"], "android")) or bool(p["manifests"])
    if p["expo"] and plats is None:
        ios = android = True
    if isinstance(plats, list):
        ios, android = "ios" in plats, "android" in plats
    return ios, android


def _first_hit(p, rx):
    """(rel file, line, text) of the first source line matching rx, or None."""
    for path in p["sources"]:
        text = _read(path) or ""
        m = rx.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            return _rel(p["root"], path), line, text.splitlines()[line - 1].strip()[:160]
    return None


def _cfg_where(p, needle):
    if p["config_file"]:
        return _rel(p["root"], p["config_file"]), _line_of(p["config_text"], needle)
    return "package.json", 1


# ------------------------------------------------------------------ scan

def scan(root, expo_config=None, privacy=None, data_safety=None, assets=None, min_target_sdk=MIN_TARGET_SDK):
    if not os.path.isdir(root):
        raise SystemExit("no project folder at %s" % root)
    p = load_project(root, expo_config, privacy)
    cfg = p["config"]
    ios_cfg = cfg.get("ios") or {}
    android_cfg = cfg.get("android") or {}
    ios, android = _platforms(p)
    hits, na, notes = [], {}, {}

    def hit(check, severity, title, where, text=""):
        f, line = where
        hits.append({"check": check, "severity": severity, "title": title, "file": f, "line": line,
                     "text": text, "ref": REFS.get(check, "")})

    if not (ios or android):
        for cid, _ in CHECKS:
            na[cid] = "no iOS or Android app found (no app.json, app.config, android/ or ios/)"
        return {"hits": hits, "not_applicable": na, "notes": notes, "platforms": {"ios": False, "android": False},
                "config_dynamic": p["dynamic"]}

    # account deletion
    auth_dep = next((d for d in AUTH_DEPS if d in p["deps"]), None)
    auth_code = _first_hit(p, AUTH_CODE)
    if not (auth_dep or auth_code):
        na["account-deletion"] = "no sign-up or login found in the dependencies or the source"
    else:
        deletion = _first_hit(p, DELETE_CODE)
        if deletion:
            notes["account-deletion"] = "delete-account path at %s:%d" % deletion[:2]
        else:
            where = auth_code[:2] if auth_code else ("package.json", _line_of(_read(os.path.join(root, "package.json")),
                                                                              auth_dep))
            hit("account-deletion", "high", "The app lets people create an account but has no way to delete it in "
                "the app", where, auth_code[2] if auth_code else "uses %s" % auth_dep)

    # sign in with apple
    if not ios:
        na["sign-in-with-apple"] = "no iOS build"
    else:
        providers = sorted({v for k, v in THIRD_PARTY_DEPS.items() if k in p["deps"]})
        code = _first_hit(p, THIRD_PARTY_CODE)
        if code and not providers:
            providers = ["a third-party provider"]
        if not providers:
            na["sign-in-with-apple"] = "no third-party login (Google, Facebook and so on) found"
        else:
            apple = (any(d in p["deps"] for d in APPLE_DEPS) or ios_cfg.get("usesAppleSignIn")
                     or _first_hit(p, APPLE_CODE))
            if apple:
                notes["sign-in-with-apple"] = "Sign in with Apple offered next to %s" % ", ".join(providers)
            else:
                dep = next((k for k in THIRD_PARTY_DEPS if k in p["deps"]), None)
                where = code[:2] if code else ("package.json", _line_of(_read(os.path.join(root, "package.json")), dep))
                hit("sign-in-with-apple", "high", "iOS login offers %s but not Sign in with Apple" % ", ".join(providers),
                    where)

    # target sdk
    if not android:
        na["target-sdk"] = "no Android build"
    else:
        found = None
        for g in p["gradle"]:
            text = _read(g) or ""
            m = re.search(r"targetSdk(?:Version)?\s*[=(]?\s*(\d+)", text)
            if m:
                found = (int(m.group(1)), (_rel(root, g), text.count("\n", 0, m.start()) + 1))
                break
        if found is None:
            for plugin in cfg.get("plugins") or []:
                if isinstance(plugin, list) and plugin and plugin[0] == "expo-build-properties" and len(plugin) > 1:
                    v = ((plugin[1] or {}).get("android") or {}).get("targetSdkVersion")
                    if v is not None:
                        found = (int(v), _cfg_where(p, "targetSdkVersion"))
        if found is None and android_cfg.get("targetSdkVersion") is not None:
            found = (int(android_cfg["targetSdkVersion"]), _cfg_where(p, "targetSdkVersion"))
        if found is not None:
            sdk, where = found
            if sdk < min_target_sdk:
                hit("target-sdk", "high", "Android targetSdkVersion is %d; Google Play requires at least %d"
                    % (sdk, min_target_sdk), where)
            else:
                notes["target-sdk"] = "targetSdkVersion %d" % sdk
        elif p["expo"]:
            notes["target-sdk"] = ("set by Expo SDK %s itself; confirm the targetSdkVersion in the EAS build log "
                                   "or with npx expo config --type prebuild" % p["deps"].get("expo", "?"))
        else:
            na["target-sdk"] = "no targetSdkVersion found in the Gradle files"

    # privacy manifest and required-reason APIs
    used = {}
    for cat, (deps, rx) in REASON_APIS.items():
        dep = next((d for d in deps if d in p["deps"]), None)
        code = _first_hit(p, rx)
        if dep or code:
            used[cat] = code[:2] if code else ("package.json", _line_of(_read(os.path.join(root, "package.json")), dep))
    declared_apis, collected, manifest_where = set(), set(), None
    manifests = []
    if isinstance(ios_cfg.get("privacyManifests"), dict):
        manifests.append((ios_cfg["privacyManifests"], _cfg_where(p, "privacyManifests")))
    for f in p["privacy_files"]:
        data = _plist(f)
        if isinstance(data, dict):
            manifests.append((data, (_rel(root, f), 1)))
    for data, where in manifests:
        manifest_where = manifest_where or where
        for entry in data.get("NSPrivacyAccessedAPITypes") or []:
            if isinstance(entry, dict) and entry.get("NSPrivacyAccessedAPIType"):
                declared_apis.add(entry["NSPrivacyAccessedAPIType"])
        for entry in data.get("NSPrivacyCollectedDataTypes") or []:
            if isinstance(entry, dict) and entry.get("NSPrivacyCollectedDataType"):
                collected.add(entry["NSPrivacyCollectedDataType"])
    if not ios:
        na["privacy-manifest"] = "no iOS build"
    elif not manifests:
        if used:
            cats = ", ".join(c.replace("NSPrivacyAccessedAPICategory", "") for c in sorted(used))
            hit("privacy-manifest", "medium", "No iOS privacy manifest, but the app uses required-reason APIs: %s"
                % cats, used[sorted(used)[0]])
        else:
            notes["privacy-manifest"] = "no privacy manifest and no required-reason API use found"
    else:
        for cat in sorted(set(used) - declared_apis):
            hit("privacy-manifest", "medium", "The privacy manifest does not declare %s, which the app uses"
                % cat.replace("NSPrivacyAccessedAPICategory", ""), used[cat],
                "add %s with a reason code to NSPrivacyAccessedAPITypes" % cat)

    # tracking SDKs against the declarations
    sdk_found = {}
    lists = [(k, "package.json") for k in p["deps"]]
    for path in p["gradle"] + ([p["podfile"]] if p["podfile"] else []):
        lists += [(line, _rel(root, path)) for line in (_read(path) or "").splitlines()]
    for item, src in lists:
        low = item.lower()
        for key, info in SDKS.items():
            if key in low and info[0] not in sdk_found:
                text = _read(os.path.join(root, src)) if src == "package.json" else _read(os.path.join(root, src))
                sdk_found[info[0]] = (info, (src, _line_of(text, item.strip()[:80])))
    play_words = None
    if data_safety:
        text = _read(data_safety)
        if text is None:
            raise SystemExit("cannot read %s" % data_safety)
        play_words = " ".join(" ".join(row) for row in csv.reader(io.StringIO(text))).lower()
    if not sdk_found:
        notes["tracking-sdks"] = "no analytics, crash or ads SDK found"
    elif not manifests and play_words is None:
        names = ", ".join(sorted(sdk_found))
        hit("tracking-sdks", "medium", "The app ships %s but no privacy declarations were found to check them "
            "against" % names, sdk_found[sorted(sdk_found)[0]][1],
            "add NSPrivacyCollectedDataTypes to the privacy manifest, or pass --data-safety with the Play export")
    else:
        for name in sorted(sdk_found):
            (label, apple_types, words), where = sdk_found[name]
            if manifests and ios:
                missing = [t for t in apple_types if t not in collected]
                if missing:
                    hit("tracking-sdks", "medium", "%s collects data the privacy manifest does not list: %s"
                        % (label, ", ".join(t.replace("NSPrivacyCollectedDataType", "") for t in missing)), where)
            if play_words is not None and android:
                if not any(w in play_words for w in words):
                    hit("tracking-sdks", "medium", "%s is not reflected in the Play Data safety form (expected: %s)"
                        % (label, ", ".join(words)), where)

    # versioning
    remote = (p["eas"].get("cli") or {}).get("appVersionSource") == "remote"
    if p["expo"] and p["config"]:
        if not cfg.get("version"):
            hit("versioning", "low", "No app version set (expo.version)", _cfg_where(p, '"name"'))
        if not remote:
            bn = ios_cfg.get("buildNumber")
            if ios and bn is not None and not re.match(r"^\d+(\.\d+){0,2}$", str(bn)):
                hit("versioning", "low", "ios.buildNumber %r is not a number" % bn, _cfg_where(p, "buildNumber"))
            vc = android_cfg.get("versionCode")
            if android and vc is not None and not re.match(r"^\d+$", str(vc)):
                hit("versioning", "low", "android.versionCode %r is not a whole number" % vc,
                    _cfg_where(p, "versionCode"))
        else:
            notes["versioning"] = "build numbers are managed by EAS (appVersionSource remote)"
    elif p["gradle"] or p["infoplists"]:
        for g in p["gradle"]:
            text = _read(g) or ""
            if android and not re.search(r"versionCode\s*[=(]?\s*\d+", text):
                hit("versioning", "low", "No numeric versionCode in %s" % _rel(root, g), (_rel(root, g), 1))
    else:
        na["versioning"] = "no app config or native build files found"

    # export compliance
    if not ios:
        na["export-compliance"] = "no iOS build"
    else:
        answered = ((ios_cfg.get("config") or {}).get("usesNonExemptEncryption") is not None
                    or "ITSAppUsesNonExemptEncryption" in (ios_cfg.get("infoPlist") or {}))
        for f in p["infoplists"]:
            data = _plist(f)
            if isinstance(data, dict) and "ITSAppUsesNonExemptEncryption" in data:
                answered = True
        if not answered:
            hit("export-compliance", "low", "Export compliance is not answered, so every upload waits for the "
                "encryption question", _cfg_where(p, "ios") if p["config_file"] else ("Info.plist", 1))

    # icons
    icon = ios_cfg.get("icon") or cfg.get("icon")
    if icon and isinstance(icon, str):
        size = image_size(os.path.join(root, icon))
        where = _cfg_where(p, icon)
        if size is None:
            hit("icons", "medium", "The app icon %s is missing or not a PNG" % icon, where)
        elif ios:
            w, h, alpha = size
            if (w, h) != (1024, 1024):
                hit("icons", "medium", "The App Store icon is %dx%d; it must be 1024x1024" % (w, h), where)
            if alpha:
                hit("icons", "medium", "The App Store icon %s has an alpha channel; App Store Connect rejects it"
                    % icon, where)
    elif ios and p["expo"]:
        hit("icons", "medium", "No app icon is set (expo.icon)", _cfg_where(p, '"name"'))
    if android:
        adaptive = (android_cfg.get("adaptiveIcon") or {}).get("foregroundImage")
        native = any(p_.endswith(os.path.join("mipmap-anydpi-v26", "ic_launcher.xml")) for p_ in _walk(root))
        if not adaptive and not native and (p["expo"] or p["manifests"]):
            hit("icons", "medium", "No Android adaptive icon (android.adaptiveIcon.foregroundImage)",
                _cfg_where(p, "android"))
    if not icon and not android and not (ios and p["expo"]):
        na["icons"] = "no icon configuration found to check"

    # screenshots
    if not assets:
        na["screenshots"] = "no screenshots folder given (--assets)"
    elif not os.path.isdir(assets):
        raise SystemExit("no screenshots folder at %s" % assets)
    else:
        sizes = []
        for path in _walk(assets):
            if os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg"):
                s = image_size(path)
                if s:
                    sizes.append((os.path.basename(path), s[0], s[1]))
        dims = {(min(w, h), max(w, h)) for _, w, h in sizes}
        where = (_rel(root, assets) if os.path.abspath(assets).startswith(os.path.abspath(root)) else assets, 1)
        if ios and not dims & IPHONE_SIZES:
            hit("screenshots", "low", "No iPhone screenshot at a required size (1320x2868, 1290x2796 or 1284x2778)",
                where, "found: %s" % (", ".join("%dx%d" % d for d in sorted(dims)) or "none"))
        if ios and ios_cfg.get("supportsTablet") and not dims & IPAD_SIZES:
            hit("screenshots", "low", "supportsTablet is on but there is no 13-inch iPad screenshot (2064x2752)", where)
        if android:
            ok = [d for d in dims if d[0] >= 320 and d[1] <= 3840 and d[1] <= 2 * d[0]]
            if len([1 for _, w, h in sizes if (min(w, h), max(w, h)) in ok]) < 2:
                hit("screenshots", "low", "Google Play needs at least two phone screenshots between 320 and 3840 px "
                    "with sides no more than 2:1", where)

    na["permissions"] = "covered by /proof-react-native (permissions against the code, iOS usage text)"
    return {"hits": hits, "not_applicable": na, "notes": notes, "platforms": {"ios": ios, "android": android},
            "config_dynamic": p["dynamic"], "min_target_sdk": min_target_sdk}


# ------------------------------------------------------------------ commands

SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def _scan(a):
    return scan(a.path, a.expo_config, a.privacy, a.data_safety, a.assets, a.min_target_sdk)


def cmd_scan(a):
    r = _scan(a)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    plats = [k for k, v in r["platforms"].items() if v]
    print("Store readiness for %s%s" % (" and ".join(plats) or "no platform",
                                        " (app.config is code; pass --expo-config for the resolved values)"
                                        if r["config_dynamic"] else ""))
    for cid, title in CHECKS:
        found = sorted((h for h in r["hits"] if h["check"] == cid), key=lambda h: SEV_ORDER[h["severity"]])
        if cid in r["not_applicable"]:
            print("  -- %s: not applicable (%s)" % (title, r["not_applicable"][cid]))
            continue
        extra = ("  (%s)" % r["notes"][cid]) if cid in r["notes"] and not found else ""
        print("  %s %s%s" % (ledger.BAD if found else ledger.OK, title, extra))
        for h in found:
            print("      [%s] %s  %s:%d" % (h["severity"], h["title"], h["file"], h["line"]))
    return 0


def cmd_record(a):
    r = _scan(a)
    ledger.init_coverage(a.dir, SKILL, CHECKS)
    ev_path = os.path.join(ledger.evidence_dir(a.dir, SKILL), "store-scan.json")
    with open(ev_path, "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=2)
    ev = os.path.relpath(ev_path, ledger.root(a.dir)).replace(os.sep, "/")
    for cid, _ in CHECKS:
        if cid in r["not_applicable"]:
            ledger.mark_coverage(a.dir, SKILL, cid, "not_applicable", note=r["not_applicable"][cid])
        else:
            n = sum(1 for h in r["hits"] if h["check"] == cid)
            ledger.mark_coverage(a.dir, SKILL, cid, "covered", evidence=[ev],
                                 note=r["notes"].get(cid) if not n else "%d problem(s)" % n)
    keep = set()
    for h in r["hits"]:
        fid = ledger.slug("store-%s-%s" % (h["check"], h["title"]))[:60]
        keep.add(fid)
        path = ledger.finding_path(a.dir, SKILL, fid)
        if os.path.exists(path) and ledger.load_finding(a.dir, SKILL, fid)["status"] == "false_positive":
            continue
        f = ledger.new_finding(SKILL, h["title"][:160], h["severity"], id=fid, category="store:%s" % h["check"],
                               location="%s:%d" % (h["file"], h["line"]), evidence=[ev], fix=FIX.get(h["check"]),
                               description=h["text"] or None, references=[h["ref"]] if h["ref"] else None)
        ledger.write_finding(a.dir, f)
        ledger.verify(a.dir, SKILL, fid, by="evidence-file")
    for old in ledger.load_findings(a.dir, SKILL):
        if old["id"] not in keep and old["status"] != "false_positive":
            os.remove(ledger.finding_path(a.dir, SKILL, old["id"]))
    high = sum(1 for h in r["hits"] if h["severity"] == "high")
    print("%s recorded store readiness: %d finding(s), %d high. Evidence: %s" % (
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
        s = sub.add_parser(name, help=hlp)
        s.add_argument("path", help="the app project folder")
        s.add_argument("--expo-config", help="the resolved config saved with: npx expo config --json > expo-config.json")
        s.add_argument("--privacy", help="a privacy manifest (PrivacyInfo.xcprivacy) outside the project")
        s.add_argument("--data-safety", help="the Play Console Data safety CSV export")
        s.add_argument("--assets", help="a folder of store screenshots")
        s.add_argument("--min-target-sdk", type=int, default=MIN_TARGET_SDK,
                       help="lowest targetSdkVersion Play accepts (default %d)" % MIN_TARGET_SDK)
        if name == "scan":
            s.add_argument("--json", action="store_true")
        s.set_defaults(func=func)
    sub.add_parser("status", help="recorded findings").set_defaults(func=cmd_status)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
