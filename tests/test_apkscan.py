import contextlib
import io
import os
import plistlib
import unittest
import zipfile

from _load import TempProof, load_tool

apkscan = load_tool("proof-mobile-audit", "apkscan.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = apkscan.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        out.write(str(e))
    return code, out.getvalue()


def make_apk(path):
    with zipfile.ZipFile(path, "w") as z:
        # A fake "binary" manifest containing permission strings and a cleartext hint.
        manifest = (b"\x00\x03android.permission.INTERNET\x00"
                    b"android.permission.ACCESS_FINE_LOCATION\x00"
                    b"android.permission.CAMERA\x00usesCleartextTraffic\x00")
        z.writestr("AndroidManifest.xml", manifest)
        z.writestr("res/values/strings.xml", b"<resources><string>api</string></resources>")
        z.writestr("res/raw/config.json", b'{"api":"http://plain.example.com/v1","fb":"https://x.firebaseio.com"}')


def make_ipa(path):
    info = {"NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
            "CFBundleURLTypes": [{"CFBundleURLSchemes": ["myapp"]}],
            "NSCameraUsageDescription": "to scan codes"}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Payload/MyApp.app/Info.plist", plistlib.dumps(info))


class ApkScan(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_apk_permissions_and_urls(self):
        apk = os.path.join(self.tp.base, "app.apk")
        make_apk(apk)
        r = apkscan.scan_apk(apk)
        self.assertIn("ACCESS_FINE_LOCATION", r["permissions"])
        self.assertIn("CAMERA", r["permissions"])
        self.assertTrue(any(p == "ACCESS_FINE_LOCATION" for p, _ in r["sensitive"]))
        self.assertIn("http://plain.example.com/v1", r["http_urls"])
        self.assertTrue(r["firebase"])

    def test_ios_ats_and_schemes(self):
        ipa = os.path.join(self.tp.base, "MyApp.ipa")
        make_ipa(ipa)
        r = apkscan.scan_ipa(ipa)
        self.assertTrue(r["ats_arbitrary_loads"])
        self.assertIn("myapp", r["url_schemes"])
        self.assertIn("NSCameraUsageDescription", r["permissions"])

    def test_record_apk_writes_findings(self):
        apk = os.path.join(self.tp.base, "app.apk")
        make_apk(apk)
        rc, out = run("--dir", self.d, "record", "apk", apk)
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-mobile-audit")
        self.assertTrue(fs)
        self.assertTrue(all(f["status"] == "verified" for f in fs))

    def test_not_an_apk(self):
        bad = os.path.join(self.tp.base, "x.apk")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("hello.txt", b"hi")
        rc, out = run("--dir", self.d, "apk", bad)
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
