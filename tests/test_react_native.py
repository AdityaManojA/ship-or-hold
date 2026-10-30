import contextlib
import io
import json
import os
import unittest

from _load import TempProof, load_tool

rn = load_tool("proof-react-native", "rncheck.py")
ledger = load_tool("proof-core", "ledger.py")

FAKE_OPENAI = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"

APP_JSON = {"expo": {
    "name": "Shop", "slug": "shop", "scheme": "shop",
    "android": {"package": "com.example.shop", "permissions": ["android.permission.CAMERA", "READ_CONTACTS"],
                "usesCleartextTraffic": True},
    "ios": {"bundleIdentifier": "com.example.shop",
            "infoPlist": {"NSPhotoLibraryUsageDescription": "This app needs access"}},
    "plugins": [["expo-image-picker", {"photosPermission": "Pick a photo for your profile picture."}]],
    "extra": {"apiUrl": "https://api.example.com", "stripeSecret": "sk_live_" + "abcdefgh12345678"},
}}

SOURCE = {
    "app/index.tsx": (
        "import AsyncStorage from '@react-native-async-storage/async-storage';\n"
        "import { useAudioRecorder } from 'expo-audio';\n"
        "const API = 'http://api.example.com/v1';\n"
        "export async function save(t) {\n"
        "  await AsyncStorage.setItem('auth_token', t);\n"
        "  console.log('saved');\n"
        "}\n"
        "const r = useAudioRecorder();\n"
        "const k = process.env.EXPO_PUBLIC_SERVICE_ROLE_KEY;\n"),
    "app/link.tsx": (
        "import * as Linking from 'expo-linking';\n"
        "Linking.addEventListener('url', ({ url }) => {\n"
        "  const target = Linking.parse(url).queryParams.next;\n"
        "  Linking.openURL(target);\n"
        "});\n"),
    "lib/camera.ts": "import { CameraView } from 'expo-camera';\nconst x = 'http://localhost:8081';\n",
    "lib/keys.ts": "export const KEY = '%s';\n" % FAKE_OPENAI,
    "__tests__/save.test.ts": "console.log('test logs are fine'); const k='%s';\n" % FAKE_OPENAI,
}

CLEAN_APP = {"expo": {
    "name": "Notes", "slug": "notes", "runtimeVersion": {"policy": "appVersion"},
    "android": {"package": "com.example.notes", "allowBackup": False},
    "ios": {"infoPlist": {"NSCameraUsageDescription": "Scan a receipt to attach it to a note."}},
}}
CLEAN_SOURCE = {
    "app/index.tsx": ("import * as SecureStore from 'expo-secure-store';\n"
                      "import { CameraView } from 'expo-camera';\n"
                      "export const save = (t) => SecureStore.setItemAsync('token', t);\n"
                      "const API = 'https://api.example.com';\n"),
    "__tests__/a.test.ts": "test('x', () => {});\n",
}


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rn.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


class ReactNative(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def project(self, name, app, sources, extra_files=None):
        root = os.path.join(self.tp.base, name)
        files = dict(sources)
        files["package.json"] = json.dumps({"dependencies": {
            "expo": "~54.0.0", "react-native": "0.81.0", "expo-updates": "~0.28.0",
            "@react-native-async-storage/async-storage": "2.0.0"}, "devDependencies": {"jest": "29.0.0"}})
        files["app.json"] = json.dumps(app, indent=2)
        files.update(extra_files or {})
        for rel, text in files.items():
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
        return root

    def checks(self, r):
        return {(h["check"], h["severity"]) for h in r["hits"]}

    def test_sloppy_project(self):
        root = self.project("shop", APP_JSON, SOURCE, {
            ".env": "EXPO_PUBLIC_API_URL=https://api.example.com\nEXPO_PUBLIC_CLIENT_SECRET=abcd1234efgh5678\n",
            "eas.json": json.dumps({"build": {"production": {"developmentClient": True, "distribution": "store"}}})})
        r = rn.scan(root)
        got = self.checks(r)
        for want in [("secrets-in-bundle", "high"), ("token-storage", "high"), ("cleartext", "high"),
                     ("cleartext", "medium"), ("permissions", "high"), ("permissions", "low"),
                     ("debug-in-release", "low"), ("debug-in-release", "medium"), ("deep-links", "medium"),
                     ("updates", "medium"), ("android-backup", "low")]:
            self.assertIn(want, got, want)
        titles = " | ".join(h["title"] for h in r["hits"])
        self.assertIn("microphone", titles)          # recorder used, no iOS microphone description
        self.assertIn("contacts", titles)            # READ_CONTACTS declared, never used
        self.assertIn("does not say why", titles)    # generic NSPhotoLibraryUsageDescription
        self.assertIn("EXPO_PUBLIC_SERVICE_ROLE_KEY", titles)
        self.assertNotIn("localhost", titles)
        # Test files are not app code: their secret and log are ignored.
        self.assertFalse(any(h["file"].startswith("__tests__") for h in r["hits"]))
        self.assertEqual(r["facts"]["test_files"], 1)
        self.assertEqual(r["not_applicable"].get("versions") is not None, True)

    def test_secrets_are_masked_everywhere(self):
        root = self.project("shop", APP_JSON, SOURCE)
        r = rn.scan(root)
        dump = json.dumps(r)
        self.assertNotIn(FAKE_OPENAI, dump)
        self.assertNotIn("abcdefgh12345678", dump)
        rc, out = run("scan", root, "--json")
        self.assertNotIn(FAKE_OPENAI, out)

    def test_clean_project(self):
        root = self.project("notes", CLEAN_APP, CLEAN_SOURCE)
        r = rn.scan(root)
        self.assertEqual(r["hits"], [])
        self.assertIn("deep-links", r["not_applicable"])
        self.assertEqual(r["facts"]["capabilities_used"], ["camera"])

    def test_expo_config_and_doctor(self):
        root = self.project("notes", CLEAN_APP, CLEAN_SOURCE)
        cfg = os.path.join(self.tp.base, "expo-config.json")
        resolved = dict(CLEAN_APP["expo"])
        resolved["ios"] = {"infoPlist": {}}  # the resolved config dropped the description
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump(resolved, fh)
        doc = os.path.join(self.tp.base, "doctor.txt")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write(u"\u2714 Check package.json for common issues\n"
                     u"\u2716 Check that packages match versions required by installed Expo SDK\n"
                     "16/17 checks passed. 1 checks failed.\n")
        r = rn.scan(root, cfg, doc)
        got = self.checks(r)
        self.assertIn(("permissions", "high"), got)
        self.assertIn(("versions", "medium"), got)
        self.assertNotIn("versions", r["not_applicable"])

    def test_app_config_js_literals(self):
        root = os.path.join(self.tp.base, "dyn")
        os.makedirs(os.path.join(root, "app"))
        with open(os.path.join(root, "package.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(root, "app.config.js"), "w", encoding="utf-8") as fh:
            fh.write("export default () => ({ name: 'x', android: { usesCleartextTraffic: true,"
                     " permissions: ['CAMERA'] }, scheme: 'dyn' });\n")
        with open(os.path.join(root, "app", "a.tsx"), "w", encoding="utf-8") as fh:
            fh.write("export const A = 1;\n")
        r = rn.scan(root)
        self.assertTrue(r["facts"]["config_dynamic"])
        got = self.checks(r)
        self.assertIn(("cleartext", "medium"), got)
        self.assertIn(("permissions", "low"), got)   # CAMERA declared, never used
        rc, out = run("scan", root)
        self.assertIn("dynamic values were not evaluated", out)

    def test_native_manifest(self):
        root = self.project("notes", CLEAN_APP, CLEAN_SOURCE, {
            "android/app/src/main/AndroidManifest.xml": (
                '<manifest><uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>'
                '<application android:debuggable="true" android:allowBackup="true"/></manifest>')})
        r = rn.scan(root)
        got = self.checks(r)
        self.assertIn(("debug-in-release", "medium"), got)
        self.assertIn(("permissions", "low"), got)    # location declared, never used
        self.assertIn(("android-backup", "low"), got)  # manifest overrides app.json and SecureStore is used

    def test_record(self):
        root = self.project("shop", APP_JSON, SOURCE)
        rc, out = run("--dir", self.d, "record", root)
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-react-native")
        self.assertTrue(fs)
        self.assertTrue(all(f["status"] == "verified" for f in fs))
        self.assertTrue(all(":" in f["location"] for f in fs))
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-react-native")["checks"]}
        self.assertEqual(cov["versions"], "not_applicable")
        self.assertEqual(cov["tests"], "covered")
        self.assertEqual(cov["secrets-in-bundle"], "covered")
        rc, out = run("--dir", self.d, "status")
        self.assertIn("token", out)

    def test_missing_folder(self):
        rc, out = run("scan", os.path.join(self.tp.base, "nope"))
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
