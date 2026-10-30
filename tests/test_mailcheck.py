import contextlib
import io
import json
import os
import unittest
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from _load import TempProof, load_tool

mail = load_tool("proof-email", "mailcheck.py")
ledger = load_tool("proof-core", "ledger.py")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mail.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        err.write(str(e))
    return code, out.getvalue() + err.getvalue()


RESET_URL = "https://app.example.com/reset?token=Ab3dEf9hIj2kLm4nOp6q"
GOOD_TEXT = "Hi,\n\nReset your password here:\n%s\n\nIf you did not ask, ignore this email.\n" % RESET_URL
GOOD_HTML = ('<html><body><img src="https://app.example.com/logo.png" alt="Example">'
             '<p>Reset your password:</p><p><a href="%s">Reset password</a></p>'
             '<p><a href="mailto:help@example.com">Contact us</a></p></body></html>' % RESET_URL)


def make_eml(subject="Reset your password", text=GOOD_TEXT, html=GOOD_HTML, headers=None, drop=()):
    msg = EmailMessage()
    base = {"From": "Example <no-reply@example.com>", "To": "user@example.org", "Subject": subject,
            "Date": formatdate(1700000000, localtime=False), "Message-ID": make_msgid(domain="example.com")}
    base.update(headers or {})
    for k, v in base.items():
        if k not in drop:
            msg[k] = v
    if text is not None:
        msg.set_content(text)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(html, subtype="html")
    return msg.as_bytes()


class MailCheck(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def eml_file(self, data, name="reset.eml"):
        path = os.path.join(self.tp.base, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def analyse(self, data, *extra):
        rc, out = run("--dir", self.d, "message", self.eml_file(data), "--json", *extra)
        self.assertEqual(rc, 0, out)
        r = json.loads(out)
        return r, dict((c["id"], c) for c in r["checks"])

    # ------------------------------------------------------------ messages

    def test_good_multipart_passes(self):
        r, c = self.analyse(make_eml(), "--expect-link-host", "example.com")
        self.assertEqual([k for k, v in c.items() if v["status"] == "fail"], [])
        self.assertEqual(r["content_type"], "multipart/alternative")
        self.assertIn("optional", c["unsubscribe"]["found"])
        rc, text = run("--dir", self.d, "message", self.eml_file(make_eml()))
        self.assertEqual(rc, 0)
        self.assertIn("0 check(s) failed", text)
        self.assertTrue(all(ord(ch) < 128 for ch in text))

    def test_html_only_flagged(self):
        _, c = self.analyse(make_eml(text=None))
        self.assertEqual(c["text-part"]["status"], "fail")
        self.assertEqual(c["text-part"]["severity"], "low")

    def test_http_link_and_localhost_link_flagged(self):
        html = GOOD_HTML.replace(RESET_URL, "http://localhost:3000/reset?token=Ab3dEf9hIj2kLm4nOp6q")
        _, c = self.analyse(make_eml(html=html))
        self.assertEqual((c["link-https"]["status"], c["link-https"]["severity"]), ("fail", "medium"))
        self.assertEqual((c["dev-hosts"]["status"], c["dev-hosts"]["severity"]), ("fail", "high"))
        self.assertIn("loopback", c["dev-hosts"]["found"])

    def test_staging_host_and_mixed_content(self):
        html = ('<html><body><img src="http://cdn.example.com/x.png" alt="x">'
                '<a href="https://staging.app.example.com/verify">Verify</a></body></html>')
        _, c = self.analyse(make_eml(subject="Verify your email", text="Verify: https://staging.app.example.com/verify", html=html))
        self.assertEqual(c["dev-hosts"]["status"], "fail")
        self.assertIn("staging", c["dev-hosts"]["found"])
        self.assertEqual(c["mixed-content"]["status"], "fail")

    def test_expect_link_host_catches_wrong_host(self):
        html = GOOD_HTML.replace("app.example.com/reset", "example-login.net/reset")
        _, c = self.analyse(make_eml(html=html), "--expect-link-host", "example.com")
        self.assertEqual(c["link-host"]["status"], "fail")
        self.assertIn("example-login.net", c["link-host"]["found"])
        self.assertIn("looks like", c["link-host"]["found"])
        _, c = self.analyse(make_eml(html=html))
        self.assertEqual(c["link-host"]["status"], "na")

    def test_oversized_flagged(self):
        big = GOOD_HTML.replace("</body>", "<p>%s</p></body>" % ("padding " * 16000))
        _, c = self.analyse(make_eml(html=big))
        self.assertEqual(c["size"]["status"], "fail")
        self.assertIn("KB", c["size"]["problem"])

    def test_missing_headers_long_subject_and_alt(self):
        html = GOOD_HTML.replace(' alt="Example"', "")
        _, c = self.analyse(make_eml(subject="x" * 90, html=html, drop=("Message-ID", "Date")))
        self.assertEqual(c["headers"]["status"], "fail")
        self.assertIn("Message-ID", c["headers"]["problem"])
        self.assertEqual(c["subject"]["status"], "fail")
        self.assertEqual(c["img-alt"]["status"], "fail")

    def test_token_printed_twice_and_marketing_without_unsubscribe(self):
        text = GOOD_TEXT + "\nOr paste this code: Ab3dEf9hIj2kLm4nOp6q\n"
        _, c = self.analyse(make_eml(text=text))
        self.assertEqual(c["secrets"]["status"], "fail")
        _, c = self.analyse(make_eml(subject="Welcome", text="Your temporary password is Xy7pq2Lm\n"))
        self.assertIn("password printed", c["secrets"]["found"])
        _, c = self.analyse(make_eml(subject="Our autumn collection is here", text="New things to look at.",
                                     html="<p>New things to look at.</p>"))
        self.assertEqual(c["unsubscribe"]["status"], "fail")
        _, c = self.analyse(make_eml(subject="Our autumn collection is here", text="New things.", html="<p>New things.</p>",
                                     headers={"List-Unsubscribe": "<https://example.com/u>"}))
        self.assertEqual(c["unsubscribe"]["status"], "pass")

    def test_not_an_email_refused(self):
        rc, out = run("--dir", self.d, "message", self.eml_file(b"just some text\n", "x.eml"))
        self.assertNotEqual(rc, 0)
        self.assertIn("does not look like an email", out)

    # ----------------------------------------------------------------- dns

    def dns(self, text, domain="example.com"):
        return dict((c["id"], c) for c in mail.check_dns(text, domain)["checks"])

    def test_dns_spf_hard_fail_ok_and_dmarc_reject(self):
        text = ('example.com.  300  IN  TXT  "v=spf1 include:mail.example.net -all"\n'
                '_dmarc.example.com. 300 IN TXT "v=DMARC1; p=reject; rua=mailto:d@example.com"\n')
        c = self.dns(text)
        self.assertEqual((c["spf"]["status"], c["dmarc"]["status"], c["dkim"]["status"]), ("pass", "pass", "na"))

    def test_dns_plus_all_bad_and_dmarc_none_warned(self):
        text = ('example.com\ttext =\n\n\t"v=spf1 +all"\n\n'
                '_dmarc.example.com\ttext =\n\n\t"v=DMARC1; p=none"\n')
        c = self.dns(text)
        self.assertEqual((c["spf"]["status"], c["spf"]["severity"]), ("fail", "medium"))
        self.assertIn("+all", c["spf"]["problem"])
        self.assertEqual((c["dmarc"]["status"], c["dmarc"]["severity"]), ("warn", "low"))

    def test_dns_missing_dmarc_flagged_and_multistring_joined(self):
        text = 'example.com. 300 IN TXT "v=spf1 include:a.example.net " "~all"\n'
        c = self.dns(text)
        self.assertEqual(c["spf"]["status"], "pass")
        self.assertEqual((c["dmarc"]["status"], c["dmarc"]["severity"]), ("fail", "medium"))
        self.assertEqual(self.dns("")["spf"]["status"], "fail")

    def test_dns_dkim_selector(self):
        text = ('example.com. IN TXT "v=spf1 -all"\n'
                's1._domainkey.example.com. IN TXT "v=DKIM1; k=rsa; p=MIIBIjANBgkqh"\n')
        self.assertEqual(self.dns(text)["dkim"]["status"], "pass")
        self.assertEqual(self.dns('s1._domainkey.example.com. IN TXT "v=DKIM1; p="')["dkim"]["status"], "warn")

    def test_dns_command_says_it_does_not_query(self):
        path = os.path.join(self.tp.base, "txt.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('v=spf1 -all\n_dmarc.example.com v=DMARC1; p=quarantine\n')
        rc, out = run("--dir", self.d, "dns", path, "--domain", "example.com")
        self.assertEqual(rc, 0, out)
        self.assertIn("not queried", out)
        self.assertNotIn(ledger.BAD, out)

    # -------------------------------------------------------------- record

    def test_record_writes_findings_and_copies_evidence(self):
        html = GOOD_HTML.replace(RESET_URL, "http://localhost:3000/reset?t=1")
        bad = self.eml_file(make_eml(text=None, html=html), "reset.eml")
        good = self.eml_file(make_eml(subject="Your receipt"), "receipt.eml")
        dns = os.path.join(self.tp.base, "txt.txt")
        with open(dns, "w", encoding="utf-8") as fh:
            fh.write('example.com. 300 IN TXT "v=spf1 +all"\n')
        rc, out = run("--dir", self.d, "record", bad, good, "--dns", dns, "--domain", "example.com")
        self.assertEqual(rc, 0, out)
        for name in ("reset.eml", "receipt.eml", "txt.txt"):
            self.assertTrue(os.path.isfile(os.path.join(self.d, "evidence", "proof-email", name)), name)
        findings = dict((f["id"], f) for f in ledger.load_findings(self.d, "proof-email"))
        self.assertEqual(findings["email-reset-your-password-dev-hosts"]["severity"], "high")
        self.assertEqual(findings["email-reset-your-password-text-part"]["severity"], "low")
        self.assertEqual(findings["email-reset-your-password-link-https"]["severity"], "medium")
        self.assertEqual(findings["email-dns-spf"]["severity"], "medium")
        self.assertEqual(findings["email-dns-dmarc"]["severity"], "medium")
        self.assertFalse(any(k.startswith("email-your-receipt") for k in findings))
        for f in findings.values():
            self.assertEqual((f["status"], f["verified_by"], f["category"]), ("verified", "evidence-file", "email"))
        self.assertEqual(findings["email-reset-your-password-dev-hosts"]["evidence"], ["evidence/proof-email/reset.eml"])
        cov = dict((c["id"], c["status"]) for c in ledger.load_coverage(self.d, "proof-email")["checks"])
        self.assertEqual(cov, {"msg-reset-your-password": "covered", "msg-your-receipt": "covered", "dns": "covered"})
        rc, out = run("--dir", self.d, "status")
        self.assertEqual(rc, 0)
        self.assertIn("3 covered", out)

    def test_record_removes_stale_finding_and_keeps_other_messages(self):
        path = self.eml_file(make_eml(text=None), "reset.eml")
        run("--dir", self.d, "record", path)
        self.assertTrue(os.path.exists(ledger.finding_path(self.d, "proof-email", "email-reset-your-password-text-part")))
        other = self.eml_file(make_eml(subject="Welcome aboard, confirm your email"), "welcome.eml")
        run("--dir", self.d, "record", other)
        with open(path, "wb") as fh:
            fh.write(make_eml())
        rc, out = run("--dir", self.d, "record", path)
        self.assertEqual(rc, 0, out)
        self.assertIn("removed stale finding", out)
        self.assertEqual(ledger.load_findings(self.d, "proof-email"), [])
        cov = dict((c["id"], c["status"]) for c in ledger.load_coverage(self.d, "proof-email")["checks"])
        self.assertEqual(cov["msg-welcome-aboard-confirm-your-email"], "covered")
        self.assertEqual(cov["dns"], "not_applicable")

    def test_record_needs_domain_with_dns(self):
        path = self.eml_file(make_eml())
        rc, out = run("--dir", self.d, "record", path, "--dns", path)
        self.assertNotEqual(rc, 0)
        self.assertIn("go together", out)


if __name__ == "__main__":
    unittest.main()
