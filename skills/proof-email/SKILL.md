---
name: proof-email
description: >-
  Checks the transactional emails an app sends (signup, verify, password reset,
  receipt) from saved .eml files and a saved DNS TXT capture, with no network calls.
  Per message it checks the required headers, subject length, a plain-text part next
  to the HTML, https links and images, localhost or staging hosts leaking into links,
  links pointing at the expected domain, List-Unsubscribe on marketing-type mail,
  tokens or secrets printed in the body, message size and image alt text. From the
  captured TXT records it checks SPF, DMARC and any DKIM selectors. Use when the user
  says "check my emails", "test the password reset email", "are my transactional
  emails ok", "email deliverability check", "check SPF and DMARC", "why do my emails
  go to spam", "check the signup email", "email links point at localhost", or
  "audit the receipt email".
argument-hint: "[one or more saved .eml files; optionally a TXT record file and the domain]"
---

# proof-email

Look at the emails an app sends the way a careful reviewer would, without sending
any mail or querying any server. The user captures the real messages the app sends
(signup, verify, password reset, receipt) as `.eml` files and pastes the domain's
TXT records into a text file; this skill reads those files, checks each message
structurally, checks SPF, DMARC and DKIM in the capture, and writes coverage and
verified findings to the ledger. Use it before launch, after changing the mail
templates or the mail provider, or when users say the reset link is broken or the
mail lands in spam. It does not send mail, resolve DNS or judge the copy.

What the user typed after `/proof-email`: `$ARGUMENTS`

## The tool

```bash
python3 "${CLAUDE_SKILL_DIR}/mailcheck.py" message <file.eml> [--expect-link-host example.com] [--json]
python3 "${CLAUDE_SKILL_DIR}/mailcheck.py" dns <txt_records_file> --domain example.com [--json]
python3 "${CLAUDE_SKILL_DIR}/mailcheck.py" record <file.eml> [<file.eml> ...] [--dns <file> --domain example.com] [--expect-link-host example.com]
python3 "${CLAUDE_SKILL_DIR}/mailcheck.py" status
```

All commands take `--dir` (default `proof`). The tool makes no network calls: no
DNS lookups, no SMTP. It reads the files it is given.

## Step 1: capture the emails

Trigger each email in the app (sign up, ask for a password reset, buy something in
test mode) and save the full message source, headers included, as an `.eml` file
under `proof/evidence/proof-email/`. Three ways, depending on what the user has:

- A local SMTP catcher the user runs on their machine. Point the app's SMTP settings
  at it in development; most catchers have a "download" or "view source" button
  that saves the raw message as `.eml`.
- The framework's development mailbox. Many web frameworks can write outgoing mail
  to a folder or show it in a dev-only page instead of sending it; save the raw
  source from there.
- A real test send. Send to a test inbox you own, open the message, use "show
  original" or "save as" in the mail client and save it as `.eml`. This is the only
  way to see what the mail provider adds (DKIM signature, tracking links).

Name the files after the flow (`reset.eml`, `receipt.eml`). Never capture mail sent
to real users; use test accounts.

## Step 2: capture the DNS records

The tool does not query DNS. Ask the user to run these and paste the output into one
text file, for example `proof/evidence/proof-email/txt-records.txt`:

```bash
dig TXT example.com
dig TXT _dmarc.example.com
dig TXT <selector>._domainkey.example.com    # the DKIM selector the mail provider gave you
```

On Windows, `nslookup -type=TXT example.com` (and the same for `_dmarc.` and the
selector) works; its output can be pasted as is. Bare record lines such as
`v=spf1 include:... -all` also work. DKIM selectors cannot be listed from the
domain, so the check only covers the selectors the file includes.

## Step 3: check each message

```bash
python3 "${CLAUDE_SKILL_DIR}/mailcheck.py" message proof/evidence/proof-email/reset.eml --expect-link-host example.com
```

Each line is one check with a mark (`ok` pass, `!!` fail, `--` not applicable):

| check | fails when | severity in record |
| --- | --- | --- |
| headers | From, To, Subject, Date or Message-ID is missing | low |
| subject | empty, or longer than 78 characters | low |
| text-part | HTML with no text/plain part (not multipart/alternative) | low |
| mixed-content | an image or other resource loads over http:// | medium |
| link-https | a link is http:// or relative (mailto and tel are skipped) | medium |
| dev-hosts | a link or URL points at localhost, 127.x, a private address, a .local or .test name, a staging or dev host, or an odd port | high |
| link-host | with `--expect-link-host`, a URL points anywhere else; lookalike hosts are named | medium |
| unsubscribe | marketing-looking mail without List-Unsubscribe (for mail that reads as transactional, such as reset, receipt or verify, it is noted as optional) | low |
| secrets | a token from a link is printed more than once, or a password or key appears in the body | medium |
| size | the message is over 100 KB (some clients clip it) | low |
| img-alt | an image has no alt attribute | low |

A third-party click-tracking domain from the mail provider fails `link-host`. That
is worth knowing (readers see an unfamiliar host), so leave it as a finding or
reject it with a reason if the user accepts it.

## Step 4: check the DNS capture

```bash
python3 "${CLAUDE_SKILL_DIR}/mailcheck.py" dns proof/evidence/proof-email/txt-records.txt --domain example.com
```

SPF passes with one `v=spf1` record ending in `-all` or `~all`; `+all` fails (any
server may send as the domain), `?all` or no `all` term warns, and two SPF records
fail. DMARC passes at `_dmarc.<domain>` with `p=quarantine` or `p=reject`, warns on
`p=none`, and fails when missing. DKIM passes for each selector record with a key and
warns on an empty `p=` (revoked key). Warnings are marked `~~`.

## Step 5: check by eye

The tool checks structure. Before calling the emails done, open each one and look:

- Rendering in the major clients: a webmail client, a desktop client and the phone's
  built-in mail app at least. Tables, buttons and images break differently in each.
- Dark mode: logos with transparent backgrounds, dark text on forced dark
  backgrounds, images that disappear.
- Mobile width: the main button is reachable and readable at about 375 px, nothing
  scrolls sideways.
- The link works: click the reset or verify link once, then again. The second click
  should fail (single use) and an old link should expire.
- Images off: the message still makes sense from the text and alt text alone.

Record what you saw as a manual finding through the ledger when something is wrong.

## Step 6: record

```bash
python3 "${CLAUDE_SKILL_DIR}/mailcheck.py" record proof/evidence/proof-email/reset.eml proof/evidence/proof-email/receipt.eml \
  --dns proof/evidence/proof-email/txt-records.txt --domain example.com --expect-link-host example.com
```

Coverage gets one check per message, named `msg-<subject>`, plus `dns`. Messages
recorded in earlier runs stay in the coverage; re-recording a message replaces its
own entries. Without `--dns`, `dns` keeps an earlier record or is marked not
applicable with a note. Each failed check becomes a finding (category `email`, id
`email-<subject>-<check>`; DNS findings are `email-dns-spf`, `email-dns-dmarc`,
`email-dns-dkim`), verified by `evidence-file`: the `.eml` or TXT file is copied into
`proof/evidence/proof-email/` when it lives elsewhere and the finding points at it.
Severities: localhost or staging links high; http links and images medium; missing
or `+all` SPF and missing DMARC medium; DMARC `p=none` low; missing text part low. A
finding whose check now passes is removed; one the user rejected as a false positive
is left alone. `status` prints the coverage and findings.

## Honest limits

This is a structural check, not a deliverability guarantee. Spam filtering depends
on sender reputation, content, volume and the receiver's own rules, none of which
the tool sees. Rendering differs by client and by dark mode; the tool parses the
HTML, it does not draw it, so Step 5 is not optional. The DNS checks are only as
current as the file the user captured, and the tool cannot tell whether the file
came from the real zone. DKIM is checked only for selectors in the file, and a
signature on the message itself is not validated. The transactional, token and
secret checks are pattern-based: they can miss a leak and can flag a harmless value.

## Output / next

`proof/coverage/proof-email.json`, findings under `proof/findings/proof-email/`,
copied messages and the TXT capture under `proof/evidence/proof-email/`. Then
`/proof-report` for the verdict, `/proof-sec-secrets` if a key or token turned up in
a message, and `/proof-web-e2e` to test the reset and verify flows end to end.
