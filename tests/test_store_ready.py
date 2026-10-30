import contextlib
import io
import json
import os
import struct
import unittest
import zlib

from _load import TempProof, load_tool

store = load_tool("proof-store-ready", "store.py")
ledger = load_tool("proof-core", "ledger.py")


def png(w, h, color_type=2):
    """A tiny PNG header with the given size and colour type (2 = RGB, 6 = RGBA)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")


RISKY_APP = {"expo": {
    "name": "Shop", "slug": "shop", "icon": "./assets/icon.png",
    "ios": {"bundleIdentifier": "com.example.shop", "supportsTablet": True, "buildNumber": "one"},
    "android": {"package": "com.example.shop"},
    "plugins": [["expo-build-properties", {"android": {"targetSdkVersion": 33}}]],
}}
RISKY_PKG = {"dependencies": {"expo": "~54.0.0", "@react-native-google-signin/google-signin": "^13.0.0",
                              "@react-native-async-storage/async-storage": "2.1.0", "posthog-react-native": "^4.0.0",
                              "@supabase/supabase-js": "^2.45.0"}}
RISKY_SRC = {
    "app/signup.tsx": "export async function go(email, pw) {\n  await supabase.auth.signUp({ email, password: pw });\n}\n",
    "app/login.tsx": "import { GoogleSignin } from '@react-native-google-signin/google-signin';\nGoogleSignin.configure();\n",
    "lib/store.ts": "import AsyncStorage from '@react-native-async-storage/async-storage';\nAsyncStorage.getItem('x');\n",
}

GOOD_APP = {"expo": {
    "name": "Notes", "slug": "notes", "version": "1.2.0", "icon": "./assets/icon.png",
    "ios": {"bundleIdentifier": "com.example.notes", "buildNumber": "7", "usesAppleSignIn": True,
            "config": {"usesNonExemptEncryption": False},
            "privacyManifests": {
                "NSPrivacyAccessedAPITypes": [{"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults",
                                               "NSPrivacyAccessedAPITypeReasons": ["CA92.1"]}],
                "NSPrivacyCollectedDataTypes": [{"NSPrivacyCollectedDataType":
                                                 "NSPrivacyCollectedDataTypeProductInteraction"}]}},
    "android": {"package": "com.example.notes", "versionCode": 7,
                "adaptiveIcon": {"foregroundImage": "./assets/adaptive.png", "backgroundColor": "#ffffff"}},
    "plugins": [["expo-build-properties", {"android": {"targetSdkVersion": 35}}]],
}}
GOOD_PKG = {"dependencies": {"expo": "~54.0.0", "@react-native-google-signin/google-signin": "^13.0.0",
                             "expo-apple-authentication": "~7.0.0", "posthog-react-native": "^4.0.0",
                             "@react-native-async-storage/async-storage": "2.1.0", "@supabase/supabase-js": "^2.45.0"}}
GOOD_SRC = {
    "app/signup.tsx": "export const go = (e, p) => supabase.auth.signUp({ email: e, password: p });\n",
    "app/settings.tsx": "export function Settings() {\n  return <Button title=\"Delete account\" onPress={deleteAccount} />;\n}\n",
}


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = store.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class StoreReady(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def project(self, name, app, pkg, src, icon=(1024, 1024, 2)):
        root = os.path.join(self.tp.base, name)
        os.makedirs(os.path.join(root, "assets"))
        with open(os.path.join(root, "app.json"), "w", encoding="utf-8") as fh:
            json.dump(app, fh, indent=2)
        with open(os.path.join(root, "package.json"), "w", encoding="utf-8") as fh:
            json.dump(pkg, fh, indent=2)
        for rel, text in src.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(os.path.join(root, "assets", "icon.png"), "wb") as fh:
            fh.write(png(*icon))
        with open(os.path.join(root, "assets", "adaptive.png"), "wb") as fh:
            fh.write(png(1024, 1024, 6))
        return root

    def checks(self, r):
        return {h["check"] for h in r["hits"]}

    def test_risky_project(self):
        root = self.project("risky", RISKY_APP, RISKY_PKG, RISKY_SRC, icon=(512, 512, 6))
        r = store.scan(root)
        got = self.checks(r)
        for cid in ("account-deletion", "sign-in-with-apple", "target-sdk", "privacy-manifest", "tracking-sdks",
                    "versioning", "export-compliance", "icons"):
            self.assertIn(cid, got, cid)
        acct = next(h for h in r["hits"] if h["check"] == "account-deletion")
        self.assertEqual((acct["file"], acct["line"], acct["severity"]), ("app/signup.tsx", 2, "high"))
        self.assertIn("5.1.1(v)", acct["ref"])
        sdk = next(h for h in r["hits"] if h["check"] == "target-sdk")
        self.assertIn("33", sdk["title"])
        icons = [h["title"] for h in r["hits"] if h["check"] == "icons"]
        self.assertTrue(any("512x512" in t for t in icons) and any("alpha" in t for t in icons))
        self.assertTrue(any("adaptive" in t for t in icons))
        self.assertIn("UserDefaults", next(h for h in r["hits"] if h["check"] == "privacy-manifest")["title"])
        self.assertIn("permissions", r["not_applicable"])
        self.assertIn("screenshots", r["not_applicable"])

    def test_good_project(self):
        root = self.project("good", GOOD_APP, GOOD_PKG, GOOD_SRC)
        r = store.scan(root)
        self.assertEqual(r["hits"], [], r["hits"])
        self.assertIn("delete-account path", r["notes"]["account-deletion"])

    def test_min_target_sdk_flag_and_data_safety(self):
        root = self.project("good2", GOOD_APP, GOOD_PKG, GOOD_SRC)
        r = store.scan(root, min_target_sdk=36)
        self.assertIn("target-sdk", self.checks(r))
        ds = os.path.join(self.tp.base, "ds.csv")
        with open(ds, "w", encoding="utf-8") as fh:
            fh.write("Data type,Collected\nCrash logs,Yes\n")
        r = store.scan(root, data_safety=ds)
        t = [h for h in r["hits"] if h["check"] == "tracking-sdks"]
        self.assertEqual(len(t), 1)
        self.assertIn("Play Data safety", t[0]["title"])

    def test_screenshots(self):
        root = self.project("shots", GOOD_APP, GOOD_PKG, GOOD_SRC)
        assets = os.path.join(self.tp.base, "store")
        os.makedirs(assets)
        with open(os.path.join(assets, "small.png"), "wb") as fh:
            fh.write(png(300, 500))
        r = store.scan(root, assets=assets)
        self.assertEqual(len([h for h in r["hits"] if h["check"] == "screenshots"]), 2)
        for i in range(2):
            with open(os.path.join(assets, "iphone%d.png" % i), "wb") as fh:
                fh.write(png(1290, 2796))
        r = store.scan(root, assets=assets)
        # iPhone sizes are taller than 2:1, so Play still needs its own.
        self.assertEqual([h["title"][:11] for h in r["hits"] if h["check"] == "screenshots"], ["Google Play"])
        for i in range(2):
            with open(os.path.join(assets, "play%d.png" % i), "wb") as fh:
                fh.write(png(1080, 1920))
        r = store.scan(root, assets=assets)
        self.assertNotIn("screenshots", self.checks(r))

    def test_native_android_gradle(self):
        root = os.path.join(self.tp.base, "native")
        os.makedirs(os.path.join(root, "android", "app", "src", "main"))
        with open(os.path.join(root, "android", "app", "build.gradle"), "w", encoding="utf-8") as fh:
            fh.write("android {\n  defaultConfig {\n    targetSdkVersion 34\n    versionCode 3\n  }\n}\n")
        with open(os.path.join(root, "android", "app", "src", "main", "AndroidManifest.xml"), "w",
                  encoding="utf-8") as fh:
            fh.write("<manifest/>")
        r = store.scan(root)
        sdk = next(h for h in r["hits"] if h["check"] == "target-sdk")
        self.assertEqual((sdk["file"], sdk["line"]), ("android/app/build.gradle", 3))
        self.assertIn("sign-in-with-apple", r["not_applicable"])

    def test_not_a_mobile_project(self):
        root = os.path.join(self.tp.base, "web")
        os.makedirs(root)
        r = store.scan(root)
        self.assertEqual(r["hits"], [])
        self.assertEqual(len(r["not_applicable"]), len(store.CHECKS))

    def test_record_and_rerecord(self):
        root = self.project("rec", RISKY_APP, RISKY_PKG, RISKY_SRC)
        rc, out = run("--dir", self.d, "record", root)
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-store-ready")
        self.assertTrue(fs)
        self.assertTrue(all(f["status"] == "verified" and f.get("references") for f in fs))
        self.assertTrue(all(":" in f["location"] for f in fs))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-store-ready")["checks"]}
        self.assertEqual(cov["permissions"], "not_applicable")
        self.assertEqual(cov["account-deletion"], "covered")
        # Fix the app; the stale findings go away on the next record.
        good = self.project("rec-good", GOOD_APP, GOOD_PKG, GOOD_SRC)
        rc, out = run("--dir", self.d, "record", good)
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_findings(self.d, "proof-store-ready"), [])

    def test_scan_cli_and_missing_folder(self):
        root = self.project("cli", RISKY_APP, RISKY_PKG, RISKY_SRC)
        rc, out = run("scan", root, "--min-target-sdk", "35")
        self.assertEqual(rc, 0, out)
        self.assertIn("Sign in with Apple", out)
        rc, out = run("scan", root, "--json")
        self.assertIn("account-deletion", json.loads(out)["hits"][0]["check"] + " account-deletion")
        rc, out = run("scan", os.path.join(self.tp.base, "nope"))
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
