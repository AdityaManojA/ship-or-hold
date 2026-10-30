---
name: proof-privacy
description: >-
  Checks a site's privacy basics from saved files: third-party trackers that load
  before the visitor consents, non-essential cookies set on the first visit,
  whether a consent banner and a privacy policy link exist, forms that send
  personal data over plain HTTP or in the URL, and personal data leaking into URLs
  in logs. Offline, read-only, results in the ledger with the saved files as
  evidence. Use when the user says "GDPR check", "privacy audit", "cookie consent",
  "are we tracking before consent", "is personal data leaking", "privacy review",
  or "CCPA".
argument-hint: "[a saved page, or blank to set it up]"
---

# proof-privacy

Most privacy problems on a website are visible in what the page loads and what the
first response sets: an analytics tag that fires before anyone clicked accept, an
advertising cookie on the first visit, an email address in a URL that ends up in
every log. This skill reads those from saved files and records each problem with
the file as proof.

What the user typed after `/proof-privacy`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/privacy.py" page saved/home.html
python3 "${CLAUDE_SKILL_DIR}/privacy.py" cookies saved/home-headers.txt
python3 "${CLAUDE_SKILL_DIR}/privacy.py" urls saved/urls.txt
python3 "${CLAUDE_SKILL_DIR}/privacy.py" record saved/home.html --headers saved/home-headers.txt --urls saved/urls.txt
```

## Step 1: capture a first visit

In a fresh browser profile (or a private window), before clicking anything on the
consent banner:

- Save the page's HTML as served (view source, or `curl -s <url> -o saved/home.html`).
- Save the response headers of that first request (`curl -sI <url> > saved/home-headers.txt`,
  or copy them from the browser's network panel). These show the cookies set
  before consent.
- Optionally collect a list of URLs the app uses, one per line, from server logs or
  a browser network export, to check for personal data in query strings.

Only capture a site you own or are authorised to test.

## Step 2: check

```bash
python3 "${CLAUDE_SKILL_DIR}/privacy.py" page saved/home.html --host shop.example
```

`--host` (or the first URL in `proof/target.json`) tells first-party from
third-party. Each known analytics, advertising, session-recording or marketing tag
is listed with whether it waits for consent (a tag marked `type="text/plain"` or
with a consent data attribute is treated as gated) or loads straight away.
Cookieless analytics are noted but not flagged.

## Step 3: record

```bash
python3 "${CLAUDE_SKILL_DIR}/privacy.py" record saved/home.html --headers saved/home-headers.txt --urls saved/urls.txt
```

Six checks go in the coverage ledger: trackers wait for consent, a consent banner
is shown, a privacy policy is linked, personal data is not sent insecurely, no
non-essential cookies on the first visit, and no personal data in URLs. Each
problem becomes a finding verified by the saved file. A check with no input file is
marked not applicable with a reason.

## What a person still has to check

- That "reject" really rejects: click it, then confirm no tracker loads and no
  non-essential cookie is set (capture again and re-run).
- That the privacy policy matches what the app actually collects and who it shares
  it with.
- Data retention, deletion requests and export, which are processes, not pages.
- Data sent from the app's servers to other services, which no page shows.

## Honest limits

This reads a snapshot of one page and one response. Tags injected later by a tag
manager or after scrolling are not in the HTML, so a clean page check does not
prove nothing tracks; repeat it with a capture of the network requests if that
matters. The tracker list covers common services, not every one. This is a
technical check, not legal advice: what counts as consent and which cookies are
strictly necessary depends on your jurisdiction.

## Output / next

`proof/coverage/proof-privacy.json`, findings under `proof/findings/proof-privacy/`,
the saved files under `proof/evidence/proof-privacy/`. Then `/proof-report`.
