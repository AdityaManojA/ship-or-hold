#!/usr/bin/env python3
"""Larkspur Goods: a tiny demo shop with problems planted on purpose.

This app exists so Proof has something real to find. Do not use it as an example of
how to build anything. Run it with:

    python3 app.py --port 8077

Planted problems (each one a Proof skill should catch):
  - an analytics tag that loads before any consent, and no consent banner (privacy)
  - an advertising cookie set on the first visit (privacy)
  - a signup form that posts personal data over plain HTTP (privacy)
  - no meta description and an image with no alt text (SEO)
  - a session cookie without HttpOnly, Secure or SameSite, no security headers (web security)
  - a hard-coded secret in config.py (secrets)
  - a pinned dependency with a known advisory in requirements.txt (dependencies)
  - a Dockerfile that runs as root on an unpinned base image (infra)
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Larkspur Goods | Handmade notebooks</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-DEMO000000"></script>
<link rel="canonical" href="http://localhost/">
</head><body>
<h1>Handmade notebooks</h1>
<img src="/static/notebook.jpg">
<p>Larkspur Goods sells handmade notebooks bound in Lisbon. Each notebook has 192 pages of 100 gsm paper,
a linen cover and a lay-flat binding, and ships within 3 working days to 28 countries in Europe.</p>
<h2>Join the list</h2>
<form action="http://localhost/signup" method="post">
  <input type="email" name="email" placeholder="Email">
  <input type="password" name="password" placeholder="Password">
  <button>Sign up</button>
</form>
<a href="/shop">Shop</a>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "LarkspurDemo/1.0"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HOME.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "session=d3m0s3ss10n; Path=/")
            self.send_header("Set-Cookie", "_ga=GA1.1.123456789.1700000000; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


def serve(port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return httpd


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8077)
    a = ap.parse_args()
    print("Larkspur demo shop on http://127.0.0.1:%d (Ctrl-C to stop)" % a.port)
    try:
        serve(a.port).serve_forever()
    except KeyboardInterrupt:
        pass
