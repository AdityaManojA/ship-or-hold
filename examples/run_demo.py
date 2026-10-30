#!/usr/bin/env python3
"""Run Proof end to end against the demo shop and show what it finds.

Starts the demo app on a free local port, captures its home page and first-visit
headers, runs the secrets, dependency, infra, privacy, SEO and web-security
skills against it, and compiles the verdict, a SARIF file and an HTML report.
Everything stays on this machine: the only connection is to 127.0.0.1.

    python3 examples/run_demo.py                 # writes examples/demo-shop/proof/
    python3 examples/run_demo.py --out /tmp/proof-demo

The demo has problems planted on purpose, so the expected verdict is Not yet.
"""

import argparse
import contextlib
import importlib.util
import io
import os
import shutil
import socket
import sys
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
DEMO = os.path.join(ROOT, "examples", "demo-shop")

REQUIRED = ["proof-sec-secrets", "proof-sec-deps", "proof-infra", "proof-privacy", "proof-seo"]

# What the demo plants, and the finding category that proves Proof caught it.
EXPECTED = [
    ("hard-coded secret in config.py", "proof-sec-secrets", "secret"),
    ("vulnerable dependency", "proof-sec-deps", "dependency"),
    ("container runs as root", "proof-infra", "infra:docker-root"),
    ("unpinned base image", "proof-infra", "infra:docker-latest"),
    ("tracker loads before consent", "proof-privacy", "privacy:trackers-before-consent"),
    ("no consent banner", "proof-privacy", "privacy:consent-banner"),
    ("form posts over plain HTTP", "proof-privacy", "privacy:insecure-forms"),
    ("advertising cookie on first visit", "proof-privacy", "privacy:first-load-cookies"),
    ("missing security headers and cookie flags", "proof-sec-web", "headers"),
]


def tool(skill, filename):
    path = os.path.join(SKILLS, skill, filename)
    spec = importlib.util.spec_from_file_location("demo_%s_%s" % (skill.replace("-", "_"), filename[:-3]), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(skill, filename, argv, show=True):
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = tool(skill, filename).main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    if show:
        for line in out.getvalue().strip().splitlines()[:6]:
            print("    " + line)
    return code, out.getvalue()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def capture(port, dest_dir):
    """Fetch the home page from the local demo app; save body and raw headers."""
    with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        status = "HTTP/1.1 %d %s" % (resp.status, resp.reason)
        headers = "\n".join("%s: %s" % (k, v) for k, v in resp.headers.items())
    os.makedirs(dest_dir, exist_ok=True)
    page = os.path.join(dest_dir, "home.html")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(body)
    hdrs = os.path.join(dest_dir, "home-headers.txt")
    with open(hdrs, "w", encoding="utf-8") as fh:
        fh.write(status + "\n" + headers + "\n\n")
    return page, hdrs


def run(out_dir, quiet=False):
    say = (lambda *a: None) if quiet else print
    d = os.path.abspath(out_dir)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    sys.path.insert(0, DEMO)
    app = tool_from(DEMO, "app.py")
    port = free_port()
    httpd = app.serve(port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        say("1. plan the target")
        call("proof-plan", "target.py", ["--dir", d, "init", "--name", "Larkspur Goods (demo)", "--kind", "web",
                                         "--authorised", "yes", "--url", "http://127.0.0.1:%d" % port,
                                         "--done", "no high findings"] + sum([["--require", s] for s in REQUIRED], []),
             show=not quiet)
        say("2. capture the home page and first-visit headers")
        page, hdrs = capture(port, os.path.join(d, "captures"))
        say("3. secrets");      call("proof-sec-secrets", "entropy.py", ["--dir", d, "record", DEMO], show=not quiet)
        # Secret findings arrive unverified until a person confirms them. Here the
        # secret in config.py is planted, so the runner confirms that one, and only that one.
        ledger = tool("proof-core", "ledger.py")
        for f in ledger.load_findings(d, "proof-sec-secrets"):
            if (f.get("location") or "").replace("\\", "/").startswith("config.py"):
                ledger.verify(d, "proof-sec-secrets", f["id"], by="manual",
                              note="planted demo secret in config.py, confirmed by the demo runner")
                say("    confirmed: %s" % f["title"])
        say("4. dependencies"); call("proof-sec-deps", "advisories.py",
                                     ["--dir", d, "audit", DEMO, "--advisories", os.path.join(DEMO, "advisories.json")],
                                     show=not quiet)
        say("5. infra");        call("proof-infra", "infra.py", ["--dir", d, "record", DEMO], show=not quiet)
        say("6. privacy");      call("proof-privacy", "privacy.py", ["--dir", d, "record", page, "--host", "127.0.0.1",
                                                                     "--headers", hdrs], show=not quiet)
        say("7. seo");          call("proof-seo", "seo_score.py", ["--dir", d, "record", page], show=not quiet)
        say("8. web security headers (optional skill, run anyway)")
        ev_dir = os.path.join(d, "evidence", "proof-sec-web")
        os.makedirs(ev_dir, exist_ok=True)
        shutil.copy2(hdrs, os.path.join(ev_dir, "home-headers.txt"))
        _, scan = call("proof-sec-web", "secweb.py", ["--dir", d, "scan-headers", hdrs], show=not quiet)
        if "missing" in scan or "no HttpOnly" in scan:
            call("proof-sec-web", "secweb.py", ["--dir", d, "finding", "add", "headers", "--title",
                                                "Missing security headers and weak session cookie flags",
                                                "--severity", "medium", "--evidence",
                                                "evidence/proof-sec-web/home-headers.txt",
                                                "--fix", "Send CSP, HSTS, nosniff and frame protection; set HttpOnly, Secure and SameSite on the session cookie."],
                 show=False)
            ledger = tool("proof-core", "ledger.py")
            for f in ledger.load_findings(d, "proof-sec-web"):
                ledger.verify(d, "proof-sec-web", f["id"], by="evidence-file")
        say("9. verdict, SARIF and HTML report")
        code, text = call("proof-report", "verdict.py", ["--dir", d, "--sarif", os.path.join(d, "proof.sarif")],
                          show=not quiet)
        call("proof-report", "htmlreport.py", ["--dir", d], show=False)
        call("proof-report", "verdict.py", ["--dir", d, "--check"], show=not quiet)
    finally:
        httpd.shutdown()
        httpd.server_close()
        if DEMO in sys.path:
            sys.path.remove(DEMO)
    return d, code


def tool_from(folder, filename):
    spec = importlib.util.spec_from_file_location("demo_app", os.path.join(folder, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def caught(d):
    """Return [(description, skill, category, found)] for every planted problem."""
    ledger = tool("proof-core", "ledger.py")
    out = []
    for desc, skill, cat in EXPECTED:
        found = any(f["category"].startswith(cat) and f["status"] == "verified"
                    for f in ledger.load_findings(d, skill))
        out.append((desc, skill, cat, found))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(DEMO, "proof"))
    a = ap.parse_args(argv)
    d, code = run(a.out)
    print("\nPlanted problems caught:")
    results = caught(d)
    for desc, skill, cat, found in results:
        print("  %s %-44s %s" % ("ok" if found else "!!", desc, skill))
    missed = [r for r in results if not r[3]]
    print("\n%d of %d planted problems caught. Report: %s" % (len(results) - len(missed), len(results),
                                                            os.path.join(d, "report.html")))
    return 0 if not missed else 1


if __name__ == "__main__":
    sys.exit(main())
