import contextlib
import io
import os
import unittest

from _load import TempProof, load_tool

secweb = load_tool("proof-sec-web", "secweb.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = secweb.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


class SecWeb(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_classes_doc_and_tool_agree(self):
        rc, out = run("classes", "--json")
        self.assertEqual(rc, 0)
        import json
        classes = json.loads(out)
        self.assertGreaterEqual(len(classes), 22)
        ids = [c["id"] for c in classes]
        self.assertEqual(len(ids), len(set(ids)))
        for key in ("id", "title", "group"):
            self.assertTrue(all(key in c for c in classes))

    def test_start_creates_full_coverage(self):
        rc, out = run("--dir", self.d, "start")
        self.assertEqual(rc, 0, out)
        cov = ledger.load_coverage(self.d, "proof-sec-web")
        self.assertTrue(all(c["status"] == "not_run" for c in cov["checks"]))

    def test_cover_and_na(self):
        run("--dir", self.d, "start")
        self.tp.write("evidence/proof-sec-web/h.txt", b"HTTP/1.1 200 OK")
        rc, _ = run("--dir", self.d, "cover", "headers", "--note", "ok", "--evidence", "evidence/proof-sec-web/h.txt")
        self.assertEqual(rc, 0)
        rc, out = run("--dir", self.d, "na", "xxe", "--note", "no xml")
        self.assertEqual(rc, 0)
        cov = ledger.load_coverage(self.d, "proof-sec-web")
        by = {c["id"]: c["status"] for c in cov["checks"]}
        self.assertEqual(by["headers"], "covered")
        self.assertEqual(by["xxe"], "not_applicable")

    def test_unknown_class_refused(self):
        run("--dir", self.d, "start")
        rc, out = run("--dir", self.d, "na", "not-a-class", "--note", "x")
        self.assertNotEqual(rc, 0)

    def test_finding_gate(self):
        run("--dir", self.d, "start")
        rc, out = run("--dir", self.d, "finding", "add", "authz-object", "--title",
                      "IDOR on invoices", "--severity", "critical")
        self.assertEqual(rc, 0, out)
        fid = [f["id"] for f in ledger.load_findings(self.d, "proof-sec-web")][0]
        # cannot verify by evidence with no evidence
        rc, out = run("--dir", self.d, "finding", "verify", fid, "--by", "evidence-file")
        self.assertNotEqual(rc, 0)
        self.assertEqual(ledger.load_finding(self.d, "proof-sec-web", fid)["status"], "unverified")
        # manual with a note works
        rc, out = run("--dir", self.d, "finding", "verify", fid, "--by", "manual", "--note", "confirmed by Dana on staging")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ledger.load_finding(self.d, "proof-sec-web", fid)["status"], "verified")
        # reject round-trip
        run("--dir", self.d, "finding", "reject", fid, "--reason", "was a test account quirk")
        self.assertEqual(ledger.load_finding(self.d, "proof-sec-web", fid)["status"], "false_positive")

    def test_scan_headers(self):
        resp = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nSet-Cookie: sid=abc; Path=/\r\n\r\n<html>"
        p = self.tp.write("evidence/proof-sec-web/response.txt", resp)
        rc, out = run("--dir", self.d, "scan-headers", os.path.join(self.d, p), "--json")
        self.assertEqual(rc, 0, out)
        import json
        res = json.loads(out)
        joined = " ".join(res["notes"])
        self.assertIn("content-security-policy", joined)
        self.assertIn("HttpOnly", joined)
        self.assertIn("Secure", joined)
        self.assertIn("SameSite", joined)

    def test_scan_headers_clean(self):
        resp = ("HTTP/1.1 200 OK\r\n"
                "Content-Security-Policy: default-src 'self'; frame-ancestors 'none'\r\n"
                "X-Content-Type-Options: nosniff\r\n"
                "Strict-Transport-Security: max-age=63072000\r\n"
                "Referrer-Policy: no-referrer\r\n"
                "Set-Cookie: sid=abc; HttpOnly; Secure; SameSite=Lax\r\n\r\n")
        p = self.tp.write("evidence/proof-sec-web/clean.txt", resp)
        rc, out = run("--dir", self.d, "scan-headers", os.path.join(self.d, p), "--json")
        import json
        self.assertEqual(json.loads(out)["notes"], [])


if __name__ == "__main__":
    unittest.main()
